package com.scanborn.ai.spaces

import org.junit.Assert.*
import org.junit.Test
import java.nio.file.Files

class StageJournalTest {
    @Test fun `restart preserves payload identity and settlement publishes checkpoint atomically`() {
        val root = Files.createTempDirectory("stage-journal").toFile()
        try {
            val journal = StageJournal(root)
            val pending = journal.begin("http://a/", "/train", "payload", "before")
            val restored = StageJournal(root)
            assertEquals(pending, restored.pending("http://a"))
            assertEquals(pending, restored.begin("http://a", "/train", "payload", "changed UI"))
            assertNull(restored.pending("http://b"))
            assertThrows(IllegalStateException::class.java) { restored.begin("http://a", "/train", "different", "x") }
            restored.settle("http://a", "old-request", "bad")
            assertEquals("before", restored.checkpoint("http://a"))
            restored.settle("http://a", pending.requestId, "after")
            assertNull(StageJournal(root).pending("http://a"))
            assertEquals("after", StageJournal(root).checkpoint("http://a"))
        } finally { root.deleteRecursively() }
    }
    @Test fun `corrupt and unwritable journals refuse admission`() {
        val root = Files.createTempDirectory("stage-corrupt").toFile()
        try {
            val journal = StageJournal(root)
            journal.begin("http://a", "/optimize", "payload", "before")
            root.listFiles()!!.single().writeText("version=1\nserver=http://a")
            assertThrows(IllegalStateException::class.java) { journal.begin("http://a", "/train", "x", "x") }
            assertThrows(IllegalArgumentException::class.java) { journal.begin("http://other", "/deploy", "x", "x") }
            val notDirectory = java.io.File(root, "file").apply { writeText("occupied") }
            assertThrows(IllegalStateException::class.java) { StageJournal(notDirectory).begin("http://a", "/train", "x", "x") }
        } finally { root.deleteRecursively() }
    }
}
