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
# Cross-compile the OpenHarmony arm64 touch injection daemon and refresh the
# client resource copy (Client/bin/arm64/mmi_touchd + mmi_touchd.sha256).
#
# Usage: bash tools/mmi_touchd/build_mmi_touchd.sh [OH_ROOT] [PRODUCT_OUT]
#   OH_ROOT      OpenHarmony source tree root   (default /home/cx/os/6.1_)
#   PRODUCT_OUT  product out directory          (default OH_ROOT/out/a333_newpines)
#
# The product out directory must provide the musl sysroot and the
# multimodalinput platform libraries, i.e. it must be an a333_newpines build.
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
OUT_DIR="$REPO_ROOT/Client/bin/arm64"
OH_ROOT="/home/cx/os/6.1_"
PRODUCT_OUT=""

if [ $# -ge 1 ]; then
    OH_ROOT="$1"
fi
if [ $# -ge 2 ]; then
    PRODUCT_OUT="$2"
fi
if [ -z "$PRODUCT_OUT" ]; then
    PRODUCT_OUT="$OH_ROOT/out/a333_newpines"
fi

CLANG="$OH_ROOT/prebuilts/clang/ohos/linux-x86_64/llvm/bin/clang"
LLVM="$OH_ROOT/prebuilts/clang/ohos/linux-x86_64/llvm"
SYSROOT="$PRODUCT_OUT/obj/third_party/musl/usr"
MUSL_INCLUDE="$SYSROOT/include/aarch64-linux-ohos"
MUSL_LIB="$SYSROOT/lib/aarch64-linux-ohos"
PLATFORM_LIBS="$PRODUCT_OUT/packages/phone/system/lib64/platformsdk"
INPUT="$OH_ROOT/foundation/multimodalinput/input"
MMI="$INPUT/interfaces/native/innerkits"
IPC="$OH_ROOT/foundation/communication/ipc/interfaces/innerkits"

if [ ! -x "$CLANG" ]; then
    echo "[ERROR] clang not found: $CLANG" >&2
    exit 1
fi
if [ ! -d "$MUSL_INCLUDE" ]; then
    echo "[ERROR] musl sysroot not found: $MUSL_INCLUDE" >&2
    exit 1
fi
if [ ! -d "$MMI" ]; then
    echo "[ERROR] multimodalinput headers not found: $MMI" >&2
    exit 1
fi

mkdir -p "$OUT_DIR"

# -idirafter keeps the musl headers behind the clang resource headers: musl
# stddef.h does not define nullptr_t, which breaks every C++ standard header.
# -B points the linker at the musl crt objects (Scrt1.o/crti.o/crtn.o).
"$CLANG" --target=aarch64-linux-ohos --sysroot="$SYSROOT" -fuse-ld=lld -std=c++17 -O2 \
    -idirafter "$MUSL_INCLUDE" -B "$MUSL_LIB" \
    -I "$HERE/stub" \
    -I "$MMI/proxy/include" \
    -I "$MMI/event/include" \
    -I "$INPUT/util/common/include" \
    -I "$INPUT/util/network/include" \
    -I "$OH_ROOT/commonlibrary/c_utils/base/include" \
    -I "$IPC/ipc_core/include" \
    "$HERE/mmi_touchd.cpp" \
    -L "$MUSL_LIB" -L "$LLVM/lib/aarch64-linux-ohos" -L "$PLATFORM_LIBS" \
    -lmmi-client.z -lmmi-util.z -lc++ -lc \
    -o "$OUT_DIR/mmi_touchd"

chmod +x "$OUT_DIR/mmi_touchd"
(cd "$OUT_DIR" && sha256sum mmi_touchd > mmi_touchd.sha256)
ls -l "$OUT_DIR/mmi_touchd"
cat "$OUT_DIR/mmi_touchd.sha256"
