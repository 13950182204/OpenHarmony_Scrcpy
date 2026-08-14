#!/usr/bin/env python

# Copyright (c) 2026 luodh0157.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
视频显示器 - 负责视频帧的渲染和显示管理
"""

import time
import gc
import threading
import tkinter as tk
from typing import Optional

from PIL import Image

from gui.device_controller import DeviceController
from core import HEARTBEAT_TIMEOUT, LogLevel, print_log


class VideoDisplay:
    """视频显示器"""
    
    def __init__(
        self,
        root: tk.Tk,
        canvas: tk.Canvas,
        device_controller: DeviceController,
        performance_label: tk.Label,
        connection_manager=None,
    ) -> None:
        self.root = root
        self.canvas = canvas
        self.device_controller = device_controller
        self.performance_label = performance_label
        self.connection_manager = connection_manager
        
        self.video_width = 0
        self.video_height = 0
        self.source_video_width = 0
        self.source_video_height = 0
        self.frame_rotation = 0
        self.video_ratio = 0.0
        self.display_width = 0
        self.display_height = 0
        
        self.fps = 0
        self.frame_counter = 0
        self.last_fps_time = time.time()
        self.displayed_frames = 0
        self.last_print_frames = 0
        
        self.last_gc_frame_count = 0
        self.gc_interval_frames = 500
        self.image_refs = []
        
        self.current_frame = None
        self.tk_image = None
        self.status_text_id = None
        self.running_status_text_id = None
        
        self._render_scheduled = False
        self._render_active = False
        self._render_after_id = None
        self.last_display_time = 0.0
        self._canvas_size = (0, 0)
        
        self.log_title = "视频显示器"
    
    def on_frame_decoded(self, frame) -> None:
        """帧解码回调"""
        self.current_frame = frame
        self.frame_counter += 1
        
        current_time = time.time()
        if current_time - self.last_fps_time >= 1.0:
            self.fps = self.frame_counter
            self.frame_counter = 0
            self.last_fps_time = current_time
    
    def schedule_render(self) -> None:
        """Start one render loop for the current connection."""
        if self._render_active:
            return
        self._render_active = True
        self._schedule_next_render(0)

    def _schedule_next_render(self, delay_ms: int) -> None:
        if self._render_active and not self._render_scheduled:
            self._render_scheduled = True
            self._render_after_id = self.root.after(delay_ms, self._do_render)

    def _orient_frame(self, frame: Image.Image) -> Image.Image:
        """Apply the rotation selected from persist.sys.orientation."""
        rotation = self.device_controller.get_display_rotation() if self.device_controller else 0
        self.frame_rotation = rotation if rotation in (0, 90, 180, 270) else 0
        if self.frame_rotation == 90:
            return frame.transpose(Image.Transpose.ROTATE_270)
        if self.frame_rotation == 180:
            return frame.transpose(Image.Transpose.ROTATE_180)
        if self.frame_rotation == 270:
            return frame.transpose(Image.Transpose.ROTATE_90)
        return frame

    def _do_render(self) -> None:
        """执行渲染"""
        self._render_scheduled = False
        self._render_after_id = None
        if not self._render_active:
            return
        
        current_time = time.time()
        
        if self.connection_manager and self.connection_manager.is_connected:
            video_client = self.connection_manager.get_video_client()
            if video_client and hasattr(video_client, 'last_data_time'):
                time_since_last_data = current_time - video_client.last_data_time
                if time_since_last_data > HEARTBEAT_TIMEOUT:
                    print_log(LogLevel.WARN, self.log_title, f"检测到心跳超时 ({time_since_last_data:.1f}秒)，断开连接")
                    return
        
        if current_time - self.last_display_time < (1.0 / 60.0):
            self._schedule_next_render(5)
            return
        
        self.last_display_time = current_time
        
        if not self.connection_manager or not self.connection_manager.is_connected:
            self._schedule_next_render(100)
            return
        
        try:
            video_client = self.connection_manager.get_video_client()
            frame = video_client.get_current_frame(timeout=0) if video_client else None
            if frame is not None:
                self.displayed_frames += 1
                
                if self.last_print_frames != self.displayed_frames and self.displayed_frames % 100 == 0:
                    self.last_print_frames = self.displayed_frames
                    print_log(LogLevel.DEBUG, self.log_title, f"已显示 {self.displayed_frames} 帧，队列大小: {video_client.frame_queue.qsize()}")
                
                if self.displayed_frames - self.last_gc_frame_count >= self.gc_interval_frames:
                    self.last_gc_frame_count = self.displayed_frames
                
                try:
                    pil_img = Image.fromarray(frame)
                    source_width, source_height = pil_img.size
                    pil_img = self._orient_frame(pil_img)
                except Exception as e:
                    print_log(LogLevel.ERROR, self.log_title, f"创建PIL图像失败: {e}")
                    self._schedule_next_render(10)
                    return
                
                canvas_width = self.canvas.winfo_width()
                canvas_width = 800 if canvas_width <= 10 else canvas_width
                canvas_height = self.canvas.winfo_height()
                canvas_height = 600 if canvas_height <= 10 else canvas_height
                canvas_size = (canvas_width, canvas_height)
                
                if (
                    self.video_width != pil_img.width
                    or self.video_height != pil_img.height
                    or self.source_video_width != source_width
                    or self.source_video_height != source_height
                    or self._canvas_size != canvas_size
                ):
                    self.source_video_width = source_width
                    self.source_video_height = source_height
                    self.video_width = pil_img.width
                    self.video_height = pil_img.height
                    self._canvas_size = canvas_size
                    self.video_ratio = 0.0
                    print_log(LogLevel.INFO, self.log_title, f"原始视频尺寸: {self.video_width}x{self.video_height}")
                    print_log(LogLevel.INFO, self.log_title, f"画布尺寸: {canvas_width}x{canvas_height}")
                    
                    if self.device_controller:
                        self.display_width, self.display_height, self.video_ratio = self.device_controller.set_display_resolution(
                            self.video_width, self.video_height, canvas_width, canvas_height)
                
                try:
                    pil_img_resized = pil_img.resize((self.display_width, self.display_height),
                                                     Image.Resampling.BILINEAR)
                except Exception as e:
                    print_log(LogLevel.ERROR, self.log_title, f"缩放图像失败: {e}")
                    pil_img_resized = pil_img
                
                x_offset = (canvas_width - self.display_width) // 2
                y_offset = (canvas_height - self.display_height) // 2
                try:
                    from PIL import ImageTk
                    self.tk_image = ImageTk.PhotoImage(pil_img_resized)
                    self.image_refs.clear()
                    self.image_refs.append(self.tk_image)
                except Exception as e:
                    print_log(LogLevel.ERROR, self.log_title, f"创建Tkinter图像失败: {e}")
                    self._schedule_next_render(10)
                    return
                
                self.canvas.delete("all")
                
                self.canvas.create_image(
                    x_offset, y_offset,
                    anchor=tk.NW,
                    image=self.tk_image
                )
                
                if self.status_text_id:
                    self.canvas.delete(self.status_text_id)
                
                fps_text = self.fps
                video_size = f"{self.video_width}x{self.video_height}"
                status_text = (
                    f"FPS: {fps_text} | "
                    f"视频: {video_size} | 显示: {self.display_width}x{self.display_height}"
                )
                self.status_text_id = self.canvas.create_text(
                    10, 10,
                    anchor=tk.NW,
                    text=status_text,
                    fill="white",
                    font=("Microsoft YaHei", 9),
                    tags="status"
                )
                
                if self.performance_label:
                    self.performance_label.config(text=f"FPS: {fps_text}")
                
                self.canvas.update_idletasks()
            
        except Exception as e:
            if self.connection_manager and self.connection_manager.is_connected:
                print_log(LogLevel.ERROR, self.log_title, f"显示错误: {e}")
        
        self._schedule_next_render(10)
    
    def show_waiting_screen(self, message: str = "等待连接...") -> None:
        """显示等待画面"""
        self.canvas.delete("all")
        self.canvas.config(bg="#1a1a2e")
        
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        
        if canvas_width <= 10 or canvas_height <= 10:
            canvas_width = 800
            canvas_height = 600
        
        self.canvas.create_text(
            canvas_width // 2, canvas_height // 2 - 30,
            text="OHScrcpy - OpenHarmony投屏工具",
            fill="white",
            font=("Microsoft YaHei", 14, "bold")
        )
        
        self.running_status_text_id = self.canvas.create_text(
            canvas_width // 2, canvas_height // 2 + 10,
            text=message,
            fill="#3498db",
            font=("Microsoft YaHei", 12)
        )
        
        self.canvas.create_text(
            canvas_width // 2, canvas_height // 2 + 50,
            text="请先选择设备，然后点击[连接]按钮进行投屏",
            fill="#95a5a6",
            font=("Microsoft YaHei", 10)
        )
        
        self.status_text_id = None
    
    def update_running_status(self, content: str) -> None:
        """更新运行状态"""
        if self.running_status_text_id:
            self.canvas.itemconfig(self.running_status_text_id, text=content)
    
    def reset(self) -> None:
        """重置显示状态"""
        self._render_active = False
        if self._render_after_id is not None:
            try:
                self.root.after_cancel(self._render_after_id)
            except (tk.TclError, AttributeError):
                pass
        self._render_after_id = None
        self.video_width = 0
        self.video_height = 0
        self.source_video_width = 0
        self.source_video_height = 0
        self.frame_rotation = 0
        self.video_ratio = 0.0
        self.display_width = 0
        self.display_height = 0
        self.displayed_frames = 0
        self.frame_counter = 0
        self.last_fps_time = time.time()
        self.last_print_frames = 0
        self.current_frame = None
        self.tk_image = None
        self.image_refs.clear()
        self._render_scheduled = False
        self._canvas_size = (0, 0)
    
    def force_garbage_collection(self) -> None:
        """强制垃圾回收"""
        def _async_garbage_collection_func() -> None:
            print_log(LogLevel.INFO, self.log_title, f"强制垃圾回收...")
            collected = gc.collect()
            print_log(LogLevel.INFO, self.log_title, f"回收了 {collected} 个对象")
        
        threading.Thread(target=_async_garbage_collection_func, daemon=True).start()


__all__ = ["VideoDisplay"]
