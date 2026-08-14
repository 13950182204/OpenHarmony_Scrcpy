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

#include "cedarc_encoder.h"

#include "logger.h"

#include <native_buffer.h>
#include <surface_type.h>
#include <memoryAdapter.h>
#include <vencoder.h>

#include <algorithm>
#include <chrono>
#include <cstring>
#include <sstream>
#include <utility>

namespace {
const std::string LOG_TAG = "Cedarc";

bool IsStartCode(const uint8_t *data, size_t size, size_t pos, size_t &length)
{
    if (pos + 3 > size || data[pos] != 0 || data[pos + 1] != 0) {
        return false;
    }
    if (data[pos + 2] == 1) {
        length = 3;
        return true;
    }
    if (pos + 4 <= size && data[pos + 2] == 0 && data[pos + 3] == 1) {
        length = 4;
        return true;
    }
    return false;
}

std::string PlaneSummary(const OHScrcpy::CapturedFrame &frame)
{
    std::ostringstream stream;
    for (uint32_t index = 0; index < frame.plane_count; ++index) {
        if (index != 0) {
            stream << ",";
        }
        const auto &plane = frame.planes[index];
        stream << index << ":off=" << plane.offset << ":row=" << plane.row_stride
               << ":column=" << plane.column_stride;
    }
    return stream.str();
}
} // namespace

namespace OHScrcpy {

using namespace OHOS;

CedarcEncoder::CedarcEncoder() = default;

CedarcEncoder::~CedarcEncoder()
{
    Destroy();
}

ErrorCode CedarcEncoder::Create(const CodecConfig &config)
{
    std::lock_guard<std::mutex> lock(mutex_);
    if (is_created_) {
        return ErrorCode::SUCCESS;
    }
    if (config.codec != "h264" || config.width <= 0 || config.height <= 0 || config.fps <= 0 || config.bitrate <= 0) {
        LOG_ERROR(LOG_TAG, "Cedarc only accepts valid H.264 stream parameters");
        return ErrorCode::ENCODER_CONFIGURE_FAILED;
    }
    config_ = config;
    is_created_ = true;
    LOG_INFO(LOG_TAG, "Cedarc v2 backend selected; initialization waits for the first captured buffer");
    return ErrorCode::SUCCESS;
}

ErrorCode CedarcEncoder::Start()
{
    std::lock_guard<std::mutex> lock(mutex_);
    if (!is_created_) {
        return ErrorCode::ENCODER_CREATE_FAILED;
    }
    is_started_ = true;
    stop_worker_ = false;
    worker_thread_ = std::thread(&CedarcEncoder::ProcessLoop, this);
    return ErrorCode::SUCCESS;
}

ErrorCode CedarcEncoder::Stop()
{
    std::thread worker;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        is_started_ = false;
        stop_worker_ = true;
        queue_cv_.notify_all();
        worker = std::move(worker_thread_);
    }
    if (worker.joinable()) {
        worker.join();
    }
    std::lock_guard<std::mutex> lock(mutex_);
    pending_frames_.clear();
    last_frame_ = {};
    has_last_frame_ = false;
    std::lock_guard<std::mutex> encoderLock(encoder_mutex_);
    ReleaseEncoder();
    return ErrorCode::SUCCESS;
}

ErrorCode CedarcEncoder::Destroy()
{
    Stop();
    std::lock_guard<std::mutex> lock(mutex_);
    is_created_ = false;
    return ErrorCode::SUCCESS;
}

bool CedarcEncoder::IsReady() const
{
    return is_created_;
}

void CedarcEncoder::SetOutputCallback(OnOutputCallback callback)
{
    std::lock_guard<std::mutex> lock(mutex_);
    output_callback_ = std::move(callback);
}

void CedarcEncoder::QueueInputFrame(const CapturedFrame &frame)
{
    std::lock_guard<std::mutex> lock(mutex_);
    if (!is_started_) {
        return;
    }
    last_frame_ = frame;
    has_last_frame_ = true;
    if (pending_frames_.size() >= 2) {
        pending_frames_.pop_front();
        ++dropped_frames_;
    }
    pending_frames_.push_back(frame);
    if (frame.sequence < 3 || (frame.sequence % 60) == 0) {
        LOG_INFO(LOG_TAG, "Queued captured frame seq=" + std::to_string(frame.sequence) +
            " queue=" + std::to_string(pending_frames_.size()));
    }
    queue_cv_.notify_one();
}

void CedarcEncoder::ProcessLoop()
{
    // The device capture path is limited to 60 Hz, but its callback and
    // scheduler jitter otherwise makes the wire rate settle below 60. The
    // cached-frame path therefore runs at a small cadence margin; negotiated
    // timestamps and the public stream configuration remain at the requested
    // frame rate.
    const int cadence_fps = std::max(1, config_.fps + 3);
    const auto frame_interval = std::chrono::microseconds(1000000 / cadence_fps);
    LOG_INFO(LOG_TAG, "Cedarc cached-frame cadence: " + std::to_string(cadence_fps) + "Hz for requested " +
        std::to_string(config_.fps) + "fps");
    auto next_frame_deadline = std::chrono::steady_clock::now();
    while (true) {
        CapturedFrame frame;
        {
            std::unique_lock<std::mutex> lock(mutex_);
            if (pending_frames_.empty() && !has_last_frame_ && !stop_worker_) {
                queue_cv_.wait(lock, [this] {
                    return stop_worker_ || !pending_frames_.empty() || has_last_frame_;
                });
            }
            if (pending_frames_.empty() && stop_worker_) {
                return;
            }
            if (!pending_frames_.empty()) {
                frame = std::move(pending_frames_.front());
                pending_frames_.pop_front();
                next_frame_deadline = std::chrono::steady_clock::now();
            } else {
                // ScreenCapture's raw callback is content-change driven on
                // A333. Re-submit the last valid frame at the negotiated
                // cadence so a static page still produces a live stream.
                next_frame_deadline += frame_interval;
                if (queue_cv_.wait_until(lock, next_frame_deadline, [this] {
                    return stop_worker_ || !pending_frames_.empty();
                })) {
                    if (stop_worker_ && pending_frames_.empty()) {
                        return;
                    }
                    if (!pending_frames_.empty()) {
                        frame = std::move(pending_frames_.front());
                        pending_frames_.pop_front();
                    } else {
                        continue;
                    }
                } else {
                    frame = last_frame_;
                }
            }
        }
        ProcessFrame(frame);
    }
}

void CedarcEncoder::ProcessFrame(const CapturedFrame &frame)
{
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!is_started_) {
            return;
        }
    }
    std::lock_guard<std::mutex> encoderLock(encoder_mutex_);
    const bool trace_frame = submitted_frames_ < 3 || (submitted_frames_ % 60) == 0;
    if (trace_frame) {
        LOG_INFO(LOG_TAG, "Processing captured frame seq=" + std::to_string(frame.sequence));
    }
    if (encoder_ == nullptr && !InitializeForFrame(frame)) {
        ++dropped_frames_;
        return;
    }
    if (frame.width != input_width_ || frame.height != input_height_ || frame.format != source_format_) {
        LOG_ERROR(LOG_TAG, "Captured buffer configuration changed after Cedarc initialization; dropping frame");
        ++dropped_frames_;
        return;
    }

    VencInputBuffer input = {};
    int ret = GetOneAllocInputBuffer(encoder_, &input);
    if (ret != 0) {
        ++dropped_frames_;
        if (dropped_frames_ == 1 || dropped_frames_ % 30 == 0) {
            LOG_WARN(LOG_TAG, "Cedarc has no free input buffer, dropped=" + std::to_string(dropped_frames_));
        }
        return;
    }

    FrameLayout layout;
    int cedarcFormat = 0;
    if (!MapFrameFormat(frame, layout, cedarcFormat)) {
        ReturnOneAllocInputBuffer(encoder_, &input);
        ++dropped_frames_;
        return;
    }
    const bool copied = layout == FrameLayout::PACKED_4 ? CopyPackedFrame(frame, &input) :
        CopyFrameToInput(frame, layout, &input);
    if (!copied) {
        ReturnOneAllocInputBuffer(encoder_, &input);
        ++dropped_frames_;
        return;
    }

    input.nPts = next_pts_us_;
    next_pts_us_ += 1000000LL / config_.fps;
    input.nFlag = 0;
    ret = FlushCacheAllocInputBuffer(encoder_, &input);
    if (ret == 0) {
        ret = AddOneInputBuffer(encoder_, &input);
    }
    if (ret == 0) {
        ret = VideoEncodeOneFrame(encoder_);
    }
    if (trace_frame) {
        LOG_INFO(LOG_TAG, "VideoEncodeOneFrame seq=" + std::to_string(frame.sequence) +
            " ret=" + std::to_string(ret));
    }
    if (ret < 0) {
        LOG_ERROR(LOG_TAG, "Cedarc encode failed, err=" + std::to_string(ret));
        ++dropped_frames_;
    } else {
        ++submitted_frames_;
    }
    // Match the Cedarc v2 demo: release the submitted input before draining
    // output so the encoder can recycle its input slot without stalling.
    AlreadyUsedInputBuffer(encoder_, &input);
    ReturnOneAllocInputBuffer(encoder_, &input);
    if (ret >= 0) {
        DrainBitstream();
    }
}

bool CedarcEncoder::InitializeForFrame(const CapturedFrame &frame)
{
    FrameLayout layout;
    int cedarcFormat = 0;
    if (!MapFrameFormat(frame, layout, cedarcFormat)) {
        return false;
    }
    input_width_ = frame.width;
    input_height_ = frame.height;
    aligned_width_ = Align16(input_width_);
    aligned_height_ = Align16(input_height_);
    source_format_ = frame.format;

    std::ostringstream message;
    message << "First capture buffer: format=" << frame.format << " width=" << frame.width
            << " height=" << frame.height << " stride=" << frame.stride
            << " planes=" << frame.plane_count << " fd=" << frame.fd
            << " seq=" << frame.sequence << " source_layout=" << static_cast<int>(layout)
            << " cedarc_format=" << cedarcFormat
            << " plane_info=[" << PlaneSummary(frame) << "]";
    LOG_INFO(LOG_TAG, message.str());

    mem_ops_ = MemAdapterGetOpsS();
    if (mem_ops_ == nullptr || CdcMemOpen(mem_ops_) != 0) {
        LOG_ERROR(LOG_TAG, "Cedarc memory adapter initialization failed");
        mem_ops_ = nullptr;
        return false;
    }
    encoder_ = VideoEncCreate(VENC_CODEC_H264);
    if (encoder_ == nullptr) {
        LOG_ERROR(LOG_TAG, "VideoEncCreate(VENC_CODEC_H264) failed");
        CdcMemClose(mem_ops_);
        mem_ops_ = nullptr;
        return false;
    }

    VencBaseConfig base = {};
    base.memops = mem_ops_;
    // ScreenCapture must use the complete physical display on A333. Cedarc's
    // scaler converts that raw input to the AVC-sized stream canvas, so the
    // right edge and navigation area are not discarded before encoding.
    base.nInputWidth = input_width_;
    base.nInputHeight = input_height_;
    base.nDstWidth = config_.width;
    base.nDstHeight = config_.height;
    base.nStride = aligned_width_;
    base.eInputFormat = static_cast<VENC_PIXEL_FMT>(cedarcFormat);
    base.bEncH264Nalu = 0;

    VencH264Param h264 = {};
    h264.bEntropyCodingCABAC = 1;
    h264.nFramerate = config_.fps;
    h264.nSrcFramerate = config_.fps;
    h264.nBitrate = config_.bitrate;
    h264.nMaxKeyInterval = config_.fps;
    h264.nCodingMode = VENC_FRAME_CODING;
    h264.sProfileLevel.nProfile = VENC_H264ProfileMain;
    h264.sProfileLevel.nLevel = VENC_H264Level41;
    h264.sQPRange.nMinqp = 10;
    h264.sQPRange.nMaxqp = 50;
    h264.sQPRange.nMinPqp = 10;
    h264.sQPRange.nMaxPqp = 50;
    h264.sRcParam.eRcMode = AW_CBR;
    unsigned int vbvSize = std::max(2 * 1024 * 1024, config_.bitrate * 2);
    VideoEncSetParameter(encoder_, VENC_IndexParamSetVbvSize, &vbvSize);
    if (VideoEncSetParameter(encoder_, VENC_IndexParamH264Param, &h264) != 0 ||
        VideoEncInit(encoder_, &base) != 0) {
        LOG_ERROR(LOG_TAG, "Cedarc VideoEncInit failed");
        ReleaseEncoder();
        return false;
    }

    VencAllocateBufferParam allocation = {};
    allocation.nBufferNum = 2;
    if (layout == FrameLayout::PACKED_4) {
        allocation.nSizeY = static_cast<unsigned int>(aligned_width_ * aligned_height_ * 4);
        allocation.nSizeC = 0;
    } else {
        allocation.nSizeY = static_cast<unsigned int>(aligned_width_ * aligned_height_);
        allocation.nSizeC = allocation.nSizeY / 2;
    }
    if (AllocInputBuffer(encoder_, &allocation) != 0) {
        LOG_ERROR(LOG_TAG, "Cedarc AllocInputBuffer failed");
        ReleaseEncoder();
        return false;
    }

    VencHeaderData header = {};
    if (VideoEncGetParameter(encoder_, VENC_IndexParamH264SPSPPS, &header) == 0 &&
        header.pBuffer != nullptr && header.nLength > 0) {
        ParseParameterSets(header.pBuffer, header.nLength);
    }
    LOG_INFO(LOG_TAG, "Cedarc initialized: input=" + std::to_string(input_width_) + "x" +
        std::to_string(input_height_) + " encoded=" + std::to_string(config_.width) + "x" +
        std::to_string(config_.height) + " aligned_input=" + std::to_string(aligned_width_) + "x" +
        std::to_string(aligned_height_) + " input_format=" +
        std::to_string(cedarcFormat));
    return true;
}

bool CedarcEncoder::MapFrameFormat(const CapturedFrame &frame, FrameLayout &layout, int &cedarcFormat) const
{
    switch (frame.format) {
        case GRAPHIC_PIXEL_FMT_RGBA_8888:
        case GRAPHIC_PIXEL_FMT_RGBX_8888:
            layout = FrameLayout::PACKED_4;
            // OHOS RGBA_8888 is byte-ordered RGBA. Cedarc's packed enum is
            // interpreted as a native 32-bit word, so ABGR selects the
            // RGBA byte order on little-endian AArch64.
            cedarcFormat = VENC_PIXEL_ABGR;
            return frame.plane_count >= 1;
        case GRAPHIC_PIXEL_FMT_BGRA_8888:
        case GRAPHIC_PIXEL_FMT_BGRX_8888:
            layout = FrameLayout::PACKED_4;
            cedarcFormat = VENC_PIXEL_ARGB;
            return frame.plane_count >= 1;
        case GRAPHIC_PIXEL_FMT_YCBCR_420_SP:
            layout = FrameLayout::YUV420_SEMIPLANAR;
            cedarcFormat = VENC_PIXEL_YUV420SP;
            return frame.plane_count >= 2;
        case GRAPHIC_PIXEL_FMT_YCRCB_420_SP:
            layout = FrameLayout::YUV420_SEMIPLANAR;
            cedarcFormat = VENC_PIXEL_YVU420SP;
            return frame.plane_count >= 2;
        case GRAPHIC_PIXEL_FMT_YCBCR_420_P:
            layout = FrameLayout::YUV420_PLANAR;
            cedarcFormat = VENC_PIXEL_YUV420P;
            return frame.plane_count >= 3;
        case GRAPHIC_PIXEL_FMT_YCRCB_420_P:
            layout = FrameLayout::YUV420_PLANAR;
            cedarcFormat = VENC_PIXEL_YVU420P;
            return frame.plane_count >= 3;
        default:
            LOG_ERROR(LOG_TAG, "Unsupported captured native-buffer format=" + std::to_string(frame.format) +
                " planes=" + std::to_string(frame.plane_count) + "; frame dropped");
            return false;
    }
}

bool CedarcEncoder::CopyFrameToInput(const CapturedFrame &frame, FrameLayout layout, void *inputPtr) const
{
    const auto *input = static_cast<const VencInputBuffer *>(inputPtr);
    if (frame.data == nullptr || input == nullptr || input->pAddrVirY == nullptr || frame.width <= 0 || frame.height <= 0) {
        return false;
    }
    const auto planeData = [&frame](uint32_t index) {
        return frame.data + frame.planes[index].offset;
    };
    const auto rowStride = [&frame](uint32_t index) {
        return frame.planes[index].row_stride;
    };
    if (input->pAddrVirC == nullptr || rowStride(0) < static_cast<uint32_t>(frame.width)) {
        LOG_ERROR(LOG_TAG, "YUV capture buffer has invalid luma plane metadata");
        return false;
    }
    for (int row = 0; row < frame.height; ++row) {
        std::memcpy(input->pAddrVirY + static_cast<size_t>(row) * aligned_width_,
            planeData(0) + static_cast<size_t>(row) * rowStride(0), frame.width);
    }
    if (layout == FrameLayout::YUV420_SEMIPLANAR) {
        if (rowStride(1) < static_cast<uint32_t>(frame.width)) {
            LOG_ERROR(LOG_TAG, "YUV420SP capture buffer has invalid chroma stride");
            return false;
        }
        for (int row = 0; row < frame.height / 2; ++row) {
            std::memcpy(input->pAddrVirC + static_cast<size_t>(row) * aligned_width_,
                planeData(1) + static_cast<size_t>(row) * rowStride(1), frame.width);
        }
        return true;
    }

    const size_t chromaWidth = static_cast<size_t>(frame.width) / 2;
    const size_t chromaHeight = static_cast<size_t>(frame.height) / 2;
    const size_t chromaPlaneSize = static_cast<size_t>(aligned_width_) * aligned_height_ / 4;
    if (rowStride(1) < chromaWidth || rowStride(2) < chromaWidth) {
        LOG_ERROR(LOG_TAG, "YUV420P capture buffer has invalid chroma plane metadata");
        return false;
    }
    for (size_t row = 0; row < chromaHeight; ++row) {
        std::memcpy(input->pAddrVirC + row * (aligned_width_ / 2),
            planeData(1) + row * rowStride(1), chromaWidth);
        std::memcpy(input->pAddrVirC + chromaPlaneSize + row * (aligned_width_ / 2),
            planeData(2) + row * rowStride(2), chromaWidth);
    }
    return true;
}

bool CedarcEncoder::CopyPackedFrame(const CapturedFrame &frame, void *inputPtr) const
{
    const auto *input = static_cast<const VencInputBuffer *>(inputPtr);
    const uint32_t sourceStride = frame.planes[0].row_stride;
    const size_t bytesPerRow = static_cast<size_t>(frame.width) * 4;
    if (frame.data == nullptr || input == nullptr || input->pAddrVirY == nullptr ||
        sourceStride < bytesPerRow) {
        LOG_ERROR(LOG_TAG, "Packed capture metadata is invalid for Cedarc input");
        return false;
    }
    const size_t destinationStride = static_cast<size_t>(aligned_width_) * 4;
    for (int row = 0; row < frame.height; ++row) {
        std::memcpy(input->pAddrVirY + static_cast<size_t>(row) * destinationStride,
            frame.data + static_cast<size_t>(row) * sourceStride, bytesPerRow);
    }
    return true;
}

void CedarcEncoder::DrainBitstream()
{
    while (ValidBitstreamFrameNum(encoder_) > 0) {
        VencOutputBuffer output = {};
        if (GetOneBitstreamFrame(encoder_, &output) != 0) {
            LOG_ERROR(LOG_TAG, "GetOneBitstreamFrame failed");
            return;
        }
        std::vector<uint8_t> packet;
        packet.reserve(static_cast<size_t>(output.nSize0) + output.nSize1 + output.nSize2);
        if (output.pData0 != nullptr && output.nSize0 > 0) {
            packet.insert(packet.end(), output.pData0, output.pData0 + output.nSize0);
        }
        if (output.pData1 != nullptr && output.nSize1 > 0) {
            packet.insert(packet.end(), output.pData1, output.pData1 + output.nSize1);
        }
        if (output.pData2 != nullptr && output.nSize2 > 0) {
            packet.insert(packet.end(), output.pData2, output.pData2 + output.nSize2);
        }
        const bool isKeyframe = (output.nFlag & VENC_BUFFERFLAG_KEYFRAME) != 0;
        ParseParameterSets(packet.data(), packet.size());
        if (!packet.empty() && output_callback_) {
            output_callback_(packet.data(), packet.size(), isKeyframe);
        }
        FreeOneBitStreamFrame(encoder_, &output);
    }
}

void CedarcEncoder::ParseParameterSets(const uint8_t *data, size_t size)
{
    if (data == nullptr || size < 4) {
        return;
    }
    for (size_t pos = 0; pos + 4 <= size;) {
        size_t startLength = 0;
        if (!IsStartCode(data, size, pos, startLength)) {
            ++pos;
            continue;
        }
        const size_t naluStart = pos + startLength;
        if (naluStart >= size) {
            break;
        }
        size_t next = naluStart;
        size_t ignoredLength = 0;
        while (next + 3 < size && !IsStartCode(data, size, next, ignoredLength)) {
            ++next;
        }
        const size_t naluEnd = next + 3 < size ? next : size;
        const uint8_t naluType = data[naluStart] & 0x1f;
        if (naluType == 7) {
            sps_data_.assign(data + pos, data + naluEnd);
        } else if (naluType == 8) {
            pps_data_.assign(data + pos, data + naluEnd);
        }
        pos = naluEnd;
    }
}

void CedarcEncoder::ReleaseEncoder()
{
    if (encoder_ != nullptr) {
        ReleaseAllocInputBuffer(encoder_);
        VideoEncUnInit(encoder_);
        VideoEncDestroy(encoder_);
        encoder_ = nullptr;
    }
    if (mem_ops_ != nullptr) {
        CdcMemClose(mem_ops_);
        mem_ops_ = nullptr;
    }
    aligned_width_ = 0;
    aligned_height_ = 0;
    source_format_ = 0;
    next_pts_us_ = 0;
}

int CedarcEncoder::Align16(int value)
{
    return (value + 15) & ~15;
}

} // namespace OHScrcpy
