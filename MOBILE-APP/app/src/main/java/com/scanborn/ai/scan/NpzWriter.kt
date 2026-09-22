package com.scanborn.ai.scan

import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.zip.CRC32
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/**
 * Writes the `.npz` files `reconstruction/reconstruct.py:load_frames` reads: a zip of `.npy` members
 * named `depth`, `intrinsics`, `pose` and, when colour was sampled, `color`.
 *
 * `.npy` v1.0 = magic + version + uint16 little-endian header length + a Python dict literal, the
 * whole preamble padded with spaces to a multiple of 64 bytes and ended by a newline, then the raw
 * little-endian array data in C order.
 */
object NpzWriter {

    private val MAGIC = byteArrayOf(
        0x93.toByte(), 'N'.code.toByte(), 'U'.code.toByte(), 'M'.code.toByte(),
        'P'.code.toByte(), 'Y'.code.toByte(),
    )

    fun npy(dtype: String, shape: IntArray, data: ByteArray): ByteArray {
        // numpy needs the trailing comma on a 1-tuple; `(3, 3)` and `(480,)` both parse.
        val dims = shape.joinToString(", ") + if (shape.size == 1) "," else ""
        val dict = "{'descr': '$dtype', 'fortran_order': False, 'shape': ($dims), }"
        val preamble = MAGIC.size + 2 + 2 + dict.length + 1
        val padding = (64 - preamble % 64) % 64
        val header = dict + " ".repeat(padding) + "\n"

        val out = ByteArrayOutputStream()
        out.write(MAGIC)
        out.write(1) // major version
        out.write(0) // minor version
        out.write(ByteBuffer.allocate(2).order(ByteOrder.LITTLE_ENDIAN)
            .putShort(header.length.toShort()).array())
        out.write(header.toByteArray(Charsets.US_ASCII))
        out.write(data)
        return out.toByteArray()
    }

    fun floats(values: FloatArray): ByteArray {
        val buffer = ByteBuffer.allocate(values.size * 4).order(ByteOrder.LITTLE_ENDIAN)
        values.forEach { buffer.putFloat(it) }
        return buffer.array()
    }

    /** The named arrays zipped into one `.npz`, stored rather than deflated: depth is noisy and
     *  the phone's battery is worth more than the bytes. */
    fun npz(members: Map<String, ByteArray>): ByteArray {
        val out = ByteArrayOutputStream()
        ZipOutputStream(out).use { zip ->
            zip.setMethod(ZipOutputStream.STORED)
            members.forEach { (name, bytes) ->
                zip.putNextEntry(ZipEntry("$name.npy").apply {
                    method = ZipEntry.STORED
                    size = bytes.size.toLong()
                    compressedSize = bytes.size.toLong()
                    crc = CRC32().apply { update(bytes) }.value
                })
                zip.write(bytes)
                zip.closeEntry()
            }
        }
        return out.toByteArray()
    }

    /** One frame in the shape fusion expects; `color` is H×W×3 RGB bytes, or null. */
    fun frame(
        depth: FloatArray, width: Int, height: Int,
        intrinsics: FloatArray, pose: FloatArray, color: ByteArray? = null,
    ): ByteArray {
        val members = linkedMapOf(
            "depth" to npy("<f4", intArrayOf(height, width), floats(depth)),
            "intrinsics" to npy("<f4", intArrayOf(3, 3), floats(intrinsics)),
            "pose" to npy("<f4", intArrayOf(4, 4), floats(pose)),
        )
        if (color != null) {
            require(color.size == width * height * 3) { "color must be H x W x 3 bytes" }
            members["color"] = npy("|u1", intArrayOf(height, width, 3), color)
        }
        return npz(members)
    }
}
