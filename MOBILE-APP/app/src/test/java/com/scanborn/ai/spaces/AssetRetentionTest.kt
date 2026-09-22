package com.scanborn.ai.spaces

import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.nio.file.Files

class AssetRetentionTest {
    @Test fun `prune retains current assets and unrelated files`() {
        val root = Files.createTempDirectory("retention").toFile()
        try {
            val keep = "11111111-1111-1111-1111-111111111111.bin"
            val old = "22222222-2222-2222-2222-222222222222.obj"
            File(root, keep).writeBytes(ByteArray(16))
            File(root, old).writeBytes(ByteArray(16))
            File(root, "last-scene.json").writeText("manifest")
            File(root, "unrelated.txt").writeText("keep")
            AssetRetention.prune(root, setOf(keep))
            assertTrue(File(root, keep).exists())
            assertFalse(File(root, old).exists())
            assertTrue(File(root, "last-scene.json").exists())
            assertTrue(File(root, "unrelated.txt").exists())
            try { AssetRetention.requireSpace(root, 17, 32); fail("Exceeded budget") }
            catch (_: IllegalStateException) { }
            AssetRetention.requireSpace(root, 16, 32)
        } finally { root.deleteRecursively() }
    }
}
