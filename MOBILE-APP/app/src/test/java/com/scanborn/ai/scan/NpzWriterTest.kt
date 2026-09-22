package com.scanborn.ai.scan

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayInputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.zip.ZipInputStream

class NpzWriterTest {

    @Test
    fun `the npy preamble is a multiple of 64 bytes ending in a newline`() {
        val npy = NpzWriter.npy("<f4", intArrayOf(120, 160), ByteArray(4))
        val headerLength = ByteBuffer.wrap(npy, 8, 2).order(ByteOrder.LITTLE_ENDIAN).short.toInt()
        assertEquals(0, (10 + headerLength) % 64)
        assertEquals('\n'.code.toByte(), npy[10 + headerLength - 1])
        val header = String(npy, 10, headerLength, Charsets.US_ASCII)
        assertTrue(header.startsWith("{'descr': '<f4', 'fortran_order': False, 'shape': (120, 160), }"))
    }

    @Test
    fun `a one-dimensional shape keeps numpy's trailing comma`() {
        val npy = NpzWriter.npy("|u1", intArrayOf(7), ByteArray(7))
        assertTrue(String(npy, 10, 60, Charsets.US_ASCII).contains("'shape': (7,)"))
    }

    @Test
    fun `a frame holds depth, intrinsics, pose and colour when given`() {
        val npz = NpzWriter.frame(FloatArray(6), 3, 2, FloatArray(9), FloatArray(16), ByteArray(18))
        val names = mutableListOf<String>()
        ZipInputStream(ByteArrayInputStream(npz)).use { zip ->
            while (true) names += (zip.nextEntry ?: break).name
        }
        assertEquals(listOf("depth.npy", "intrinsics.npy", "pose.npy", "color.npy"), names)
        val bare = NpzWriter.frame(FloatArray(6), 3, 2, FloatArray(9), FloatArray(16))
        assertTrue(bare.size < npz.size)
    }
}
