package com.scanborn.ai.spaces

import org.junit.Assert.*
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.File
import java.io.IOException
import java.nio.file.Files

class BoundedAssetTest {
    @Test fun `bounded downloads publish only complete valid assets`() {
        val dir = Files.createTempDirectory("viewer-assets").toFile()
        try {
            val target = File(dir, "points.bin")
            saveBoundedAsset(ByteArrayInputStream(ByteArray(32)), target, 32, 32, 16)
            assertEquals(32L, target.length())
            for ((bytes, declared) in listOf(33 to -1L, 1 to 100L, 15 to -1L, 16 to 32L)) {
                val failed = File(dir, "failed.bin")
                try {
                    saveBoundedAsset(ByteArrayInputStream(ByteArray(bytes)), failed, 32, declared, 16)
                    fail("Invalid asset accepted")
                } catch (_: IOException) { }
                assertFalse(failed.exists())
                assertFalse(File(dir, "failed.bin.tmp").exists())
                assertEquals(32L, target.length())
            }
        } finally { dir.deleteRecursively() }
    }
}
