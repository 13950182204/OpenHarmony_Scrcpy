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
OpenHarmony_Scrcpy 触控注入守护进程

设备端常驻进程（tools/mmi_touchd）直接调用
OHOS::MMI::InputManager::SimulateInputEvent()，单次注入耗时实测 2~10ms。

对比：/bin/uinput 每次调用都要 fork+exec 新进程并连接 multimodalinput 服务，
实测单次约 340ms（time uinput -T -c 400 300 -> 0m00.34s real）。投屏时的点击/拖动
每帧都走一次 uinput，就会堆积成"点了半天没反应"，这是远程触控迟钝的根因。

守护进程由客户端按需部署：校验本地 aarch64 二进制 -> 推送到设备 -> 端口转发 ->
拉起进程 -> 复用同一条 TCP 连接发送 D/M/U 命令。任一步失败都只告警并回退到
原有的服务端/MMI 注入路径，不影响投屏本身。
"""

import hashlib
import os
import re
import socket
import sys
import threading
import time
from typing import Any, Dict, Optional, Set

from .constants import HOST, LogLevel
from .hdc_executor import HDCCommandExecutor
from .logger import print_log

# ==================== 常量 ====================
LOCAL_BINARY_NAME = "mmi_touchd"
REMOTE_BINARY_PATH = "/data/local/tmp/mmi_touchd"
REMOTE_LOG_PATH = "/data/local/tmp/mmi_touchd.log"
DAEMON_BASE_PORT = 27196
DAEMON_PORT_SCAN = 12

# 换行符用 chr() 拼装，避免源码里出现转义字符导致可读性问题
_LINE_END = chr(10)
_LINE_END_BYTES = _LINE_END.encode("ascii")

# 客户端动作 -> 守护进程命令字
_ACTION_COMMANDS = {1: "D", 2: "M", 3: "U"}

_HASH_PATTERN = re.compile(r"[0-9a-fA-F]{64}")
_PORT_PATTERN = re.compile(r":([0-9]+)")


def _sha256_file(path: str) -> str:
    """计算文件 SHA-256，失败返回空串"""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _is_aarch64_elf(path: str) -> bool:
    """校验 ELF64 + EM_AARCH64（e_machine=183），避免把错误架构推上设备"""
    try:
        with open(path, "rb") as handle:
            header = handle.read(20)
    except OSError:
        return False
    if len(header) < 20 or header[0:4] != bytes([127]) + b"ELF":
        return False
    if header[4] != 2:  # 2 = 64 位
        return False
    machine = header[18] | (header[19] << 8)  # 小端读取 e_machine
    return machine == 183


class TouchDaemon:
    """设备端触控注入守护进程的客户端"""

    def __init__(self, hdc_executor: HDCCommandExecutor, log_title: str = "触控守护进程") -> None:
        self.hdc = hdc_executor
        self.log_title = log_title
        self.available = False
        self.local_binary = ""
        self.local_sha256 = ""
        self.remote_path = REMOTE_BINARY_PATH
        self.port = -1
        self.socket: Optional[socket.socket] = None
        self.last_error = ""
        self.last_cost_ms = -1
        self._recv_buffer = b""
        self._lock = threading.RLock()

    # ==================== 本地资源 ====================
    def _bin_base_dir(self) -> str:
        """打包环境取 _MEIPASS，开发环境取 Client 目录"""
        if getattr(sys, "_MEIPASS", ""):
            return str(getattr(sys, "_MEIPASS"))
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _read_checksum_file(self, path: str) -> str:
        """读取随包生成的 .sha256 文件（允许 certutil/ sha256sum 两种格式）"""
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                text = handle.read()
        except OSError:
            return ""
        match = _HASH_PATTERN.search(text)
        return match.group(0).lower() if match else ""

    def _resolve_local_binary(self) -> bool:
        """定位并校验本地守护进程二进制"""
        candidate = os.path.join(self._bin_base_dir(), "bin", "arm64", LOCAL_BINARY_NAME)
        if not os.path.isfile(candidate):
            self.last_error = "缺少本地资源: " + candidate
            print_log(LogLevel.WARN, self.log_title, self.last_error + "，触控回退服务端注入")
            return False
        if not _is_aarch64_elf(candidate):
            self.last_error = "本地资源不是 aarch64 可执行文件: " + candidate
            print_log(LogLevel.ERROR, self.log_title, self.last_error)
            return False
        actual = _sha256_file(candidate)
        if not actual:
            self.last_error = "无法计算本地资源哈希: " + candidate
            print_log(LogLevel.ERROR, self.log_title, self.last_error)
            return False
        expected = self._read_checksum_file(candidate + ".sha256")
        if expected and expected != actual:
            self.last_error = "本地资源校验失败，期望 " + expected + " 实际 " + actual
            print_log(LogLevel.ERROR, self.log_title, self.last_error)
            return False
        self.local_binary = candidate
        self.local_sha256 = actual
        return True

    # ==================== 设备侧部署 ====================
    def _remote_sha256(self, remote_path: str) -> str:
        """读取设备上文件的 SHA-256，失败返回空串"""
        result = self.hdc.execute(["shell", "sha256sum", remote_path], timeout=10.0)
        for key in ("stdout", "output"):
            text = str(result.get(key) or "")
            match = _HASH_PATTERN.search(text)
            if match:
                return match.group(0).lower()
        return ""

    def _ensure_remote_binary(self) -> bool:
        """确保设备上的二进制与本地一致（哈希相同则不重复上传）"""
        remote = self._remote_sha256(self.remote_path)
        if remote and remote == self.local_sha256:
            print_log(LogLevel.DEBUG, self.log_title, "设备端二进制已是最新，跳过上传")
            return True
        staged = self.remote_path + ".package"
        print_log(LogLevel.INFO, self.log_title, "推送触控守护进程到设备...")
        result = self.hdc.execute(["file", "send", self.local_binary, staged], timeout=30.0)
        if not result.get("success"):
            self.last_error = "推送守护进程失败: " + str(result.get("error") or result.get("stderr") or "")
            print_log(LogLevel.ERROR, self.log_title, self.last_error)
            return False
        for args in (["shell", "chmod", "755", staged], ["shell", "mv", "-f", staged, self.remote_path]):
            result = self.hdc.execute(args, timeout=10.0)
            if not result.get("success"):
                self.last_error = "部署守护进程失败: " + " ".join(args)
                print_log(LogLevel.ERROR, self.log_title, self.last_error)
                return False
        remote = self._remote_sha256(self.remote_path)
        if not remote or remote != self.local_sha256:
            self.last_error = "设备端二进制校验失败，期望 " + self.local_sha256 + " 实际 " + (remote or "空")
            print_log(LogLevel.ERROR, self.log_title, self.last_error)
            return False
        return True

    def _kill_remote_instances(self) -> None:
        """清理历史实例（pkill 无匹配会返回非 0，忽略即可）"""
        self.hdc.execute(["shell", "pkill", "-f", self.remote_path], timeout=10.0)

    # ==================== 端口 ====================
    def _forwarded_ports(self) -> Set[int]:
        """解析 hdc fport ls，拿到已被占用的本地转发端口"""
        ports: Set[int] = set()
        result = self.hdc.execute(["fport", "ls"], timeout=10.0)
        if not result.get("success"):
            return ports
        for line in str(result.get("stdout") or "").splitlines():
            if "tcp:" not in line:
                continue
            match = _PORT_PATTERN.search(line)
            if match:
                ports.add(int(match.group(1)))
        return ports

    def _device_listening_ports(self) -> Set[int]:
        """解析设备 netstat，避免选中设备上已被监听（但未转发）的端口"""
        ports: Set[int] = set()
        result = self.hdc.execute(["shell", "netstat", "-an"], timeout=10.0)
        if not result.get("success"):
            return ports
        for line in str(result.get("stdout") or "").splitlines():
            if "LISTEN" not in line:
                continue
            matches = _PORT_PATTERN.findall(line)
            if matches:
                ports.add(int(matches[-1]))
        return ports

    def _select_port(self) -> int:
        """从基址向后找第一个既未转发也未被监听的端口"""
        used = self._forwarded_ports() | self._device_listening_ports()
        for offset in range(DAEMON_PORT_SCAN):
            port = DAEMON_BASE_PORT + offset
            if port not in used:
                return port
        return -1

    def _setup_forward(self, port: int) -> bool:
        """建立 fport 转发；历史转发残留会导致 TCP Port listen failed，先移除再重试一次"""
        forward = "tcp:" + str(port)
        result = self.hdc.execute(["fport", forward, forward], timeout=15.0)
        if result.get("success"):
            return True
        self.hdc.execute(["fport", "rm", forward, forward], timeout=10.0)
        result = self.hdc.execute(["fport", forward, forward], timeout=15.0)
        if not result.get("success"):
            self.last_error = "建立触控守护进程端口转发失败: " + str(result.get("error") or result.get("stdout") or "")
            print_log(LogLevel.WARN, self.log_title, self.last_error)
            return False
        return True

    def _remove_forward(self, port: int) -> None:
        if port and port > 0:
            forward = "tcp:" + str(port)
            self.hdc.execute(["fport", "rm", forward, forward], timeout=10.0)

    # ==================== 连接 ====================
    def _launch(self, port: int) -> bool:
        """在设备上后台拉起守护进程"""
        command = "".join([
            self.remote_path, " ", str(port),
            " >", REMOTE_LOG_PATH, " 2>&1 & echo started",
        ])
        result = self.hdc.execute(["shell", command], timeout=15.0)
        if not result.get("success"):
            self.last_error = "拉起守护进程失败: " + str(result.get("error") or result.get("stderr") or "")
            print_log(LogLevel.WARN, self.log_title, self.last_error)
            return False
        return True

    def _connect(self, port: int, timeout: float = 1.5) -> bool:
        """连接本机转发端口（守护进程启动需要一点时间，短重试）"""
        deadline = time.monotonic() + max(timeout, 0.2)
        self._close_socket()
        while time.monotonic() < deadline:
            try:
                sock = socket.create_connection((HOST, port), timeout=0.5)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.settimeout(1.0)
                self.socket = sock
                self._recv_buffer = b""
                return True
            except OSError:
                time.sleep(0.05)
        self.last_error = "连接触控守护进程失败，端口 " + str(port)
        print_log(LogLevel.WARN, self.log_title, self.last_error + "，触控回退服务端注入")
        return False

    def _close_socket(self) -> None:
        if self.socket is not None:
            try:
                self.socket.close()
            except OSError:
                pass
        self.socket = None
        self._recv_buffer = b""

    def _request(self, line: str, timeout: float = 1.0, quiet: bool = False) -> Optional[str]:
        """发送一行命令并按行读取回复；通信异常即标记不可用"""
        if self.socket is None:
            return None
        with self._lock:
            try:
                self.socket.sendall((line + _LINE_END).encode("ascii"))
                deadline = time.monotonic() + max(timeout, 0.05)
                while _LINE_END_BYTES not in self._recv_buffer:
                    remain = deadline - time.monotonic()
                    if remain <= 0:
                        raise OSError("等待回复超时")
                    self.socket.settimeout(remain)
                    chunk = self.socket.recv(256)
                    if not chunk:
                        raise OSError("守护进程已关闭连接")
                    self._recv_buffer += chunk
                reply, self._recv_buffer = self._recv_buffer.split(_LINE_END_BYTES, 1)
                return reply.decode("ascii", errors="ignore").strip()
            except OSError as error:
                self.last_error = "触控守护进程通信失败: " + str(error)
                if not quiet:
                    print_log(LogLevel.WARN, self.log_title, self.last_error)
                self._close_socket()
                self.available = False
                return None

    def _cleanup_remote(self) -> None:
        """清理连接与设备侧残留"""
        self._close_socket()
        self._remove_forward(self.port)
        self._kill_remote_instances()
        self.port = -1
        self.available = False

    # ==================== 对外接口 ====================
    def start(self) -> bool:
        """部署并启动守护进程，成功返回 True"""
        self.available = False
        self.last_error = ""
        if not self._resolve_local_binary():
            return False
        if not self._ensure_remote_binary():
            return False
        self._kill_remote_instances()
        port = self._select_port()
        if port < 0:
            self.last_error = "没有可用的触控守护进程端口"
            print_log(LogLevel.WARN, self.log_title, self.last_error);
            return False
        self.port = port
        if not self._setup_forward(port) or not self._launch(port):
            self._cleanup_remote()
            return False
        if not self._wait_ready(port):
            self._cleanup_remote()
            return False
        self.available = True
        self.last_error = ""
        print_log(LogLevel.INFO, self.log_title,
                  "触控注入守护进程就绪，端口 " + str(port) + "（单次注入约 2~10ms）")
        return True

    def ping(self, quiet: bool = False) -> bool:
        """探活：P -> ok 0"""
        reply = self._request("P", timeout=1.0, quiet=quiet)
        return bool(reply and reply.startswith("ok"))

    def _wait_ready(self, port: int, timeout: float = 5.0) -> bool:
        """等到守护进程真正可用：hdc 转发端口会先 accept 再拨号设备侧，
        因此 TCP 连上不代表守护进程已监听，必须用 P 命令确认。"""
        deadline = time.monotonic() + max(timeout, 0.5)
        while time.monotonic() < deadline:
            if self._connect(port, timeout=0.6) and self.ping(quiet=True):
                return True
            self._close_socket()
            time.sleep(0.05)
        self.last_error = "触控守护进程未就绪，端口 " + str(port)
        print_log(LogLevel.WARN, self.log_title, self.last_error + "，触控回退服务端注入")
        return False

    def reconnect(self) -> bool:
        """同端口重连（fport 抖动后视频连接仍在时使用）"""
        if self.port <= 0:
            return False
        if self._wait_ready(self.port, timeout=1.2):
            self.available = True
            return True
        self.available = False
        return False

    def send(self, action: int, x: int, y: int) -> bool:
        """发送一次触控事件：action 1=DOWN 2=MOVE 3=UP，坐标为设备物理像素"""
        if not self.available or self.socket is None:
            return False
        command = _ACTION_COMMANDS.get(action)
        if command is None:
            return False
        try:
            device_x = int(x)
            device_y = int(y)
        except (TypeError, ValueError):
            return False
        if device_x < 0 or device_y < 0:
            return False
        reply = self._request(command + " " + str(device_x) + " " + str(device_y), timeout=1.0)
        if not reply:
            return False
        if reply.startswith("ok"):
            parts = reply.split()
            if len(parts) > 1 and parts[1].isdigit():
                self.last_cost_ms = int(parts[1])
            return True
        self.last_error = "触控守护进程拒绝请求: " + reply
        return False

    def stop(self) -> None:
        """停止守护进程并清理设备侧残留"""
        with self._lock:
            if self.socket is not None:
                try:
                    self.socket.sendall(("Q" + _LINE_END).encode("ascii"))
                except OSError:
                    pass
            self._close_socket()
            self._remove_forward(self.port)
            self.port = -1
            self._kill_remote_instances()
            self.available = False
        print_log(LogLevel.INFO, self.log_title, "触控注入守护进程已停止")
