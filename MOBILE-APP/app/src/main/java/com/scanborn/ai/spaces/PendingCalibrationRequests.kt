package com.scanborn.ai.spaces

import com.scanborn.ai.scan.UploadQueue
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.Properties
import java.util.UUID

/** One unresolved motion request per server. Kept until its recorded session is terminal. */
class PendingCalibrationRequests(private val root: File) {
    data class Pending(val robot: String, val mode: String, val requestId: String,
                       val duration: Double = 1.0, val speed: Double = 0.1)
    private fun server(base: String) = base.trimEnd('/')
    private fun file(base: String) = File(root,
        UUID.nameUUIDFromBytes(server(base).toByteArray(Charsets.UTF_8)).toString() + ".properties")

    @Synchronized fun read(base: String): Pending? {
        val file = file(base)
        if (!file.exists()) return null
        val properties = Properties().apply { file.inputStream().use { load(it) } }
        check(properties.getProperty("server") == server(base)) { "Saved calibration server mismatch" }
        val robot = properties.getProperty("robot").orEmpty()
        val mode = properties.getProperty("mode").orEmpty()
        val request = properties.getProperty("request").orEmpty()
        val duration = properties.getProperty("duration")?.toDoubleOrNull()
        val speed = properties.getProperty("speed")?.toDoubleOrNull()
        check(properties.getProperty("version") == "1" && duration != null &&
            duration.isFinite() && duration in 0.1..3.0 && speed != null &&
            speed.isFinite() && speed > 0 && speed <= 0.2) { "Saved calibration parameters are damaged" }
        check(robot.matches(Regex("[A-Za-z0-9_-]{1,128}")) &&
            mode in listOf("straight", "spin", "wheel_step") &&
            request.matches(Regex("[A-Za-z0-9_-]{1,128}"))) { "Saved calibration request is damaged" }
        return Pending(robot, mode, request, duration, speed)
    }

    @Synchronized fun begin(base: String, robot: String, mode: String): Pending {
        read(base)?.let {
            check(it.robot == robot && it.mode == mode) { "Resolve the saved calibration first" }
            return it
        }
        require(robot.matches(Regex("[A-Za-z0-9_-]{1,128}")))
        require(mode in listOf("straight", "spin", "wheel_step"))
        check(root.isDirectory || root.mkdirs()) { "Cannot save calibration recovery identity" }
        val pending = Pending(robot, mode, UUID.randomUUID().toString())
        val properties = Properties().apply {
            setProperty("server", server(base)); setProperty("robot", robot)
            setProperty("mode", mode); setProperty("request", pending.requestId)
            setProperty("version", "1"); setProperty("duration", pending.duration.toString())
            setProperty("speed", pending.speed.toString())
        }
        val bytes = ByteArrayOutputStream().apply { properties.store(this, null) }.toByteArray()
        UploadQueue.atomicWrite(file(base), bytes)
        return pending
    }

    @Synchronized fun settle(base: String, requestId: String) {
        if (read(base)?.requestId == requestId) {
            check(file(base).delete()) { "Could not clear settled calibration identity" }
        }
    }
}
