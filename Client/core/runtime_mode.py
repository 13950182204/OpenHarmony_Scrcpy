"""Runtime package mode helpers."""

import os
import sys


_A333_TEMPORARY_MARKER = "a333_temporary_mode.flag"


def is_a333_temporary_mode() -> bool:
    """Return whether this executable is the all-device A333 temporary build."""
    base_path = getattr(sys, "_MEIPASS", None)
    if not base_path:
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.isfile(os.path.join(base_path, _A333_TEMPORARY_MARKER))


def get_runtime_resource_profile(manufacturer: str) -> str:
    """Resolve the bundled service resource profile for the running package."""
    if is_a333_temporary_mode():
        return "Dnakeiot"
    return manufacturer
