package com.scanborn.ai.scan

import com.google.ar.core.Camera

/** ARCore's camera → the plain arrays [Frames], [HomeFrame] and [TurnRate] work on. */
object ArFrames {

    /** ARCore world ← the physical camera, row-major. */
    fun worldFromCamera(camera: Camera): DoubleArray {
        val columnMajor = FloatArray(16)
        camera.pose.toMatrix(columnMajor, 0)
        return Frames.rowMajor(columnMajor)
    }

    /** The camera's orientation as a unit quaternion (x, y, z, w). */
    fun quaternion(camera: Camera): FloatArray = camera.pose.let {
        floatArrayOf(it.qx(), it.qy(), it.qz(), it.qw())
    }

    /** Intrinsics for a depth image of `depthWidth` × `depthHeight`. */
    fun intrinsics(camera: Camera, depthWidth: Int, depthHeight: Int): FloatArray {
        val k = camera.imageIntrinsics
        val focal = k.focalLength
        val principal = k.principalPoint
        val size = k.imageDimensions
        return Frames.intrinsics(focal[0], focal[1], principal[0], principal[1], size[0], size[1],
            depthWidth, depthHeight)
    }
}
