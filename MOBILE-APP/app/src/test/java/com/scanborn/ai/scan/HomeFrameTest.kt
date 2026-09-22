package com.scanborn.ai.scan

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test
import kotlin.math.cos
import kotlin.math.sin

class HomeFrameTest {

    /** ARCore world ← camera for a phone at (x, y, z), turned `yaw` about world +Y and pitched
     *  `pitch` about its own X (negative looks down). Row-major. */
    private fun pose(x: Double, y: Double, z: Double, yaw: Double = 0.0, pitch: Double = 0.0): DoubleArray {
        val cy = cos(yaw); val sy = sin(yaw)
        val cp = cos(pitch); val sp = sin(pitch)
        // R = Ry(yaw) · Rx(pitch)
        return doubleArrayOf(
            cy, sy * sp, sy * cp, x,
            0.0, cp, -sp, y,
            -sy, cy * sp, cy * cp, z,
            0.0, 0.0, 0.0, 1.0,
        )
    }

    /** Where the camera looks, in ARCore world: its −Z axis. */
    private fun forward(p: DoubleArray) = doubleArrayOf(-p[2], -p[6], -p[10])

    @Test
    fun `at ARCore's own start the home frame is the old fixed mapping`() {
        val home = HomeFrame.fromStart(pose(0.0, 0.0, 0.0))!!
        // (x, y, z) ARCore → (x, −z, y) home, exactly what the capture app's MAP_FROM_AR did.
        assertArrayEquals(doubleArrayOf(1.0, -3.0, 2.0), home.toMap(1.0, 2.0, 3.0), 1e-12)
    }

    @Test
    fun `the phone's position is the origin and its forward is plus Y, whatever ARCore's yaw`() {
        for (yaw in listOf(0.0, 0.7, 2.0, -2.5)) {
            val start = pose(1.5, 1.2, -0.8, yaw = yaw)
            val home = HomeFrame.fromStart(start)!!
            assertArrayEquals(doubleArrayOf(0.0, 0.0, 0.0), home.toMap(1.5, 1.2, -0.8), 1e-12)
            val f = forward(start)
            assertArrayEquals(doubleArrayOf(0.0, 2.0, 0.0),
                home.toMap(1.5 + 2 * f[0], 1.2 + 2 * f[1], -0.8 + 2 * f[2]), 1e-9)
            // Up in ARCore is up in the home frame.
            assertArrayEquals(doubleArrayOf(0.0, 0.0, 1.0), home.toMap(1.5, 2.2, -0.8), 1e-12)
        }
    }

    @Test
    fun `a phone tilted down still defines forward by its horizontal direction`() {
        val start = pose(0.0, 1.3, 0.0, yaw = 0.4, pitch = -0.5) // 29 degrees down
        val home = HomeFrame.fromStart(start)
        assertNotNull(home)
        val f = forward(start)
        val ahead = home!!.toMap(3 * f[0], 1.3 + 3 * f[1], 3 * f[2])
        assertEquals(0.0, ahead[0], 1e-9)       // straight ahead, nothing sideways
        assertEquals(3 * cos(0.5), ahead[1], 1e-9)
        assertEquals(-3 * sin(0.5), ahead[2], 1e-9)
    }

    @Test
    fun `start is refused when the phone points too steeply up or down`() {
        assertNull(HomeFrame.fromStart(pose(0.0, 1.3, 0.0, pitch = -1.2))) // 69 degrees down
        assertNull(HomeFrame.fromStart(pose(0.0, 1.3, 0.0, pitch = 1.3)))
        assertNotNull(HomeFrame.fromStart(pose(0.0, 1.3, 0.0, pitch = -1.0))) // 57 degrees
    }

    @Test
    fun `the home pose faces plus Y from the origin`() {
        assertArrayEquals(doubleArrayOf(0.0, 0.0, Math.PI / 2), HomeFrame.HOME_POSE, 0.0)
    }
}
