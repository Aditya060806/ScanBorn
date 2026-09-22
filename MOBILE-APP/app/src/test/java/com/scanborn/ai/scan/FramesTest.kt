package com.scanborn.ai.scan

import org.junit.Assert.assertArrayEquals
import org.junit.Test

class FramesTest {

    @Test
    fun `intrinsics are scaled from the camera image to the depth image`() {
        val k = Frames.intrinsics(500f, 520f, 320f, 240f, 640, 480, 160, 120)
        assertArrayEquals(floatArrayOf(125f, 0f, 80f, 0f, 130f, 60f, 0f, 0f, 1f), k, 1e-5f)
    }

    @Test
    fun `column-major becomes row-major`() {
        val columnMajor = FloatArray(16) { it.toFloat() } // column c holds 4c..4c+3
        val rows = Frames.rowMajor(columnMajor)
        assertArrayEquals(doubleArrayOf(0.0, 4.0, 8.0, 12.0), rows.copyOfRange(0, 4), 0.0)
        assertArrayEquals(doubleArrayOf(3.0, 7.0, 11.0, 15.0), rows.copyOfRange(12, 16), 0.0)
    }

    @Test
    fun `a phone at the start, level and facing forward, looks along plus Y from the origin`() {
        val start = doubleArrayOf(
            1.0, 0.0, 0.0, 0.4,
            0.0, 1.0, 0.0, 1.3,
            0.0, 0.0, 1.0, -0.2,
            0.0, 0.0, 0.0, 1.0,
        )
        val home = HomeFrame.fromStart(start)!!
        val pose = Frames.cameraToHome(home, start)
        // The OpenCV camera looks down its +Z, which must be home +Y; its +Y (image down) is −Z.
        assertArrayEquals(floatArrayOf(0f, 1f, 0f), floatArrayOf(pose[2], pose[6], pose[10]), 1e-6f)
        assertArrayEquals(floatArrayOf(0f, 0f, -1f), floatArrayOf(pose[1], pose[5], pose[9]), 1e-6f)
        assertArrayEquals(floatArrayOf(0f, 0f, 0f), floatArrayOf(pose[3], pose[7], pose[11]), 1e-6f)
    }
}
