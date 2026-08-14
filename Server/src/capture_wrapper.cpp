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

#include "capture_wrapper.h"
#include "error_codes.h"
#include "logger.h"

#include <native_buffer.h>
#include <native_buffer_inner.h>

#include <algorithm>
#include <cstring>

const std::string LOG_TAG = "Capture";

namespace OHScrcpy {

CaptureWrapper* CaptureWrapper::active_instance_ = nullptr;

CaptureWrapper::CaptureWrapper()
    : capture_(nullptr)
    , is_created_(false)
    , is_capturing_(false) {
}

CaptureWrapper::~CaptureWrapper() {
    Destroy();
}

ErrorCode CaptureWrapper::Create() {
    if (is_created_) {
        LOG_INFO(LOG_TAG, "ScreenCapturer has been initialized");
        return ErrorCode::SUCCESS;
    }
    
    LOG_INFO(LOG_TAG, "Initializing ScreenCapturer...");
    
    capture_ = OH_AVScreenCapture_Create();
    if (capture_ == nullptr) {
        LOG_ERROR(LOG_TAG, "OH_AVScreenCapture_Create fail");
        return ErrorCode::CAPTURE_CREATE_FAILED;
    }
    
    is_created_ = true;
    return ErrorCode::SUCCESS;
}

ErrorCode CaptureWrapper::Init(const CaptureConfig& config) {
    if (!is_created_) {
        LOG_ERROR(LOG_TAG, "ScreenCapturer has not been created");
        return ErrorCode::CAPTURE_CREATE_FAILED;
    }
    
    config_ = config;

    // The legacy callback must be registered before Init on 6.1.
    OH_AVScreenCaptureCallback callback = {};
    callback.onVideoBufferAvailable = &CaptureWrapper::OnVideoBufferAvailable;
    active_instance_ = this;
    int32_t ret = OH_AVScreenCapture_SetCallback(capture_, callback);
    if (ret != AV_SCREEN_CAPTURE_ERR_OK) {
        active_instance_ = nullptr;
        LOG_ERROR(LOG_TAG, "OH_AVScreenCapture_SetCallback fail, err: " + std::to_string(ret));
        return ErrorCode::CAPTURE_SET_CALLBACK_FAILED;
    }
    
    OH_VideoCaptureInfo videoCapInfo = {
        .videoFrameWidth = config.width,
        .videoFrameHeight = config.height,
        .videoSource = OH_VIDEO_SOURCE_SURFACE_RGBA
    };
    
    OH_VideoEncInfo videoEncInfo = {
        .videoCodec = OH_H264,
        .videoBitrate = 1500000,
        .videoFrameRate = config.fps
    };
    
    OH_VideoInfo videoInfo = {
        .videoCapInfo = videoCapInfo,
        .videoEncInfo = videoEncInfo
    };
    
    OH_AVScreenCaptureConfig screenConfig = {
        .captureMode = OH_CAPTURE_HOME_SCREEN,
        .dataType = OH_ORIGINAL_STREAM,
        .videoInfo = videoInfo
    };
    
    ret = OH_AVScreenCapture_Init(capture_, screenConfig);
    if (ret != AV_SCREEN_CAPTURE_ERR_OK) {
        LOG_ERROR(LOG_TAG, "OH_AVScreenCapture_Init fail, err: " + std::to_string(ret));
        return ErrorCode::CAPTURE_INIT_FAILED;
    }
    
    OH_AVScreenCapture_SetMicrophoneEnabled(capture_, false);
    ret = OH_AVScreenCapture_SetErrorCallback(capture_, &CaptureWrapper::OnError, this);
    if (ret != AV_SCREEN_CAPTURE_ERR_OK) {
        LOG_ERROR(LOG_TAG, "OH_AVScreenCapture_SetErrorCallback fail, err: " + std::to_string(ret));
        return ErrorCode::CAPTURE_SET_CALLBACK_FAILED;
    }
    
    ret = OH_AVScreenCapture_SetStateCallback(capture_, &CaptureWrapper::OnStateChange, this);
    if (ret != AV_SCREEN_CAPTURE_ERR_OK) {
        LOG_ERROR(LOG_TAG, "OH_AVScreenCapture_SetStateCallback fail, err: " + std::to_string(ret));
        return ErrorCode::CAPTURE_SET_CALLBACK_FAILED;
    }

    LOG_INFO(LOG_TAG, "Screen capturer initialized: " + std::to_string(config.width) + "x" + std::to_string(config.height));
    return ErrorCode::SUCCESS;
}

ErrorCode CaptureWrapper::Start() {
    if (!is_created_ || capture_ == nullptr) {
        return ErrorCode::CAPTURE_CREATE_FAILED;
    }
    {
        std::lock_guard<std::mutex> lock(buffer_event_mutex_);
        stop_buffer_thread_ = false;
        pending_buffer_events_ = 0;
    }
    buffer_thread_ = std::thread(&CaptureWrapper::VideoBufferThreadMain, this);
    int32_t ret = OH_AVScreenCapture_StartScreenCapture(capture_);
    if (ret != AV_SCREEN_CAPTURE_ERR_OK) {
        {
            std::lock_guard<std::mutex> lock(buffer_event_mutex_);
            stop_buffer_thread_ = true;
            buffer_event_cv_.notify_all();
        }
        if (buffer_thread_.joinable()) {
            buffer_thread_.join();
        }
        LOG_ERROR(LOG_TAG, "OH_AVScreenCapture_StartScreenCapture fail, err: " + std::to_string(ret));
        return ErrorCode::CAPTURE_START_FAILED;
    }
    is_capturing_ = true;
    int32_t refreshRet = OH_AVScreenCapture_SetMaxVideoFrameRate(capture_, config_.fps);
    if (refreshRet != AV_SCREEN_CAPTURE_ERR_OK) {
        LOG_WARN(LOG_TAG, "OH_AVScreenCapture_SetMaxVideoFrameRate failed, err: " +
            std::to_string(refreshRet) + ", requested=" + std::to_string(config_.fps));
    } else {
        LOG_INFO(LOG_TAG, "Screen capture max frame rate set to " + std::to_string(config_.fps));
    }
    LOG_INFO(LOG_TAG, "Screen capture started in buffer mode");
    return ErrorCode::SUCCESS;
}

ErrorCode CaptureWrapper::StartWithSurface(OHNativeWindow* surface) {
    if (!is_created_ || capture_ == nullptr) {
        LOG_ERROR(LOG_TAG, "ScreenCapturer has not been initialized");
        return ErrorCode::CAPTURE_CREATE_FAILED;
    }
    
    if (surface == nullptr) {
        LOG_ERROR(LOG_TAG, "Surface is null");
        return ErrorCode::GENERAL_INVALID_PARAMETER;
    }
    
    int32_t ret = OH_AVScreenCapture_StartScreenCaptureWithSurface(capture_, surface);
    if (ret != 0) {
        LOG_ERROR(LOG_TAG, "OH_AVScreenCapture_StartScreenCaptureWithSurface fail, err: " + std::to_string(ret));
        return ErrorCode::CAPTURE_START_FAILED;
    }
    
    is_capturing_ = true;
    LOG_INFO(LOG_TAG, "Screen capture started");
    return ErrorCode::SUCCESS;
}

ErrorCode CaptureWrapper::Stop() {
    if (capture_ && is_capturing_) {
        {
            std::lock_guard<std::mutex> lock(buffer_event_mutex_);
            stop_buffer_thread_ = true;
            pending_buffer_events_ = 0;
            buffer_event_cv_.notify_all();
        }
        if (buffer_thread_.joinable()) {
            buffer_thread_.join();
        }
        OH_AVScreenCapture_StopScreenCapture(capture_);
        is_capturing_ = false;
        LOG_INFO(LOG_TAG, "Screen capture stopped");
    }
    return ErrorCode::SUCCESS;
}

ErrorCode CaptureWrapper::Destroy() {
    if (capture_) {
        if (is_capturing_) {
            Stop();
        }
        OH_AVScreenCapture_Release(capture_);
        capture_ = nullptr;
        if (active_instance_ == this) active_instance_ = nullptr;
        LOG_INFO(LOG_TAG, "Screen capture released");
    }
    
    is_created_ = false;
    return ErrorCode::SUCCESS;
}

void CaptureWrapper::SetMicrophoneEnabled(bool enabled) {
    if (capture_) {
        OH_AVScreenCapture_SetMicrophoneEnabled(capture_, enabled);
    }
}

void CaptureWrapper::SetErrorCallback(ErrorCallback callback) {
    error_callback_ = callback;
}

void CaptureWrapper::SetStateCallback(StateCallback callback) {
    state_callback_ = callback;
}

void CaptureWrapper::SetVideoFrameCallback(VideoFrameCallback callback) {
    video_frame_callback_ = callback;
}

bool CaptureWrapper::IsReady() const {
    return is_created_ && capture_ != nullptr;
}

bool CaptureWrapper::IsCapturing() const {
    return is_capturing_;
}

void CaptureWrapper::OnError(OH_AVScreenCapture* capture, int32_t errorCode, void* userData) {
    CaptureWrapper* self = static_cast<CaptureWrapper*>(userData);
    if (self) {
        self->HandleError(errorCode);
    }
}

void CaptureWrapper::OnStateChange(OH_AVScreenCapture* capture, OH_AVScreenCaptureStateCode stateCode, void* userData) {
    CaptureWrapper* self = static_cast<CaptureWrapper*>(userData);
    if (self) {
        self->HandleStateChange(stateCode);
    }
}

void CaptureWrapper::OnVideoBufferAvailable(OH_AVScreenCapture* capture, bool isReady) {
    if (isReady && active_instance_ != nullptr && active_instance_->capture_ == capture) {
        active_instance_->SignalVideoBuffer();
    }
}

void CaptureWrapper::SignalVideoBuffer()
{
    std::lock_guard<std::mutex> lock(buffer_event_mutex_);
    if (stop_buffer_thread_) {
        return;
    }
    // Keep one event per queued surface buffer. The producer-side listener
    // expects the client to acquire and release every queued buffer.
    ++pending_buffer_events_;
    buffer_event_cv_.notify_one();
}

void CaptureWrapper::VideoBufferThreadMain()
{
    while (true) {
        {
            std::unique_lock<std::mutex> lock(buffer_event_mutex_);
            buffer_event_cv_.wait(lock, [this] {
                return stop_buffer_thread_ || pending_buffer_events_ != 0;
            });
            if (stop_buffer_thread_ && pending_buffer_events_ == 0) {
                return;
            }
            if (pending_buffer_events_ != 0) {
                --pending_buffer_events_;
            }
        }
        HandleVideoBuffer();
    }
}

void CaptureWrapper::HandleError(int32_t errorCode) {
    LOG_ERROR(LOG_TAG, "Screen capture error: " + std::to_string(errorCode));
    if (error_callback_) {
        error_callback_(errorCode);
    }
}

void CaptureWrapper::HandleStateChange(OH_AVScreenCaptureStateCode stateCode) {
    switch (stateCode) {
        case OH_SCREEN_CAPTURE_STATE_STARTED:
            LOG_INFO(LOG_TAG, "Screen capture state: STARTED");
            break;
        case OH_SCREEN_CAPTURE_STATE_STOPPED_BY_CALL:
            LOG_INFO(LOG_TAG, "Screen capture state: STOPPED_BY_CALL");
            break;
        case OH_SCREEN_CAPTURE_STATE_CANCELED:
            LOG_INFO(LOG_TAG, "Screen capture state: CANCELED");
            break;
        default:
            LOG_INFO(LOG_TAG, "Screen capture state code: " + std::to_string(static_cast<int32_t>(stateCode)));
            break;
    }
    
    if (state_callback_) {
        state_callback_(static_cast<int32_t>(stateCode));
    }
}

void CaptureWrapper::HandleVideoBuffer() {
    if (capture_ == nullptr || !video_frame_callback_) return;
    if (callback_count_ < 3 || (callback_count_ % 60) == 0) {
        LOG_INFO(LOG_TAG, "HandleVideoBuffer entered, callback_count=" + std::to_string(callback_count_));
    }
    int32_t fence = -1;
    int64_t timestamp = 0;
    OH_Rect region = {};
    OH_NativeBuffer* buffer = OH_AVScreenCapture_AcquireVideoBuffer(capture_, &fence, &timestamp, &region);
    if (buffer == nullptr) {
        LOG_ERROR(LOG_TAG, "OH_AVScreenCapture_AcquireVideoBuffer fail, callback_count=" +
            std::to_string(callback_count_));
        return;
    }
    OH_NativeBuffer_Config config = {};
    OH_NativeBuffer_GetConfig(buffer, &config);
    void* addr = nullptr;
    int32_t ret = OH_NativeBuffer_Map(buffer, &addr);
    if (ret == 0 && addr != nullptr && config.width > 0 && config.height > 0 && config.stride > 0) {
        CapturedFrame frame;
        const size_t bytes = static_cast<size_t>(config.stride) * static_cast<size_t>(config.height);
        frame.owned_data = std::make_shared<std::vector<uint8_t>>(bytes);
        std::memcpy(frame.owned_data->data(), addr, bytes);
        frame.data = frame.owned_data->data();
        frame.width = config.width;
        frame.height = config.height;
        frame.stride = config.stride;
        frame.format = config.format;
        frame.timestamp = timestamp;
        frame.sequence = OH_NativeBuffer_GetSeqNum(buffer);
        frame.plane_count = 1;
        const BufferHandle *handle = OH_NativeBuffer_GetBufferHandle(buffer);
        frame.fd = handle == nullptr ? -1 : handle->fd;
        frame.planes[0].offset = 0;
        frame.planes[0].row_stride = static_cast<uint32_t>(config.stride);
        frame.planes[0].column_stride = 4;
        ++callback_count_;
        if (callback_count_ <= 3 || (callback_count_ % 60) == 0) {
            LOG_INFO(LOG_TAG, "Captured frame callback count=" + std::to_string(callback_count_) +
                " seq=" + std::to_string(frame.sequence));
        }
        video_frame_callback_(frame);
        OH_NativeBuffer_Unmap(buffer);
        int32_t releaseRet = OH_AVScreenCapture_ReleaseVideoBuffer(capture_);
        if (callback_count_ <= 3 || (callback_count_ % 60) == 0) {
            LOG_INFO(LOG_TAG, "Released captured buffer count=" + std::to_string(callback_count_) +
                " ret=" + std::to_string(releaseRet));
        }
        return;
    } else {
        LOG_ERROR(LOG_TAG, "Captured native buffer cannot be mapped with plane metadata, err=" + std::to_string(ret));
        if (addr != nullptr) {
            OH_NativeBuffer_Unmap(buffer);
        }
    }
    int32_t releaseRet = OH_AVScreenCapture_ReleaseVideoBuffer(capture_);
    LOG_ERROR(LOG_TAG, "Released invalid captured buffer ret=" + std::to_string(releaseRet));
}


} // namespace OHScrcpy
