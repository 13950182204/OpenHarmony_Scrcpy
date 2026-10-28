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
SoC 识别单元测试（不依赖厂商/产品字符串的芯片判定）
"""

from unittest.mock import patch

import pytest

from core.runtime_mode import get_runtime_resource_profile
from core.soc_profile import (
    SOC_ALLWINNER,
    SOC_ROCKCHIP,
    SOC_UNKNOWN,
    classify_codec_libs,
    classify_compatible,
    classify_hardware,
    codec_lib_names,
    probe_device_soc,
)


class FakeHdc:
    """按子命令返回预设输出的假 hdc 执行器"""

    def __init__(self, libs="", compatible="", hardware="", success=True):
        self.outputs = {"ls": libs, "cat": compatible, "param": hardware}
        self.success = success
        self.calls = []
        self.need_sn_flags = []

    def execute(self, args, need_sn=True):
        self.calls.append(list(args))
        self.need_sn_flags.append(need_sn)
        verb = args[3] if len(args) > 3 else ""
        return {"success": self.success, "stdout": self.outputs.get(verb, "")}


A537_LIBS = (
    "ls: /vendor/lib64/librockchip_mpp.z.so: No such file or directory\n"
    "ls: /vendor/lib64/librknnrt.so: No such file or directory\n"
    "/vendor/lib64/libawcodec_dec.z.so\n"
    "/vendor/lib64/libawcodec_enc.z.so\n"
)


class TestClassifyCompatible:
    def test_allwinner_with_sun_sibling(self):
        """全志板卡：allwinner,a537 + arm,sun65iw1p1（NUL 分隔）"""
        assert classify_compatible("allwinner,a537\x00arm,sun65iw1p1\x00") == SOC_ALLWINNER

    def test_sun_token_only(self):
        assert classify_compatible("arm,sun55iw3") == SOC_ALLWINNER

    def test_rockchip_board(self):
        assert classify_compatible("rockchip,rk3568-evb1-ddr4-v10") == SOC_ROCKCHIP
        assert classify_compatible("rockchip,rk3588") == SOC_ROCKCHIP

    def test_unknown_vendor(self):
        assert classify_compatible("honor,board-rev-a") == SOC_UNKNOWN

    def test_empty(self):
        assert classify_compatible("") == SOC_UNKNOWN
        assert classify_compatible(None) == SOC_UNKNOWN


class TestClassifyHardware:
    def test_allwinner(self):
        assert classify_hardware("sun65iw1p1 ") == SOC_ALLWINNER
        assert classify_hardware("sun50iw9") == SOC_ALLWINNER

    def test_rockchip(self):
        assert classify_hardware("rk3568") == SOC_ROCKCHIP
        assert classify_hardware("rockchip,rk3588") == SOC_ROCKCHIP

    def test_param_failure_message(self):
        assert classify_hardware('Get parameter "ohos.boot.hardware" fail! errNum is:106!') == SOC_UNKNOWN

    def test_empty(self):
        assert classify_hardware("") == SOC_UNKNOWN


class TestClassifyCodecLibs:
    def test_cedarc(self):
        assert classify_codec_libs(A537_LIBS) == SOC_ALLWINNER

    def test_rockchip_mpp(self):
        assert classify_codec_libs("/vendor/lib64/librockchip_mpp.z.so\n") == SOC_ROCKCHIP

    def test_error_lines_do_not_count(self):
        """ls 的错误提示不是绝对路径行，不能算命中"""
        noise = "ls: /vendor/lib64/libawcodec_enc.z.so: No such file or directory\n"
        assert classify_codec_libs(noise) == SOC_UNKNOWN
        assert codec_lib_names(noise) == ()

    def test_empty(self):
        assert classify_codec_libs("") == SOC_UNKNOWN


class TestProbeDeviceSoc:
    def test_codec_libs_win_over_product_string(self):
        """有 CedarC 编码后端即为全志，与 build.product 字符串无关"""
        hdc = FakeHdc(libs=A537_LIBS, compatible="allwinner,a537",
                      hardware="sun65iw1p1")
        detection = probe_device_soc(hdc, "sn1")

        assert detection.family == SOC_ALLWINNER
        assert detection.source == "codec-libs"
        assert detection.is_allwinner

    def test_device_tree_fallback(self):
        hdc = FakeHdc(libs="", compatible="allwinner,a537", hardware="")
        detection = probe_device_soc(hdc, "sn1")

        assert detection.family == SOC_ALLWINNER
        assert detection.source == "device-tree"

    def test_hardware_fallback(self):
        hdc = FakeHdc(libs="", compatible="", hardware="rk3568")
        detection = probe_device_soc(hdc, "sn1")

        assert detection.family == SOC_ROCKCHIP
        assert detection.source == "boot-hardware"

    def test_unknown_when_nothing_readable(self):
        hdc = FakeHdc()
        detection = probe_device_soc(hdc, "sn1")

        assert detection.family == SOC_UNKNOWN
        assert detection.source == ""

    def test_failed_commands_do_not_raise(self):
        hdc = FakeHdc(success=False)
        detection = probe_device_soc(hdc, "sn1")

        assert detection.family == SOC_UNKNOWN

    def test_probe_uses_three_shell_calls_without_duplicate_sn(self):
        hdc = FakeHdc(libs=A537_LIBS)
        probe_device_soc(hdc, "sn1")

        assert len(hdc.calls) == 3
        assert all(call[:3] == ["-t", "sn1", "shell"] for call in hdc.calls)
        # need_sn 必须为 False，否则执行器会再拼一个 -t，命令失效
        assert hdc.need_sn_flags == [False, False, False]

    def test_ls_probe_covers_all_lib_dirs(self):
        hdc = FakeHdc()
        probe_device_soc(hdc, "sn1")

        ls_args = hdc.calls[0]
        joined = " ".join(ls_args)
        assert "/vendor/lib64/libawcodec_enc.z.so" in joined
        assert "/vendor/lib/libawcodec_enc.z.so" in joined
        assert "/system/lib64/libawcodec_enc.z.so" in joined


class TestRuntimeResourceProfile:
    def test_allwinner_family_uses_cedarc_profile(self):
        """不管产品字符串是什么，全志实测一律走 CedarC 资源"""
        assert get_runtime_resource_profile("DHong", "DH-76AM-HS26A", SOC_ALLWINNER) == "Dnakeiot"
        assert get_runtime_resource_profile("default", "default", SOC_ALLWINNER) == "Dnakeiot"

    def test_rockchip_family_uses_generic_profile(self):
        assert get_runtime_resource_profile("DHong", "76A", SOC_ROCKCHIP) == "Dnakeiot_RK3568"

    def test_unknown_family_falls_back_to_product_string(self):
        assert get_runtime_resource_profile("DHong", "76A", SOC_UNKNOWN) == "Dnakeiot"
        assert get_runtime_resource_profile("DHong", "769", SOC_UNKNOWN) == "Dnakeiot_RK3568"
        # 实际板卡名是复合串，兜底规则必须按子串匹配，否则又漏判成全志板
        assert get_runtime_resource_profile("DHong", "DH-76AM-HS26A", SOC_UNKNOWN) == "Dnakeiot"
        assert get_runtime_resource_profile("DHong", "DHong-RK3568-Development-Board",
                                            SOC_UNKNOWN) == "Dnakeiot_RK3568"
        assert get_runtime_resource_profile("DHong", "", SOC_UNKNOWN) == "Dnakeiot_RK3568"

    @patch("core.runtime_mode.is_a333_temporary_mode", return_value=True)
    def test_temporary_mode_still_forces_cedarc(self, _temporary_mode):
        assert get_runtime_resource_profile("anything", "", SOC_ROCKCHIP) == "Dnakeiot"
