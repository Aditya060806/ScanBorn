package com.scanborn.ai.spaces

import com.scanborn.ai.scan.UploadQueue
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.Properties
import java.util.UUID

/** One unresolved non-motion stage per server, atomically settled with its pipeline checkpoint. */
class StageJournal(private val root: File) {
    data class Pending(val path: String, val body: String, val requestId: String, val context: String)
    private val paths = setOf("/reconstruct", "/segment", "/generate-twin", "/train", "/optimize")
    private fun file(base: String) = File(root,
        UUID.nameUUIDFromBytes(base.trimEnd('/').toByteArray(Charsets.UTF_8)).toString() + ".properties")
    private fun load(base: String): Properties {
        val file = file(base)
        if (!file.exists()) return Properties()
        check(file.length() <= 2 * 1024 * 1024) { "Stage journal exceeds size limit" }
        val p = Properties().apply { file.inputStream().use { load(it) } }
        check(p.getProperty("version") == "1" && p.getProperty("server") == base.trimEnd('/')) {
            "Stage recovery journal is damaged"
        }
        check(p.getProperty("checkpoint") != null) { "Stage checkpoint is missing" }
        val pendingKeys = setOf("request", "path", "body", "context")
        check(pendingKeys.none { p.containsKey(it) } || pendingKeys.all { p.containsKey(it) }) {
            "Pending stage is incomplete"
        }
        if (p.containsKey("request")) {
            check(p.getProperty("path") in paths && p.getProperty("body") != null &&
                p.getProperty("context") != null &&
                p.getProperty("request").matches(Regex("[A-Za-z0-9_-]{1,128}"))) { "Pending stage is damaged" }
        }
        return p
    }
    private fun write(base: String, p: Properties) {
        check(root.isDirectory || root.mkdirs()) { "Cannot persist stage recovery" }
        p.setProperty("version", "1"); p.setProperty("server", base.trimEnd('/'))
        val bytes = ByteArrayOutputStream().apply { p.store(this, null) }.toByteArray()
        require(bytes.size <= 2 * 1024 * 1024) { "Stage checkpoint exceeds size limit" }
        UploadQueue.atomicWrite(file(base), bytes)
    }
    @Synchronized fun pending(base: String): Pending? {
        val p = load(base)
        return p.getProperty("request")?.let {
            Pending(p.getProperty("path"), p.getProperty("body"), it, p.getProperty("context"))
        }
    }
    @Synchronized fun checkpoint(base: String): String? = load(base).getProperty("checkpoint")
    @Synchronized fun begin(base: String, path: String, body: String, context: String): Pending {
        pending(base)?.let {
            check(it.path == path && it.body == body) { "Resolve the saved stage before starting different work" }
            return it
        }
        require(path in paths)
        val entry = Pending(path, body, UUID.randomUUID().toString(), context)
        val p = load(base)
        p.setProperty("checkpoint", context); p.setProperty("context", context)
        p.setProperty("path", path); p.setProperty("body", body); p.setProperty("request", entry.requestId)
        write(base, p)
        return entry
    }
    @Synchronized fun settle(base: String, requestId: String, checkpoint: String) {
        if (pending(base)?.requestId != requestId) return
        write(base, Properties().apply { setProperty("checkpoint", checkpoint) })
    }
}
