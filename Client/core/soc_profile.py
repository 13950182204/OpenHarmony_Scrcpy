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
OpenHarmony_Scrcpy 设备芯片（SoC）识别

只依据设备硬件事实判定 SoC 家族，不依赖 const.build.product /
const.product.manufacturer 这类厂商产品字符串。

背景：全志 A537 板卡的 build.product 形如 DH-76AM-HS26A，旧逻辑（只认 76A/76B/769）
把它判成 RK3568 通用资源，部署的服务端在设备上拿不到 CedarC 硬编帧，表现为“无法投屏”。
反过来，CedarC 服务端 NEEDED libawcodec_enc.z.so，板卡上没有这组库也一定起不来。

判据优先级（逐级降级，最终回退到旧的字符串规则并告警）：
  1. 编码后端能力：/vendor/lib64（/vendor/lib、/system/lib64）下的 CedarC
     （libawcodec_enc.z.so / libawcodec_dec.z.so）或 Rockchip MPP（librockchip_mpp.z.so、
     librknnrt.so、librockchip_rga.z.so）。这是“哪份服务端二进制能跑起来”的直接证据。
  2. 设备树 compatible（/proc/device-tree/compatible 的首段，hdc 会在 NUL 处截断，
     首项即主 compatible），如 allwinner,a537、rockchip,rk3568。
  3. 系统硬件属性 ohos.boot.hardware，如 sun65iw1p1（全志）、rk3568（瑞芯微）。
"""

import re
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from .constants import LogLevel
from .logger import print_log


SOC_ALLWINNER = "allwinner"
SOC_ROCKCHIP = "rockchip"
SOC_UNKNOWN = "unknown"

# 编码后端签名库：目录名 -> 家族。这些库是否存在，直接决定对应资源里的服务端能否加载。
_CODEC_LIB_FAMILIES = (
    (SOC_ALLWINNER, ("libawcodec_enc.z.so", "libawcodec_dec.z.so")),
    (SOC_ROCKCHIP, ("librockchip_mpp.z.so", "librknnrt.so", "librockchip_rga.z.so")),
)
_LIB_DIRS = ("/vendor/lib64", "/vendor/lib", "/system/lib64")

# 设备树 compatible 片段：全志为 allwinner,* / sun* / arm,sun*；瑞芯微为 rockchip,* / rk3xxx / rv11xx。
_ALLWINNER_TOKEN_PREFIXES = ("allwinner,", "sunxi,", "arm,sun")
_ROCKCHIP_TOKEN = re.compile(r"^(?:rockchip,)?r(?:k|v)[0-9]{4}")
_ROCKCHIP_TOKEN_PREFIXES = ("rockchip,",)


@dataclass(frozen=True)
class SocDetection:
    """一次设备 SoC 识别的结果（含判定证据）。"""

    family: str = SOC_UNKNOWN
    compatible: str = ""
    hardware: str = ""
    codec_libs: Tuple[str, ...] = ()
    source: str = ""

    @property
    def is_allwinner(self) -> bool:
        """是否为全志（含 A333/A537）平台。"""
        return self.family == SOC_ALLWINNER

    def describe(self) -> str:
        """返回单行可读的判定证据，用于日志。"""
        parts = [f"family={self.family}", f"source={self.source or 'none'}"]
        if self.compatible:
            parts.append(f"compatible={self.compatible}")
        if self.hardware:
            parts.append(f"hardware={self.hardware}")
        if self.codec_libs:
            parts.append("codec=" + ",".join(self.codec_libs))
        return " ".join(parts)


def _short_sn(sn: str) -> str:
    if len(sn) > 16:
        return f"{sn[:8]}****{sn[-8:]}"
    return sn


def _tokens(text: Optional[str]) -> list:
    """把设备树 compatible 拆成条目（NUL/换行/回车都可能出现）。"""
    if not text:
        return []
    return [item.strip().lower() for item in re.split(r"[\x00\r\n]", text) if item and item.strip()]


def classify_compatible(text: Optional[str]) -> str:
    """按设备树 compatible 条目判定 SoC 家族。

    不只看首项：全志板卡常见 allwinner,a537 + arm,sun65iw1p1，瑞芯微常见
    rockchip,rk3568，首项可能是无关的 board 标识，因此按“最具体优先”逐项匹配。
    """
    for token in _tokens(text):
        if token.startswith(_ALLWINNER_TOKEN_PREFIXES) or token.startswith("sun"):
            return SOC_ALLWINNER
        if token.startswith(_ROCKCHIP_TOKEN_PREFIXES) or _ROCKCHIP_TOKEN.match(token):
            return SOC_ROCKCHIP
    return SOC_UNKNOWN


def classify_hardware(text: Optional[str]) -> str:
    """按 ohos.boot.hardware 等硬件属性判定 SoC 家族。"""
    value = (text or "").strip().lower()
    if not value or "fail" in value or "no such file" in value or "not found" in value:
        return SOC_UNKNOWN
    token = value.split()[0]
    if token.startswith("sun") or "sunxi" in token:
        return SOC_ALLWINNER
    if token.startswith("rockchip") or _ROCKCHIP_TOKEN.match(token):
        return SOC_ROCKCHIP
    return SOC_UNKNOWN


def classify_codec_libs(text: Optional[str]) -> str:
    """按 ls 输出判定编码后端所属家族（CedarC 优先）。"""
    present = codec_lib_names(text)
    for family, names in _CODEC_LIB_FAMILIES:
        if any(name in present for name in names):
            return family
    return SOC_UNKNOWN


def codec_lib_names(text: Optional[str]) -> Tuple[str, ...]:
    """从 ls 输出里提取实际存在的编码后端库文件名（绝对路径行才算命中）。"""
    names = set()
    for line in (text or "").splitlines():
        entry = line.strip()
        if not entry.startswith("/"):
            continue
        names.add(entry.rsplit("/", 1)[-1])
    return tuple(sorted(names))


def _shell(hdc, sn: str, args: Sequence[str]) -> str:
    """在指定设备上执行 shell 命令并返回 stdout；失败返回空串。

    need_sn 固定为 False：设备号已显式拼在 args 里，避免与执行器自身的
    device_sn 叠加成两个 -t。
    """
    try:
        result = hdc.execute(["-t", sn, "shell"] + list(args), False)
    except Exception as e:  # 探测失败不应阻断设备列表
        print_log(LogLevel.WARN, "芯片识别", f"执行设备探测命令失败: {e}")
        return ""
    if not isinstance(result, dict) or not result.get("success"):
        return ""
    return result.get("stdout", "") or ""


def _lib_probe_args() -> list:
    args = []
    for directory in _LIB_DIRS:
        for _, names in _CODEC_LIB_FAMILIES:
            args.extend(f"{directory}/{name}" for name in names)
    return args


def probe_device_soc(hdc, sn: str, log_title: str = "芯片识别") -> SocDetection:
    """探测单台设备的 SoC 家族（3 次 hdc 调用，全部失败也不抛异常）。"""
    lib_output = _shell(hdc, sn, ["ls"] + _lib_probe_args())
    compatible_text = _shell(hdc, sn, ["cat", "/proc/device-tree/compatible"])
    hardware_text = _shell(hdc, sn, ["param", "get", "ohos.boot.hardware"])

    family = SOC_UNKNOWN
    source = ""

    lib_family = classify_codec_libs(lib_output)
    if lib_family != SOC_UNKNOWN:
        family, source = lib_family, "codec-libs"

    if family == SOC_UNKNOWN:
        dt_family = classify_compatible(compatible_text)
        if dt_family != SOC_UNKNOWN:
            family, source = dt_family, "device-tree"

    if family == SOC_UNKNOWN:
        hw_family = classify_hardware(hardware_text)
        if hw_family != SOC_UNKNOWN:
            family, source = hw_family, "boot-hardware"

    detection = SocDetection(
        family=family,
        compatible=_first_line(compatible_text),
        hardware=_first_line(hardware_text),
        codec_libs=codec_lib_names(lib_output),
        source=source,
    )

    if family == SOC_UNKNOWN:
        print_log(LogLevel.WARN, log_title,
                  f"设备 {_short_sn(sn)} SoC 识别失败（编码库/设备树/硬件属性均不可用），"
                  f"回退产品字符串规则: {detection.describe()}")
    else:
        print_log(LogLevel.INFO, log_title,
                  f"设备 {_short_sn(sn)} SoC 识别: {detection.describe()}")
    return detection


def _first_line(text: Optional[str]) -> str:
    for line in (text or "").splitlines():
        entry = line.strip()
        if entry and "fail" not in entry.lower() and "no such file" not in entry.lower():
            return entry
    return ""


__all__ = [
    "SOC_ALLWINNER",
    "SOC_ROCKCHIP",
    "SOC_UNKNOWN",
    "SocDetection",
    "classify_codec_libs",
    "classify_compatible",
    "classify_hardware",
    "codec_lib_names",
    "probe_device_soc",
]
