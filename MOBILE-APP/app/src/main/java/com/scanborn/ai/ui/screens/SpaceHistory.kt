package com.scanborn.ai.ui.screens

import androidx.compose.foundation.layout.Column
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import com.scanborn.ai.spaces.SpacesState
import com.scanborn.ai.spaces.SpacesViewModel

@Composable
fun SpaceHistory(state: SpacesState, enabled: Boolean, vm: SpacesViewModel) {
    var expanded by rememberSaveable { mutableStateOf(false) }
    TextButton(onClick = { expanded = !expanded }) { Text("Saved missions and deployment history") }
    if (!expanded) return
    Column {
        Text("Select a twin to reopen its missions. Stale routes must be planned again.")
        state.missions.forEach { mission ->
            val matches = mission.optString("twin_id") == state.twinId
            OutlinedButton(onClick = { vm.reopenMission(mission.getString("id")) },
                enabled = enabled && matches && mission.optString("status") != "stale") {
                Text("Mission ${mission.getString("id").take(8)} · twin ${mission.optString("twin_id").take(8)} · ${mission.optString("status")}")
            }
        }
        if ("missions" in state.nextPages) TextButton(onClick = { vm.loadMore("missions") }, enabled = enabled) {
            Text("Load more missions")
        }
        state.deployments.forEach { deployment ->
            OutlinedButton(onClick = { vm.inspectDeployment(deployment.getString("id")) }, enabled = enabled) {
                Text("Deployment ${deployment.getString("id").take(8)} · ${deployment.optString("status")}")
            }
        }
        if ("deployments" in state.nextPages) TextButton(onClick = { vm.loadMore("deployments") }, enabled = enabled) {
            Text("Load more deployments")
        }
        state.inspectedDeployment?.let {
            Text("Inspected record ${it.getString("deployment_id")} · ${it.optString("status")}")
            Text("Robot ${it.optString("robot_id")} · artifact ${it.optString("artifact_id")}")
            Text(if (it.optBoolean("live")) "This record was active when fetched. Reopen to refresh; viewing it sends no command."
                else "${it.optJSONObject("result") ?: "No measured result recorded"}")
        }
    }
}
