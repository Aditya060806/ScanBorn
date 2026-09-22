package com.scanborn.ai.scan

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.cos
import kotlin.math.sin

class TurnRateTest {

    private fun aboutY(radians: Double) =
        floatArrayOf(0f, sin(radians / 2).toFloat(), 0f, cos(radians / 2).toFloat())

    @Test
    fun `a quarter turn in one second is 90 degrees per second`() {
        assertEquals(90.0, TurnRate.degreesPerSecond(aboutY(0.0), aboutY(Math.PI / 2), 1_000_000_000L), 1e-4)
    }

    @Test
    fun `the same orientation written as q and minus q is no turn`() {
        val q = aboutY(1.0)
        val minus = FloatArray(4) { -q[it] }
        assertEquals(0.0, TurnRate.angle(q, minus), 1e-3)
    }

    @Test
    fun `a slow pan stays under the limit and a whip turn goes over it`() {
        val third = 333_000_000L
        assertTrue(TurnRate.degreesPerSecond(aboutY(0.0), aboutY(Math.toRadians(10.0)), third) < TurnRate.MAX_DEG_PER_S)
        assertTrue(TurnRate.degreesPerSecond(aboutY(0.0), aboutY(Math.toRadians(20.0)), third) > TurnRate.MAX_DEG_PER_S)
        assertTrue(TurnRate.degreesPerSecond(aboutY(0.0), aboutY(0.1), 0L).isInfinite())
    }
}
