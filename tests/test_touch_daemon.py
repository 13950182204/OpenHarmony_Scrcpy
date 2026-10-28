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
触控注入守护进程单元测试（部署/端口选择/协议/通道回退）
"""

import hashlib
import socket
import threading
import time

import pytest

from core.touch_daemon import DAEMON_BASE_PORT, TouchDaemon, _is_aarch64_elf
from gui.device_controller import DeviceController

_NL = bytes([10])


def _elf64_aarch64() -> bytes:
    """构造一个最小 ELF64 头：magic + 64 位 + e_machine=183"""
    header = bytes([127]) + b"ELF" + bytes([2, 1, 1]) + bytes(9) + bytes([0, 0]) + bytes([183, 0])
    return header + bytes(64)


class FakeHdc:
    """模拟 hdc 执行器：fport/file/shell 三类子命令"""

    def __init__(self, local_hash="", remote_hash="", netstat="", forward_fail_times=0):
        self.local_hash = local_hash
        self.remote_hash = remote_hash
        self.netstat = netstat
        self.forward_fail_times = forward_fail_times
        self.calls = []
        self.forwarded = set()
        self.launch_commands = []

    def execute(self, args, need_sn=True, timeout=5.0):
        self.calls.append(list(args))
        verb = args[0]
        if verb == "fport" and args[1] == "ls":
            lines = "".join("SN    tcp:%d tcp:%d    [Forward]%s" % (port, port, chr(10))
                            for port in sorted(self.forwarded))
            return {"success": True, "stdout": lines}
        if verb == "fport" and args[1] == "rm":
            self.forwarded.discard(int(args[2].split(":")[1]))
            return {"success": True, "stdout": ""}
        if verb == "fport":
            if self.forward_fail_times > 0:
                self.forward_fail_times -= 1
                return {"success": False, "stdout": "[Fail]TCP Port listen failed"}
            self.forwarded.add(int(args[1].split(":")[1]))
            return {"success": True, "stdout": ""}
        if verb == "file":
            self.remote_hash = self.local_hash
            return {"success": True, "stdout": ""}
        if verb == "shell" and args[1] == "sha256sum":
            if not self.remote_hash:
                return {"success": False, "stdout": "No such file or directory"}
            return {"success": True, "stdout": self.remote_hash + "  " + args[2]}
        if verb == "shell" and args[1] == "netstat":
            return {"success": True, "stdout": self.netstat}
        if verb == "shell" and len(args) > 1 and "mmi_touchd" in args[1] and "pkill" not in args[1]:
            self.launch_commands.append(args[1])
            return {"success": True, "stdout": "started"}
        return {"success": True, "stdout": ""}


class FakeVideoClient:
    def __init__(self, result=True):
        self.result = result
        self.events = []

    def send_touch_event(self, action, x, y):
        self.events.append((action, x, y))
        return self.result


class FakeDaemon:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.events = []
        self.reconnects = 0

    def send(self, action, x, y):
        self.events.append((action, x, y))
        if self.results:
            return self.results.pop(0)
        return True

    def reconnect(self):
        self.reconnects += 1
        return True


@pytest.fixture
def local_binary(tmp_path):
    target = tmp_path / "bin" / "arm64"
    target.mkdir(parents=True)
    binary = target / "mmi_touchd"
    binary.write_bytes(_elf64_aarch64())
    return tmp_path, binary


class TestLocalBinary:
    def test_is_aarch64_elf(self, local_binary):
        _, binary = local_binary
        assert _is_aarch64_elf(str(binary))

    def test_reject_wrong_arch(self, tmp_path):
        bad = tmp_path / "bad.bin"
        bad.write_bytes(bytes([127]) + b"ELF" + bytes([1, 1, 0]) + bytes(13) + bytes(40))
        assert not _is_aarch64_elf(str(bad))
        assert not _is_aarch64_elf(str(tmp_path / "missing.bin"))

    def test_resolve_local_binary(self, local_binary):
        tmp_path, binary = local_binary
        daemon = TouchDaemon(FakeHdc())
        daemon._bin_base_dir = lambda: str(tmp_path)
        assert daemon._resolve_local_binary()
        expected = hashlib.sha256(binary.read_bytes()).hexdigest()
        assert daemon.local_sha256 == expected

    def test_resolve_rejects_checksum_mismatch(self, local_binary):
        tmp_path, binary = local_binary
        (tmp_path / "bin" / "arm64" / "mmi_touchd.sha256").write_text("0" * 64, encoding="utf-8")
        daemon = TouchDaemon(FakeHdc())
        daemon._bin_base_dir = lambda: str(tmp_path)
        assert not daemon._resolve_local_binary()
        assert "校验失败" in daemon.last_error

    def test_resolve_missing_binary(self, tmp_path):
        daemon = TouchDaemon(FakeHdc())
        daemon._bin_base_dir = lambda: str(tmp_path)
        assert not daemon._resolve_local_binary()
        assert "缺少本地资源" in daemon.last_error


class TestPortSelection:
    def test_skips_forwarded_and_listening_ports(self):
        hdc = FakeHdc(netstat="tcp 0 0 0.0.0.0:%d 0.0.0.0:* LISTEN%s" % (DAEMON_BASE_PORT + 1, chr(10)))
        hdc.forwarded.add(DAEMON_BASE_PORT)
        daemon = TouchDaemon(hdc)
        assert daemon._select_port() == DAEMON_BASE_PORT + 2

    def test_no_free_port(self):
        hdc = FakeHdc()
        for offset in range(12):
            hdc.forwarded.add(DAEMON_BASE_PORT + offset)
        daemon = TouchDaemon(hdc)
        assert daemon._select_port() == -1

    def test_forward_retries_after_removing_stale_entry(self):
        hdc = FakeHdc(forward_fail_times=1)
        daemon = TouchDaemon(hdc)
        assert daemon._setup_forward(DAEMON_BASE_PORT)
        verbs = [call[1] for call in hdc.calls if call[0] == "fport"]
        assert verbs == ["tcp:%d" % DAEMON_BASE_PORT, "rm", "tcp:%d" % DAEMON_BASE_PORT]


class TestStartAndDeploy:
    def test_start_uploads_when_hash_differs(self, local_binary, monkeypatch):
        tmp_path, binary = local_binary
        expected = hashlib.sha256(binary.read_bytes()).hexdigest()
        hdc = FakeHdc(local_hash=expected, remote_hash="1" * 64)
        daemon = TouchDaemon(hdc)
        daemon._bin_base_dir = lambda: str(tmp_path)
        monkeypatch.setattr(daemon, "_wait_ready", lambda port, timeout=5.0: True)
        assert daemon.start()
        assert daemon.available
        assert daemon.port == DAEMON_BASE_PORT
        assert any(call[0] == "file" for call in hdc.calls)
        assert hdc.launch_commands and str(DAEMON_BASE_PORT) in hdc.launch_commands[0]

    def test_start_skips_upload_when_hash_matches(self, local_binary, monkeypatch):
        tmp_path, binary = local_binary
        expected = hashlib.sha256(binary.read_bytes()).hexdigest()
        hdc = FakeHdc(local_hash=expected, remote_hash=expected)
        daemon = TouchDaemon(hdc)
        daemon._bin_base_dir = lambda: str(tmp_path)
        monkeypatch.setattr(daemon, "_wait_ready", lambda port, timeout=5.0: True)
        assert daemon.start()
        assert not any(call[0] == "file" for call in hdc.calls)

    def test_start_cleans_up_when_connect_fails(self, local_binary, monkeypatch):
        tmp_path, binary = local_binary
        expected = hashlib.sha256(binary.read_bytes()).hexdigest()
        hdc = FakeHdc(local_hash=expected, remote_hash=expected)
        daemon = TouchDaemon(hdc)
        daemon._bin_base_dir = lambda: str(tmp_path)
        monkeypatch.setattr(daemon, "_wait_ready", lambda port, timeout=5.0: False)
        assert not daemon.start()
        assert not daemon.available
        assert daemon.port == -1
        assert any(call[0] == "fport" and call[1] == "rm" for call in hdc.calls)
        assert hdc.forwarded == set()

    def test_start_fails_without_local_binary(self, tmp_path):
        daemon = TouchDaemon(FakeHdc())
        daemon._bin_base_dir = lambda: str(tmp_path)
        assert not daemon.start()
        assert not daemon.available


class TestProtocol:
    def test_send_requires_connection(self):
        daemon = TouchDaemon(FakeHdc())
        assert not daemon.send(1, 10, 10)

    def test_send_rejects_invalid_input(self, monkeypatch):
        daemon = TouchDaemon(FakeHdc())
        daemon.available = True
        daemon.socket = object()
        assert not daemon.send(9, 10, 10)
        assert not daemon.send(1, -1, 10)

    def test_wait_ready_retries_until_ping_succeeds(self, monkeypatch):
        server = _DaemonServer()
        server.start()
        try:
            daemon = TouchDaemon(FakeHdc())
            assert daemon._wait_ready(server.port, timeout=2.0)
            assert server.wait_for("P")
        finally:
            server.close()

    def test_request_talks_to_device_daemon(self, monkeypatch):
        server = _DaemonServer()
        server.start()
        try:
            daemon = TouchDaemon(FakeHdc())
            assert daemon._connect(server.port)
            assert daemon.ping()
            daemon.available = True
            assert daemon.send(1, 10, 20)
            assert daemon.last_cost_ms == 3
            assert daemon.send(2, 30, 40)
            assert server.received[:2] == ["P", "D 10 20"]
            daemon.stop()
            assert server.wait_for("Q")
        finally:
            server.close()

    def test_send_marks_unavailable_when_daemon_dies(self):
        server = _DaemonServer()
        server.start()
        daemon = TouchDaemon(FakeHdc())
        assert daemon._connect(server.port)
        daemon.available = True
        server.close_connection()
        assert not daemon.send(1, 10, 20)
        assert not daemon.available


class _DaemonServer(threading.Thread):
    """本地线程 TCP server，模拟设备端 mmi_touchd 的行协议"""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(2)
        self.port = self.sock.getsockname()[1]
        self.received = []
        self.reply = "ok 3"
        self.conn = None
        self.accepted = threading.Event()

    def run(self):
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        self.conn = conn
        self.accepted.set()
        with conn:
            buffer = b""
            while True:
                chunk = conn.recv(256)
                if not chunk:
                    return
                buffer += chunk
                while _NL in buffer:
                    line, buffer = buffer.split(_NL, 1)
                    text = line.decode("ascii")
                    self.received.append(text)
                    try:
                        if text == "Q":
                            conn.sendall(b"bye" + _NL)
                            return
                        conn.sendall((self.reply).encode("ascii") + _NL)
                    except OSError:
                        return

    def wait_for(self, text, timeout=1.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if text in self.received:
                return True
            time.sleep(0.01)
        return text in self.received

    def close_connection(self):
        """模拟设备端守护进程退出（连接被关闭）"""
        self.accepted.wait(timeout=1.0)
        if self.conn is None:
            return
        try:
            self.conn.close()
        except OSError:
            pass

    def close(self):
        self.close_connection()
        try:
            self.sock.close()
        except OSError:
            pass


class TestDeviceControllerChannel:
    def _controller(self, daemon=None, video_client=None):
        controller = DeviceController(FakeHdc())
        controller.set_video_client(video_client)
        controller.set_touch_daemon(daemon)
        return controller

    def test_daemon_preferred_over_stream(self):
        daemon = FakeDaemon()
        video = FakeVideoClient()
        controller = self._controller(daemon, video)
        assert controller._send_stream_touch(1, 10, 20)
        assert daemon.events == [(1, 10, 20)]
        assert video.events == []
        assert controller.touch_channel == "daemon"

    def test_sticky_channel_ignores_stream_for_move(self):
        daemon = FakeDaemon([True, False, False])
        video = FakeVideoClient()
        controller = self._controller(daemon, video)
        assert controller._send_stream_touch(1, 10, 20)
        assert not controller._send_stream_touch(2, 30, 40)
        assert video.events == []

    def test_up_falls_back_to_stream(self):
        daemon = FakeDaemon([True, False, False])
        video = FakeVideoClient()
        controller = self._controller(daemon, video)
        assert controller._send_stream_touch(1, 10, 20)
        assert controller._send_stream_touch(3, 30, 40)
        assert video.events == [(3, 30, 40)]
        assert controller.touch_channel == "stream"

    def test_fallback_to_stream_when_daemon_missing(self):
        video = FakeVideoClient(result=False)
        controller = self._controller(None, video)
        assert not controller._send_stream_touch(1, 0, 0)
        video.result = True
        assert controller._send_stream_touch(1, 0, 0)
        assert video.events == [(1, 0, 0), (1, 0, 0)]
        assert controller.touch_channel == "stream"

    def test_reset_clears_daemon(self):
        daemon = FakeDaemon()
        controller = self._controller(daemon, FakeVideoClient())
        controller._send_stream_touch(1, 1, 1)
        controller.reset()
        assert controller.touch_daemon is None
        assert controller.touch_channel is None
