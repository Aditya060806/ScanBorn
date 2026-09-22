package com.scanborn.ai.ui.screens

import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import com.scanborn.ai.spaces.SpacesState
import com.scanborn.ai.spaces.SpacesViewModel

@Composable
fun StageRecoveryControls(state: SpacesState, vm: SpacesViewModel) {
    if (!state.pendingStage) return
    Text("Unresolved processing stage", style = MaterialTheme.typography.titleMedium)
    Text(state.stageStatus)
    Text("Check the saved result first. Retry sends the original inputs and request identity; it does not create a new request.")
    val enabled = state.online && !state.busy
    OutlinedButton(onClick = { vm.recoverStage() }, enabled = enabled) { Text("Check saved stage result") }
    if (state.stageStatus in listOf("failed", "interrupted")) {
        OutlinedButton(onClick = vm::acknowledgeStageFailure, enabled = enabled) { Text("Acknowledge stage failure") }
    } else {
        OutlinedButton(onClick = { vm.recoverStage(true) }, enabled = enabled && state.stageStatus != "running") {
            Text("Retry original stage request")
        }
    }
}
