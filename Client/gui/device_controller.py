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
OpenHarmony_Scrcpy 设备控制器
"""

import re
import time
import tkinter as tk
from typing import Tuple, Optional, Any

from core.constants import LogLevel
from core.logger import print_log
from core.hdc_executor import HDCCommandExecutor


class DeviceController:
    """设备控制器"""

    ORIENTATION_ROTATIONS = {
        "1": 0,
        "2": 90,
        "3": 180,
        "4": 270,
    }
    
    KEY_MAPPINGS: dict = {
        "home": 1,
        "back": 2,
        "volume_up": 16,
        "volume_down": 17,
        "power": 18,
        "camera": 19,
    }
    
    def __init__(self, hdc_executor: HDCCommandExecutor) -> None:
        self.hdc: HDCCommandExecutor = hdc_executor
        self.display_width: int = 0
        self.display_height: int = 0
        self.display_ratio: float = 0.0
        self.video_width: int = 0
        self.video_height: int = 0
        self.device_width: int = 0
        self.device_height: int = 0
        self.display_rotation: int = 0
        self.orientation_value: str = "1"
        self.left: int = 0
        self.right: int = 0
        self.top: int = 0
        self.bottom: int = 0
        self.video_canvas: Optional[tk.Canvas] = None
        self.drag_start: Optional[Tuple[int, int]] = None
        self.video_client: Optional[Any] = None
        self.stream_touch_active: bool = False
        self.last_touch_move_time: float = 0.0
        self.log_title: str = "设备控制器"
    
    def set_display_resolution(self, video_width: int, video_height: int, canvas_width: int, canvas_height: int) -> Tuple[int, int, float]:
        """计算并设置显示分辨率"""
        if canvas_width <= 10:
            canvas_width = 800
        if canvas_height <= 10:
            canvas_height = 600
        
        self.video_width = video_width
        self.video_height = video_height
        if self.device_width <= 0 or self.device_height <= 0:
            self.device_width = video_width
            self.device_height = video_height

        self.display_ratio = min(canvas_width / video_width, canvas_height / video_height)
        self.display_width = int(video_width * self.display_ratio)
        self.display_height = int(video_height * self.display_ratio)
        print_log(LogLevel.INFO, self.log_title, f"显示尺寸: {self.display_width}x{self.display_height} ratio:{self.display_ratio}")

        self.left = int((canvas_width - self.display_width) / 2)
        self.right = self.left + self.display_width
        self.top = int((canvas_height - self.display_height) / 2)
        self.bottom = self.top + self.display_height
        return self.display_width, self.display_height, self.display_ratio

    def refresh_device_orientation(self) -> int:
        """Read the device orientation without changing device state."""
        result = self.hdc.execute(["shell", "param", "get", "persist.sys.orientation"])
        value = result.get("stdout", "").strip() if result.get("success") else ""
        rotation = self.ORIENTATION_ROTATIONS.get(value)
        if rotation is None:
            self.orientation_value = "1"
            self.display_rotation = 0
            print_log(
                LogLevel.WARN,
                self.log_title,
                "Unable to read a supported display orientation; using portrait.",
            )
            return self.display_rotation

        self.orientation_value = value
        self.display_rotation = rotation
        print_log(
            LogLevel.INFO,
            self.log_title,
            f"Device orientation={value}, display rotation={rotation}",
        )
        return self.display_rotation

    def get_display_rotation(self) -> int:
        """Return the clockwise rotation applied to the displayed frame."""
        return self.display_rotation

    def refresh_device_resolution(self, fallback_width: int, fallback_height: int) -> Tuple[int, int]:
        """Read the physical display size used to derive uinput coordinates.

        ScreenCapture publishes the A333 encoder canvas (672x1072), while
        RenderService reports the physical panel as 800x1280. Landscape
        uinput coordinates use the corresponding logical size 1280x800.
        """
        self.device_width = fallback_width
        self.device_height = fallback_height
        result = self.hdc.execute(["shell", "hidumper", "-s", "RenderService", "-a", "screen"])
        output = result.get("stdout", "") if result.get("success") else ""
        match = re.search(
            r"screen\[0\]:.*?render resolution=(\d+)x(\d+), physical resolution=(\d+)x(\d+)",
            output,
            flags=re.DOTALL,
        )
        if match:
            render_width, render_height, physical_width, physical_height = map(int, match.groups())
            self.device_width = physical_width or render_width
            self.device_height = physical_height or render_height
            print_log(
                LogLevel.INFO,
                self.log_title,
                f"触控坐标尺寸: {self.device_width}x{self.device_height} "
                f"(视频 canvas: {fallback_width}x{fallback_height})",
            )
        else:
            print_log(
                LogLevel.WARN,
                self.log_title,
                f"未读取到 RenderService 主屏尺寸，触控回退到视频 canvas: {fallback_width}x{fallback_height}",
            )
        return self.device_width, self.device_height

    def bind_video_canvas(self, canvas: tk.Canvas) -> None:
        """绑定视频画布"""
        self.video_canvas = canvas
        
        canvas.bind("<ButtonPress-1>", self._on_mouse_down)
        canvas.bind("<B1-Motion>", self._on_mouse_drag)
        canvas.bind("<ButtonRelease-1>", self._on_mouse_up)

    def set_video_client(self, video_client: Optional[Any]) -> None:
        self.video_client = video_client
    
    def reset(self) -> None:
        """重置控制器状态（切换设备时调用）"""
        self.display_width = 0
        self.display_height = 0
        self.display_ratio = 0.0
        self.video_width = 0
        self.video_height = 0
        self.device_width = 0
        self.device_height = 0
        self.display_rotation = 0
        self.orientation_value = "1"
        self.left = 0
        self.right = 0
        self.top = 0
        self.bottom = 0
        self.drag_start = None
        self.video_client = None
        self.stream_touch_active = False
        self.last_touch_move_time = 0.0

    def _get_touch_resolution(self) -> Tuple[int, int]:
        """Return uinput bounds in the orientation shown to the user."""
        if self.display_rotation in (90, 270):
            return self.device_height, self.device_width
        return self.device_width, self.device_height
    
    def _window_to_device_coords(self, window_x: int, window_y: int) -> Tuple[int, int]:
        """窗口坐标转设备坐标"""
        if not self.video_canvas:
            return 0, 0

        display_x = int((window_x - self.left) / self.display_ratio) if self.display_ratio != 0 else 0
        display_y = int((window_y - self.top) / self.display_ratio) if self.display_ratio != 0 else 0
        if self.video_width:
            display_x = max(0, min(display_x, self.video_width - 1))
        if self.video_height:
            display_y = max(0, min(display_y, self.video_height - 1))

        touch_width, touch_height = self._get_touch_resolution()
        device_x = (
            round(display_x * (touch_width - 1) / max(self.video_width - 1, 1))
            if self.video_width and touch_width
            else 0
        )
        device_y = (
            round(display_y * (touch_height - 1) / max(self.video_height - 1, 1))
            if self.video_height and touch_height
            else 0
        )
        if touch_width:
            device_x = max(0, min(device_x, touch_width - 1))
        if touch_height:
            device_y = max(0, min(device_y, touch_height - 1))
        return device_x, device_y

    def _on_mouse_down(self, event: tk.Event) -> None:
        """鼠标按下"""
        if self.left <= event.x <= self.right and self.top <= event.y <= self.bottom:
            self.drag_start = (event.x, event.y)
            device_x, device_y = self._window_to_device_coords(event.x, event.y)
            self.stream_touch_active = self._send_stream_touch(1, device_x, device_y)
            self.last_touch_move_time = 0.0
    
    def _on_mouse_drag(self, event: tk.Event) -> None:
        if self.drag_start is None or not self.stream_touch_active:
            return
        now = time.monotonic()
        if now - self.last_touch_move_time < 1 / 60:
            return
        device_x, device_y = self._window_to_device_coords(event.x, event.y)
        self.stream_touch_active = self._send_stream_touch(2, device_x, device_y)
        self.last_touch_move_time = now
        """鼠标拖动"""
        pass  # 实时预览可以在这里实现
    
    def _send_stream_touch(self, action: int, x: int, y: int) -> bool:
        if self.video_client is None:
            return False
        try:
            return bool(self.video_client.send_touch_event(action, x, y))
        except (AttributeError, OSError):
            return False

    def _on_mouse_up(self, event: tk.Event) -> None:
        """鼠标释放"""
        if self.drag_start is None:
            return
        
        start_x, start_y = self.drag_start
        end_x, end_y = event.x, event.y
        
        dev_start_x, dev_start_y = self._window_to_device_coords(start_x, start_y)
        dev_end_x, dev_end_y = self._window_to_device_coords(end_x, end_y)
        
        drag_distance = ((end_x - start_x)**2 + (end_y - start_y)**2)**0.5
        
        if self.stream_touch_active:
            self._send_stream_touch(3, dev_end_x, dev_end_y)
        elif drag_distance > 10:
            # 滑动操作
            smooth_time = 100
            self.send_swipe(dev_start_x, dev_start_y, dev_end_x, dev_end_y, smooth_time)
        else:
            # 点击操作
            self.send_tap(dev_start_x, dev_start_y)
        
        self.drag_start = None
        self.stream_touch_active = False
    
    def send_key(self, key_name: str) -> bool:
        """发送按键"""
        if key_name not in self.KEY_MAPPINGS:
            print_log(LogLevel.WARN, self.log_title, f"未知按键: {key_name}")
            return False
        
        keycode = self.KEY_MAPPINGS[key_name]
        args = ["shell", "uinput", "-K", "-d", str(keycode), "-u", str(keycode)]
        result = self.hdc.execute(args)
        
        if result["success"]:
            print_log(LogLevel.DEBUG, self.log_title, f"发送按键: {key_name} (keycode={keycode})")
        else:
            print_log(LogLevel.WARN, self.log_title, f"发送按键失败: {key_name}")
        
        return result["success"]
    
    def send_swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 100) -> bool:
        if self._send_stream_touch(1, x1, y1):
            self._send_stream_touch(2, x2, y2)
            return self._send_stream_touch(3, x2, y2)
        """发送滑动"""
        args = ["shell", "uinput", "-T", "-m", str(x1), str(y1), str(x2), str(y2), str(duration_ms)]
        result = self.hdc.execute(args)
        
        if result["success"]:
            print_log(LogLevel.DEBUG, self.log_title, f"发送滑动: ({x1},{y1}) -> ({x2},{y2}), duration={duration_ms}ms")
        
        return result["success"]
    
    def send_tap(self, x: int, y: int) -> bool:
        if self._send_stream_touch(1, x, y):
            return self._send_stream_touch(3, x, y)
        """发送点击"""
        args = ["shell", "uinput", "-T", "-d", str(x), str(y), "-u", str(x), str(y)]
        result = self.hdc.execute(args)
        
        if result["success"]:
            print_log(LogLevel.DEBUG, self.log_title, f"发送点击: ({x},{y})")
        
        return result["success"]
    
    def power_key(self) -> bool:
        """电源键"""
        return self.send_key("power")
    
    def home_key(self) -> bool:
        """Home键"""
        return self.send_key("home")
    
    def back_key(self) -> bool:
        """返回键"""
        return self.send_key("back")
    
    def unlock_screen(self) -> bool:
        """解锁屏幕"""
        return self.send_swipe(350, 1100, 350, 500, 200)
    
    def volume_up(self) -> bool:
        """音量加"""
        return self.send_key("volume_up")
    
    def volume_down(self) -> bool:
        """音量减"""
        return self.send_key("volume_down")



__all__ = ["DeviceController"]
