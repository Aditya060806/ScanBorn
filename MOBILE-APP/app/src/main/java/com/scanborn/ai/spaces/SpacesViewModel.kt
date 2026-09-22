package com.scanborn.ai.spaces

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.viewModelScope
import com.scanborn.ai.data.OrchestratorPreference
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

data class SpacesState(
    val scans: List<JSONObject> = emptyList(), val twins: List<JSONObject> = emptyList(),
    val robots: List<JSONObject> = emptyList(), val scene: JSONObject? = null,
    val missions: List<JSONObject> = emptyList(), val deployments: List<JSONObject> = emptyList(),
    val inspectedDeployment: JSONObject? = null,
    val mission: JSONObject? = null, val training: JSONObject? = null,
    val deployment: JSONObject? = null, val message: String = "Connecting…",
    val calibration: JSONObject? = null,
    val providers: JSONObject? = null,
    val pendingDeployment: Boolean = false, val deploymentRequestStatus: String = "",
    val pendingStage: Boolean = false, val stageStatus: String = "",
    val nextPages: Map<String, Int> = emptyMap(),
    val robotStatus: JSONObject? = null, val recoveringRobot: Boolean = false,
    val busy: Boolean = false, val online: Boolean = false,
    val pipelineReady: Boolean = false,
    val scanId: String = "", val twinId: String = "", val robotId: String = "",
    val start: String = "0,0,0", val goal: String = "1,0", val via: String = "",
    val selection: String = "goal", val rendererError: String? = null,
    val playbackProgress: Float = 0f, val playbackPlaying: Boolean = false,
)

class SpacesViewModel(app: Application, private val saved: SavedStateHandle,
                      storageRoot: File, serverUrls: Flow<String>) : AndroidViewModel(app) {
    constructor(app: Application, saved: SavedStateHandle) :
        this(app, saved, app.filesDir, OrchestratorPreference(app).url)
    private val repo = SpacesRepository(File(storageRoot, "viewer-cache"))
    private val pendingCalibrations = PendingCalibrationRequests(File(storageRoot, "pending-calibrations"))
    private val pendingDeployments = PendingDeploymentRequests(File(storageRoot, "pending-deployments"))
    private val stageJournal = StageJournal(File(storageRoot, "stage-journal"))
    private val pipelineKeys = listOf("scan", "mesh", "segmented_mesh", "twin", "mission", "policy", "artifact",
        "training", "robot", "start", "goal", "via")
    private var base = ""
    private var serverGeneration = 0
    private val mutable = MutableStateFlow(SpacesState(
        scanId = saved["scan"] ?: "", twinId = saved["twin"] ?: "", robotId = saved["robot"] ?: "",
        start = saved["start"] ?: "0,0,0", goal = saved["goal"] ?: "1,0", via = saved["via"] ?: "",
        deployment = saved.get<String>("deployment")?.let { JSONObject().put("deployment_id", it).put("live", true).put("status", "reconnecting") },
        calibration = saved.get<String>("calibration")?.let { JSONObject().put("id", it).put("status", "reconnecting") },
    ))
    val state = mutable.asStateFlow()
    private var watching: Job? = null
    private var working: Job? = null
    private var calibrating: Job? = null
    init {
        viewModelScope.launch {
            serverUrls.collectLatest { url ->
                val previous = saved.get<String>("server")
                if (previous != null && previous != url) {
                    serverGeneration++
                    watching?.cancel(); working?.cancel(); calibrating?.cancel()
                    saved.keys().toList().forEach { saved.remove<Any>(it) }
                    mutable.value = SpacesState()
                }
                saved["server"] = url
                base = url
                mutable.update { it.copy(scene = repo.cached(url), online = false) }
                try {
                    if (saved.get<String>("scan") == null && saved.get<String>("twin") == null)
                        stageJournal.checkpoint(url)?.let { restorePipeline(it) }
                    stageJournal.pending(url)?.let {
                        restorePipeline(it.context)
                        mutable.update { state -> state.copy(pendingStage = true, stageStatus = "Saved ${it.path}; check its result before continuing") }
                    }
                    pendingDeployments.read(url)?.let { showPendingDeployment(it) }
                    pendingCalibrations.read(url)?.let { pending -> showPendingCalibration(pending) }
                } catch (e: Exception) {
                    mutable.update { it.copy(message = e.message ?: "Saved work recovery failed", pipelineReady = false) }
                    return@collectLatest
                }
                refresh()
            }
        }
    }
    private fun list(response: JSONObject): List<JSONObject> {
        val items = response.getJSONArray("items")
        return (0 until items.length()).map { items.getJSONObject(it) }
    }
    private suspend fun discovery(kind: String, append: Boolean = false) {
        require(kind in listOf("scans", "twins", "robots", "missions", "deployments"))
        val offset = if (append) mutable.value.nextPages[kind] ?: return else 0
        val response = get("/$kind?limit=100&offset=$offset")
        val incoming = list(response)
        val next = if (response.isNull("next_offset")) null else response.getInt("next_offset")
        require(next == null || next > offset) { "Invalid discovery pagination response" }
        mutable.update { state ->
            val previous = when (kind) {
                "scans" -> state.scans; "twins" -> state.twins; "robots" -> state.robots
                "missions" -> state.missions; else -> state.deployments
            }
            val rows = ((if (append) previous else emptyList()) + incoming).distinctBy { it.getString("id") }
            val cursors = state.nextPages.toMutableMap().apply {
                if (next == null) remove(kind) else put(kind, next)
            }
            when (kind) {
                "scans" -> state.copy(scans = rows, nextPages = cursors)
                "twins" -> state.copy(twins = rows, nextPages = cursors)
                "robots" -> state.copy(robots = rows, nextPages = cursors)
                "missions" -> state.copy(missions = rows, nextPages = cursors)
                else -> state.copy(deployments = rows, nextPages = cursors)
            }
        }
    }
    fun loadMore(kind: String) = action {
        discovery(kind, append = true)
        mutable.update { it.copy(message = "Loaded more $kind") }
    }
    private fun action(block: suspend () -> Unit) {
        if (mutable.value.busy) return
        mutable.update { it.copy(busy = true, message = "Working…") }
        val generation = serverGeneration
        working = viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) { repo.request(base, "/health") }
                mutable.update { it.copy(online = true) }
                block()
            } catch (e: Exception) {
                if (e is CancellationException) throw e
                mutable.update { it.copy(message = e.message ?: "Request failed") }
                val reachable = withContext(Dispatchers.IO) { runCatching { repo.request(base, "/health") }.isSuccess }
                mutable.update { it.copy(online = reachable) }
            } finally {
                if (generation == serverGeneration) {
                    mutable.update { it.copy(busy = false) }
                    saved.get<String>("pending_capture")?.takeIf { mutable.value.online }?.let {
                        saved.remove<String>("pending_capture")
                        selectScan(it); reconstruct()
                    }
                }
            }
        }
    }
    private suspend fun get(path: String) = withContext(Dispatchers.IO) { repo.request(base, path) }
    private fun pipelineCheckpoint(): String = JSONObject().apply {
        pipelineKeys.forEach { key -> saved.get<String>(key)?.let { put(key, it) } }
    }.toString()
    private fun restorePipeline(raw: String) {
        val record = JSONObject(raw)
        val values = pipelineKeys.associateWith { key ->
            if (record.has(key)) {
                require(record.get(key) is String) { "Invalid stage checkpoint: $key" }
                record.getString(key)
            } else null
        }
        values.forEach { (key, value) ->
            saved.remove<String>(key)
            if (value != null) saved[key] = value
        }
        mutable.update { it.copy(scanId = saved["scan"] ?: "", twinId = saved["twin"] ?: "",
            robotId = saved["robot"] ?: "", start = saved["start"] ?: "0,0,0", goal = saved["goal"] ?: "1,0",
            via = saved["via"] ?: "", pipelineReady = false) }
    }
    private suspend fun post(path: String, body: JSONObject = JSONObject(), deduplicate: Boolean = false): JSONObject {
        if (deduplicate) {
            val pending = stageJournal.begin(base, path, body.toString(), pipelineCheckpoint())
            mutable.update { it.copy(pendingStage = true, stageStatus = "Waiting for $path") }
            body.put("request_id", pending.requestId)
        }
        return withContext(Dispatchers.IO) { repo.request(base, path, body) }
    }
    private suspend fun applyStage(path: String, body: JSONObject, result: JSONObject) {
        val pending = stageJournal.pending(base) ?: error("Stage recovery identity missing")
        require(pending.path == path) { "Stage recovery path mismatch" }
        val original = JSONObject(pending.body)
        require(original.keys().asSequence().all { original.get(it).toString() == body.opt(it)?.toString() }) {
            "Stage recovery input mismatch"
        }
        when (path) {
            "/reconstruct" -> {
                val mesh = result.getString("mesh_id")
                invalidateMission(); saved.remove<String>("twin"); saved.remove<String>("segmented_mesh")
                saved["mesh"] = mesh
                mutable.update { it.copy(twinId = "") }
                scene(mesh, null)
            }
            "/segment" -> { saved["segmented_mesh"] = body.getString("mesh_id") }
            "/generate-twin" -> {
                val twin = result.getString("twin_id"); invalidateMission(); saved["twin"] = twin
                mutable.update { it.copy(twinId = twin) }; scene(null, twin)
            }
            "/train" -> {
                val policy = result.getString("policy_id")
                saved.remove<String>("artifact")
                saved["policy"] = policy; saved["training"] = result.toString()
                val scene = mutable.value.scene?.let { JSONObject(it.toString()) }
                scene?.getJSONObject("scene")?.put("spec", result.getJSONObject("robot_spec"))
                mutable.update { it.copy(training = result, scene = scene) }
            }
            "/optimize" -> { saved["artifact"] = result.getString("artifact_id") }
            else -> error("Unsupported saved stage")
        }
        stageJournal.settle(base, pending.requestId, pipelineCheckpoint())
        mutable.update { it.copy(pendingStage = false, stageStatus = "", message = "$path complete") }
    }
    private suspend fun runStage(path: String, body: JSONObject) {
        val result = post(path, body, true)
        applyStage(path, body, result)
    }
    fun recoverStage(retry: Boolean = false) = action {
        val pending = stageJournal.pending(base) ?: error("No pending stage")
        val response = if (retry) {
            post(pending.path, JSONObject(pending.body).put("request_id", pending.requestId))
        } else {
            val record = get("/stages/${pending.path.removePrefix("/")}/requests/${pending.requestId}")
            require(record.getString("request_id") == pending.requestId &&
                record.getString("stage") == pending.path.removePrefix("/")) { "Stage result identity mismatch" }
            val status = record.getString("status")
            mutable.update { it.copy(stageStatus = status) }
            if (status != "done") return@action
            record.getJSONObject("response")
        }
        restorePipeline(pending.context)
        saved.get<String>("mission")?.let { id ->
            val mission = getMission(id)
            require(mission.getString("twin_id") == mutable.value.twinId && mission.optString("status") != "stale") {
                "Saved stage mission is stale or belongs to another twin"
            }
            mutable.update { it.copy(mission = mission) }
        }
        applyStage(pending.path, JSONObject(pending.body), response)
        mutable.update { it.copy(message = "Stage recovered. Reconnect to revalidate the complete pipeline.", pipelineReady = false) }
    }
    fun acknowledgeStageFailure() = action {
        val pending = stageJournal.pending(base) ?: error("No pending stage")
        val record = get("/stages/${pending.path.removePrefix("/")}/requests/${pending.requestId}")
        require(record.getString("request_id") == pending.requestId &&
            record.getString("stage") == pending.path.removePrefix("/")) { "Stage result identity mismatch" }
        require(record.getString("status") in listOf("failed", "interrupted")) { "Stage is not terminal" }
        stageJournal.settle(base, pending.requestId, pending.context)
        mutable.update { it.copy(pendingStage = false, stageStatus = "", pipelineReady = false,
            message = "Stage failure acknowledged. Reconnect before starting new work.") }
    }
    fun refresh() {
        if (!mutable.value.busy) mutable.update { it.copy(pipelineReady = false) }
        // Resume safety polling even when health/discovery is unavailable.
        if (mutable.value.pendingDeployment) recoverDeployment()
        else saved.get<String>("deployment")?.let { watch(it) }
        saved.get<String>("calibration")?.let { watchCalibration(it) }
        action {
        mutable.update { it.copy(pipelineReady = false) }
        if (mutable.value.robotId.isNotBlank()) restoreRobot()

        for (kind in listOf("scans", "twins", "robots", "missions", "deployments")) discovery(kind)
        mutable.update { it.copy(message = "Connected") }
        val selectedTwin = mutable.value.twinId
        if (selectedTwin.isNotBlank()) {
            val twin = get("/twins/$selectedTwin")
            val scanId = twin.getString("scan_id")
            require(mutable.value.scanId.isBlank() || mutable.value.scanId == scanId) { "Saved scan and twin do not match" }
            saved["scan"] = scanId
            mutable.update { it.copy(scanId = scanId) }
            scene(null, selectedTwin)
        }
        saved.get<String>("mission")?.let { id ->
            val mission = getMission(id)
            require(mission.getString("twin_id") == selectedTwin) { "Saved mission belongs to another twin" }
            require(mission.optString("status") != "stale") { "Saved mission is stale; select a space and plan again" }
            val scene = mutable.value.scene?.let { JSONObject(it.toString()) }
            scene?.getJSONObject("scene")?.put("path", mission.getJSONArray("waypoints"))
            mutable.update { it.copy(mission = mission, scene = scene) }
        }
        saved.get<String>("training")?.let { raw ->
            val training = JSONObject(raw)
            val payload = mutable.value.scene?.let { JSONObject(it.toString()) }
            training.optJSONObject("robot_spec")?.let { payload?.getJSONObject("scene")?.put("spec", it) }
            mutable.update { it.copy(training = training, scene = payload) }
        }
        mutable.update { it.copy(pipelineReady = true) }
    }
    }
    private suspend fun getMission(id: String) = get("/missions/$id")
    fun inspectDeployment(id: String) = action {
        val result = get("/deployments/$id")
        require(result.getString("deployment_id") == id) { "Deployment identity mismatch" }
        mutable.update { it.copy(inspectedDeployment = result,
            message = "Deployment record loaded; active drive controls are unchanged") }
    }
    fun reopenMission(id: String) = action {
        requirePipelineReady()
        require(mutable.value.deployment?.optBoolean("live") != true &&
            mutable.value.calibration?.optString("status") !in listOf("running", "reconnecting")) {
            "Stop the active session before changing missions"
        }
        val mission = getMission(id)
        require(mission.getString("mission_id") == id && mission.getString("twin_id") == mutable.value.twinId) {
            "Select this mission's twin before reopening it"
        }
        require(mission.optString("status") != "stale") { "Saved mission is stale; plan a new route" }
        fun text(point: JSONArray) = (0 until point.length()).joinToString(",") { point.getDouble(it).toString() }
        val start = text(mission.getJSONArray("start")); val goal = text(mission.getJSONArray("goal"))
        val points = mission.getJSONArray("via")
        val via = (0 until points.length()).joinToString(";") { text(points.getJSONArray(it)) }
        invalidateMission()
        watching?.cancel()
        saved.remove<String>("deployment")
        saved["mission"] = id; saved["start"] = start; saved["goal"] = goal; saved["via"] = via
        val scene = mutable.value.scene?.let { JSONObject(it.toString()) }
        scene?.getJSONObject("scene")?.put("path", mission.getJSONArray("waypoints"))
        mutable.update { it.copy(mission = mission, scene = scene, deployment = null,
            start = start, goal = goal, via = via,
            message = "Saved route reopened; train and validate before deployment") }
    }
    fun captured(id: String) {
        if (saved.get<String>("handled_capture") == id) return
        saved["handled_capture"] = id
        if (base.isBlank() || mutable.value.busy || !mutable.value.online) saved["pending_capture"] = id
        else { selectScan(id); reconstruct() }
    }
    fun selectScan(id: String) {
        if (mutable.value.pendingStage || mutable.value.busy || mutable.value.deployment?.optBoolean("live") == true ||
            mutable.value.calibration?.optString("status") in listOf("running", "reconnecting")) return
        saved["scan"] = id; saved["twin"] = ""
        saved.remove<String>("mesh")
        invalidateMission()
        mutable.update { it.copy(scanId = id, twinId = "", scene = null, pipelineReady = true) }
    }
    private suspend fun attachRobot(id: String) {
        require(!mutable.value.pendingStage) { "Resolve the saved stage before changing robots" }
        require(!mutable.value.pendingDeployment) { "Resolve the saved deployment before changing robots" }
        require(mutable.value.deployment?.optBoolean("live") != true &&
            mutable.value.calibration?.optString("status") !in listOf("running", "reconnecting")) { "Stop the active session before changing robots" }
        calibrating?.cancel()
        saved["robot"] = id
        saved.remove<String>("calibration"); saved.remove<String>("calibration_robot")
        saved.remove<String>("policy"); saved.remove<String>("artifact"); saved.remove<String>("training")
        mutable.update { it.copy(robotId = id, robotStatus = null, calibration = null,
            training = null, recoveringRobot = true) }
        restoreRobot()
    }
    private suspend fun restoreRobot() {
        val robot = mutable.value.robotId
        mutable.update { it.copy(recoveringRobot = true) }
        val status = get("/robots/$robot/status")
        pendingCalibrations.read(base)?.let { pending ->
            require(pending.robot == robot) { "Resolve the saved calibration robot first" }
            val result = get("/robots/$robot/calibration-requests/${pending.requestId}")
            acceptCalibration(result, pending)
            mutable.update { it.copy(robotStatus = status, recoveringRobot = false) }
            return
        }
        val latest = get("/robots/$robot/calibrations?limit=1").getJSONArray("items").optJSONObject(0)
        mutable.update { it.copy(robotStatus = status, calibration = latest, recoveringRobot = false) }
        if (latest != null) {
            saved["calibration"] = latest.getString("id"); saved["calibration_robot"] = robot
            if (latest.optString("status") == "running") watchCalibration(latest.getString("id"))
        } else {
            saved.remove<String>("calibration"); saved.remove<String>("calibration_robot")
        }
    }
    fun selectRobot(id: String) = action { attachRobot(id) }
    fun selection(kind: String) { mutable.update { it.copy(selection = kind) } }
    fun coordinate(key: String, value: String) {
        if (mutable.value.pendingStage) return
        saved[key] = value; invalidateMission()
        mutable.update { when(key) { "start" -> it.copy(start = value); "via" -> it.copy(via = value); else -> it.copy(goal = value) } }
    }
    private fun invalidateMission() {
        saved.remove<String>("mission"); saved.remove<String>("policy"); saved.remove<String>("artifact")
        saved.remove<String>("training")
        mutable.update { it.copy(mission = null, training = null) }
    }
    fun viewerMessage(raw: String) {
        runCatching {
            val message = JSONObject(raw)
            when (message.optString("type")) {
                "sceneLoaded" -> {
                    val id = message.optString("cache_id")
                    if (id.isNotBlank() && mutable.value.scene?.optString("cache_id") == id) {
                        viewModelScope.launch(Dispatchers.IO) { repo.acknowledgeScene(id) }
                    }
                }
                "playback" -> {
                    val progress = message.getDouble("progress")
                    require(progress.isFinite() && progress in 0.0..1.0)
                    mutable.update { it.copy(playbackProgress = progress.toFloat(), playbackPlaying = message.getBoolean("playing")) }
                }
                "renderer" -> mutable.update { it.copy(rendererError = message.optString("error").takeIf { text -> text.isNotBlank() && text != "null" }) }
                "selection" -> if (message.optString("kind") == "point" && mutable.value.online &&
                    !mutable.value.busy && mutable.value.deployment?.optBoolean("live") != true) {
                    val point = message.getJSONArray("position")
                    val x = point.getDouble(0); val y = point.getDouble(1)
                    require(x.isFinite() && y.isFinite())
                    val xy = "%.3f,%.3f".format(java.util.Locale.US, x, y)
                    val key = mutable.value.selection
                    coordinate(key, if (key == "via") listOf(mutable.value.via, xy).filter { it.isNotBlank() }.joinToString(";") else if (key == "start") "$xy,0" else xy)
                }
            }
        }.onFailure { mutable.update { it.copy(rendererError = "Invalid viewer message") } }
    }
    private suspend fun scene(mesh: String?, twin: String?) {
        val manifest = get(if (twin != null) "/twins/$twin/visualization" else "/meshes/$mesh/visualization")
        val navmesh = twin?.let { get("/twins/$it/navmesh") }
        val payload = withContext(Dispatchers.IO) { repo.cacheScene(base, manifest, navmesh) }
        mutable.update { it.copy(scene = payload, message = "Measured scene ready") }
    }
    fun reconstruct() = action {
        requirePipelineReady()
        runStage("/reconstruct", JSONObject().put("scan_id", mutable.value.scanId))
    }
    fun buildTwin() = action {
        requirePipelineReady()
        val mesh = saved.get<String>("mesh") ?: error("Reconstruct a scan first")
        if (saved.get<String>("segmented_mesh") != mesh) runStage("/segment", JSONObject().put("mesh_id", mesh))
        runStage("/generate-twin", JSONObject().put("mesh_id", mesh).put("objects_id", mesh)
            .put("robot_id", mutable.value.robotId.takeIf { it.isNotBlank() }))
    }
    fun inspectTwin(id: String) = action {
        require(!mutable.value.pendingStage) { "Resolve the saved stage before changing spaces" }
        val twin = get("/twins/$id")
        val scan = twin.getString("scan_id")
        invalidateMission(); saved["twin"] = id; saved["scan"] = scan
        saved.remove<String>("mesh")
        mutable.update { it.copy(twinId = id, scanId = scan, pipelineReady = false) }; scene(null, id)
        mutable.update { it.copy(pipelineReady = true) }
    }
    private fun point(text: String, heading: Boolean = false): JSONArray {
        val values = text.split(',').map { it.trim().toDouble() }
        require(values.size in (if (heading) 2..3 else 2..2) && values.all { it.isFinite() }) { "Enter x,y in metres; start may include heading in radians" }
        return JSONArray(values)
    }
    fun planMission() = action {
        requirePipelineReady()
        val s = mutable.value
        val via = JSONArray(s.via.split(';').filter { it.isNotBlank() }.map { point(it) })
        val mission = post("/missions", JSONObject().put("twin_id", s.twinId)
            .put("start", point(s.start, true)).put("goal", point(s.goal)).put("via", via))
        saved["mission"] = mission.getString("mission_id")
        val payload = s.scene?.let { JSONObject(it.toString()) }
        payload?.getJSONObject("scene")?.put("path", mission.getJSONArray("waypoints"))
        mutable.update { it.copy(mission = mission, scene = payload, message = "Route accepted") }
    }
    fun train() = action {
        requirePipelineReady()
        runStage("/train", JSONObject().put("twin_id", mutable.value.twinId)
            .put("robot_id", mutable.value.robotId.takeIf { it.isNotBlank() })
            .put("mission_id", saved.get<String>("mission") ?: error("Plan a mission first")))
    }
    fun optimize() = action {
        requirePipelineReady()
        runStage("/optimize", JSONObject().put("policy_id", saved.get<String>("policy") ?: error("Train first")))
    }
    fun registerFake() = action {
        val result = post("/robots", JSONObject().put("kind", "fake_rover"))
        attachRobot(result.getString("robot_id"))
        discovery("robots")
        mutable.update { it.copy(message = "Fake rover ready") }
    }
    fun registerPhysical(address: String, name: String) = action {
        val uri = java.net.URI(address.trim())
        require(uri.scheme in listOf("http", "https") && !uri.host.isNullOrBlank() &&
            uri.userInfo == null && uri.query == null && uri.fragment == null &&
            (uri.path.isNullOrEmpty() || uri.path == "/")) { "Enter rover URL such as http://192.168.1.50" }
        val result = post("/robots", JSONObject().put("kind", "rover")
            .put("address", address.trim().trimEnd('/')).put("name", name.trim()))
        attachRobot(result.getString("robot_id"))
        discovery("robots")
        mutable.update { it.copy(message = "Rover registered; verify calibration before training") }
    }
    fun deploy() = action {
        requirePipelineReady()
        val s = mutable.value
        require(s.robotId.isNotBlank()) { "Select a robot" }
        val pending = pendingDeployments.begin(base, s.robotId, saved.get<String>("artifact") ?: error("Optimize first"))
        showPendingDeployment(pending)
        try {
            post("/deploy", JSONObject().put("artifact_id", pending.artifact)
                .put("robot_id", pending.robot).put("request_id", pending.requestId))
        } finally {
            // Reconcile by GET even if the POST response was lost; never replay motion here.
            if (currentCoroutineContext().isActive) recoverDeployment()
        }
    }
    private fun showPendingDeployment(pending: PendingDeploymentRequests.Pending) {
        saved["robot"] = pending.robot
        saved.remove<String>("deployment")
        mutable.update { it.copy(robotId = pending.robot, pendingDeployment = true,
            deploymentRequestStatus = "Checking saved deployment request; no drive restarts automatically",
            deployment = JSONObject().put("live", true).put("status", "reconnecting")) }
    }
    private fun recoverDeployment() {
        watching?.cancel()
        watching = viewModelScope.launch {
            while (isActive) {
                try {
                    val pending = pendingDeployments.read(base) ?: break
                    val record = get("/deploy/requests/${pending.requestId}")
                    require(record.getString("request_id") == pending.requestId) { "Deployment request identity mismatch" }
                    val status = record.getString("status")
                    mutable.update { it.copy(deploymentRequestStatus = status) }
                    if (status == "done") {
                        val id = record.getJSONObject("response").getString("deployment_id")
                        val deployment = get("/deployments/$id")
                        require(deployment.getString("robot_id") == pending.robot &&
                            deployment.getString("artifact_id") == pending.artifact) { "Recovered deployment lineage mismatch" }
                        saved["deployment"] = id
                        mutable.update { it.copy(deployment = deployment) }
                        // Transfer polling ownership without cancelling this completed lookup.
                        watching = null
                        watch(id)
                        return@launch
                    }
                    if (status in listOf("failed", "interrupted")) break
                } catch (e: Exception) {
                    if (e is CancellationException) throw e
                    mutable.update { it.copy(deploymentRequestStatus = "Outcome unknown: ${e.message}. E-STOP remains available") }
                }
                delay(1000)
            }
        }
    }
    fun acknowledgeDeploymentFailure() = action {
        val pending = pendingDeployments.read(base) ?: error("No pending deployment")
        val record = get("/deploy/requests/${pending.requestId}")
        require(record.getString("request_id") == pending.requestId) { "Deployment result identity mismatch" }
        require(record.getString("status") in listOf("failed", "interrupted")) { "Deployment outcome is not terminal" }
        pendingDeployments.settle(base, pending.requestId)
        mutable.update { it.copy(pendingDeployment = false, deployment = null, deploymentRequestStatus = "",
            message = "Failed deployment acknowledged; no motion started") }
    }
    fun retryDeploymentRequest() = action {
        val pending = pendingDeployments.read(base) ?: error("No pending deployment")
        require(mutable.value.deploymentRequestStatus.startsWith("Outcome unknown:")) {
            "Only an unknown request needs explicit retry"
        }
        try {
            post("/deploy", JSONObject().put("artifact_id", pending.artifact)
                .put("robot_id", pending.robot).put("request_id", pending.requestId))
        } finally {
            if (currentCoroutineContext().isActive) recoverDeployment()
        }
    }
    fun refreshProviders() = action {
        val report = get("/providers")
        mutable.update { it.copy(providers = report, message = "Provider settings loaded") }
    }
    fun saveProviders(drafts: Map<String, String>, consent: Set<String>) = action {
        val workloads = JSONObject()
        val scopes = JSONObject()
        val report = mutable.value.providers
        for (name in listOf("planner", "profiler", "export")) {
            val chain = drafts[name].orEmpty().split(',').map { it.trim() }.filter { it.isNotBlank() }
            require(chain.isNotEmpty()) { "Choose a provider for $name" }
            workloads.put(name, JSONArray(chain))
            if (name in consent) scopes.put(name, report?.getJSONObject("remote_data_scopes")?.getJSONArray(name)
                ?: error("Refresh provider status before authorizing remote data"))
        }
        val result = post("/providers/settings", JSONObject().put("workloads", workloads).put("remote_consent", scopes))
        mutable.update { it.copy(providers = result, message = "Provider settings saved for subsequent stages") }
    }
    private fun requirePipelineReady() {
        check(mutable.value.pipelineReady && !mutable.value.recoveringRobot &&
            !mutable.value.pendingDeployment && !mutable.value.pendingStage) {
            "Reconcile the saved space and rover before running a mission stage"
        }
    }
    private fun watch(id: String) {
        watching?.cancel()
        watching = viewModelScope.launch {
            while (isActive) {
                try {
                    val result = get("/deployments/$id")
                    mutable.update { it.copy(deployment = result, online = true) }
                    if (PendingDeploymentRequests.terminal(result.optBoolean("live"), result.optString("status"))) {
                        pendingDeployments.read(base)?.let { pending ->
                            require(result.getString("robot_id") == pending.robot) { "Deployment robot mismatch" }
                            pendingDeployments.settle(base, pending.requestId)
                        }
                        mutable.update { it.copy(pendingDeployment = false, deploymentRequestStatus = "") }
                        break
                    }
                } catch (e: Exception) {
                    if (e is CancellationException) throw e
                    mutable.update { it.copy(online = false, message = "Telemetry disconnected; reconnecting") }
                }
                delay(500)
            }
        }
    }
    fun prepareSquare() {
        try {
            val start = point(mutable.value.start, true)
            val x = start.getDouble(0); val y = start.getDouble(1)
            val heading = if (start.length() > 2) start.getDouble(2) else 0.0
            val c = kotlin.math.cos(heading); val s = kotlin.math.sin(heading)
            fun corner(a: Double, b: Double) = "${x+a*c-b*s},${y+a*s+b*c}"
            coordinate("goal", "$x,$y")
            coordinate("via", listOf(corner(1.0,0.0), corner(1.0,1.0), corner(0.0,1.0)).joinToString(";"))
            mutable.update { it.copy(message = "One-metre square prepared. Plan and inspect the route before training; detours are not a clean square trial.") }
        } catch (e: Exception) { mutable.update { it.copy(message = e.message ?: "Enter a valid start") } }
    }
    fun startCalibration(mode: String) = action {
        require(!mutable.value.pendingDeployment) { "Resolve the saved deployment first" }
        val robot = mutable.value.robotId
        require(robot.isNotBlank()) { "Select a robot first" }
        val pending = pendingCalibrations.begin(base, robot, mode)
        showPendingCalibration(pending)
        submitCalibration(pending)
    }
    fun retryCalibration() = action {
        val pending = pendingCalibrations.read(base) ?: error("No saved calibration request")
        submitCalibration(pending)
    }
    private fun showPendingCalibration(pending: PendingCalibrationRequests.Pending) {
        calibrating?.cancel()
        saved["robot"] = pending.robot; saved["calibration_robot"] = pending.robot
        saved.remove<String>("calibration")
        mutable.update { it.copy(robotId = pending.robot, recoveringRobot = true,
            calibration = JSONObject().put("mode", pending.mode).put("request_id", pending.requestId)
                .put("status", "reconnecting"), message = "Checking saved calibration; no motion restarted") }
    }
    private suspend fun submitCalibration(pending: PendingCalibrationRequests.Pending) {
        val result = try {
            post("/robots/${pending.robot}/calibrations", JSONObject().put("mode", pending.mode)
                .put("duration_s", pending.duration).put("target_mps", pending.speed)
                .put("request_id", pending.requestId))
        } catch (e: Exception) {
            if (e is CancellationException) throw e
            // Only GET after uncertain delivery; never automatically repeat a motion command.
            get("/robots/${pending.robot}/calibration-requests/${pending.requestId}")
        }
        acceptCalibration(result, pending)
    }
    private fun acceptCalibration(result: JSONObject, pending: PendingCalibrationRequests.Pending) {
        require(result.optString("robot_id") == pending.robot &&
            result.optString("request_id") == pending.requestId) { "Calibration recovery identity mismatch" }
        val id = result.getString("id")
        saved["calibration"] = id; saved["calibration_robot"] = pending.robot
        if (result.optString("status") != "running") pendingCalibrations.settle(base, pending.requestId)
        mutable.update { it.copy(calibration = result, recoveringRobot = false,
            message = "Calibration ${result.optString("status")}") }
        if (result.optString("status") == "running") watchCalibration(id)
    }
    fun resetRobot() = action {
        require(mutable.value.robotId.isNotBlank()) { "Select a robot first" }
        post("/robots/${mutable.value.robotId}/reset")
        mutable.update { it.copy(message = "Rover reset to idle; no motion started") }
    }
    private fun watchCalibration(id: String) {
        calibrating?.cancel()
        val robot = saved.get<String>("calibration_robot") ?: return
        calibrating = viewModelScope.launch {
            while (isActive) {
                try {
                    val result = get("/robots/$robot/calibrations/$id")
                    mutable.update { it.copy(calibration = result, online = true) }
                    if (result.optString("status") != "running") {
                        result.optString("request_id").takeIf { it.isNotBlank() }?.let {
                            pendingCalibrations.settle(base, it)
                        }
                        break
                    }
                } catch (e: Exception) {
                    if (e is CancellationException) throw e
                    mutable.update { it.copy(online = false, message = "Calibration status unavailable; stop controls remain active") }
                }
                delay(400)
            }
        }
    }
    fun measureCalibration(value: String) = action {
        val measured = value.toDouble()
        require(measured.isFinite() && measured > 0) { "Enter a finite positive measurement" }
        val id = saved.get<String>("calibration") ?: error("No calibration")
        val robot = saved.get<String>("calibration_robot") ?: error("No calibration robot")
        val result = post("/robots/$robot/calibrations/$id/measurement", JSONObject().put("measured", measured))
        mutable.update { it.copy(calibration = result, message = "Review proposed geometry before applying") }
    }
    fun applyCalibration() = action {
        val id = saved.get<String>("calibration") ?: error("No calibration")
        val robot = saved.get<String>("calibration_robot") ?: error("No calibration robot")
        val result = post("/robots/$robot/calibrations/$id/apply")
        saved.remove<String>("policy"); saved.remove<String>("artifact"); saved.remove<String>("training")
        mutable.update { it.copy(calibration = result, training = null, message = "Retrain with verified configuration · ${result.optString("source")}") }
    }
    fun reconcileCalibration() = action {
        val id = saved.get<String>("calibration") ?: error("No calibration")
        val robot = saved.get<String>("calibration_robot") ?: error("No calibration robot")
        val result = post("/robots/$robot/calibrations/$id/reconcile")
        saved.remove<String>("policy"); saved.remove<String>("artifact"); saved.remove<String>("training")
        mutable.update { it.copy(calibration = result, training = null,
            message = "Configuration checked: ${result.optString("apply_state")}. No motion commanded.") }
    }
    // Independent of busy state and renderer health; never queue Stop behind training or uploads.
    fun stop(emergency: Boolean) { viewModelScope.launch {
        try {
            val calibrationActive = mutable.value.calibration?.optString("status") in listOf("running", "reconnecting")
            val robot = if (calibrationActive) saved.get<String>("calibration_robot") else mutable.value.robotId
            val unknownCalibration = calibrationActive && saved.get<String>("calibration") == null
            val unknownDeployment = mutable.value.pendingDeployment && saved.get<String>("deployment") == null
            val path = if (emergency || unknownCalibration || unknownDeployment) "/robots/$robot/estop"
                else if (calibrationActive) "/robots/$robot/calibrations/${saved.get<String>("calibration")}/stop"
                else "/deployments/${saved.get<String>("deployment") ?: error("No active deployment")}/stop"
            post(path)
            mutable.update { it.copy(message = if (emergency || unknownCalibration || unknownDeployment) "E-STOP sent" else "Stop sent") }
        } catch (e: Exception) { mutable.update { it.copy(message = "Stop could not be confirmed: ${e.message}") } }
    } }
}
