package com.scanborn.ai.spaces

import java.io.File
import java.io.IOException
import java.io.InputStream

internal class AssetTooLarge : IOException("Asset exceeds the mobile preview limit")

/** Stream to a new cache file without allocating the response body in memory. */
internal fun saveBoundedAsset(input: InputStream, target: File, limit: Long,
                              declaredLength: Long = -1, stride: Int = 1) {
    require(limit > 0 && stride > 0)
    require(!target.exists()) { "Cache assets must have unique names" }
    val temporary = File(target.parentFile, target.name + ".tmp")
    try {
        if (declaredLength > limit) throw AssetTooLarge()
        var total = 0L
        temporary.outputStream().use { output ->
            val buffer = ByteArray(8192)
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                total += count
                if (total > limit) throw AssetTooLarge()
                output.write(buffer, 0, count)
            }
            if (total == 0L || total % stride != 0L ||
                (declaredLength >= 0 && total != declaredLength)) throw IOException("Incomplete or invalid asset")
            output.fd.sync()
        }
        if (!temporary.renameTo(target)) throw IOException("Could not publish cached asset")
    } finally { temporary.delete() }
}
