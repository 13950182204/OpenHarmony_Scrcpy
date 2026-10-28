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

PyInstaller is host-specific: using Linux Python would create a Linux ELF executable, not `OHScrcpy.exe`. On this WSL host, Windows Python and PyInstaller are available through `/init /mnt/c/Windows/System32/cmd.exe`; the batch file may therefore be run through that bridge. A host without that bridge must build from a Windows Command Prompt instead.

## Background

The previous Windows package used a generic resource fallback and only checked whether device files existed. On the A333 device this left a stale server in place. Its service log completed the TCP handshake but failed every raw capture buffer mapping before Cedarc encoding, leaving the Windows GUI at zero frames. The new package does not fall back to the default resource for `Dnakeiot`, validates the package/server identity, and serializes deployment and connection requests so one port has one session.

## Final Acceptance

1. Confirm `Release\OHScrcpy-A333-Dnakeiot\OHScrcpy.exe` and `_internal\Dnakeiot\ohscrcpy_server` exist.
2. Connect only the target A333 device and select it once.
3. Confirm the client log reports uploads to `/data/local/tmp/`, port `27184`, and the A333 `672x1072` configuration, not `1280x800`.
4. Operate the device for at least 30 seconds and confirm the image changes and FPS is at least 60. A static page is still encoded at the negotiated 60fps; capture callbacks are content-change driven on A333.
5. Save the client log, matching `/data/local/tmp/server_*.log`, and one displayed frame.
## RK3568 Combined Release (2026-08-15)

### New release: `Release\OHScrcpy-Dnakeiot\OHScrcpy.exe` (v2.4.0-dnakeiot)

A single executable now serves both device families (window title shows `v2.4.0-dnakeiot`):

| Device | manufacturer | product name | profile resource | encoder path |
|---|---|---|---|---|
| A333 (a333_newpines) | Dnakeiot | (non-RK3568) | `_internal\Dnakeiot\` | CedarC AVC (672x1072) |
| RK3568 (DHong-RK3568-Development-Board) | Dnakeiot | contains `RK3568` | `_internal\Dnakeiot_RK3568\` | generic OH_VideoEncoder (H.264/H.265, 800x1280) |

### Build

```cmd
cd /d D:\ohos\OpenHarmony_Scrcpy
Package\build_dnakeiot_combined_windows.bat
```

Also available: `Package\build_dnakeiot_rk3568_windows.bat` (RK3568-only release, `Release\OHScrcpy-Dnakeiot-RK3568`).
The original A333-only build (`build_dnakeiot_a333_windows.bat`) is unchanged.

### What changed in the client

- `Client/core/device_manager.py`: `DeviceInfo` carries `build_product` from `const.build.product` and
  forwards it when creating `ServerManager`.
- `Client/core/runtime_mode.py`: `get_runtime_resource_profile(manufacturer, build_product)` selects
  `Dnakeiot` for `76A`/`76B`, `Dnakeiot_RK3568` for `769` and the generic 64-bit profile for other products;
  `dnakeiot_combined_mode.flag` (checked at `_internal` root) selects the combined version string.
- `Client/core/server_manager.py`: profile-aware resource lookup (packaged `_internal/<profile>/`,
  dev fallback `Server/bin/<profile>/`), Dnakeiot-family runtime-port behavior for both profiles,
  plus `check_device_abi()`: probes `/lib/ld-musl-aarch64.so.1` then `/system/lib64/` before any
  deployment and rejects 32-bit images with a clear error.
- `Client/gui/connection_manager.py`, `main_window.py`, `server_deployer.py`: thread
  `product_name` through.
- `Client/core/constants.py`: version `v2.4.0-dnakeiot` when the combined marker is present
  (A333 builds keep `v2.3.2-a333`).

### Resource manifest fix

`Server/bin/Dnakeiot/server_manifest.json` had a stale `config_sha256` (matched an old CRLF/BOM
packaged cfg). Both manifests now point at the canonical LF cfg
(`6a63d4d1...`). `tools/verify_dnakeiot_a333_resource.py` passes for both
`Server/bin/Dnakeiot` and `Server/bin/Dnakeiot_RK3568`.

### Verification (device 150100414a54443452061dcd48f7c800, DHong-RK3568-Development-Board)

The `Dnakeiot_RK3568` resource is the tree-built 64-bit server
(sha256 `5ea0051a...`). On the device it negotiated `SCREEN_INFO:800:1280:60:1500000:h265:input-v1`,
streamed valid H.265 VPS/SPS/PPS/IDR/frames and processed touch input. See the wiki section
"RK3568 64位兼容" for the full record. Headless checks against the packaged `_internal` resources
pass for both profiles (resource selection, manifest SHA-256 validation, device ABI check).

### Acceptance for the combined exe

1. Connect an A333 device: expect `_internal\Dnakeiot\` upload, port 27184+, `672x1072`.
2. Connect the RK3568 device: expect `_internal\Dnakeiot_RK3568\` upload, port 27184+,
   `SCREEN_INFO:800:1280:...`, H.265 hardware encode, FPS ~60.
3. Window title shows `v2.4.0-dnakeiot`; client log includes post-deployment device SHA-256
   verification before connecting.

## SHA-256 Diagnostic Release (2026-09-08)

`Release\OHScrcpy-Dnakeiot\OHScrcpy.exe` was rebuilt from commit `df82a48` with Windows Python 3.8 and
PyInstaller 6.22.0. Its SHA-256 is
`7562c37f74969c4113a8d7197056ba59cd5ba3ce8764df93aba3c2b917ee427e`.

The release keeps `_internal\Dnakeiot\ohscrcpy_server` SHA-256
`e9b825992a2b49e029e6e4af79107c91f20b5f7ab099b8fefc8f5648cea75fca` and cfg SHA-256
`6a63d4d1a5d7897f808641da1c44da164878d73ecf58c7646b1adae360f8a469`, both matching its manifest.
The client now accepts either `stdout` or `output` from an HDC result when parsing a remote SHA-256. If
parsing still fails, it records the command status, return code, raw output and stderr instead of only
reporting `binary=None, config=None`.

On the currently connected 76A device `ea010e325333324247102b4ed1a48c99`, a clean runtime upload verified
both device-side hashes, started `27184`, negotiated `672x1072@60/h264`, and decoded 672 frames with zero
failures during a 12-second headless run. The test then removed the runtime files and HDC forwarding and
restarted the init-managed service. This is not a 76B GUI acceptance: the original 76B device/package must
still run this complete `onedir` directory, with `_internal` kept next to the exe, and retain the resulting log.

The source regression suite after aligning the device-property tests with the current `const.build.product`
contract is `132 passed` (four Pillow deprecation warnings only).

## SoC-Based Resource Selection (2026-10-28)

旧规则只比对 `const.build.product` 是否**精确等于** `76A`/`76B`/`769`。实际板卡名是复合串
（例如全志 A537 主板为 `DH-76AM-HS26A`，厂商串是 `DHong`），精确比对必然落空，于是全志板拿到
RK3568 通用资源 `Dnakeiot_RK3568`；该服务端在本镜像上所有 H.264 标准分辨率探测都返回不支持，
最终强制 `720x1280` 并在 `OH_VideoEncoder_Configure` 返回 err 3，表现为“TCP 已连接、零帧”。

新增 `Client/core/soc_profile.py`，按硬件实测判定芯片家族（三次 hdc shell 调用，不依赖任何厂商/产品字符串）：

| 优先级 | 探测 | 判据 |
|---|---|---|
| 1 | `ls` 候选编解码私有库（/vendor/lib64、/vendor/lib、/system/lib64） | 出现 `libawcodec_enc.z.so`/`libawcodec_dec.z.so` → `allwinner`；出现 `librockchip_mpp.z.so`/`librknnrt.so`/`librockchip_rga.z.so` → `rockchip` |
| 2 | `cat /proc/device-tree/compatible` | 任一段匹配 `allwinner,` 或 `arm,sun[0-9]+` → `allwinner`；`rockchip,` 或 `rk|rv[0-9]{4}` → `rockchip` |
| 3 | `param get ohos.boot.hardware` | `sun[0-9]` → `allwinner`；`rk|rv[0-9]{4}` → `rockchip` |

解析 `ls` 输出时只认以 `/` 开头的行（`ls: ... No such file or directory` 也是 stdout，不能算命中）。
`hdc` 传输会在 NUL 处截断，所以 device-tree 只依赖第一段 `allwinner,a537`；三段全失败时返回
`unknown`，仅告警不阻断连接。

`DeviceInfo.soc_family / soc_hardware / soc_source` 由 `DeviceManager.discover_devices()` 写入，
经 `ServerManager`、`ConnectionManager`、`ServerDeployer` 透传到 `get_runtime_resource_profile()`：
`allwinner` → `Dnakeiot`（CedarC），`rockchip` → `Dnakeiot_RK3568`。只有探测失败才回退到产品字符串，
且兜底改为子串匹配（`76A`/`76B` → Dnakeiot，`RK35*`/`769` → Dnakeiot_RK3568），避免 `DH-76AM-HS26A` 再被漏判。

### 真机验证（device `ea010e325333324247102b4f80429c69`，A537 / sun65iw1p1）

- 探测结果：`family=allwinner source=codec-libs compatible=allwinner,a537 hardware=sun65iw1p1`。
- 客户端全链路（`ensure_server`）：上传 `Server/bin/Dnakeiot`（sha256 `e9b8259...`）→ 端口 27184+ →
  `SCREEN_INFO:600:1024:60:1500000:h264:input-v1` → 服务端日志出现
  `Cedarc raw-buffer config: 600x1024`、`Cedarc initialized: input=600x1024 aligned_input=608x1024`。
- 裸流探针 6 秒统计：`{CONFIG:1, SPS:1, PPS:1, KEYFRAME:7, FRAME:364}`（约 60fps）。
- 反面验证：把 `Dnakeiot_RK3568`（sha256 `5ea0051a...`）推到同一设备，日志即走通用能力探测并
  `OH_VideoEncoder_Configure fail, err: 3`，零帧——这正是旧的错误资源路径。
- 单元测试：`python3 -m pytest tests` → `158 passed`（`tests/test_soc_profile.py` 覆盖分类器、探测优先级、
  need_sn=False、回退规则）。
- 发布包已重建：`Release\OHScrcpy-Dnakeiot\OHScrcpy.exe` 版本 `v2.4.1-dnakeiot`，sha256
  `20662d95d0655c5853a7909542214249322f29bd3b2dfa7233c4d31cc6323bc2`（构建须用 Python 3.8 + PyInstaller 6.22.0，
  `Package/build_dnakeiot_combined_windows.bat`）。

## Touch Injection Daemon (2026-10-28)

远程点击/拖动迟钝的根因不在坐标映射，而在注入方式：客户端原先回退到 `hdc shell uinput -T -c x y`，
`Client/gui/device_controller.py` 的 `_send_stream_touch` 每发一个事件就 `fork()+execl("/bin/uinput")` 一次。
实测单次 `uinput -T -c 400 300` 约 340ms（`0m00.34s real`），一次点击含 DOWN/UP 两个事件，
因此表现为“点了半天才反应”，拖动更明显（每 16ms 一个 MOVE 全部排队）。

服务端注入路径（MMI 协议 `PACKET_INPUT`）在部分镜像上同样受 uinput 影响，故新增设备端常驻守护进程：

| 项目 | 内容 |
|---|---|
| 源码 | `tools/mmi_touchd/mmi_touchd.cpp`（Apache 头 + 协议注释） |
| 构建 | `bash tools/mmi_touchd/build_mmi_touchd.sh [OH_ROOT] [PRODUCT_OUT]`，默认 `OH_ROOT=/home/cx/os/6.1_`、`PRODUCT_OUT=$OH_ROOT/out/a333_newpines`，产物写 `Client/bin/arm64/mmi_touchd` 并生成 `.sha256` |
| 依赖 | `libmmi-client.z` + `libmmi-util.z`（OpenHarmony MMI proxy 客户端），静态链接 `-lc++ -lc` |
| 产物 | `Client/bin/arm64/mmi_touchd`，23728 字节，sha256 `f24e9896bf1e866f23e9333c7a3247291977430673633369b521da4956cb426a`（v2.4.3 起为微秒时间戳版本） |

守护进程在设备 `/data/local/tmp/mmi_touchd <port>` 常驻，TCP 行协议（`\n` 结尾，ASCII）：

```text
P            -> ok 0                  # ping
S            -> ok 1 <events> <pid>   # 状态
D x y        -> ok <cost_ms>          # DOWN
M x y        -> ok <cost_ms>          # MOVE
U x y        -> ok <cost_ms>          # UP
R            -> ok 3                  # 仅抬起，不移动
Q            -> bye                   # 退出
err ...                               # 未知命令
```

坐标为物理显示像素（与 `uinput` 一致）。stdout 无缓冲，启动打印 `mmi_touchd listening <port> pid=<pid>`，
每 300 个事件打印一次 `events=<n> last_cost=<ms>`；`bind` 失败打印 `bind failed port=... errno=...`。

### 客户端集成

- 新增 `Client/core/touch_daemon.py`：`TouchDaemon(hdc_executor, log_title=...)` 负责解析本地二进制（校验 ELF64/AArch64 + 可选 `.sha256`）、
  比对设备端 sha256（一致则跳过上传）、`hdc fport` 端口选择（27196 起扫 12 个，跳过已转发/已监听）、启动与 `_wait_ready`（连上 TCP 后还要 ping 通才算就绪）、
  `send(action, x, y)` 与 `stop()`。`fport` 失败时会先 `fport rm` 再重试一次（历史转发残留会报 `TCP Port listen failed`）。
- `Client/gui/device_controller.py` 的 `_send_stream_touch` 改为**通道粘滞**：手势首个 DOWN 决定走守护进程还是服务端流，
  后续 MOVE/UP 固定同一通道（避免一次拖动里两条通道坐标互相覆盖）；UP 时若另一通道可用则补发一次，避免设备停留在“按下”状态；
  守护进程失败会自动 `reconnect()` 重试一次，再退回 `video_client.send_touch_event`。
- `Client/gui/main_window.py`：连接成功后 `_start_touch_daemon()`，断开时 `_stop_touch_daemon()`；部署失败只告警，不影响投屏。
- 打包：`Package/build_dnakeiot_combined_windows.bat` 增加 `Client\bin` → staging 的 xcopy、`--add-data "bin;bin"` 与产物检查
  `dist\OHScrcpy\_internal\bin\arm64\mmi_touchd`。运行时通过 `sys._MEIPASS` 找到该二进制。

### 真机验证（device `150100424a5444345206517525298929`）

- 部署与启动：`start()` 耗时 772ms（含上传、fport、等待 ping）。
- 40 次点击：0 失败，平均 25.66ms/次（每次 3 个事件：DOWN + 2 MOVE + UP），中位数 23.4ms，最大 116ms。
- 20 次拖动：平均 5.51ms/次，最大 9.01ms；守护进程自报 `last_cost` 2ms。
- 对比旧路径：单事件约 340ms（`/bin/uinput`），相差两个数量级。
- 单元测试：`python3 -m pytest tests -q` → `179 passed`（新增 `tests/test_touch_daemon.py` 22 例，含线程 TCP 假守护进程）。
- 发布包：`Release\OHScrcpy-Dnakeiot\OHScrcpy.exe` 版本 `v2.4.2-dnakeiot`，sha256
  `46b194881755f19d37e78cc8d221d0aa6e6e3fe9004457c7c242c047cfca375f`；`_internal\bin\arm64\mmi_touchd` 哈希与源文件一致。

### 排障要点

- `hdc fport tcp:X tcp:X` 报 `[Fail]TCP Port listen failed at X`：历史转发仍在（守护进程被杀后条目不自动消失），先 `fport rm`。
- TCP 连上但 ping 无响应：`fport` 会先 accept 再拨号，首次连接出现 `WinError 10054` 属正常抖动，重试即可。
- 设备端清理：`pkill -x mmi_touchd`（**不要**用 `pkill -f /data/local/tmp/mmi_touchd`，会连当前 shell 一起杀）。

## Touch Timestamp Unit Fix (2026-10-28)

### 现象

v2.4.2 上线后，在设备 `ea010e325333324247102b4f80429c69` 上每次点击桌面图标都会弹出「打开 / 卸载」长按菜单，
轻点无法正常打开应用。客户端日志 `Release/OHScrcpy-Dnakeiot/logs/client_20261028_152042.log` 显示守护进程已就绪
（`触控注入守护进程就绪，端口 27196`，无回退告警），即事件确实由守护进程注入 —— 问题在事件内容而非通道。

### 根因：时间戳单位差 1000 倍

multimodalinput 的事件时间单位是**微秒**：

- `foundation/multimodalinput/input/util/common/src/util.cpp:67` `GetSysClockTime()` 用 `CLOCK_MONOTONIC` 计算
  `tv_sec * 1000 * 1000 + tv_nsec / 1000`（微秒）。
- 官方注入工具 `tools/inject_event/src/input_manager_command.cpp`（即设备上的 `/bin/uinput`）滑动分支用
  `pointerEvent->SetActionTime(currentTimeMs * TIME_TRANSITION)`，`TIME_TRANSITION = 1000`，即毫秒乘 1000 转微秒；
  其单指点击分支（`-T -c`）与 `-T -d/-u` 干脆**不设置** actionTime/downTime，交给框架补时间。
- `frameworks/proxy/events/src/input_manager.cpp` 与 `frameworks/proxy/event_handler/src/input_manager_impl.cpp`
  的 `SimulateInputEvent` 只加 `EVENT_FLAG_SIMULATE` 后就直接 `InjectPointerEvent`，**不会改写 actionTime**。

守护进程 v2.4.2 用 `steady_clock` 的**毫秒**填充 `SetActionTime()` 与 `PointerItem::SetDownTime()`，
数值量级约 1.79e12，而框架按微秒解释时当前时刻约 1.79e15 —— 每次按压的持续时长被算成数小时，
launcher 的点击识别直接判成长按。

### 修复（`tools/mmi_touchd/mmi_touchd.cpp`）

- `NowMs()` → `NowUs()`：`duration_cast<microseconds>(steady_clock::now().time_since_epoch()).count()`。
- 全局 `g_downTime` → `g_downTimeUs`，DOWN 时记录，`PointerItem::SetDownTime()` 用同一微秒基准。
- 注入耗时改为 `(NowUs() - startedUs) / 1000`，协议 `ok <cost_ms>` 语义不变。
- 去掉 `SetButtonId(0)` / `SetButtonPressed(...)`：触摸屏没有按键，与 `/bin/uinput` 的实际写法一致。
- 重新生成 `Client/bin/arm64/mmi_touchd`：23728 字节，sha256
  `f24e9896bf1e866f23e9333c7a3247291977430673633369b521da4956cb426a`。

### 真机验证（device `ea010e325333324247102b4f80429c69`，600x1024 竖屏面板 / 当前横屏 1024x600）

| 步骤 | 结果 |
|---|---|
| 部署 | `hdc file send` + `chmod 755`，设备端 `sha256sum` 与本地一致；`fport tcp:27196 tcp:27196` |
| 启动 | `mmi_touchd listening 27196 pid=3457`，`netstat -an` 见 `127.0.0.1:27196 LISTEN` |
| 关闭长按菜单 | `D 300 470` + `U 300 470` → 菜单消失（此前会一直卡住） |
| 点击图标 | `D 797 140` + `U 797 140` → 计算器打开，无长按菜单 |
| 连续点击 | 计算器键位 (481,212)/(646,212) → 显示 `76`，每次都是独立点击 |
| 拖动手势 | `D 512 4` → 20 个 `M` → `U 512 450` → 进入最近任务，手势被识别 |
| 注入耗时 | `ok 3`~`ok 12`（3~12ms/事件），与 v2.4.2 的延迟水平一致 |

结论：时间戳改成微秒后，点击恢复为“按下即抬起”的短按，拖动仍是连续 MOVE 序列；
守护进程仍未设置任何 button 字段，且不再依赖框架兜底时间。

### 排障顺序（复用）

1. 先看客户端日志确认走的是守护进程通道还是服务端流（有 `触控注入守护进程就绪` 即守护进程）。
2. 再比对手势语义（长按菜单 / 无响应 / 坐标错位）—— 语义错先怀疑事件字段（时间戳单位、button、pressed）。
3. 事件字段以系统 `/bin/uinput` 为基准对齐：它能正常点击，就照它的字段集合写。
4. 用 `snapshot_display -f` 截图闭环验证，不要只看 socket 返回的 `ok`。

