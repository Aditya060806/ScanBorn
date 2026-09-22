package com.scanborn.ai.scan

import org.junit.Assert.assertEquals
import org.junit.Test
import java.nio.ByteBuffer

class DepthFilterTest {

    /** A 16-bit little-endian depth plane with `pad` extra bytes at the end of each row. */
    private fun depthPlane(width: Int, millimetres: List<Int>, pad: Int = 0): Pair<ByteBuffer, Int> {
        val rowStride = width * 2 + pad
        val rows = millimetres.size / width
        val buffer = ByteBuffer.allocate(rowStride * rows)
        millimetres.forEachIndexed { i, mm ->
            val at = (i / width) * rowStride + (i % width) * 2
            buffer.put(at, (mm and 0xFF).toByte())
            buffer.put(at + 1, ((mm shr 8) and 0xFF).toByte())
        }
        return buffer to rowStride
    }

    private fun confidencePlane(width: Int, values: List<Int>, pad: Int = 0): Pair<ByteBuffer, Int> {
        val rowStride = width + pad
        val buffer = ByteBuffer.allocate(rowStride * (values.size / width))
        values.forEachIndexed { i, c -> buffer.put((i / width) * rowStride + i % width, c.toByte()) }
        return buffer to rowStride
    }

    @Test
    fun `all sixteen bits are depth - nothing past 8191 mm is cut`() {
        val (depth, depthStride) = depthPlane(2, listOf(9000, 65535))
        val (confidence, confidenceStride) = confidencePlane(2, listOf(255, 255))
        val result = DepthFilter.filter(depth, depthStride, 2, confidence, confidenceStride, 1,
            width = 2, height = 1, maxRangeM = 70f)
        assertEquals(9.0f, result.metres[0], 1e-6f)
        assertEquals(65.535f, result.metres[1], 1e-4f)
    }

    @Test
    fun `no estimate, low confidence and out of range are dropped`() {
        val (depth, depthStride) = depthPlane(3, listOf(0, 1500, 1500, 200, 4500, 3999), pad = 4)
        val (confidence, confidenceStride) = confidencePlane(3, listOf(255, 127, 128, 255, 255, 200), pad = 1)
        val result = DepthFilter.filter(depth, depthStride, 2, confidence, confidenceStride, 1,
            width = 3, height = 2)
        assertEquals(listOf(0f, 0f, 1.5f, 0f, 0f, 3.999f), result.metres.toList())
        assertEquals(2, result.kept)
        assertEquals(2f / 6f, result.keptShare, 1e-6f)
    }

    @Test
    fun `row padding is skipped, so the second row reads its own pixels`() {
        val (depth, depthStride) = depthPlane(2, listOf(1000, 1100, 2000, 2100), pad = 6)
        val (confidence, confidenceStride) = confidencePlane(2, listOf(255, 255, 255, 255), pad = 3)
        val result = DepthFilter.filter(depth, depthStride, 2, confidence, confidenceStride, 1,
            width = 2, height = 2)
        assertEquals(listOf(1.0f, 1.1f, 2.0f, 2.1f), result.metres.toList())
    }
}
