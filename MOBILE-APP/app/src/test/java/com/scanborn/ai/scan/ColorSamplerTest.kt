package com.scanborn.ai.scan

import org.junit.Assert.assertEquals
import org.junit.Test
import java.nio.ByteBuffer

class ColorSamplerTest {

    private fun plane(width: Int, height: Int, value: (Int, Int) -> Int) =
        ColorSampler.Plane(ByteBuffer.allocate(width * height).apply {
            for (row in 0 until height) for (col in 0 until width) put(row * width + col, value(row, col).toByte())
        }, rowStride = width, pixelStride = 1)

    @Test
    fun `neutral chroma gives grey at the luma value`() {
        val rgb = ColorSampler.sample(plane(8, 8) { _, _ -> 128 }, plane(4, 4) { _, _ -> 128 },
            plane(4, 4) { _, _ -> 128 }, imageWidth = 8, imageHeight = 8, width = 2, height = 2)
        assertEquals(List(12) { 128 }, rgb.map { it.toInt() and 0xFF })
    }

    @Test
    fun `each depth pixel samples the matching part of the camera image`() {
        // Left half of the camera image dark, right half bright; a 2x1 depth image sees one of each.
        val rgb = ColorSampler.sample(plane(8, 4) { _, col -> if (col < 4) 20 else 230 },
            plane(4, 2) { _, _ -> 128 }, plane(4, 2) { _, _ -> 128 },
            imageWidth = 8, imageHeight = 4, width = 2, height = 1)
        assertEquals(listOf(20, 20, 20, 230, 230, 230), rgb.map { it.toInt() and 0xFF })
    }

    @Test
    fun `strong red chroma pushes red up and blue down`() {
        val rgb = ColorSampler.sample(plane(2, 2) { _, _ -> 100 }, plane(1, 1) { _, _ -> 128 },
            plane(1, 1) { _, _ -> 228 }, imageWidth = 2, imageHeight = 2, width = 1, height = 1)
        val (r, g, b) = rgb.map { it.toInt() and 0xFF }
        assertEquals(240, r)  // 100 + 1.402 * 100
        assertEquals(28, g)   // 100 - 0.714 * 100
        assertEquals(100, b)
    }
}
