package com.scanborn.ai.spaces

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.scanborn.ai.scan.ChunkSink
import com.scanborn.ai.scan.PendingScans
import com.scanborn.ai.scan.UploadQueue
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Synthetic bytes exercise durable admission; this does not claim camera/ARCore coverage. */
@RunWith(AndroidJUnit4::class)
class CaptureProcessTest {
    @Test fun processDeathRecovery() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val args = InstrumentationRegistry.getArguments()
        val phase = args.getString("recovery_phase")
        org.junit.Assume.assumeTrue(phase in listOf("prepare", "recover"))
        val token = args.getString("recovery_token")!!
        require(token.matches(Regex("[a-f0-9-]{36}")))
        val root = File(instrumentation.targetContext.cacheDir, "capture-process-$token")
        val scans = PendingScans(File(root, "scans"))
        val url = "http://127.0.0.1:1"
        val meta = "{\"device\":\"synthetic-process-test\"}"
        val bytes = byteArrayOf(1, 9, 3, 7)
        if (phase == "prepare") {
            scans.create(token, url, meta)
            UploadQueue.atomicWrite(File(root, "pid"), android.os.Process.myPid().toString().toByteArray())
        } else {
            assertNotEquals(File(root, "pid").readText(), android.os.Process.myPid().toString())
        }
        val saved = scans.saved(url)!!
        assertEquals(token, saved.id)
        assertEquals(meta, saved.meta)
        val entered = CountDownLatch(1)
        val completed = CountDownLatch(1)
        var outcome: UploadQueue.Outcome? = null
        val sink = object : ChunkSink {
            override val scanId = saved.id
            override fun upload(index: Int, npz: ByteArray, meta: String?) {
                assertEquals(0, index)
                assertArrayEquals(bytes, npz)
                assertEquals(saved.meta, meta)
                entered.countDown()
                if (phase == "prepare") Thread.sleep(120000)
            }
            override fun complete(meta: String): Int {
                assertEquals("recover", phase)
                return 1
            }
        }
        val queue = UploadQueue(sink, saved.meta!!, directory = saved.directory)
        try {
            if (phase == "prepare") {
                assertTrue(queue.submit(0, bytes))
                assertTrue(entered.await(10, TimeUnit.SECONDS))
                instrumentation.sendStatus(0, android.os.Bundle().apply { putString("scanborn_ready", token) })
                Thread.sleep(120000)
                fail("Harness did not stop the pending process")
            } else {
                assertEquals(1, queue.snapshot().pending)
                assertEquals(0, queue.snapshot().uploaded)
                assertEquals(1L, entered.count) // Construction does not replay uploads.
                queue.finish(finalMeta = { "{}" }) { outcome = it; completed.countDown() }
                assertTrue(completed.await(10, TimeUnit.SECONDS))
                assertTrue(outcome.toString(), outcome is UploadQueue.Outcome.Done)
                assertEquals(token, (outcome as UploadQueue.Outcome.Done).scanId)
                assertEquals(1, queue.snapshot().uploaded)
                assertEquals(0, queue.snapshot().pending)
                assertEquals(1, scans.purgeCompleted())
                assertNull(scans.saved(url))
            }
        } finally {
            queue.shutdown()
            if (phase == "recover") root.deleteRecursively()
        }
    }
}
