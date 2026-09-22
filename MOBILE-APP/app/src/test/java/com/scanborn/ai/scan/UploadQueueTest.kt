package com.scanborn.ai.scan

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class UploadQueueTest {

    @Test
    fun `closing while finish waits for an upload does not complete the scan`() {
        val entered = CountDownLatch(1)
        val release = CountDownLatch(1)
        val finished = CountDownLatch(1)
        var completed = false
        var outcome: UploadQueue.Outcome? = null
        val sink = object : ChunkSink {
            override val scanId = "stable-scan"
            override fun upload(index: Int, npz: ByteArray, meta: String?) {
                entered.countDown()
                check(release.await(5, TimeUnit.SECONDS))
            }
            override fun complete(meta: String): Int { completed = true; return 1 }
        }
        val queue = UploadQueue(sink, "META", sleep = {})
        try {
            queue.submit(0, byteArrayOf(1))
            assertTrue(entered.await(5, TimeUnit.SECONDS))
            queue.finish(finalMeta = { "META" }) { outcome = it; finished.countDown() }
            queue.shutdown()
            release.countDown()
            assertTrue(finished.await(5, TimeUnit.SECONDS))
            assertEquals(false, completed)
            assertTrue(outcome.toString(), outcome is UploadQueue.Outcome.Failed)
            assertEquals(0, queue.snapshot().uploaded)
            assertEquals(1, queue.snapshot().pending)
        } finally { release.countDown(); queue.shutdown() }
    }

    @Test
    fun `discard during upload cannot write receipts or emit late counters`() {
        val dir = java.nio.file.Files.createTempDirectory("scanborn-discard").toFile()
        val entered = CountDownLatch(1)
        val release = CountDownLatch(1)
        val finished = CountDownLatch(1)
        val changes = java.util.concurrent.atomic.AtomicInteger()
        val sink = object : ChunkSink {
            override val scanId = "stable-scan"
            override fun upload(index: Int, npz: ByteArray, meta: String?) {
                entered.countDown()
                check(release.await(5, TimeUnit.SECONDS))
            }
            override fun complete(meta: String): Int = error("must not complete")
        }
        val queue = UploadQueue(sink, "META", directory = dir, onChange = { changes.incrementAndGet() })
        try {
            queue.submit(0, byteArrayOf(1))
            assertTrue(entered.await(5, TimeUnit.SECONDS))
            queue.finish(finalMeta = { "META" }) { finished.countDown() }
            queue.shutdown()
            val before = changes.get()
            assertTrue(dir.deleteRecursively())
            release.countDown()
            assertTrue(finished.await(5, TimeUnit.SECONDS))
            assertEquals(before, changes.get())
            assertEquals(false, dir.exists())
            assertEquals(0, queue.snapshot().uploaded)
        } finally { release.countDown(); queue.shutdown(); dir.deleteRecursively() }
    }

    @Test
    fun `settled frame identities remain unique in memory and after restart`() {
        val dir = java.nio.file.Files.createTempDirectory("scanborn-receipts").toFile()
        val settled = CountDownLatch(1)
        val sink = FakeSink()
        val first = UploadQueue(sink, "META", directory = dir, onChange = {
            if (it.uploaded == 1) settled.countDown()
        })
        try {
            assertTrue(first.submit(0, byteArrayOf(1)))
            assertTrue(settled.await(5, TimeUnit.SECONDS))
            assertEquals(false, first.submit(0, byteArrayOf(9)))
            assertEquals(1, first.snapshot().accepted)
            first.shutdown()
            val recovered = UploadQueue(sink, "META", directory = dir)
            try {
                assertEquals(false, recovered.submit(0, byteArrayOf(9)))
                assertEquals(1, recovered.snapshot().accepted)
                assertEquals(0, recovered.snapshot().dropped)
            } finally { recovered.shutdown() }
        } finally { first.shutdown(); dir.deleteRecursively() }
    }

    @Test
    fun `rejected frame identities cannot be submitted again`() {
        val settled = CountDownLatch(1)
        val queue = UploadQueue(FakeSink(refused = setOf(7)), "META", onChange = {
            if (it.rejected == 1) settled.countDown()
        })
        try {
            assertTrue(queue.submit(7, byteArrayOf(1)))
            assertTrue(settled.await(5, TimeUnit.SECONDS))
            assertEquals(false, queue.submit(7, byteArrayOf(2)))
            assertEquals(1, queue.snapshot().accepted)
        } finally { queue.shutdown() }
    }

    /** Chunks refused or unreachable as scripted; records what was sent with which meta. */
    private class FakeSink(
        val unreachableTimes: MutableMap<Int, Int> = mutableMapOf(),
        val refused: Set<Int> = emptySet(),
        val alwaysUnreachable: Boolean = false,
    ) : ChunkSink {
        override var scanId: String? = null
        val sent = mutableListOf<Pair<Int, String?>>()
        var completedMeta: String? = null

        override fun upload(index: Int, npz: ByteArray, meta: String?) {
            if (alwaysUnreachable) throw IOException("no route to host")
            val left = unreachableTimes[index] ?: 0
            if (left > 0) {
                unreachableTimes[index] = left - 1
                throw IOException("connection reset")
            }
            if (index in refused) throw ChunkRejected("chunk $index refused: 400")
            sent += index to meta
            if (scanId == null) scanId = "scan-1"
        }

        override fun complete(meta: String): Int {
            completedMeta = meta
            return sent.size
        }
    }

    private fun finish(queue: UploadQueue, allowPartial: Boolean = false): UploadQueue.Outcome {
        val latch = CountDownLatch(1)
        var outcome: UploadQueue.Outcome? = null
        queue.finish(allowPartial = allowPartial, finalMeta = { "{\"uploaded\": ${it.uploaded}}" }) {
            outcome = it
            latch.countDown()
        }
        assertTrue(latch.await(10, TimeUnit.SECONDS))
        return outcome!!
    }

    @Test
    fun `a chunk that failed to send goes again before the next, and nothing is lost`() {
        val sink = FakeSink(unreachableTimes = mutableMapOf(0 to 1))
        val queue = UploadQueue(sink, "{\"home_pose\": [0, 0, 1.5708]}", sleep = {})
        queue.submit(0, ByteArray(1))
        queue.submit(1, ByteArray(1))
        val outcome = finish(queue) as UploadQueue.Outcome.Done
        assertEquals(listOf(0, 1), sink.sent.map { it.first })
        assertEquals(2, outcome.frames)
        assertEquals(0, outcome.counts.pending)
        assertEquals("{\"uploaded\": 2}", sink.completedMeta)
    }

    @Test
    fun `the chunk that creates the scan carries the meta, whichever index it is`() {
        val sink = FakeSink(refused = setOf(0))
        val queue = UploadQueue(sink, "META", sleep = {})
        queue.submit(0, ByteArray(1))
        queue.submit(1, ByteArray(1))
        queue.submit(2, ByteArray(1))
        assertTrue(finish(queue) is UploadQueue.Outcome.Failed)
        assertNull(sink.completedMeta)
        val outcome = finish(queue, allowPartial = true) as UploadQueue.Outcome.Done
        assertEquals(listOf(1 to "META", 2 to null), sink.sent)
        assertEquals(1, outcome.counts.rejected)
    }

    @Test
    fun `with no network the scan fails with the reason, and frames past the cap are counted`() {
        val sink = FakeSink(alwaysUnreachable = true)
        val queue = UploadQueue(sink, "META", maxPending = 2, sleep = {})
        repeat(5) { queue.submit(it, ByteArray(1)) }
        val outcome = finish(queue) as UploadQueue.Outcome.Failed
        assertTrue(outcome.reason, outcome.reason.contains("no route to host"))
        assertTrue(outcome.reason.contains("Settings"))
        assertEquals(2, outcome.counts.pending)
        assertEquals(3, outcome.counts.dropped)
        assertNull(sink.completedMeta)
    }

    @Test
    fun `disk queue survives close and refuses submissions after finish`() {
        val dir = java.nio.file.Files.createTempDirectory("scanborn-queue").toFile()
        try {
            val first = UploadQueue(FakeSink(alwaysUnreachable = true), "META", directory = dir, sleep = {})
            first.submit(0, byteArrayOf(1, 2, 3))
            val failed = finish(first) as UploadQueue.Outcome.Failed
            assertEquals(1, failed.counts.pending)
            assertEquals(false, first.submit(1, byteArrayOf(4)))
            first.shutdown()
            val sink = FakeSink()
            val recovered = UploadQueue(sink, "META", directory = dir, sleep = {})
            assertEquals(1, recovered.snapshot().pending)
            // The frame refused after Finish remains a dropped frame across restart.
            assertTrue(finish(recovered) is UploadQueue.Outcome.Failed)
            assertEquals(1, recovered.snapshot().dropped)
            val outcome = finish(recovered, allowPartial = true)
            assertTrue(outcome.toString(), outcome is UploadQueue.Outcome.Done)
            val result = outcome as UploadQueue.Outcome.Done
            assertEquals(1, result.frames)
            assertTrue(java.io.File(dir, "COMPLETE").exists())
        } finally { dir.deleteRecursively() }
    }

    @Test
    fun `byte capacity and failed disk admission do not create pending frames`() {
        val queue = UploadQueue(FakeSink(alwaysUnreachable = true), "META", maxPendingBytes = 3)
        try {
            assertTrue(queue.submit(0, byteArrayOf(1, 2, 3)))
            assertEquals(false, queue.submit(1, byteArrayOf(4)))
            assertEquals(1, queue.snapshot().pending)
            assertEquals(1, queue.snapshot().dropped)
        } finally { queue.shutdown() }
        val notDirectory = java.nio.file.Files.createTempFile("scanborn-full", ".tmp").toFile()
        val failed = UploadQueue(FakeSink(), "META", directory = notDirectory)
        try {
            assertEquals(false, failed.submit(0, byteArrayOf(1)))
            assertEquals(0, failed.snapshot().accepted)
            assertEquals(1, failed.snapshot().dropped)
        } finally { failed.shutdown(); notDirectory.delete() }
    }

    @Test
    fun `unreadable pending entry is rejected without blocking the next frame`() {
        val dir = java.nio.file.Files.createTempDirectory("scanborn-unreadable").toFile()
        // A directory in place of a payload produces a deterministic read error on every OS.
        java.io.File(dir, "frame_0.npz").mkdir()
        java.io.File(dir, "frame_1.npz").writeBytes(byteArrayOf(1))
        val sink = FakeSink()
        val queue = UploadQueue(sink, "META", directory = dir, sleep = {})
        try {
            assertTrue(finish(queue) is UploadQueue.Outcome.Failed)
            assertEquals(1, queue.snapshot().rejected)
            assertEquals(1, queue.snapshot().uploaded)
            assertEquals(listOf(1 to "META"), sink.sent)
            assertTrue(finish(queue, allowPartial = true) is UploadQueue.Outcome.Done)
        } finally { queue.shutdown(); dir.deleteRecursively() }
    }
}
