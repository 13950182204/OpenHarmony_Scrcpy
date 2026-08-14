/*
 * Copyright (c) 2026 luodh0157.
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#ifndef OHSCRCPY_CAPTURE_WRAPPER_H
#define OHSCRCPY_CAPTURE_WRAPPER_H

#include "error_codes.h"
#include "logger.h"

#include <array>
#include <condition_variable>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

// OpenHarmony屏幕捕获C-API头文件
#include <native_avscreen_capture.h>
#include <native_avscreen_capture_base.h>
#include <native_avscreen_capture_errors.h>
#include <native_avbuffer.h>

namespace OHScrcpy {

struct CaptureConfig {
    int width;
    int height;
    int fps;
    uint64_t displayId;
};

struct CapturedPlane {
    uint64_t offset = 0;
    uint32_t row_stride = 0;
    uint32_t column_stride = 0;
};

// The pixel data remains valid only for the synchronous callback invocation.
struct CapturedFrame {
    const uint8_t *data = nullptr;
    int32_t width = 0;
    int32_t height = 0;
    int32_t stride = 0;
    int32_t format = 0;
    int32_t fd = -1;
    uint32_t sequence = 0;
    int64_t timestamp = 0;
    uint32_t plane_count = 0;
    std::array<CapturedPlane, 4> planes {};
    std::shared_ptr<std::vector<uint8_t>> owned_data;
};

/**
 * RAII wrapper for OH_AVScreenCapture
 * 
 * Based on real OpenHarmony API:
 * - OH_AVScreenCapture_Create
 * - OH_AVScreenCapture_Init
 * - OH_AVScreenCapture_SetMicrophoneEnabled
 * - OH_AVScreenCapture_SetErrorCallback
 * - OH_AVScreenCapture_SetStateCallback
 * - OH_AVScreenCapture_StartScreenCaptureWithSurface
 * - OH_AVScreenCapture_StopScreenCapture
 * - OH_AVScreenCapture_Release
 */
class CaptureWrapper {
public:
    using ErrorCallback = std::function<void(int32_t errorCode)>;
    using StateCallback = std::function<void(int32_t stateCode)>;
    using VideoFrameCallback = std::function<void(const CapturedFrame &frame)>;
    
    CaptureWrapper();
    ~CaptureWrapper();
    
    ErrorCode Create();
    ErrorCode Init(const CaptureConfig& config);
    ErrorCode Start();
    ErrorCode StartWithSurface(OHNativeWindow* surface);
    ErrorCode Stop();
    ErrorCode Destroy();
    
    void SetMicrophoneEnabled(bool enabled);
    void SetErrorCallback(ErrorCallback callback);
    void SetStateCallback(StateCallback callback);
    void SetVideoFrameCallback(VideoFrameCallback callback);
    
    bool IsReady() const;
    bool IsCapturing() const;
    
    OH_AVScreenCapture* GetCapture() const { return capture_; }
    const CaptureConfig& GetConfig() const { return config_; }

private:
    static void OnError(OH_AVScreenCapture* capture, int32_t errorCode, void* userData);
    static void OnStateChange(OH_AVScreenCapture* capture, OH_AVScreenCaptureStateCode stateCode, void* userData);
    static void OnVideoBufferAvailable(OH_AVScreenCapture* capture, bool isReady);
    
    void HandleError(int32_t errorCode);
    void HandleStateChange(OH_AVScreenCaptureStateCode stateCode);
    void HandleVideoBuffer();
    void SignalVideoBuffer();
    void VideoBufferThreadMain();
    
    OH_AVScreenCapture* capture_;
    CaptureConfig config_;
    
    ErrorCallback error_callback_;
    StateCallback state_callback_;
    VideoFrameCallback video_frame_callback_;
    
    bool is_created_;
    bool is_capturing_;
    uint64_t callback_count_ = 0;

    std::mutex buffer_event_mutex_;
    std::condition_variable buffer_event_cv_;
    uint32_t pending_buffer_events_ = 0;
    bool stop_buffer_thread_ = false;
    std::thread buffer_thread_;

    static CaptureWrapper* active_instance_;
};

} // namespace OHScrcpy

#endif // OHSCRCPY_CAPTURE_WRAPPER_H
