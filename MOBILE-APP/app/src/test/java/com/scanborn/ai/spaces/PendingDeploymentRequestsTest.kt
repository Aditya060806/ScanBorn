package com.scanborn.ai.spaces

import org.junit.Assert.*
import org.junit.Test
import java.nio.file.Files

class PendingDeploymentRequestsTest {
    @Test fun `missing live session alone is not a terminal outcome`() {
        assertFalse(PendingDeploymentRequests.terminal(false, "running"))
        assertFalse(PendingDeploymentRequests.terminal(false, "unknown"))
        assertFalse(PendingDeploymentRequests.terminal(true, "done"))
        assertTrue(PendingDeploymentRequests.terminal(false, "done"))
        assertTrue(PendingDeploymentRequests.terminal(false, "interrupted"))
        assertTrue(PendingDeploymentRequests.terminal(false, "recording_failed"))
    }
    @Test fun `identity survives recreation and explicit retry preserves parameters`() {
        val root = Files.createTempDirectory("deployment-recovery").toFile()
        try {
            val first = PendingDeploymentRequests(root).begin("http://server/", "robot-1", "artifact-1")
            val restored = PendingDeploymentRequests(root)
            assertEquals(first, restored.read("http://server"))
            assertEquals(first, restored.begin("http://server", "robot-1", "artifact-1"))
            assertThrows(IllegalStateException::class.java) { restored.begin("http://server", "robot-1", "artifact-2") }
            assertThrows(IllegalStateException::class.java) { restored.begin("http://server", "robot-2", "artifact-1") }
            assertNull(restored.read("http://other-server"))
            restored.settle("http://server", "stale-callback")
            assertEquals(first, restored.read("http://server"))
            restored.settle("http://server", first.requestId)
            assertNull(restored.read("http://server"))
            assertNotEquals(first.requestId, restored.begin("http://server", "robot-1", "artifact-1").requestId)
        } finally { root.deleteRecursively() }
    }

    @Test fun `damaged identity fails closed instead of starting a new request`() {
        val root = Files.createTempDirectory("deployment-damaged").toFile()
        try {
            val store = PendingDeploymentRequests(root)
            store.begin("http://server", "robot-1", "artifact-1")
            root.listFiles()!!.single().writeText("server=http://server\nrobot=robot-1")
            assertThrows(IllegalStateException::class.java) { store.read("http://server") }
            assertThrows(IllegalStateException::class.java) { store.begin("http://server", "robot-1", "artifact-1") }
        } finally { root.deleteRecursively() }
    }

    @Test fun `failed persistence refuses to provide a new motion identity`() {
        val root = Files.createTempFile("deployment-unwritable", ".file").toFile()
        try {
            assertThrows(IllegalStateException::class.java) {
                PendingDeploymentRequests(root).begin("http://server", "robot-1", "artifact-1")
            }
        } finally { root.delete() }
    }
}
