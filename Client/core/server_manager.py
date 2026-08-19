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
OpenHarmony_Scrcpy 服务端管理器
"""

import sys
import os
import time
import subprocess
import hashlib
import json
import re
import threading
from typing import Optional

from .constants import LogLevel
from .logger import print_log, get_log_file
from .hdc_executor import HDCCommandExecutor
from .runtime_mode import DNAKEIOT_FAMILY, get_runtime_resource_profile

_SERVER_OPERATION_LOCK = threading.RLock()


class ServerManager:
    """服务端管理器"""
    
    def __init__(self, manufacturer: str, hdc_executor: HDCCommandExecutor,
                 build_product: str = "") -> None:
        self.hdc = hdc_executor
        self.server_process: Optional[subprocess.Popen] = None
        self.manufacturer = manufacturer
        self.build_product = build_product
        self.log_title = "服务端管理器"
        # Runtime deployment is per-device and does not require the system
        # partition or an init entry. This also works on a clean device.
        self.remote_server_path = "/data/local/tmp/ohscrcpy_server"
        self.remote_config_path = "/data/local/tmp/ohscrcpy_server.cfg"
        
        self._refresh_resource_paths()

    def _refresh_resource_paths(self) -> None:
        """Refresh paths after a device or packaged resource profile changes."""
        self.resource_profile = get_runtime_resource_profile(self.manufacturer, self.build_product)
        # 二进制与 cfg 必须取自同一资源目录（_get_resource_path 对缺失目录有
        # 通用 64 位兜底）；此前 cfg 走 "default" 路径在打包环境下不存在，
        # 导致 SHA-256 校验 actual=None 而部署失败。
        self.server_exe_file = self._get_resource_path("ohscrcpy_server", self.resource_profile)
        self.server_cfg_file = self._get_resource_path("ohscrcpy_server.cfg", self.resource_profile)
    
    def _get_resource_path(self, filename: str, manufacturer: str = "default") -> str:
        """获取资源文件的正确路径（支持PyInstaller打包）"""
        try:
            if hasattr(sys, '_MEIPASS'):
                base_path = sys._MEIPASS
            else:
                # 开发环境 - 获取 Client 目录（父目录）
                current_dir = os.path.dirname(os.path.abspath(__file__))
                base_path = os.path.dirname(current_dir)  # 从 core/ 向上到 Client/
            
            if manufacturer != "default":
                manu_path = os.path.join(base_path, manufacturer)
                file_path = os.path.join(manu_path, filename)
                if os.path.isfile(file_path):
                    base_path = os.path.join(base_path, manufacturer)
                elif manufacturer in DNAKEIOT_FAMILY:
                    # 开发环境回退到仓库 Server/bin/<profile>/ 下的受管资源
                    source_resource = os.path.join(
                        os.path.dirname(base_path), "Server", "bin", manufacturer, filename)
                    if not hasattr(sys, '_MEIPASS') and os.path.isfile(source_resource):
                        return source_resource
                    print_log(LogLevel.ERROR, self.log_title,
                              f"缺少 {manufacturer} AArch64 服务端资源: {file_path}")
                    return file_path
                else:
                    # 未知厂商：回退到通用 64 位资源（标准 OH_VideoEncoder，无厂商依赖）
                    generic_file = os.path.join(base_path, "Dnakeiot_RK3568", filename)
                    if os.path.isfile(generic_file):
                        print_log(LogLevel.WARN, self.log_title,
                                  f"未找到 {manufacturer} 专用资源，回退通用 64 位资源: {generic_file}")
                        return generic_file
                    source_generic = os.path.join(
                        os.path.dirname(base_path), "Server", "bin", "Dnakeiot_RK3568", filename)
                    if not hasattr(sys, '_MEIPASS') and os.path.isfile(source_generic):
                        print_log(LogLevel.WARN, self.log_title,
                                  f"未找到 {manufacturer} 专用资源，回退通用 64 位资源: {source_generic}")
                        return source_generic
                    print_log(LogLevel.ERROR, self.log_title,
                              f"缺少 {manufacturer} 专用资源且无通用 64 位资源: {file_path}")
                    return file_path
            
            server_path = os.path.join(base_path, filename)
            print_log(LogLevel.DEBUG, self.log_title, f"待安装服务端可执行文件路径: {server_path}")
            return server_path
        except Exception as e:
            print_log(LogLevel.ERROR, self.log_title, f"获取资源路径失败: {e}")
            return filename
    
    def update_manufacturer(self, manufacturer: str, build_product: str = "") -> None:
        """更新设备制造商与构建产品信息"""
        self.manufacturer = manufacturer
        if build_product:
            self.build_product = build_product
        self._refresh_resource_paths()

    def _get_server_manifest(self) -> Optional[dict]:
        manifest_path = os.path.join(os.path.dirname(self.server_exe_file), "server_manifest.json")
        if not os.path.isfile(manifest_path):
            return None

        try:
            with open(manifest_path, "r", encoding="utf-8") as manifest_file:
                manifest = json.load(manifest_file)
            if not isinstance(manifest, dict):
                raise ValueError("manifest 根节点不是对象")
            return manifest
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print_log(LogLevel.ERROR, self.log_title, f"读取服务端 manifest 失败: {exc}")
            return None

    @staticmethod
    def _sha256_file(path: str) -> Optional[str]:
        try:
            digest = hashlib.sha256()
            with open(path, "rb") as executable:
                for block in iter(lambda: executable.read(1024 * 1024), b""):
                    digest.update(block)
            return digest.hexdigest()
        except OSError:
            return None

    @staticmethod
    def _is_aarch64_elf(path: str) -> bool:
        try:
            with open(path, "rb") as executable:
                header = executable.read(20)
        except OSError:
            return False

        return (
            len(header) >= 20
            and header[:4] == b"\x7fELF"
            and header[4] == 2
            and int.from_bytes(header[18:20], byteorder="little") == 183
        )

    def _validate_local_server_resource(self) -> bool:
        if not os.path.isfile(self.server_exe_file):
            print_log(LogLevel.ERROR, self.log_title, f"服务端资源不存在: {self.server_exe_file}")
            return False

        manifest = self._get_server_manifest()
        if manifest is None:
            if self.resource_profile in DNAKEIOT_FAMILY:
                print_log(LogLevel.ERROR, self.log_title,
                          f"{self.resource_profile} 服务端缺少 manifest")
                return False
            return True

        expected_sha256 = manifest.get("sha256")
        if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            print_log(LogLevel.ERROR, self.log_title, "服务端 manifest 的 SHA-256 无效")
            return False

        actual_sha256 = self._sha256_file(self.server_exe_file)
        if actual_sha256 != expected_sha256:
            print_log(LogLevel.ERROR, self.log_title,
                      f"服务端资源 SHA-256 不匹配: expected={expected_sha256}, actual={actual_sha256}")
            return False

        if manifest.get("target_abi") == "aarch64" and not self._is_aarch64_elf(self.server_exe_file):
            print_log(LogLevel.ERROR, self.log_title, "服务端资源不是 ELF64/AArch64")
            return False

        expected_cfg_sha256 = manifest.get("config_sha256")
        actual_cfg_sha256 = self._sha256_file(self.server_cfg_file)
        if expected_cfg_sha256 is not None and actual_cfg_sha256 != expected_cfg_sha256:
            print_log(LogLevel.ERROR, self.log_title,
                      f"服务配置 SHA-256 不匹配: expected={expected_cfg_sha256}, actual={actual_cfg_sha256}")
            return False

        return True

    def _get_remote_server_sha256(self) -> Optional[str]:
        return self._get_remote_sha256(self.remote_server_path)

    def _get_remote_sha256(self, remote_path: str) -> Optional[str]:
        result = self.hdc.execute(["shell", "sha256sum", remote_path])
        if not result.get("success"):
            return None

        output = result.get("stdout", "")
        match = re.search(r"\b([0-9a-fA-F]{64})\b", output)
        return match.group(1).lower() if match else None
    
    def check_device_abi(self) -> bool:
        """确认设备为64位镜像（OHScrcpy 64位版本仅支持64位镜像设备）。

        动态加载器位置随镜像不同：优先 /lib（本厂商 v9611/6.1 镜像），
        其次 /system/lib64（musl 动态链接标准布局）。
        """
        print_log(LogLevel.INFO, self.log_title, "检查设备 ABI（要求 64 位镜像）...")
        if self.hdc.check_file_exists("/lib/ld-musl-aarch64.so.1") or \
                self.hdc.check_file_exists("/system/lib64/ld-musl-aarch64.so.1"):
            print_log(LogLevel.INFO, self.log_title, "设备为 64 位镜像，ABI 检查通过")
            return True
        if self.hdc.check_file_exists("/lib/ld-musl-arm.so.1") or \
                self.hdc.check_file_exists("/system/lib/ld-musl-arm.so.1"):
            print_log(LogLevel.FATAL, self.log_title,
                      "设备为 32 位镜像：OHScrcpy 已停止 32 位支持，请将设备升级为 64 位镜像")
            return False
        print_log(LogLevel.ERROR, self.log_title,
                  "无法确认设备 ABI（未找到 64/32 位动态加载器），已中止部署")
        return False

    def install_server(self) -> bool:
        """安装服务端"""
        print_log(LogLevel.INFO, self.log_title, f"开始安装...")

        if not self.check_device_abi():
            return False

        if not self._validate_local_server_resource():
            return False
        
        if not os.path.exists(self.server_cfg_file):
            print_log(LogLevel.FATAL, self.log_title, f"错误: {self.server_cfg_file} 文件不存在")
            return False

        # Stop any init-managed or temporary instance before replacement.
        self._stop_device_service()
        
        server_tmp = "/data/local/tmp/ohscrcpy_server.package"
        config_tmp = "/data/local/tmp/ohscrcpy_server.cfg.package"

        print_log(LogLevel.DEBUG, self.log_title, f"推送可执行文件到临时路径...")
        result = self.hdc.execute(["file", "send", self.server_exe_file, server_tmp])
        if not result["success"]:
            print_log(LogLevel.ERROR, self.log_title, f"推送 ohscrcpy_server 失败: {result.get('stderr', '未知错误')}")
            return False

        print_log(LogLevel.DEBUG, self.log_title, f"替换服务端可执行文件...")
        result = self.hdc.execute(["shell", "chmod", "+x", server_tmp])
        if not result["success"]:
            print_log(LogLevel.ERROR, self.log_title, f"设置可执行权限失败: {result.get('stderr', '未知错误')}")
            return False
        result = self.hdc.execute(["shell", "mv", "-f", server_tmp, self.remote_server_path])
        if not result["success"]:
            print_log(LogLevel.ERROR, self.log_title, f"替换 ohscrcpy_server 失败: {result.get('stderr', '未知错误')}")
            return False

        print_log(LogLevel.DEBUG, self.log_title, f"推送配置文件到临时路径...")
        result = self.hdc.execute(["file", "send", self.server_cfg_file, config_tmp])
        if not result["success"]:
            print_log(LogLevel.ERROR, self.log_title, f"推送 ohscrcpy_server.cfg 失败: {result.get('stderr', '未知错误')}")
            return False

        result = self.hdc.execute(["shell", "mv", "-f", config_tmp, self.remote_config_path])
        if not result["success"]:
            print_log(LogLevel.ERROR, self.log_title, f"替换 ohscrcpy_server.cfg 失败: {result.get('stderr', '未知错误')}")
            return False

        manifest = self._get_server_manifest()
        if manifest is not None:
            remote_sha256 = self._get_remote_server_sha256()
            remote_cfg_sha256 = self._get_remote_sha256(self.remote_config_path)
            if remote_sha256 != manifest["sha256"] or remote_cfg_sha256 != manifest.get("config_sha256"):
                print_log(LogLevel.ERROR, self.log_title,
                          "部署后设备端哈希仍不匹配: "
                          f"binary={remote_sha256}, config={remote_cfg_sha256}")
                return False

        print_log(LogLevel.INFO, self.log_title, f"安装完成")
        return True

    def _stop_device_service(self) -> None:
        """Stop the init-managed service before replacing its executable."""
        self.hdc.execute(["shell", "param", "set", "ctl.stop", "ohscrcpy_server"])
        self.hdc.execute(["shell", "pkill", "-f", "ohscrcpy_server"])
        self.hdc.execute(["shell", "killall", "ohscrcpy_server"])
        time.sleep(0.2)

    def uninstall_server(self) -> bool:
        """卸载服务端"""
        print_log(LogLevel.INFO, self.log_title, f"开始卸载...")
        
        self.stop_server()
        
        result = self.hdc.execute(["shell", "rm", "-f", self.remote_server_path])
        if not result["success"]:
            print_log(LogLevel.ERROR, self.log_title, f"删除 ohscrcpy_server 失败: {result.get('stderr', '未知错误')}")
        
        result = self.hdc.execute(["shell", "rm", "-f", self.remote_config_path])
        if not result["success"]:
            print_log(LogLevel.ERROR, self.log_title, f"删除 ohscrcpy_server.cfg 失败: {result.get('stderr', '未知错误')}")
        
        print_log(LogLevel.INFO, self.log_title, f"卸载完成")
        return True
    
    def start_server(self, port: int) -> bool:
        """启动服务端"""
        if self.resource_profile in DNAKEIOT_FAMILY and self._is_runtime_server_running(port):
            print_log(LogLevel.INFO, self.log_title, f"运行时服务已监听端口: {port}")
            return True
        if self.resource_profile not in DNAKEIOT_FAMILY and self.check_server_running(port=port):
            print_log(LogLevel.INFO, self.log_title, f"服务已监听端口: {port}")
            return True

        self.prepare_server()
        print_log(LogLevel.INFO, self.log_title, f"开始启动...")
        
        device_sn = self.hdc.get_current_device()
        cmd_args = ["shell", self.remote_server_path, "-p", f"{port}"]
        
        if get_log_file() is not None:
            cmd_args.append("--log")
            print_log(LogLevel.DEBUG, self.log_title, "服务端日志落盘已使能")
        
        self.server_process = self.hdc.execute_async_in_shell(cmd_args, title=f"ohscrcpy_server {device_sn}")
        
        if self.server_process:
            print_log(LogLevel.INFO, self.log_title, f"启动命令已执行")
            need_print = True
            start_time = time.time()
            while time.time() - start_time < 5.0:
                if self._is_runtime_server_running(port):
                    print_log(LogLevel.INFO, self.log_title, f"启动成功")
                    return True
                need_print = False
                time.sleep(0.2)
            
            print_log(LogLevel.WARN, self.log_title, f"等待服务启动超时，可能启动失败")
            return False
        else:
            print_log(LogLevel.ERROR, self.log_title, f"启动失败")
            return False
    
    def stop_server(self) -> bool:
        """停止服务端"""
        print_log(LogLevel.INFO, self.log_title, f"开始停止...")

        self._stop_device_service()
        
        if self.server_process:
            try:
                self.server_process.terminate()
                self.server_process.wait(timeout=1)
                print_log(LogLevel.INFO, self.log_title, f"服务进程已停止")
            except (OSError, subprocess.TimeoutExpired):
                try:
                    self.server_process.kill()
                except (OSError, subprocess.SubprocessError):
                    pass
            finally:
                self.server_process = None
        
        self.hdc.stop_async_processes()
        print_log(LogLevel.INFO, self.log_title, f"停止完成")
        return True
    
    def check_server_installed(self) -> bool:
        """检查服务端是否已安装"""
        if not self._validate_local_server_resource():
            return False

        executable_exists = self.hdc.check_file_exists(self.remote_server_path)
        config_exists = self.hdc.check_file_exists(self.remote_config_path)
        
        if executable_exists and config_exists:
            manifest = self._get_server_manifest()
            if manifest is not None:
                remote_sha256 = self._get_remote_server_sha256()
                remote_cfg_sha256 = self._get_remote_sha256(self.remote_config_path)
                if remote_sha256 != manifest["sha256"] or remote_cfg_sha256 != manifest.get("config_sha256"):
                    print_log(LogLevel.INFO, self.log_title,
                              "设备服务端版本不匹配，将更新: "
                              f"binary={remote_sha256}, config={remote_cfg_sha256}")
                    return False
            print_log(LogLevel.INFO, self.log_title, f"服务已安装")
            return True
        
        print_log(LogLevel.INFO, self.log_title, f"服务未安装")
        return False
    
    def check_server_running(self, need_print: bool = True, port: Optional[int] = None) -> bool:
        """检查服务端是否在运行"""
        if port is not None:
            result = self.hdc.execute(["shell", "netstat", "-an"])
            listening = result.get("stdout", "")
            pattern = re.compile(rf":{port}(?:\s|$).*\bLISTEN\b")
            if result.get("success") and pattern.search(listening):
                print_log(LogLevel.INFO, self.log_title, f"服务正在监听端口: {port}")
                return True
            if need_print:
                print_log(LogLevel.INFO, self.log_title, f"服务未监听端口: {port}")
            return False

        result = self.hdc.execute(["shell", "pgrep", "-f", "ohscrcpy_server"])
        
        if result["success"] and result["stdout"]:
            pid = result["stdout"].strip()
            print_log(LogLevel.INFO, self.log_title, f"服务正在运行，PID: {pid}")
            return True
        else:
            if need_print:
                print_log(LogLevel.INFO, self.log_title, f"服务未运行")
            return False

    def _is_runtime_server_running(self, port: int) -> bool:
        # Dnakeiot reserves the default port for its init-managed legacy
        # service. DeviceManager allocates the runtime service from 27184, so
        # a listener on that selected port identifies this client session.
        return self.check_server_running(need_print=False, port=port)

    def ensure_server(self, port: int) -> bool:
        """串行部署指定端口的服务端，避免预部署和连接流程并发启动。"""
        if port <= 0 or port > 65535:
            print_log(LogLevel.ERROR, self.log_title, f"无效服务端口: {port}")
            return False

        with _SERVER_OPERATION_LOCK:
            if not self.check_server_installed():
                self.stop_server()
                if not self.install_server():
                    return False

            return self.start_server(port)
    
    def prepare_server(self) -> bool:
        """准备服务端（唤醒设备等）"""
        print_log(LogLevel.DEBUG, self.log_title, f"准备环境...")
        
        print_log(LogLevel.INFO, self.log_title, f"唤醒设备...")
        result = self.hdc.execute(["shell", "power-shell", "wakeup"])
        if not result["success"]:
            print_log(LogLevel.ERROR, self.log_title, f"唤醒设备失败，继续执行...")
        
        unlock_args = ["shell", "uinput", "-T", "-m", str(350), str(1100), str(350), str(500), str(200)]
        result = self.hdc.execute(unlock_args)
        if not result["success"]:
            print_log(LogLevel.ERROR, self.log_title, f"解锁设备屏幕失败，继续执行...")
        
        print_log(LogLevel.INFO, self.log_title, f"设置屏幕常亮...")
        result = self.hdc.execute(["shell", "power-shell", "setmode", str(602)])
        result = self.hdc.execute(["shell", "power-shell", "timeout", "-o", str(86400000)])
        result = self.hdc.execute(["shell", "hidumper", "-s", str(3301), "-a", "-t"])
        if not result["success"]:
            print_log(LogLevel.ERROR, self.log_title, f"设置屏幕常亮失败，继续执行...")

        return True


__all__ = ["ServerManager"]
