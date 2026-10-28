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


def get_runtime_resource_profile(manufacturer: str, build_product: str = "",
                                 soc_family: str = "") -> str:
    """Resolve the bundled service resource profile for the running package.

    设备识别以 SoC 家族（soc_profile.probe_device_soc 的实测结论）为准：
      - allwinner（A333/A537 同族）→ Dnakeiot（CedarC 硬编，NEEDED libawcodec_enc.z.so）
      - rockchip（RK3568/RK3588 等）→ Dnakeiot_RK3568（标准 OH_VideoEncoder 通用编码）
      - 未知 → 回退到 `const.build.product` 字符串规则（仅兜底，soc_profile 会告警）：
          产品名含 76A / 76B → Dnakeiot；含 RK35xx 或等于 769 → Dnakeiot_RK3568；
          其余 → Dnakeiot_RK3568。字符串按子串匹配，因为实际板卡名形如
          "DH-76AM-HS26A"，精确相等会漏判。

    资源目录名即 profile 名（打包于 _internal/<profile>/）。
    """
    # 延迟导入：constants -> runtime_mode -> soc_profile -> constants 会形成环，
    # 这里只在真正解析 profile 时才需要 soc_profile 的家族常量。
    from .soc_profile import SOC_ALLWINNER, SOC_ROCKCHIP

    if is_a333_temporary_mode():
        return "Dnakeiot"
    if soc_family == SOC_ALLWINNER:
        return "Dnakeiot"
    if soc_family == SOC_ROCKCHIP:
        return "Dnakeiot_RK3568"
    product = build_product.strip().upper() if build_product else ""
    # 先判 Rockchip 型号，避免 "RK3568-76A" 这类复合名被 76A 规则抢走。
    if product == "769" or "RK35" in product:
        return "Dnakeiot_RK3568"
    if "76A" in product or "76B" in product:
        return "Dnakeiot"
    return "Dnakeiot_RK3568"
