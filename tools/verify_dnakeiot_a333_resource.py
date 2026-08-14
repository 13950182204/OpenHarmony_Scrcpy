#!/usr/bin/env python3
"""Validate the A333 Dnakeiot service resource before packaging it for Windows."""

import hashlib
import json
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as resource:
        for block in iter(lambda: resource.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def is_aarch64_elf(path: Path) -> bool:
    header = path.read_bytes()[:20]
    return len(header) >= 20 and header[:4] == b"\x7fELF" and header[4] == 2 and header[18:20] == b"\xb7\x00"


def main() -> int:
    resource_dir = Path(sys.argv[1]) if len(sys.argv) == 2 else Path("Server/bin/Dnakeiot")
    server = resource_dir / "ohscrcpy_server"
    config = resource_dir / "ohscrcpy_server.cfg"
    manifest_path = resource_dir / "server_manifest.json"

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read manifest: {exc}")
        return 1

    if not is_aarch64_elf(server):
        print("ERROR: service binary is not ELF64/AArch64")
        return 1

    checks = (("server", server, "sha256"), ("config", config, "config_sha256"))
    for label, path, manifest_key in checks:
        actual = sha256(path) if path.is_file() else None
        expected = manifest.get(manifest_key)
        if actual != expected:
            print(f"ERROR: {label} SHA-256 mismatch: expected={expected}, actual={actual}")
            return 1

    if manifest.get("target_manufacturer") != "Dnakeiot" or manifest.get("target_abi") != "aarch64":
        print("ERROR: manifest target is not Dnakeiot/AArch64")
        return 1

    print(f"PASS: {server} is ELF64/AArch64 with SHA-256 {manifest['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
