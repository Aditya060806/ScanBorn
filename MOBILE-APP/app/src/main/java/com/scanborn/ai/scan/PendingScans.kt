package com.scanborn.ai.scan

import org.json.JSONObject
import java.io.File

/**
 * Scans saved on the phone (filesDir/pending-scans/<id>/): `capture.json` names the scan ID, the
 * orchestrator it belongs to and the meta its first frame carries; beside it are the queued
 * frames and receipts ([UploadQueue]) and, once the orchestrator closed the scan, COMPLETE.
 *
 * The scan ID is chosen on the phone before the first frame, so a lost response or a restart
 * never creates a second scan on the orchestrator: every retry names the same ID.
 */
class PendingScans(private val root: File, private val codec: Codec = JsonCodec) {

    /** The manifest's three fields; the app uses JSON, the JVM unit tests a stand-in (android.jar's
     *  org.json is a stub off the device). */
    interface Codec {
        fun encode(manifest: Manifest): String
        fun decode(text: String): Manifest
    }
    data class Manifest(val id: String, val url: String, val meta: String)

    object JsonCodec : Codec {
        override fun encode(manifest: Manifest): String = JSONObject().put("id", manifest.id)
            .put("url", manifest.url).put("meta", manifest.meta).toString()
        override fun decode(text: String): Manifest = JSONObject(text).let {
            Manifest(it.getString("id"), it.getString("url"), it.getString("meta"))
        }
    }

    /** An unfinished scan: [meta] is null when its manifest cannot be read (it cannot be
     *  uploaded, only discarded). */
    data class Saved(val id: String, val directory: File, val url: String?, val meta: String?) {
        val damaged: Boolean get() = meta == null
    }

    private fun read(directory: File): Saved {
        val manifest = runCatching { codec.decode(File(directory, MANIFEST).readText()) }.getOrNull()
            ?.takeIf { it.id == directory.name && it.meta.isNotEmpty() }
        return Saved(directory.name, directory, manifest?.url, manifest?.meta)
    }

    private fun unfinished(): List<File> = root.listFiles().orEmpty()
        .filter { it.isDirectory && !File(it, UploadQueue.COMPLETE).exists() }
        .sortedWith(compareBy({ File(it, MANIFEST).lastModified() }, { it.name }))

    /** The oldest unfinished scan for this orchestrator, then any damaged one; else null. A scan
     *  saved for another orchestrator waits until that address is selected again. */
    fun saved(baseUrl: String): Saved? {
        val scans = unfinished().map(::read)
        return scans.firstOrNull { !it.damaged && it.url == baseUrl } ?: scans.firstOrNull { it.damaged }
    }

    /** Record a new scan before its first frame is queued. */
    fun create(id: String, baseUrl: String, meta: String): File {
        require(SCAN_ID.matches(id)) { "invalid scan id" }
        val directory = File(root, id)
        if (directory.exists()) {
            val existing = read(directory)
            require(!existing.damaged && existing.url == baseUrl && existing.meta == meta) {
                "Saved scan identity already belongs to another capture"
            }
            return directory
        }
        check(directory.mkdirs()) { "Could not create scan directory" }
        UploadQueue.atomicWrite(File(directory, MANIFEST),
            codec.encode(Manifest(id, baseUrl, meta)).toByteArray())
        return directory
    }

    /** Delete one saved scan the operator chose to give up; true when it is gone. */
    fun discard(id: String): Boolean {
        if (!SCAN_ID.matches(id)) return false
        val directory = File(root, id)
        return !directory.exists() || directory.deleteRecursively()
    }

    /** Remove scans the orchestrator already closed: their frames are on the orchestrator. */
    fun purgeCompleted(): Int = root.listFiles().orEmpty()
        .filter { it.isDirectory && File(it, UploadQueue.COMPLETE).exists() }
        .count { it.deleteRecursively() }

    companion object {
        const val MANIFEST = "capture.json"
        private val SCAN_ID = Regex("[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
    }
}
