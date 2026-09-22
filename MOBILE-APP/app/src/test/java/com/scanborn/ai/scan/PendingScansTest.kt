package com.scanborn.ai.scan

import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.nio.file.Files

class PendingScansTest {
    private val codec = object : PendingScans.Codec {
        override fun encode(manifest: PendingScans.Manifest) =
            listOf(manifest.id, manifest.url, manifest.meta).joinToString("\n")
        override fun decode(text: String): PendingScans.Manifest {
            val parts = text.split('\n')
            require(parts.size == 3)
            return PendingScans.Manifest(parts[0], parts[1], parts[2])
        }
    }

    @Test fun `restart preserves capture identity and server association`() {
        val root = Files.createTempDirectory("pending-scans").toFile()
        try {
            val first = PendingScans(root, codec)
            val directory = first.create("stable-id", "http://server-a", "META")
            File(directory, "frame_0.npz").writeBytes(byteArrayOf(1))
            val restored = PendingScans(root, codec)
            assertNull(restored.saved("http://server-b"))
            assertEquals("stable-id", restored.saved("http://server-a")!!.id)
            assertEquals(directory, restored.create("stable-id", "http://server-a", "META"))
            for ((server, meta) in listOf("http://server-b" to "META", "http://server-a" to "OTHER")) {
                try { restored.create("stable-id", server, meta); fail("Identity overwritten") }
                catch (_: IllegalArgumentException) { }
            }
            assertEquals("META", restored.saved("http://server-a")!!.meta)
            assertTrue(File(directory, "frame_0.npz").exists())
        } finally { root.deleteRecursively() }
    }

    @Test fun `damaged records are surfaced and completed cleanup keeps pending scans`() {
        val root = Files.createTempDirectory("pending-scans").toFile()
        try {
            val store = PendingScans(root, codec)
            val done = store.create("completed", "server", "META")
            File(done, UploadQueue.COMPLETE).writeText("1")
            val damaged = File(root, "damaged").apply { mkdir() }
            File(damaged, PendingScans.MANIFEST).writeText("broken")
            assertEquals(1, store.purgeCompleted())
            assertTrue(store.saved("server")!!.damaged)
            assertFalse(store.discard("../elsewhere"))
            assertTrue(damaged.exists())
            assertTrue(store.discard("damaged"))
            assertNull(store.saved("server"))
        } finally { root.deleteRecursively() }
    }
}
