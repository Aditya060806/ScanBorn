package com.scanborn.ai.scan

import java.nio.ByteBuffer

/**
 * ARCore raw depth → metres, keeping only pixels ARCore is confident about and within range
 * (docs/superpowers/plans/2026-09-15-phase-5-capture.md, decision 3).
 *
 * Formats, from the ARCore `Frame` reference (checked 2026-09-15):
 * - `acquireRawDepthImage16Bits()`: 16-bit little-endian millimetres, all 16 bits are depth (range
 *   0–65535 mm), 0 where there is no estimate. The old capture app masked `& 0x1FFF`, the layout of
 *   the deprecated 13-bit image, which cut everything past 8.191 m.
 * - `acquireRawDepthConfidenceImage()`: 8 bits, 0 (lowest) to 255 (highest); ARCore suggests
 *   dropping pixels below 128.
 */
object DepthFilter {

    const val MIN_CONFIDENCE = 128
    const val MIN_RANGE_M = 0.25f
    const val MAX_RANGE_M = 4.0f

    /** Kept depth in metres (0 where dropped), row-major, and how many pixels were kept. */
    class Result(val metres: FloatArray, val kept: Int, val total: Int) {
        val keptShare: Float get() = if (total == 0) 0f else kept.toFloat() / total
    }

    fun filter(
        depth: ByteBuffer, depthRowStride: Int, depthPixelStride: Int,
        confidence: ByteBuffer, confidenceRowStride: Int, confidencePixelStride: Int,
        width: Int, height: Int,
        minConfidence: Int = MIN_CONFIDENCE,
        minRangeM: Float = MIN_RANGE_M,
        maxRangeM: Float = MAX_RANGE_M,
    ): Result {
        val metres = FloatArray(width * height)
        var kept = 0
        for (row in 0 until height) {
            for (col in 0 until width) {
                val at = row * depthRowStride + col * depthPixelStride
                // Absolute gets never touch the buffer's position or byte order.
                val millimetres = (depth.get(at).toInt() and 0xFF) or
                    ((depth.get(at + 1).toInt() and 0xFF) shl 8)
                if (millimetres == 0) continue
                val sure = confidence.get(row * confidenceRowStride + col * confidencePixelStride)
                    .toInt() and 0xFF
                val value = millimetres / 1000f
                if (sure >= minConfidence && value >= minRangeM && value <= maxRangeM) {
                    metres[row * width + col] = value
                    kept++
                }
            }
        }
        return Result(metres, kept, width * height)
    }
}
