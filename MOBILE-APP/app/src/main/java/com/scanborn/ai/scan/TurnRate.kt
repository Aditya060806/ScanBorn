package com.scanborn.ai.scan

import kotlin.math.abs
import kotlin.math.acos
import kotlin.math.min

/**
 * How fast the phone is turning, from two camera orientations and their timestamps. Frames taken
 * while it turns faster than [MAX_DEG_PER_S] are motion-blurred and poorly tracked, so they are
 * skipped (decision 4).
 */
object TurnRate {

    const val MAX_DEG_PER_S = 45.0

    /** The angle in radians between two unit quaternions given as (x, y, z, w). */
    fun angle(a: FloatArray, b: FloatArray): Double {
        val dot = abs(
            a[0].toDouble() * b[0] + a[1].toDouble() * b[1] +
                a[2].toDouble() * b[2] + a[3].toDouble() * b[3]
        )
        return 2.0 * acos(min(1.0, dot)) // q and −q are the same orientation
    }

    /** Degrees per second between two orientations `dtNanos` apart; infinite for a zero interval. */
    fun degreesPerSecond(a: FloatArray, b: FloatArray, dtNanos: Long): Double =
        if (dtNanos <= 0) Double.POSITIVE_INFINITY
        else Math.toDegrees(angle(a, b)) / (dtNanos / 1e9)
}
