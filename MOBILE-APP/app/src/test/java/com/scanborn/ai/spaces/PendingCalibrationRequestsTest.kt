package com.scanborn.ai.spaces

import org.junit.Assert.*
import org.junit.Test
import java.nio.file.Files

class PendingCalibrationRequestsTest {
    @Test fun `identity survives recreation and explicit retry preserves parameters`() {
        val root = Files.createTempDirectory("calibration-recovery").toFile()
        try {
            val first = PendingCalibrationRequests(root).begin("http://server/", "robot-1", "spin")
            val restored = PendingCalibrationRequests(root)
            assertEquals(first, restored.read("http://server"))
            assertEquals(first, restored.begin("http://server", "robot-1", "spin"))
            assertThrows(IllegalStateException::class.java) { restored.begin("http://server", "robot-1", "straight") }
            assertThrows(IllegalStateException::class.java) { restored.begin("http://server", "robot-2", "spin") }
            assertNull(restored.read("http://other-server"))
            restored.settle("http://server", "stale-callback")
            assertEquals(first, restored.read("http://server"))
            restored.settle("http://server", first.requestId)
            assertNull(restored.read("http://server"))
            assertNotEquals(first.requestId, restored.begin("http://server", "robot-1", "spin").requestId)
        } finally { root.deleteRecursively() }
    }

    @Test fun `damaged identity fails closed instead of starting a new request`() {
        val root = Files.createTempDirectory("calibration-damaged").toFile()
        try {
            val store = PendingCalibrationRequests(root)
            store.begin("http://server", "robot-1", "spin")
            root.listFiles()!!.single().writeText("server=http://server\nrobot=robot-1")
            assertThrows(IllegalStateException::class.java) { store.read("http://server") }
            assertThrows(IllegalStateException::class.java) { store.begin("http://server", "robot-1", "spin") }
        } finally { root.deleteRecursively() }
    }

    @Test fun `failed persistence refuses to provide a new motion identity`() {
        val root = Files.createTempFile("calibration-unwritable", ".file").toFile()
        try {
            assertThrows(IllegalStateException::class.java) {
                PendingCalibrationRequests(root).begin("http://server", "robot-1", "spin")
            }
        } finally { root.delete() }
    }
}
