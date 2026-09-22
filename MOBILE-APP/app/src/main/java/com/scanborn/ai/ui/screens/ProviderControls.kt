package com.scanborn.ai.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.scanborn.ai.spaces.SpacesViewModel
import org.json.JSONObject

@Composable
fun ProviderControls(report: JSONObject?, enabled: Boolean, vm: SpacesViewModel) {
    var expanded by remember { mutableStateOf(false) }
    var drafts by remember(report) { mutableStateOf(listOf("planner", "profiler", "export").associateWith {
        report?.optJSONObject("workloads")?.optJSONObject(it)?.optString("selected") ?: "local"
    }) }
    var consent by remember(report) { mutableStateOf(emptySet<String>()) }
    OutlinedButton(onClick = { expanded = !expanded; if (expanded) vm.refreshProviders() }, enabled = enabled) {
        Text("Processing providers and privacy")
    }
    if (!expanded) return
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Local processing is the default. Credentials stay on the server. Changes apply to subsequent stages.")
        if (report != null) {
            val workloads = report.getJSONObject("workloads")
            for (name in listOf("planner", "profiler", "export")) {
                val entry = workloads.getJSONObject(name)
                OutlinedTextField(value = drafts[name].orEmpty(), enabled = enabled, modifier = Modifier.fillMaxWidth(),
                    label = { Text("$name providers in order, comma separated") }, onValueChange = {
                        drafts = drafts + (name to it); consent = consent - name
                    })
                Text("Available: ${entry.getJSONArray("allowed")}. Current: ${entry.getString("selected")}.")
                Text(if (entry.optBoolean("ready")) "Ready" else entry.optString("error"))
                val remote = drafts[name].orEmpty().split(',').any {
                    report.getJSONObject("provider_locations").optString(it.trim()) == "remote"
                }
                if (remote) {
                    Row {
                        Checkbox(checked = name in consent, enabled = enabled, onCheckedChange = {
                            consent = if (it) consent + name else consent - name
                        })
                        Text("Authorize outgoing ${report.getJSONObject("remote_data_scopes").getJSONArray(name)}")
                    }
                }
            }
            val limits = report.getJSONObject("limits")
            Text("Remote ceiling ${limits.optInt("remote_daily_limit")} requests per UTC day; deadline ${limits.optDouble("deadline_s")} s. Cost is unknown.")
            Text("Remote/QAIRT exports are experiments, not deployable rover bundles.")
            Button(onClick = { vm.saveProviders(drafts, consent) }, enabled = enabled) { Text("Save provider settings") }
        }
        OutlinedButton(onClick = { vm.saveProviders(listOf("planner", "profiler", "export").associateWith { "local" }, emptySet()) }, enabled = enabled) {
            Text("Use local providers for all stages")
        }
    }
}
