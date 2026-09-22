package com.scanborn.ai.scan

import kotlin.math.sqrt

/**
 * The scan's home frame, fixed when the operator taps Start standing over the home marker and
 * pointing along its arrow (docs/superpowers/plans/2026-09-15-phase-5-capture.md, decision 5).
 *
 * Origin at the phone; +Z up (ARCore's world +Y is gravity-up); +Y the phone's forward direction
 * projected onto the floor; +X = Y × Z, so the frame is right-handed. The scan's `home_pose` is
 * therefore always `[0, 0, π/2]`: at the origin, facing +Y.
 *
 * Fixing the frame at the tap rather than at ARCore's session start makes registration independent
 * of when tracking began. Matrices are 4×4, row-major.
 */
class HomeFrame private constructor(
    /** Home frame ← ARCore world. */
    val mapFromWorld: DoubleArray,
) {

    /** A point in ARCore world coordinates, in home-frame coordinates. */
    fun toMap(x: Double, y: Double, z: Double): DoubleArray = DoubleArray(3) { row ->
        mapFromWorld[row * 4] * x + mapFromWorld[row * 4 + 1] * y +
            mapFromWorld[row * 4 + 2] * z + mapFromWorld[row * 4 + 3]
    }

    companion object {
        /** Start needs the phone's view direction to be at least this horizontal (cos of 60°). */
        const val MIN_HORIZONTAL = 0.5

        /** The home pose every registered scan reports: x, y, heading (facing +Y). */
        val HOME_POSE = doubleArrayOf(0.0, 0.0, Math.PI / 2)

        /**
         * The home frame from the ARCore camera pose at Start (ARCore world ← camera, row-major;
         * the ARCore camera looks down its −Z). Null when the phone points too steeply up or down
         * for its forward direction to mean anything.
         */
        fun fromStart(worldFromCamera: DoubleArray): HomeFrame? {
            require(worldFromCamera.size == 16) { "a 4x4 pose has 16 values" }
            // The camera's −Z axis in world coordinates: the third column, negated.
            val fx = -worldFromCamera[2]
            val fy = -worldFromCamera[6]
            val fz = -worldFromCamera[10]
            val length = sqrt(fx * fx + fy * fy + fz * fz)
            val horizontal = sqrt(fx * fx + fz * fz)
            if (length < 1e-9 || horizontal / length < MIN_HORIZONTAL) return null

            val forward = doubleArrayOf(fx / horizontal, 0.0, fz / horizontal) // home +Y
            val up = doubleArrayOf(0.0, 1.0, 0.0)                              // home +Z
            val right = doubleArrayOf(-forward[2], 0.0, forward[0])            // forward × up, home +X
            val origin = doubleArrayOf(worldFromCamera[3], worldFromCamera[7], worldFromCamera[11])

            val matrix = DoubleArray(16)
            for ((row, axis) in listOf(right, forward, up).withIndex()) {
                matrix[row * 4] = axis[0]
                matrix[row * 4 + 1] = axis[1]
                matrix[row * 4 + 2] = axis[2]
                matrix[row * 4 + 3] = -(axis[0] * origin[0] + axis[1] * origin[1] + axis[2] * origin[2])
            }
            matrix[15] = 1.0
            return HomeFrame(matrix)
        }
    }
}
