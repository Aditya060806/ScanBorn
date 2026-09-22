package com.scanborn.ai.spaces

import com.scanborn.ai.scan.UploadQueue
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.Properties
import java.util.UUID

/** One unresolved motion request per server. Kept until its recorded session is terminal. */
class PendingDeploymentRequests(private val root: File) {
    companion object {
        fun terminal(live: Boolean, status: String) = !live &&
            status in setOf("done", "estop", "error", "failed", "stopped", "interrupted", "recording_failed")
    }
    data class Pending(val robot: String, val artifact: String, val requestId: String)
    private fun server(base: String) = base.trimEnd('/')
    private fun file(base: String) = File(root,
        UUID.nameUUIDFromBytes(server(base).toByteArray(Charsets.UTF_8)).toString() + ".properties")

    @Synchronized fun read(base: String): Pending? {
        val file = file(base)
        if (!file.exists()) return null
        val properties = Properties().apply { file.inputStream().use { load(it) } }
        check(properties.getProperty("server") == server(base)) { "Saved deployment server mismatch" }
        val robot = properties.getProperty("robot").orEmpty()
        val artifact = properties.getProperty("artifact").orEmpty()
        val request = properties.getProperty("request").orEmpty()
        check(properties.getProperty("version") == "1") { "Saved deployment version is invalid" }
        check(robot.matches(Regex("[A-Za-z0-9_-]{1,128}")) &&
            artifact.matches(Regex("[A-Za-z0-9_-]{1,128}")) &&
            request.matches(Regex("[A-Za-z0-9_-]{1,128}"))) { "Saved deployment request is damaged" }
        return Pending(robot, artifact, request)
    }

    @Synchronized fun begin(base: String, robot: String, artifact: String): Pending {
        read(base)?.let {
            check(it.robot == robot && it.artifact == artifact) { "Resolve the saved deployment first" }
            return it
        }
        require(robot.matches(Regex("[A-Za-z0-9_-]{1,128}")))
        require(artifact.matches(Regex("[A-Za-z0-9_-]{1,128}")))
        check(root.isDirectory || root.mkdirs()) { "Cannot save deployment recovery identity" }
        val pending = Pending(robot, artifact, UUID.randomUUID().toString())
        val properties = Properties().apply {
            setProperty("server", server(base)); setProperty("robot", robot)
            setProperty("artifact", artifact); setProperty("request", pending.requestId)
            setProperty("version", "1")
        }
        val bytes = ByteArrayOutputStream().apply { properties.store(this, null) }.toByteArray()
        UploadQueue.atomicWrite(file(base), bytes)
        return pending
    }

    @Synchronized fun settle(base: String, requestId: String) {
        if (read(base)?.requestId == requestId) {
            check(file(base).delete()) { "Could not clear settled deployment identity" }
        }
    }
}
