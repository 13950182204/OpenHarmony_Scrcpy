# Dnakeiot A333 Windows Release Handoff

## Purpose

This checkout is the `D:\ohos\OpenHarmony_Scrcpy` Windows release source for the A333 `Dnakeiot` device. It packages the verified AArch64 Cedarc service binary, rather than the upstream default 32-bit ARM server.

## Build on Windows

Open a Windows Command Prompt in a non-Chinese path and run:

```cmd
cd /d D:\ohos\OpenHarmony_Scrcpy
Package\build_dnakeiot_a333_windows.bat
```

The release executable is:

```text
Release\OHScrcpy-A333-Dnakeiot\OHScrcpy.exe
```

The build first runs `tools\verify_dnakeiot_a333_resource.py`; it rejects a non-ELF64/AArch64 server or a binary/cfg whose SHA-256 differs from the manifest.

The directory is a PyInstaller `onedir` release. Keep `_internal` beside `OHScrcpy.exe`; it contains `hdc.exe`, the Dnakeiot service binary, its init cfg, and `server_manifest.json`.

## Device Behavior

The client recognizes manufacturer `Dnakeiot` and selects `_internal\Dnakeiot\ohscrcpy_server`. Before each deployment it checks the packaged ELF64/AArch64 header and SHA-256, then compares the device-side runtime server and cfg SHA-256. A clean device does not need to contain `ohscrcpy_server`: the client uploads both files to `/data/local/tmp/`, verifies them, and starts the uploaded binary on a non-default port. It does not replace the init-managed `/system/bin` service.

Expected first successful connection log:

```text
SCREEN_INFO:672:1072:60:...
```

The GUI must show non-zero FPS. `连接成功` with `FPS: 0` is not a successful projection.

The Dnakeiot system image has an init-managed legacy server on `27183`.
The current client reserves that port and starts the verified runtime-uploaded
server from `27184`; this is automatic in the GUI and must not be changed to
the default port in local release edits.

The packaged server was regression-tested on target
`2f011131315330303010f2290bdcace3` through the same automatic upload and
startup path used by the GUI, starting from an empty `/data/local/tmp` runtime
directory. On port `27184`, a 30-second client run decoded 1829 frames at
60.91 fps with zero decode failures and negotiated
`SCREEN_INFO:672:1072:60:...`. The server uses a 63 Hz internal cached-frame
cadence to compensate for scheduler jitter while keeping the public stream
configuration at 60 fps.

`672x1072` is an aspect-preserving downscale of the portrait `800x1280`
display so it remains within the A333 AVC coded-height limit. The black bands
at the sides of a landscape Windows canvas are letterboxing, not cropped
application content. The current packed-frame mapping has also been verified
against a device screenshot: OHOS `RGBA_8888` maps to Cedarc `ABGR` on the
little-endian AArch64 implementation.

The old `Release\OHScrcpy-A333-Dnakeiot\OHScrcpy.exe` must not be reused. Its deployment code can report success after HDC returns `text file is busy` for an init-managed executable, then start the stale device binary. Rebuild with the current source; the window title must show `v2.3.2-a333`, and the client log must include post-deployment device SHA-256 verification before connecting.

## WSL Boundary

The WSL environment can edit `D:\` through `/mnt/d`, but it has no `cmd.exe`, `powershell.exe`, Windows Python, or Windows PyInstaller runtime. PyInstaller is host-specific: executing it in WSL would create a Linux ELF executable, not `OHScrcpy.exe`. Therefore the source and service resource are prepared from WSL, while the final `.exe` must be built by a Windows-host Codex/session using the batch file above.

## Background

The previous Windows package used a generic resource fallback and only checked whether device files existed. On the A333 device this left a stale server in place. Its service log completed the TCP handshake but failed every raw capture buffer mapping before Cedarc encoding, leaving the Windows GUI at zero frames. The new package does not fall back to the default resource for `Dnakeiot`, validates the package/server identity, and serializes deployment and connection requests so one port has one session.

## Final Acceptance

1. Confirm `Release\OHScrcpy-A333-Dnakeiot\OHScrcpy.exe` and `_internal\Dnakeiot\ohscrcpy_server` exist.
2. Connect only the target A333 device and select it once.
3. Confirm the client log reports uploads to `/data/local/tmp/`, port `27184`, and the A333 `672x1072` configuration, not `1280x800`.
4. Operate the device for at least 30 seconds and confirm the image changes and FPS is at least 60. A static page is still encoded at the negotiated 60fps; capture callbacks are content-change driven on A333.
5. Save the client log, matching `/data/local/tmp/server_*.log`, and one displayed frame.
