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

#ifndef OHSCRCPY_CEDARC_ENCODER_H
#define OHSCRCPY_CEDARC_ENCODER_H

#include "capture_wrapper.h"
#include "error_codes.h"

#include <vencoder.h>

#include <functional>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace OHScrcpy {

struct CodecConfig {
    int width;
    int height;
    int fps;
    int bitrate;
    std::string codec;
};

// A333-only raw-frame H.264 backend using the Cedarc v2 allocation lifecycle.
class CedarcEncoder {
public:
    using OnOutputCallback = std::function<void(uint8_t *data, size_t size, bool isKeyframe)>;

    CedarcEncoder();
    ~CedarcEncoder();

    ErrorCode Create(const CodecConfig &config);
    ErrorCode Start();
    ErrorCode Stop();
    ErrorCode Destroy();
    void QueueInputFrame(const CapturedFrame &frame);

    bool IsReady() const;
    void SetOutputCallback(OnOutputCallback callback);

    const std::vector<uint8_t> &GetVPSData() const { return vps_data_; }
    const std::vector<uint8_t> &GetSPSData() const { return sps_data_; }
    const std::vector<uint8_t> &GetPPSData() const { return pps_data_; }
    const CodecConfig &GetConfig() const { return config_; }

private:
    enum class FrameLayout {
        PACKED_4,
        YUV420_SEMIPLANAR,
        YUV420_PLANAR,
    };

    bool InitializeForFrame(const CapturedFrame &frame);
    void ProcessLoop();
    void ProcessFrame(const CapturedFrame &frame);
    bool MapFrameFormat(const CapturedFrame &frame, FrameLayout &layout, int &cedarcFormat) const;
    bool CopyFrameToInput(const CapturedFrame &frame, FrameLayout layout, void *inputBuffer) const;
    bool CopyPackedFrame(const CapturedFrame &frame, void *inputBuffer) const;
    void DrainBitstream();
    void ParseParameterSets(const uint8_t *data, size_t size);
    void ReleaseEncoder();
    static int Align16(int value);

    CodecConfig config_ {};
    VideoEncoder *encoder_ = nullptr;
    ScMemOpsS *mem_ops_ = nullptr;
    OnOutputCallback output_callback_;
    std::vector<uint8_t> vps_data_;
    std::vector<uint8_t> sps_data_;
    std::vector<uint8_t> pps_data_;
    int aligned_width_ = 0;
    int aligned_height_ = 0;
    int input_width_ = 0;
    int input_height_ = 0;
    int source_format_ = 0;
    int64_t next_pts_us_ = 0;
    uint64_t submitted_frames_ = 0;
    uint64_t dropped_frames_ = 0;
    bool is_created_ = false;
    bool is_started_ = false;
    bool stop_worker_ = false;
    std::deque<CapturedFrame> pending_frames_;
    CapturedFrame last_frame_ {};
    bool has_last_frame_ = false;
    std::condition_variable queue_cv_;
    std::thread worker_thread_;
    std::mutex encoder_mutex_;
    std::mutex mutex_;
};

} // namespace OHScrcpy

#endif // OHSCRCPY_CEDARC_ENCODER_H
