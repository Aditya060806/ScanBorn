package com.scanborn.ai.spaces

import android.app.Application
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModelStore
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.flow.MutableStateFlow
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.net.ServerSocket
import java.util.UUID
import java.util.concurrent.CopyOnWriteArrayList

/** Real Android networking and ViewModel disposal/recreation; no camera, AI or rover is started. */
@RunWith(AndroidJUnit4::class)
class RecoveryHttpTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private fun onMain(block: () -> Unit) = instrumentation.runOnMainSync(block)
    private fun until(predicate: () -> Boolean) {
        val end = System.currentTimeMillis() + 15000
        while (!predicate() && System.currentTimeMillis() < end) Thread.sleep(25)
        assertTrue("Condition did not become true", predicate())
    }
    internal class Server(port: Int = 0) : AutoCloseable {
        private val socket = ServerSocket().apply {
            reuseAddress = true
            bind(java.net.InetSocketAddress(java.net.InetAddress.getByName("127.0.0.1"), port), 16)
        }
        val url = "http://127.0.0.1:${socket.localPort}"
        val calls = CopyOnWriteArrayList<String>()
        @Volatile var resolved = false
        @Volatile var history = false
        @Volatile var staleMission = false
        @Volatile var foreignMission = false
        private val worker = Thread {
            while (!socket.isClosed) {
                try {
                    socket.accept().use { client ->
                        client.soTimeout = 5000
                        val reader = client.getInputStream().bufferedReader()
                        val request = reader.readLine().split(' ')
                        val method = request[0]; val path = request[1]
                        var length = 0
                        while (true) {
                            val line = reader.readLine() ?: break
                            if (line.isEmpty()) break
                            if (line.startsWith("Content-Length:", true)) length = line.substringAfter(':').trim().toInt()
                        }
                        repeat(length) { reader.read() }
                        calls.add("$method $path")
                        var code = 200
                        val body = when {
                            history && path.startsWith("/missions?") -> {
                                val second = path.contains("offset=100")
                                """{"items":[{"id":"mission-${if (second) 2 else 1}","twin_id":"twin-1","status":"planned"}],"next_offset":${if (second) "null" else "100"}}"""
                            }
                            history && path.startsWith("/missions/") ->
                                """{"mission_id":"${path.substringAfterLast('/')}","twin_id":"${if (foreignMission) "other" else "twin-1"}","status":"${if (staleMission) "stale" else "planned"}","start":[1,2,0],"goal":[3,4],"via":[[2,3]],"waypoints":[[1,2],[2,3],[3,4]]}"""
                            history && path == "/twins/twin-1" -> """{"scan_id":"scan-1"}"""
                            history && path == "/twins/twin-1/visualization" ->
                                """{"mesh_id":"mesh-1","geometry":[{"url":"/meshes/mesh-1/geometry/100000"}],"objects":[],"robot_spec":{}}"""
                            history && path == "/twins/twin-1/navmesh" -> "{}"
                            history && path == "/meshes/mesh-1/geometry/100000" -> "\u0000".repeat(16)
                            history && path.startsWith("/deployments?") ->
                                """{"items":[{"id":"drive-1","status":"done"}],"next_offset":null}"""
                            path.startsWith("/stages/segment/requests/") -> if (resolved) {
                                """{"stage":"segment","request_id":"${path.substringAfterLast('/')}","status":"done","response":{}}"""
                            } else { code = 404; """{"detail":"unknown request"}""" }
                            path.startsWith("/deploy/requests/") -> if (resolved) {
                                """{"request_id":"${path.substringAfterLast('/')}","status":"done","response":{"deployment_id":"drive-1"}}"""
                            } else { code = 404; """{"detail":"unknown request"}""" }
                            path == "/deployments/drive-1" ->
                                """{"deployment_id":"drive-1","robot_id":"robot-1","artifact_id":"artifact-1","live":false,"status":"done"}"""
                            path.contains("/calibration-requests/") -> if (resolved) {
                                """{"id":"cal-1","robot_id":"robot-1","request_id":"${path.substringAfterLast('/')}","status":"done"}"""
                            } else { code = 404; """{"detail":"unknown request"}""" }
                            path.endsWith("/status") -> """{"state":"idle"}"""
                            path == "/health" || method == "POST" -> "{}"
                            else -> """{"items":[],"next_offset":null}"""
                        }.toByteArray()
                        client.getOutputStream().apply {
                            write("HTTP/1.1 $code OK\r\nContent-Type: application/json\r\nContent-Length: ${body.size}\r\nConnection: close\r\n\r\n".toByteArray())
                            write(body); flush()
                        }
                    }
                } catch (e: java.io.IOException) { if (socket.isClosed) break }
            }
        }.apply { isDaemon = true; start() }
        override fun close() { socket.close(); worker.join(6000) }
    }

    @Test fun deploymentRecoversAcrossFreshViewModelsWithoutReplayingMotion() = exercise(false)
    @Test fun calibrationRecoversAcrossFreshViewModelsWithoutReplayingMotion() = exercise(true)
    @Test fun serverSwitchKeepsPendingDriveIsolatedAndRestoresItOnReturn() = exercise(false, true)

    @Test fun paginatedHistoryReopensMatchingRoutesAndOnlyInspectsDeployment() {
        val app = instrumentation.targetContext.applicationContext as Application
        val root = File(app.cacheDir, "history-test-${UUID.randomUUID()}")
        val store = ViewModelStore()
        Server().use { server ->
            server.history = true
            try {
                lateinit var vm: SpacesViewModel
                onMain {
                    vm = SpacesViewModel(app, SavedStateHandle(mapOf("twin" to "twin-1", "deployment" to "drive-1")), root,
                        MutableStateFlow(server.url))
                    store.put("spaces", vm)
                }
                until { vm.state.value.pipelineReady && !vm.state.value.busy }
                assertEquals(1, vm.state.value.missions.size)
                onMain { vm.loadMore("missions") }
                until { vm.state.value.missions.size == 2 && !vm.state.value.busy }
                assertFalse("missions" in vm.state.value.nextPages)
                onMain { vm.reopenMission("mission-1") }
                until { vm.state.value.mission?.optString("mission_id") == "mission-1" && !vm.state.value.busy }
                assertEquals("1.0,2.0,0.0", vm.state.value.start)
                assertEquals("2.0,3.0", vm.state.value.via)
                assertNull("Previous drive trace must not appear on the reopened mission", vm.state.value.deployment)
                server.staleMission = true
                onMain { vm.reopenMission("mission-2") }
                until { !vm.state.value.busy && vm.state.value.message.contains("stale") }
                assertEquals("mission-1", vm.state.value.mission!!.getString("mission_id"))
                server.staleMission = false; server.foreignMission = true
                onMain { vm.reopenMission("mission-2") }
                until { !vm.state.value.busy && vm.state.value.message.contains("Select this mission") }
                onMain { vm.inspectDeployment("drive-1") }
                until { vm.state.value.inspectedDeployment != null && !vm.state.value.busy }
                assertNull(vm.state.value.deployment)
                assertTrue(vm.state.value.robotId.isBlank())
                assertTrue(server.calls.none { it.startsWith("POST") })
            } finally { onMain { store.clear() }; root.deleteRecursively() }
        }
    }

    @Test fun stageRecoverySurvivesLostUiStateAndDoesNotResubmit() {
        val app = instrumentation.targetContext.applicationContext as Application
        val root = File(app.cacheDir, "stage-test-${UUID.randomUUID()}")
        val store = ViewModelStore()
        Server().use { server ->
            val journal = StageJournal(File(root, "stage-journal"))
            val pending = journal.begin(server.url, "/segment", """{"mesh_id":"mesh-1"}""",
                """{"scan":"scan-1","mesh":"mesh-1"}""")
            try {
                lateinit var vm: SpacesViewModel
                fun create() = onMain {
                    vm = SpacesViewModel(app, SavedStateHandle(), root, MutableStateFlow(server.url))
                    store.put("spaces", vm)
                }
                create()
                until { vm.state.value.pendingStage && !vm.state.value.busy && vm.state.value.online }
                onMain { vm.coordinate("goal", "9,9"); vm.selectScan("other"); vm.recoverStage() }
                until { !vm.state.value.busy && vm.state.value.message.contains("unknown request") }
                assertEquals("scan-1", vm.state.value.scanId)
                assertEquals("1,0", vm.state.value.goal)
                assertEquals(pending, journal.pending(server.url))
                onMain { store.clear() }
                server.resolved = true
                create()
                until { vm.state.value.pendingStage && !vm.state.value.busy && vm.state.value.online }
                onMain { vm.recoverStage() }
                until { !vm.state.value.pendingStage && !vm.state.value.busy }
                assertNull(journal.pending(server.url))
                assertEquals("mesh-1", org.json.JSONObject(journal.checkpoint(server.url)!!).getString("segmented_mesh"))
                assertFalse(vm.state.value.pipelineReady)
                assertTrue(server.calls.none { it.startsWith("POST") })
            } finally { onMain { store.clear() }; root.deleteRecursively() }
        }
    }

    /** Driven in two separate instrumentation processes by check_android_process_recovery.py. */
    @Test fun processDeathRecovery() {
        val args = InstrumentationRegistry.getArguments()
        val phase = args.getString("recovery_phase")
        org.junit.Assume.assumeTrue(phase in listOf("prepare", "recover"))
        val token = args.getString("recovery_token")!!
        require(token.matches(Regex("[a-f0-9-]{36}")))
        val calibration = args.getString("recovery_kind") == "calibration"
        val stage = args.getString("recovery_kind") == "stage"
        val app = instrumentation.targetContext.applicationContext as Application
        val root = File(app.cacheDir, "process-recovery-$token")
        val metadata = File(root, "process.properties")
        val properties = java.util.Properties()
        if (phase == "recover") metadata.inputStream().use { properties.load(it) }
        Server(if (phase == "recover") properties.getProperty("port").toInt() else 0).use { server ->
            val store = ViewModelStore()
            try {
                if (phase == "prepare") {
                    root.mkdirs()
                    if (stage) StageJournal(File(root, "stage-journal")).begin(server.url,
                        "/segment", """{"mesh_id":"mesh-1"}""", """{"mesh":"mesh-1","scan":"scan-1"}""")
                    else if (calibration) PendingCalibrationRequests(File(root, "pending-calibrations"))
                        .begin(server.url, "robot-1", "spin")
                    else PendingDeploymentRequests(File(root, "pending-deployments"))
                        .begin(server.url, "robot-1", "artifact-1")
                    properties.setProperty("port", server.url.substringAfterLast(':'))
                    properties.setProperty("pid", android.os.Process.myPid().toString())
                    val bytes = java.io.ByteArrayOutputStream().apply { properties.store(this, null) }.toByteArray()
                    com.scanborn.ai.scan.UploadQueue.atomicWrite(metadata, bytes)
                } else {
                    assertNotEquals("Must recover in another OS process", properties.getProperty("pid"),
                        android.os.Process.myPid().toString())
                    server.resolved = true
                }
                lateinit var vm: SpacesViewModel
                onMain {
                    vm = SpacesViewModel(app, SavedStateHandle(), root, MutableStateFlow(server.url))
                    store.put("spaces", vm)
                }
                if (phase == "prepare") {
                    if (stage) until { vm.state.value.pendingStage && !vm.state.value.busy }
                    else until { server.calls.any { it.contains(if (calibration) "/calibration-requests/" else "/deploy/requests/") } }
                    instrumentation.sendStatus(0, android.os.Bundle().apply { putString("scanborn_ready", token) })
                    Thread.sleep(120000)
                    fail("Harness did not interrupt the pending process")
                } else {
                    if (stage) {
                        until { vm.state.value.pendingStage && vm.state.value.online && !vm.state.value.busy }
                        onMain { vm.recoverStage() }
                    }
                    until {
                        if (stage) !vm.state.value.pendingStage && !vm.state.value.busy
                        else if (calibration) vm.state.value.calibration?.optString("status") == "done"
                        else vm.state.value.deployment?.optString("status") == "done" && !vm.state.value.pendingDeployment
                    }
                    assertTrue("Process recovery must not replay motion", server.calls.none { it.startsWith("POST") })
                    assertNull(if (stage) StageJournal(File(root, "stage-journal")).pending(server.url)
                        else if (calibration) PendingCalibrationRequests(File(root, "pending-calibrations")).read(server.url)
                        else PendingDeploymentRequests(File(root, "pending-deployments")).read(server.url))
                    if (stage) assertEquals("mesh-1", org.json.JSONObject(
                        StageJournal(File(root, "stage-journal")).checkpoint(server.url)!!).getString("segmented_mesh"))
                }
            } finally {
                onMain { store.clear() }
                if (phase == "recover") root.deleteRecursively()
            }
        }
    }

    private fun exercise(calibration: Boolean, switchServer: Boolean = false) {
        val app = instrumentation.targetContext.applicationContext as Application
        val root = File(app.cacheDir, "recovery-test-${UUID.randomUUID()}").apply { mkdirs() }
        val store = ViewModelStore()
        Server().use { server ->
            val urls = MutableStateFlow(server.url)
            try {
                if (calibration) PendingCalibrationRequests(File(root, "pending-calibrations"))
                    .begin(server.url, "robot-1", "spin")
                else PendingDeploymentRequests(File(root, "pending-deployments"))
                    .begin(server.url, "robot-1", "artifact-1")
                fun create(): SpacesViewModel {
                    lateinit var vm: SpacesViewModel
                    onMain {
                        vm = SpacesViewModel(app, SavedStateHandle(), root, urls)
                        store.put("spaces", vm)
                    }
                    return vm
                }
                var vm = create()
                val lookup = if (calibration) "/calibration-requests/" else "/deploy/requests/"
                until { server.calls.any { it.contains(lookup) } && !vm.state.value.busy }
                onMain { vm.stop(false) }
                until { server.calls.contains("POST /robots/robot-1/estop") }
                assertTrue(server.calls.filter { it.startsWith("POST") }.all { it.endsWith("/estop") })
                if (switchServer) Server().use { other ->
                    onMain { urls.value = other.url }
                    until { vm.state.value.pipelineReady && vm.state.value.robotId.isBlank() }
                    assertFalse(vm.state.value.pendingDeployment)
                    assertTrue(other.calls.none { it.contains("/deploy/requests/") || it.startsWith("POST") })
                    onMain { urls.value = server.url }
                    until { vm.state.value.pendingDeployment && vm.state.value.robotId == "robot-1" }
                }
                onMain { store.clear() }
                server.calls.clear()
                server.resolved = true
                vm = create() // Empty SavedStateHandle models losing all transient saved state.
                until {
                    if (calibration) vm.state.value.calibration?.optString("status") == "done"
                    else vm.state.value.deployment?.optString("status") == "done" && !vm.state.value.pendingDeployment
                }
                assertTrue("Recovery must only GET", server.calls.none { it.startsWith("POST") })
                assertNull(if (calibration) PendingCalibrationRequests(File(root, "pending-calibrations")).read(server.url)
                    else PendingDeploymentRequests(File(root, "pending-deployments")).read(server.url))
            } finally { onMain { store.clear() }; root.deleteRecursively() }
        }
    }
}
