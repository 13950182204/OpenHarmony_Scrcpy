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


def get_runtime_resource_profile(manufacturer: str, build_product: str = "") -> str:
    """Resolve the bundled service resource profile for the running package.

    设备识别以 `const.build.product` 为准：
      - 76A / 76B            → A333 定制资源（Dnakeiot，CedarC 硬编）
      - 769                  → Dnakeiot_RK3568（标准 OH_VideoEncoder 通用编码）
      - 其他任何值（含 default/未知厂商）→ Dnakeiot_RK3568（通用 64 位编码，
        不依赖 libawcodec_enc 等厂商私有库）

    资源目录名即 profile 名（打包于 _internal/<profile>/）。
    """
    if is_a333_temporary_mode():
        return "Dnakeiot"
    product = build_product.strip() if build_product else ""
    if product.upper() in ("76A", "76B"):
        return "Dnakeiot"
    if product == "769":
        return "Dnakeiot_RK3568"
    return "Dnakeiot_RK3568"
