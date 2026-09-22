package com.scanborn.ai.scan

import java.io.File
import java.io.IOException
import java.util.Properties
import java.util.TreeMap
import java.util.concurrent.Executors
import kotlin.math.min

interface ChunkSink {
    val scanId: String?
    fun upload(index: Int, npz: ByteArray, meta: String?)
    fun complete(meta: String): Int
}
class ChunkRejected(message: String) : Exception(message)

/**
 * Bounded synchronous admission, one worker, durable pending frames.
 *
 * Counters (every frame handed to [submit], except a repeated index or one after [shutdown],
 * lands in exactly one):
 * - uploaded: the orchestrator acknowledged it (a `.uploaded` receipt is on disk).
 * - rejected: the orchestrator refused it (4xx), or its saved copy could not be read back on
 *   this phone; it will never be sent again (a `.rejected` receipt).
 * - pending: saved and not yet settled; it is sent again until acknowledged or refused.
 * - dropped: never saved — the queue was full (count or bytes), the disk write failed, or it
 *   arrived after Finish closed admission.
 * accepted = uploaded + pending + rejected, and accepted + dropped = frames submitted.
 * Receipts are the record for uploaded and rejected; dropped is kept in counts.properties.
 */
class UploadQueue(
    private val sink: ChunkSink,
    private val firstMeta: String,
    private val maxPending: Int = MAX_PENDING,
    private val retryDelayMs: (Int) -> Long = { 500L shl min(it, 4) },
    private val sleep: (Long) -> Unit = { Thread.sleep(it) },
    private val directory: File? = null,
    private val maxPendingBytes: Long = MAX_PENDING_BYTES,
    private val onChange: (Counts) -> Unit = {},
) {
    data class Counts(
        val uploaded: Int, val pending: Int, val rejected: Int, val dropped: Int,
        val scanId: String?, val lastError: String?,
    ) { val accepted: Int get() = uploaded + pending + rejected }
    sealed class Outcome {
        data class Done(val scanId: String, val frames: Int, val counts: Counts) : Outcome()
        data class Failed(val reason: String, val counts: Counts) : Outcome()
    }
    private val executor = Executors.newSingleThreadExecutor()
    private val lock = Any()
    private val pending = TreeMap<Int, ByteArray?>()
    private val settledIndices = HashSet<Int>()
    private val sizes = HashMap<Int, Long>()
    private var uploaded = 0
    private var rejected = 0
    private var dropped = 0
    private var pendingBytes = 0L
    private var lastError: String? = null
    private var accepting = true
    private var closed = false
    private var finishing = false
    private var scheduled = false

    init {
        require(maxPending > 0)
        directory?.let { dir ->
            dir.mkdirs()
            val files = dir.listFiles().orEmpty()
            // An interrupted atomic write leaves only its temporary; the target is untouched.
            files.filter { it.name.endsWith(".tmp") }.forEach { it.delete() }
            val state = File(dir, "counts.properties")
            val saved = if (!state.exists()) null else runCatching {
                Properties().apply { state.inputStream().use { load(it) } }
            }.getOrNull()
            if (state.exists() && saved == null) lastError = "Saved counts were unreadable; recounted from receipts"
            fun savedCount(key: String) = saved?.getProperty(key)?.trim()?.toIntOrNull()?.coerceAtLeast(0) ?: 0
            val receipts = files.filter { it.name.endsWith(".uploaded") || it.name.endsWith(".rejected") }
            receipts.forEach { receipt ->
                FRAME.matchEntire(receipt.name.substringBeforeLast('.'))?.groupValues?.get(1)
                    ?.toIntOrNull()?.let { settledIndices.add(it) }
            }
            if (receipts.isNotEmpty()) {
                uploaded = receipts.count { it.name.endsWith(".uploaded") }
                rejected = receipts.count { it.name.endsWith(".rejected") }
            } else {
                uploaded = savedCount("uploaded"); rejected = savedCount("rejected")
            }
            dropped = savedCount("dropped")
            for (f in files) {
                val index = FRAME.matchEntire(f.name)?.groupValues?.get(1)?.toIntOrNull() ?: continue
                val settled = File(dir, f.name + ".uploaded").exists() || File(dir, f.name + ".rejected").exists()
                if (settled) { f.delete(); continue } // settled before a crash could delete it
                pending[index] = null
                sizes[index] = f.length()
                pendingBytes += f.length()
            }
        }
    }
    fun snapshot(): Counts = synchronized(lock) { counts() }
    fun submit(index: Int, npz: ByteArray): Boolean = synchronized(lock) {
        require(index >= 0)
        if (closed || pending.containsKey(index) || index in settledIndices) return false
        if (!accepting) {
            dropped++; lastError = "Frame $index arrived after Finish"; persist(); onChange(counts()); return false
        }
        if (pending.size >= maxPending || pendingBytes + npz.size > maxPendingBytes) {
            dropped++; persist(); onChange(counts()); return false
        }
        try {
            if (directory == null) pending[index] = npz else {
                atomicWrite(File(directory, "frame_$index.npz"), npz)
                pending[index] = null
            }
        } catch (e: Exception) {
            dropped++; lastError = "Could not save frame: ${e.message}"
            persist(); onChange(counts()); return false
        }
        sizes[index] = npz.size.toLong()
        pendingBytes += npz.size
        if (!scheduled) {
            scheduled = true
            executor.execute {
                try { drain() } catch (e: Exception) {
                    synchronized(lock) { lastError = "Pending frames remain saved: ${e.message}" }
                } finally {
                    synchronized(lock) { scheduled = false; if (!closed) onChange(counts()) }
                }
            }
        }
        onChange(counts())
        true
    }
    private fun drain() {
        while (true) {
            val (index, bytes, meta) = synchronized(lock) {
                if (closed || pending.isEmpty()) return
                val index = pending.firstKey()
                val bytes = pending[index] ?: try {
                    File(directory!!, "frame_$index.npz").readBytes()
                } catch (e: IOException) {
                    null
                }
                Triple(index, bytes, if (uploaded == 0) firstMeta else null)
            }
            if (bytes == null) {
                // Its saved copy is unreadable, so it can never be sent: settle it rather than
                // let it block every frame behind it.
                synchronized(lock) {
                    if (closed) return
                    settle(index, refused = true)
                    lastError = "Frame $index could not be read back on this phone"
                    onChange(counts())
                }
                continue
            }
            var refused = false
            try {
                sink.upload(index, bytes, meta)
            } catch (e: ChunkRejected) {
                refused = true
                synchronized(lock) { lastError = e.message }
            } catch (e: Exception) {
                synchronized(lock) { lastError = e.message ?: e.javaClass.simpleName }
                return
            }
            synchronized(lock) {
                // Close may discard this directory while HTTP is in flight. Keep an unrecorded
                // acceptance pending on normal exit; the stable scan/index makes retry safe.
                if (closed) return
                settle(index, refused)
            }
        }
    }
    /** Under [lock]. The receipt is written first: if that fails the frame stays pending and is
     *  sent again (the orchestrator overwrites an index), so nothing is counted twice or lost. */
    private fun settle(index: Int, refused: Boolean) {
        directory?.let { dir ->
            atomicWrite(File(dir, "frame_$index.npz." + if (refused) "rejected" else "uploaded"), ByteArray(0))
        }
        if (refused) rejected++ else { uploaded++; lastError = null }
        settledIndices.add(index)
        pendingBytes -= sizes.remove(index) ?: 0L
        pending.remove(index)
        directory?.let { File(it, "frame_$index.npz").delete() }
        persist(); onChange(counts())
    }
    fun finish(attempts: Int = 5, allowPartial: Boolean = false,
               finalMeta: (Counts) -> String, done: (Outcome) -> Unit) = synchronized(lock) {
        if (closed || finishing) return
        accepting = false; finishing = true
        executor.execute {
            try {
                ensureOpenForFinish()
                var attempt = 0
                while (snapshot().pending > 0 && attempt < attempts) {
                    if (attempt > 0) sleep(retryDelayMs(attempt))
                    ensureOpenForFinish()
                    drain(); attempt++
                }
                ensureOpenForFinish()
                val count = snapshot()
                val id = sink.scanId
                val result = if (count.uploaded == 0 || id == null) {
                    Outcome.Failed("No frame reached the orchestrator: ${count.lastError ?: "offline"}. " +
                        "Check the URL in Settings. Pending frames are saved; retry when connected.", count)
                } else if ((count.pending > 0 || count.rejected > 0 || count.dropped > 0) && !allowPartial) {
                    Outcome.Failed("${count.pending} pending, ${count.rejected} rejected and ${count.dropped} dropped frames. Retry upload, or explicitly " +
                        "finish with uploaded frames.", count)
                } else {
                    ensureOpenForFinish()
                    val frames = sink.complete(finalMeta(count))
                    directory?.let { atomicWrite(File(it, COMPLETE), frames.toString().toByteArray()) }
                    Outcome.Done(id, frames, count)
                }
                synchronized(lock) { finishing = false }
                if (result is Outcome.Done) shutdown()
                done(result)
            } catch (e: Exception) {
                synchronized(lock) { finishing = false; lastError = e.message }
                done(Outcome.Failed("Upload remains resumable: ${e.message}", snapshot()))
            }
        }
    }
    private fun ensureOpenForFinish() = synchronized(lock) {
        if (closed) throw IOException("Scan closed before completion; retry the saved scan")
    }
    fun shutdown() = synchronized(lock) {
        accepting = false; closed = true; executor.shutdown()
    }
    private fun counts() = Counts(uploaded, pending.size, rejected, dropped, sink.scanId, lastError)
    /** Best effort: receipts already record uploaded and rejected frames. */
    private fun persist() {
        val dir = directory ?: return
        try {
            atomicWrite(File(dir, "counts.properties"),
                "uploaded=$uploaded\nrejected=$rejected\ndropped=$dropped\n".toByteArray())
        } catch (e: Exception) {
            lastError = "Could not save counts: ${e.message}"
        }
    }
    companion object {
        const val MAX_PENDING = 300
        const val MAX_PENDING_BYTES = 64L * 1024 * 1024
        const val COMPLETE = "COMPLETE"
        private val FRAME = Regex("frame_([0-9]{1,9})\\.npz")

        /** Write to a temporary, sync, rename over the target. A failed write leaves the target
         *  as it was and no temporary behind. */
        fun atomicWrite(target: File, bytes: ByteArray) {
            val temporary = File(target.parentFile, target.name + ".tmp")
            try {
                temporary.outputStream().use { it.write(bytes); it.fd.sync() }
                // Android's rename (POSIX) replaces the target atomically. Only the Windows JVM that
                // runs the unit tests refuses to rename over an existing file, so only there is
                // the target removed first.
                if (!temporary.renameTo(target) &&
                    !(File.separatorChar == '\\' && target.delete() && temporary.renameTo(target))) {
                    throw IOException("Cannot persist ${target.name}")
                }
            } catch (e: Exception) {
                temporary.delete()
                throw e
            }
        }
    }
}
