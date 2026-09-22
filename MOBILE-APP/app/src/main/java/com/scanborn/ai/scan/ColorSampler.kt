package com.scanborn.ai.scan

import java.nio.ByteBuffer

/**
 * The camera image's colour at each depth pixel (decision 6): nearest sample of the YUV_420_888 CPU
 * image at the position that corresponds to the depth pixel, converted with BT.601 full-range
 * coefficients. ARCore's depth image covers the same field of view as the CPU image, which is the
 * same proportional mapping [Frames.intrinsics] uses for the intrinsics.
 */
object ColorSampler {

    class Plane(val buffer: ByteBuffer, val rowStride: Int, val pixelStride: Int)

    /** RGB bytes, row-major, `width` × `height` × 3. */
    fun sample(
        y: Plane, u: Plane, v: Plane, imageWidth: Int, imageHeight: Int, width: Int, height: Int,
    ): ByteArray {
        val rgb = ByteArray(width * height * 3)
        for (row in 0 until height) {
            val iy = ((row + 0.5) * imageHeight / height).toInt().coerceIn(0, imageHeight - 1)
            for (col in 0 until width) {
                val ix = ((col + 0.5) * imageWidth / width).toInt().coerceIn(0, imageWidth - 1)
                val luma = y.buffer.get(iy * y.rowStride + ix * y.pixelStride).toInt() and 0xFF
                val cb = (u.buffer.get((iy / 2) * u.rowStride + (ix / 2) * u.pixelStride)
                    .toInt() and 0xFF) - 128
                val cr = (v.buffer.get((iy / 2) * v.rowStride + (ix / 2) * v.pixelStride)
                    .toInt() and 0xFF) - 128
                val at = (row * width + col) * 3
                rgb[at] = clamp(luma + 1.402 * cr)
                rgb[at + 1] = clamp(luma - 0.344136 * cb - 0.714136 * cr)
                rgb[at + 2] = clamp(luma + 1.772 * cb)
            }
        }
        return rgb
    }

    private fun clamp(value: Double): Byte = value.toInt().coerceIn(0, 255).toByte()
}
