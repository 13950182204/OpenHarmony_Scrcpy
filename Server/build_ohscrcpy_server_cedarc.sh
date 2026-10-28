#!/usr/bin/env bash
#
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
#
# Cross-compile the CedarC (Allwinner A333/A537) server build from this
# repository and refresh Server/bin/Dnakeiot/ohscrcpy_server.
#
# Usage: bash Server/build_ohscrcpy_server_cedarc.sh [OH_ROOT] [PRODUCT_OUT]
#   OH_ROOT      OpenHarmony source tree root   (default /home/cx/os/6.1_)
#   PRODUCT_OUT  product out directory          (default OH_ROOT/out/a333_newpines)
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
OH_ROOT="/home/cx/os/6.1_"
PRODUCT_OUT=""

if [ $# -ge 1 ]; then OH_ROOT="$1"; fi
if [ $# -ge 2 ]; then PRODUCT_OUT="$2"; fi
if [ -z "$PRODUCT_OUT" ]; then PRODUCT_OUT="$OH_ROOT/out/a333_newpines"; fi

CLANG="$OH_ROOT/prebuilts/clang/ohos/linux-x86_64/llvm/bin/clang"
SDK="$OH_ROOT/prebuilts/ohos-sdk/linux/23/native/sysroot/usr"
MUSL="$PRODUCT_OUT/obj/third_party/musl/usr"
MEDIA="$OH_ROOT/device/soc/allwinner/a333/hardware/codec/media"
SYS_LIB="$PRODUCT_OUT/packages/phone/system/lib64"
PLAT_LIB="$SYS_LIB/platformsdk"
BUILD_DIR="$REPO_ROOT/.build_out"
OUT="$BUILD_DIR/ohscrcpy_server_cedarc"

mkdir -p "$BUILD_DIR"

for d in "$CLANG" "$SDK" "$MUSL" "$MEDIA" "$SYS_LIB"; do
    if [ ! -e "$d" ]; then
        echo "[ERROR] missing: $d" >&2
        exit 1
    fi
done

"$CLANG" --target=aarch64-linux-ohos --sysroot="$MUSL" -fuse-ld=lld -std=c++17 -O2 \
    -idirafter "$MUSL/include/aarch64-linux-ohos" -B "$MUSL/lib/aarch64-linux-ohos" \
    -idirafter "$SDK/include" \
    -DOHSCRCPY_USE_CEDARC -Wno-c++11-narrowing -fno-rtti \
    -I "$HERE/include" \
    -I "$MEDIA/include" \
    -I "$SDK/include/multimedia/player_framework" \
    -idirafter "$SDK/include" \
    -I "$SDK/include/native_buffer" \
    -I "$OH_ROOT/foundation/window/window_manager/interfaces/innerkits/dm" \
    -I "$OH_ROOT/foundation/window/window_manager/utils/include" \
    -I "$OH_ROOT/foundation/window/window_manager/interfaces/innerkits" \
    -I "$OH_ROOT/foundation/window/window_manager/wm/include" \
    -I "$OH_ROOT/foundation/graphic/graphic_surface/interfaces/inner_api/surface" \
    -I "$OH_ROOT/foundation/graphic/graphic_surface/interfaces/inner_api/utils" \
    -I "$OH_ROOT/foundation/graphic/graphic_surface/interfaces/inner_api/common" \
    -I "$OH_ROOT/foundation/graphic/graphic_surface/interfaces/inner_api/buffer_handle" \
    -I "$OH_ROOT/foundation/communication/ipc/interfaces/innerkits/ipc_core/include" \
    -I "$OH_ROOT/commonlibrary/c_utils/base/include" \
    -I "$OH_ROOT/foundation/graphic/graphic_2d/rosen/modules/platform/image_native" \
    "$HERE/src/ohscrcpy_server.cpp" \
    "$HERE/src/capture_wrapper.cpp" \
    "$HERE/src/cedarc_encoder.cpp" \
    "$HERE/src/logger.cpp" \
    -L "$SYS_LIB" -L "$PLAT_LIB" -L "$MEDIA/lib64" \
    -L "$PRODUCT_OUT/graphic/graphic_surface" \
    -L "$PRODUCT_OUT/lib.unstripped/graphic/graphic_surface" \
    -L "$PRODUCT_OUT/lib.unstripped/window/window_manager" \
    -L "$PRODUCT_OUT/lib.unstripped/commonlibrary/c_utils" \
    -l:libnative_avscreen_capture.so -l:libsurface.z.so -l:libdm.z.so \
    -l:libutils.z.so -l:libawcodec_enc.z.so -l:libnative_buffer.so \
    -lc++ -lc -o "$OUT"

chmod +x "$OUT"
ls -l "$OUT"
sha256sum "$OUT"

echo "[OK] scratch build: $OUT"
echo "[HINT] install with: cp $OUT $REPO_ROOT/Server/bin/Dnakeiot/ohscrcpy_server"
