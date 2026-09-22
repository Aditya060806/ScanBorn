package com.scanborn.ai.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.scanborn.ai.spaces.EmbeddedScene
import com.scanborn.ai.spaces.SpacesViewModel
import java.text.DateFormat
import java.util.Date

@Composable
fun SpacesScreen(onScan: () -> Unit, capturedId: String? = null,
                 bottomPadding: androidx.compose.ui.unit.Dp = 0.dp,
                 vm: SpacesViewModel = viewModel()) {
    val state by vm.state.collectAsState()
    var roverAddress by rememberSaveable { mutableStateOf("") }
    var roverName by rememberSaveable { mutableStateOf("") }
    var mode by rememberSaveable { mutableStateOf("fit") }
    var signsChecked by rememberSaveable { mutableStateOf(false) }
    var measurement by rememberSaveable { mutableStateOf("") }
    var points by rememberSaveable { mutableStateOf(true) }
    var objects by rememberSaveable { mutableStateOf(true) }
    var occupancy by rememberSaveable { mutableStateOf(false) }
    var surface by rememberSaveable { mutableStateOf(false) }
    var pointSize by rememberSaveable { mutableFloatStateOf(3f) }
    var playing by rememberSaveable { mutableStateOf(false) }
    var progress by rememberSaveable { mutableFloatStateOf(0f) }
    var speed by rememberSaveable { mutableFloatStateOf(1f) }
    var playback by remember { mutableStateOf<org.json.JSONObject?>(null) }
    LaunchedEffect(state.playbackProgress, state.playbackPlaying) {
        progress = state.playbackProgress; playing = state.playbackPlaying
    }
    LaunchedEffect(capturedId) { if (!capturedId.isNullOrBlank()) vm.captured(capturedId) }
    val calibrationActive = state.calibration?.optString("status") in listOf("running", "reconnecting")
    val active = state.pendingDeployment || state.deployment?.optBoolean("live") == true || calibrationActive
    val browsing = state.online && !state.busy && !active && !state.pendingStage
    val mutable = browsing && state.pipelineReady && !state.recoveringRobot
    LaunchedEffect(state.robotId) { signsChecked = false; measurement = "" }
    Column(Modifier.fillMaxSize().statusBarsPadding().padding(bottom = bottomPadding)) {
        // Always native, outside the WebView and scroll region.
        Surface(tonalElevation = 3.dp) {
            Column(Modifier.fillMaxWidth().padding(12.dp)) {
                Text("Spaces & missions", style = MaterialTheme.typography.titleLarge)
                Text(if (state.online) "Connected · ${state.deployment?.optString("status") ?: "idle"}" else "Offline · mutations disabled")
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    OutlinedButton(onClick = { vm.stop(false) }, enabled = state.deployment != null || calibrationActive) { Text("Stop") }
                    Button(onClick = { vm.stop(true) }, enabled = state.robotId.isNotBlank(),
                        colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error)) { Text("E-STOP") }
                    TextButton(onClick = vm::refresh, enabled = !state.busy) { Text("Reconnect") }
                }
            }
        }
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(state.message)
            StageRecoveryControls(state, vm)
            if (state.online && !state.pipelineReady) Text("Connected; saved pipeline needs reconciliation. Reconnect or select a saved space.")
            state.scene?.let {
                if (!state.online) Text("Cached scene · ${DateFormat.getDateTimeInstance().format(Date(it.optLong("cached_at")))}")
                val layers = remember(points, objects, occupancy, surface) {
                    org.json.JSONObject().put("geometry", points).put("semantics", objects).put("occupancy", occupancy).put("surface", surface)
                }
                EmbeddedScene(it, state.deployment, mode, Modifier.fillMaxWidth().height(340.dp), vm::viewerMessage, layers, playback, pointSize)
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    listOf("fit" to "Room", "inspect" to "Rover", "top" to "Top", "follow" to "Follow").forEach { (key, label) ->
                        TextButton(onClick = { mode = key }) { Text(label) }
                    }
                }
                TextButton(onClick = { mode = "perspective" }) { Text("Perspective") }
                Text("Point size: ${pointSize.toInt()} px", style = MaterialTheme.typography.bodySmall)
                Slider(value = pointSize, onValueChange = { pointSize = it }, valueRange = 1f..8f, steps = 6)
                Text("Measured RGB points · inferred object boxes · metres, Z-up. Rover hardware details are illustrative.", style = MaterialTheme.typography.bodySmall)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(selected = points, onClick = { points = !points }, label = { Text("Points") })
                    FilterChip(selected = objects, onClick = { objects = !objects }, label = { Text("Objects") })
                    FilterChip(selected = occupancy, onClick = { occupancy = !occupancy }, label = { Text("Occupancy") })
                }
                if (it.has("surface_warning")) Text(it.getString("surface_warning"), style = MaterialTheme.typography.bodySmall)
                if (it.has("surface")) {
                    FilterChip(selected = surface, onClick = { surface = !surface }, label = { Text("Imported mesh") })
                }
                if (state.deployment?.optBoolean("live") == false &&
                    (state.deployment?.optJSONArray("pose_samples")?.length() ?: 0) > 0) {
                    Text("Recorded playback · timestamped poses and headings")
                    Row {
                        TextButton(onClick = { playing = !playing; playback = org.json.JSONObject().put("playing", playing) }) { Text(if (playing) "Pause" else "Play") }
                        TextButton(onClick = { speed = if (speed >= 4f) 0.5f else speed * 2; playback = org.json.JSONObject().put("speed", speed) }) { Text("${speed}×") }
                    }
                    Slider(value = progress, onValueChange = { progress = it; playing = false; playback = org.json.JSONObject().put("progress", it).put("playing", false) }, valueRange = 0f..1f)
                }
            }
            state.rendererError?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            Button(onClick = onScan, enabled = !active && !state.pendingStage) { Text("Scan a space") }
            Text("Saved scans", style = MaterialTheme.typography.titleMedium)
            state.scans.forEach { scan ->
                OutlinedButton(onClick = { vm.selectScan(scan.getString("id")) }, enabled = browsing) {
                    Text("${scan.optString("device")} · ${scan.optString("status")} · ${scan.getString("id").take(8)}")
                }
            }
            if ("scans" in state.nextPages) {
                TextButton(onClick = { vm.loadMore("scans") }, enabled = state.online && !state.busy) { Text("Load more scans") }
            }
            if (state.scanId.isNotBlank()) {
                Text("Selected scan ${state.scanId.take(8)}")
                Button(onClick = vm::reconstruct, enabled = mutable) { Text("Reconstruct and inspect") }
                Button(onClick = vm::buildTwin, enabled = mutable && state.scene != null) { Text("Build twin") }
            }
            Text("Saved twins", style = MaterialTheme.typography.titleMedium)
            state.twins.forEach { twin ->
                OutlinedButton(onClick = { vm.inspectTwin(twin.getString("id")) }, enabled = browsing) {
                    Text("${twin.getString("id").take(8)} · ${twin.optString("created_at")}")
                }
            }
            if ("twins" in state.nextPages) {
                TextButton(onClick = { vm.loadMore("twins") }, enabled = state.online && !state.busy) { Text("Load more twins") }
            }
            SpaceHistory(state, browsing, vm)
            Text("Robot", style = MaterialTheme.typography.titleMedium)
            state.robots.forEach { robot ->
                FilterChip(selected = state.robotId == robot.getString("id"), onClick = { vm.selectRobot(robot.getString("id")) },
                    enabled = browsing, label = { Text("${robot.optString("kind")} · ${robot.getString("id").take(8)}") })
            }
            if ("robots" in state.nextPages) {
                TextButton(onClick = { vm.loadMore("robots") }, enabled = state.online && !state.busy) { Text("Load more robots") }
            }
            OutlinedButton(onClick = vm::registerFake, enabled = browsing) { Text("Register fake rover") }
            OutlinedTextField(value = roverAddress, onValueChange = { roverAddress = it },
                label = { Text("Physical rover URL") }, enabled = !state.busy && !active)
            OutlinedTextField(value = roverName, onValueChange = { roverName = it },
                label = { Text("Rover name (optional)") }, enabled = !state.busy && !active)
            OutlinedButton(onClick = { vm.registerPhysical(roverAddress, roverName) },
                enabled = state.online && !state.busy && !active && roverAddress.isNotBlank()) { Text("Register physical rover") }
            if (state.recoveringRobot) Text("Checking selected rover and calibration history� Reconnect retries this check.")
            state.robotStatus?.let { Text("${it.optString("firmware")} � ${it.optString("state")} � ${it.optJSONArray("capabilities") ?: ""}") }
            if (state.twinId.isNotBlank()) {
                Text("Mission · ${state.twinId.take(8)}", style = MaterialTheme.typography.titleMedium)
                Row { listOf("start", "goal", "via").forEach { key ->
                    FilterChip(selected = state.selection == key, onClick = { vm.selection(key) }, label = { Text("Pick $key") })
                } }
                OutlinedTextField(value = state.start, onValueChange = { vm.coordinate("start", it) }, label = { Text("Start x,y,heading (m, rad)") }, enabled = mutable)
                OutlinedTextField(value = state.goal, onValueChange = { vm.coordinate("goal", it) }, label = { Text("Goal x,y (m)") }, enabled = mutable)
                OutlinedTextField(value = state.via, onValueChange = { vm.coordinate("via", it) }, label = { Text("Via x,y; x,y (m)") }, enabled = mutable)
                Button(onClick = vm::planMission, enabled = mutable) { Text("Plan route") }
                OutlinedButton(onClick = vm::prepareSquare, enabled = mutable) { Text("Prepare one-metre square") }
                state.mission?.let { Text("Route ${it.optDouble("length_m")} m · clearance ${it.optDouble("min_clearance_m")} m · ${it.optString("status")}") }
                Button(onClick = vm::train, enabled = mutable && state.mission != null) { Text("Train and validate") }
                state.training?.let { Text("Simulation success: ${(it.optDouble("sim_success_rate") * 100).toInt()}%\n${it.optJSONObject("mission_metrics") ?: ""}") }
                Button(onClick = vm::optimize, enabled = mutable && state.training != null) { Text("Optimize and validate int8") }
                Button(onClick = vm::deploy, enabled = mutable && state.robotId.isNotBlank()) { Text("Deploy validated mission") }
            }
            if (state.pendingDeployment) {
                Text("Saved deployment: ${state.deploymentRequestStatus}. Recovery never starts a drive automatically.")
                if (state.deploymentRequestStatus in listOf("failed", "interrupted")) {
                    OutlinedButton(onClick = vm::acknowledgeDeploymentFailure, enabled = !state.busy) {
                        Text("Acknowledge failed deployment")
                    }
                } else if (state.deploymentRequestStatus.startsWith("Outcome unknown:")) {
                    Text("Retry sends the original saved request. Stop uses E-STOP until its deployment is known.")
                    OutlinedButton(onClick = vm::retryDeploymentRequest, enabled = !state.busy && state.online) {
                        Text("Retry saved deployment request")
                    }
                }
            }
            state.deployment?.let { Text("Drive result: ${it.optString("status")}\n${it.optJSONObject("result") ?: "Awaiting result"}") }
            ProviderControls(state.providers, browsing, vm)
            if (state.robotId.isNotBlank()) {
                Text("Calibration", style = MaterialTheme.typography.titleMedium)
                val pendingStart = state.calibration?.optString("status") == "reconnecting" &&
                    state.calibration?.has("request_id") == true
                if (pendingStart) {
                    Text("Start outcome unknown. Recovery only reads server state; retry retains the original request. Stop uses E-STOP until the session is known.")
                    OutlinedButton(onClick = vm::retryCalibration, enabled = state.online && !state.busy) {
                        Text("Retry saved calibration request")
                    }
                }
                OutlinedButton(onClick = vm::resetRobot, enabled = mutable ||
                    (pendingStart && state.online && !state.busy)) { Text("Reset rover to idle") }
                Text("After a mission or E-STOP, reset before calibrating. Reset does not start motion.")
                Text("Verify forward motor motion and increasing encoders. Counterclockwise rotation must increase gyro yaw, decrease the left encoder and increase the right. Fake experiments retain simulated provenance.")
                Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                    Checkbox(checked = signsChecked, onCheckedChange = { signsChecked = it }, enabled = !active)
                    Text("Signs checked (or simulated rover)")
                }
                listOf("straight" to "Straight distance", "spin" to "Three-turn spin", "wheel_step" to "One-second wheel step").forEach { (key, label) ->
                    OutlinedButton(onClick = { vm.startCalibration(key) }, enabled = mutable && signsChecked) { Text(label) }
                }
                state.calibration?.let { session ->
                    Text("${session.optString("mode")} · ${session.optString("status")} · ${session.optString("source")}")
                    Text("Encoder counts ${session.optJSONArray("encoder_counts")} · gyro ${session.optDouble("gyro_yaw_rad")} rad")
                    if (session.optString("status") == "done" && session.optString("mode") != "wheel_step") {
                        OutlinedTextField(value = measurement, onValueChange = { measurement = it },
                            label = { Text(if (session.optString("mode") == "straight") "Measured travel (metres)" else "Measured rotation (radians)") },
                            enabled = mutable && !session.optBoolean("applied"))
                        Button(onClick = { vm.measureCalibration(measurement) }, enabled = mutable && !session.optBoolean("applied")) { Text("Calculate proposal") }
                    }
                    session.optJSONObject("proposal")?.let { proposal ->
                        OutlinedButton(onClick = vm::reconcileCalibration, enabled = mutable) { Text("Check and reconcile configuration") }
                        Text("Reads live configuration; no motion or settings are sent.")
                        Text("Before: ${proposal.optJSONObject("before")}\nProposed: ${proposal.optJSONObject("settings")}\nNoise parameters unchanged.")
                        Button(onClick = vm::applyCalibration, enabled = mutable && !session.optBoolean("applied")) { Text("Apply and verify configuration") }
                    }
                }
            }
        }
    }
}
