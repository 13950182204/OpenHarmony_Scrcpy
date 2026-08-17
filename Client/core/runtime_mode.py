"""Runtime package mode helpers."""

import os
import sys


_A333_TEMPORARY_MARKER = "a333_temporary_mode.flag"
_COMBINED_MARKER = "dnakeiot_combined_mode.flag"

# Dnakeiot 厂商家族：设备侧镜像均有 init 托管的 27183 旧服务，
# 客户端统一走运行时上传（/data/local/tmp）+ 非默认端口。
DNAKEIOT_FAMILY = ("Dnakeiot", "Dnakeiot_RK3568")


def _marker_exists(marker: str) -> bool:
    base_path = getattr(sys, "_MEIPASS", None)
    if not base_path:
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.isfile(os.path.join(base_path, marker))


def is_a333_temporary_mode() -> bool:
    """Return whether this executable is the all-device A333 temporary build."""
    return _marker_exists(_A333_TEMPORARY_MARKER)


def is_dnakeiot_combined_mode() -> bool:
    """Return whether this executable bundles both A333 and RK3568 resources."""
    return _marker_exists(_COMBINED_MARKER)


def get_runtime_resource_profile(manufacturer: str, product_name: str = "") -> str:
    """Resolve the bundled service resource profile for the running package.

    Dnakeiot 设备按产品名区分：RK3568 开发板走通用编码（H.264/H.265 由媒体服务
    协商），A333 走 CedarC 硬编。资源目录名即 profile 名（打包于 _internal/<profile>/）。

    公版/未配置厂商参数的设备（manufacturer 为 default/unknown/空白）没有厂商私有
    依赖，统一使用通用 64 位编码资源（Dnakeiot_RK3568 即标准 OH_VideoEncoder，
    不依赖 libawcodec_enc 等厂商库，可直接复用）。
    """
    if is_a333_temporary_mode():
        return "Dnakeiot"
    if manufacturer in DNAKEIOT_FAMILY:
        if "RK3568" in product_name:
            return "Dnakeiot_RK3568"
        return "Dnakeiot"
    if not manufacturer or manufacturer.strip().lower() in ("default", "unknown"):
        return "Dnakeiot_RK3568"
    return manufacturer
