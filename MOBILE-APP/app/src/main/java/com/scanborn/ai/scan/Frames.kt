package com.scanborn.ai.scan

/**
 * Camera geometry → the arrays `reconstruction/fast_path/fusion.py` back-projects. No Android or
 * ARCore types here, so all of it runs in JVM unit tests; `ArFrames` feeds it from ARCore.
 *
 * Two frame changes happen in this file and nowhere downstream:
 *  1. Camera basis: ARCore's camera looks down −Z with +Y up; the pinhole model in `backproject()`
 *     is the OpenCV camera, looking down +Z with +Y down — a flip of Y and Z.
 *  2. World basis: ARCore's Y-up world → the scan's Z-up home frame ([HomeFrame]).
 *
 * Matrices are 4×4, row-major.
 */
object Frames {

    /** ARCore camera ← OpenCV camera. */
    val AR_FROM_CV = doubleArrayOf(
        1.0, 0.0, 0.0, 0.0,
        0.0, -1.0, 0.0, 0.0,
        0.0, 0.0, -1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    )

    /**
     * 3×3 pinhole intrinsics for the depth image, row-major.
     *
     * ARCore reports intrinsics against the CPU camera image, which is larger than the depth image;
     * unscaled they would put the principal point off the depth map.
     */
    fun intrinsics(
        focalX: Float, focalY: Float, principalX: Float, principalY: Float,
        imageWidth: Int, imageHeight: Int, depthWidth: Int, depthHeight: Int,
    ): FloatArray {
        val sx = depthWidth.toFloat() / imageWidth
        val sy = depthHeight.toFloat() / imageHeight
        return floatArrayOf(
            focalX * sx, 0f, principalX * sx,
            0f, focalY * sy, principalY * sy,
            0f, 0f, 1f,
        )
    }

    /** A column-major 4×4 (OpenGL, ARCore's `Pose.toMatrix`) as row-major doubles. */
    fun rowMajor(columnMajor: FloatArray): DoubleArray {
        require(columnMajor.size == 16) { "a 4x4 matrix has 16 values" }
        return DoubleArray(16) { i -> columnMajor[(i % 4) * 4 + i / 4].toDouble() }
    }

    /** The OpenCV camera's pose in the home frame (home ← OpenCV camera), as the frame stores it. */
    fun cameraToHome(home: HomeFrame, worldFromCamera: DoubleArray): FloatArray {
        val pose = multiply(home.mapFromWorld, multiply(worldFromCamera, AR_FROM_CV))
        return FloatArray(16) { pose[it].toFloat() }
    }

    fun multiply(a: DoubleArray, b: DoubleArray): DoubleArray {
        val out = DoubleArray(16)
        for (row in 0 until 4) {
            for (col in 0 until 4) {
                var sum = 0.0
                for (k in 0 until 4) sum += a[row * 4 + k] * b[k * 4 + col]
                out[row * 4 + col] = sum
            }
        }
        return out
    }
}
