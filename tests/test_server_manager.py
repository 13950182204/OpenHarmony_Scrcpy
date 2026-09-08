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
服务端管理器单元测试
"""

import pytest
import os
import hashlib
import json
from unittest.mock import Mock, patch, MagicMock

from core.server_manager import ServerManager
from core.constants import LogLevel


class TestServerManager:
    
    @pytest.fixture
    def mock_hdc(self):
        """创建模拟的HDC执行器"""
        hdc = Mock()
        hdc.device_sn = "test_device"
        hdc.log_title = "MockHDC"
        hdc.hdc_path = "/mock/hdc"
        hdc.execute = Mock(return_value={"success": True, "output": "mock output", "error": ""})
        hdc.execute_async_in_shell = Mock(return_value=Mock())
        return hdc
    
    @pytest.fixture
    def server_manager(self, mock_hdc):
        """创建ServerManager实例"""
        with patch.object(ServerManager, '_get_resource_path', return_value='/mock/path/server'):
            manager = ServerManager(manufacturer="default", hdc_executor=mock_hdc)
            return manager
    
    def test_init(self, server_manager, mock_hdc):
        """测试初始化"""
        assert server_manager.hdc == mock_hdc
        assert server_manager.manufacturer == "default"
        assert server_manager.log_title == "服务端管理器"
    
    def test_update_manufacturer(self, server_manager):
        """测试更新制造商"""
        server_manager.update_manufacturer("HUAWEI")
        assert server_manager.manufacturer == "HUAWEI"
    
    def test_is_installed_true(self, server_manager, mock_hdc):
        """测试已安装状态"""
        server_manager._validate_local_server_resource = Mock(return_value=True)
        mock_hdc.check_file_exists = Mock(return_value=True)
        result = server_manager.check_server_installed()
        assert result == True

    def test_is_installed_false(self, server_manager, mock_hdc):
        """测试未安装状态"""
        server_manager._validate_local_server_resource = Mock(return_value=True)
        mock_hdc.check_file_exists = Mock(return_value=False)
        result = server_manager.check_server_installed()
        assert result == False

    def test_is_running_true(self, server_manager, mock_hdc):
        """测试正在运行状态"""
        mock_hdc.execute.return_value = {"success": True, "stdout": "12345", "stderr": ""}
        result = server_manager.check_server_running()
        assert result == True

    def test_is_running_false(self, server_manager, mock_hdc):
        """测试未运行状态"""
        mock_hdc.execute.return_value = {"success": True, "stdout": "", "stderr": ""}
        result = server_manager.check_server_running()
        assert result == False

    def test_stop(self, server_manager, mock_hdc):
        """测试停止服务"""
        mock_hdc.execute.return_value = {"success": True, "output": ""}
        server_manager.stop_server()
        mock_hdc.execute.assert_called()

    def test_stop_device_service_uses_init_control(self, server_manager, mock_hdc):
        server_manager._stop_device_service()
        calls = [call.args[0] for call in mock_hdc.execute.call_args_list]
        assert ["shell", "param", "set", "ctl.stop", "ohscrcpy_server"] in calls
        assert ["shell", "pkill", "-f", "ohscrcpy_server"] in calls

    def test_get_server_state(self, server_manager):
        """测试获取服务状态"""
        assert hasattr(server_manager, 'check_server_installed')
        assert hasattr(server_manager, 'check_server_running')
        assert hasattr(server_manager, 'stop_server')

    def test_dnakeiot_resource_requires_matching_aarch64_manifest(self, mock_hdc, tmp_path):
        resource_dir = tmp_path / "Dnakeiot"
        resource_dir.mkdir()
        server_path = resource_dir / "ohscrcpy_server"
        cfg_path = resource_dir / "ohscrcpy_server.cfg"
        server_path.write_bytes(b"\x7fELF\x02\x01\x01" + b"\x00" * 11 + (183).to_bytes(2, "little"))
        cfg_path.write_text("{}", encoding="utf-8")
        manifest = {
            "target_abi": "aarch64",
            "sha256": hashlib.sha256(server_path.read_bytes()).hexdigest(),
            "config_sha256": hashlib.sha256(cfg_path.read_bytes()).hexdigest(),
        }
        (resource_dir / "server_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        manager = ServerManager(manufacturer="Dnakeiot", hdc_executor=mock_hdc)
        manager.server_exe_file = str(server_path)
        manager.server_cfg_file = str(cfg_path)

        assert manager._validate_local_server_resource() is True

    def test_installed_server_hash_mismatch_requires_reinstall(self, server_manager, mock_hdc, tmp_path):
        server_path = tmp_path / "ohscrcpy_server"
        cfg_path = tmp_path / "ohscrcpy_server.cfg"
        server_path.write_bytes(b"server")
        cfg_path.write_text("{}", encoding="utf-8")
        manifest = {
            "sha256": hashlib.sha256(server_path.read_bytes()).hexdigest(),
            "config_sha256": hashlib.sha256(cfg_path.read_bytes()).hexdigest(),
        }
        (tmp_path / "server_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        server_manager.server_exe_file = str(server_path)
        server_manager.server_cfg_file = str(cfg_path)
        mock_hdc.check_file_exists = Mock(return_value=True)
        mock_hdc.execute.return_value = {
            "success": True,
            "stdout": "0" * 64 + "  /system/bin/ohscrcpy_server",
            "stderr": "",
        }

        assert server_manager.check_server_installed() is False

    def test_remote_sha256_accepts_output_field_from_hdc_wrapper(self, server_manager, mock_hdc):
        expected = "a" * 64
        mock_hdc.execute.return_value = {
            "success": True,
            "output": f"{expected}  /data/local/tmp/ohscrcpy_server",
            "stderr": "",
        }

        assert server_manager._get_remote_sha256("/data/local/tmp/ohscrcpy_server") == expected

    def test_remote_sha256_returns_none_when_hdc_has_no_digest(self, server_manager, mock_hdc):
        mock_hdc.execute.return_value = {
            "success": False,
            "stdout": "sha256sum: No such file or directory",
            "stderr": "",
            "returncode": 1,
        }

        assert server_manager._get_remote_sha256("/data/local/tmp/missing") is None

    def test_runtime_deployment_uses_data_partition(self, server_manager):
        assert server_manager.remote_server_path == "/data/local/tmp/ohscrcpy_server"
        assert server_manager.remote_config_path == "/data/local/tmp/ohscrcpy_server.cfg"

    def test_start_server_replaces_other_listener_with_runtime_server(self, server_manager, mock_hdc):
        server_manager.manufacturer = "Dnakeiot"
        server_manager._is_runtime_server_running = Mock(return_value=False)
        server_manager.prepare_server = Mock(return_value=True)
        server_manager._is_runtime_server_running = Mock(side_effect=[False, True])
        mock_hdc.execute_async_in_shell.return_value = Mock()

        assert server_manager.start_server(27183) is True
        mock_hdc.execute_async_in_shell.assert_called_once()


class TestServerManagerResourcePath:
    
    def test_get_resource_path_dev_env(self):
        """测试开发环境资源路径"""
        with patch('sys._MEIPASS', None, create=True):
            with patch('os.path.dirname') as mock_dir:
                mock_dir.return_value = '/mock/core'
                with patch('os.path.abspath') as mock_abs:
                    mock_abs.return_value = '/mock/core/__file__'
                    with patch('os.path.exists', return_value=True):
                        hdc = Mock()
                        hdc.hdc_path = "/mock/hdc"
                        manager = ServerManager(manufacturer="default", hdc_executor=hdc)
                        path = manager._get_resource_path("test_file")
                        assert "test_file" in path
    
    def test_get_resource_path_pyinstaller(self):
        """测试PyInstaller打包环境资源路径"""
        with patch('sys._MEIPASS', '/mock/_MEIPASS', create=True):
            hdc = Mock()
            hdc.hdc_path = "/mock/hdc"
            manager = ServerManager(manufacturer="default", hdc_executor=hdc)
            path = manager._get_resource_path("test_file")
            assert "test_file" in path


class TestServerManufacturerHandling:
    
    @pytest.fixture
    def mock_hdc(self):
        """创建模拟的HDC执行器"""
        hdc = Mock()
        hdc.device_sn = "test_device"
        hdc.hdc_path = "/mock/hdc"
        hdc.execute = Mock(return_value={"success": True, "output": "", "error": ""})
        return hdc
    
    def test_manufacturer_default(self, mock_hdc):
        """测试default制造商"""
        with patch.object(ServerManager, '_get_resource_path', return_value='/mock/default/server'):
            manager = ServerManager(manufacturer="default", hdc_executor=mock_hdc)
            assert manager.manufacturer == "default"
    
    def test_manufacturer_huawei(self, mock_hdc):
        """测试HUAWEI制造商"""
        with patch.object(ServerManager, '_get_resource_path', return_value='/mock/HUAWEI/server'):
            manager = ServerManager(manufacturer="HUAWEI", hdc_executor=mock_hdc)
            assert manager.manufacturer == "HUAWEI"

    def test_dnakeiot_source_resource_path(self, mock_hdc, tmp_path):
        client_dir = tmp_path / "Client"
        core_dir = client_dir / "core"
        source_dir = tmp_path / "Server" / "bin" / "Dnakeiot"
        core_dir.mkdir(parents=True)
        source_dir.mkdir(parents=True)
        resource = source_dir / "ohscrcpy_server"
        resource.write_bytes(b"server")

        with patch('os.path.abspath', return_value=str(core_dir / "server_manager.py")):
            manager = ServerManager(manufacturer="Dnakeiot", hdc_executor=mock_hdc, build_product="76A")

        assert manager.server_exe_file == str(resource)

    @patch("core.server_manager.get_runtime_resource_profile", return_value="Dnakeiot")
    def test_temporary_a333_mode_uses_dnakeiot_resource_for_default_device(
            self, _resource_profile, mock_hdc):
        with patch.object(ServerManager, "_get_resource_path", return_value="/mock/Dnakeiot/server"):
            manager = ServerManager(manufacturer="default", hdc_executor=mock_hdc)

        assert manager.manufacturer == "default"
        assert manager.resource_profile == "Dnakeiot"
