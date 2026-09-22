package com.scanborn.ai.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.scanborn.ai.data.OrchestratorPreference
import com.scanborn.ai.scan.OrchestratorUrl
import com.scanborn.ai.ui.theme.ErrorRed
import com.scanborn.ai.ui.theme.SuccessGreen
import com.scanborn.ai.ui.theme.TextPrimary
import com.scanborn.ai.ui.theme.TextPrimaryLight
import com.scanborn.ai.ui.theme.TextSecondary
import com.scanborn.ai.ui.theme.TextSecondaryLight
import kotlinx.coroutines.launch

/** Settings → Orchestrator: where scans upload, and a test that it answers. */
@Composable
fun OrchestratorSettings(isDarkTheme: Boolean) {
    val context = LocalContext.current
    val preference = remember { OrchestratorPreference(context.applicationContext) }
    val saved by preference.url.collectAsState(initial = "")
    val verified by preference.verifiedUrl.collectAsState(initial = "")
    var text by remember(saved) { mutableStateOf(saved) }
    var result by remember { mutableStateOf<Pair<Boolean, String>?>(null) }
    var testing by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val secondary = if (isDarkTheme) TextSecondary else TextSecondaryLight

    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("The laptop running the orchestrator, for example http://192.168.1.20:8000. " +
            "Scans upload there over your own network.",
            style = MaterialTheme.typography.bodySmall, color = secondary)
        OutlinedTextField(
            value = text,
            onValueChange = { text = it; result = null },
            singleLine = true,
            label = { Text("Orchestrator URL") },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            modifier = Modifier.fillMaxWidth(),
        )
        Button(
            enabled = !testing,
            onClick = {
                val url = OrchestratorUrl.normalise(text)
                if (url == null) {
                    result = false to "Not an address: use http://host:port"
                } else {
                    testing = true
                    scope.launch {
                        preference.setUrl(url)
                        text = url
                        val answer = OrchestratorUrl.health(url)
                        if (answer.isSuccess) preference.markVerified(url)
                        result = answer.fold(
                            { true to "Reachable" },
                            { false to "Not reachable: ${it.message ?: it.javaClass.simpleName}" },
                        )
                        testing = false
                    }
                }
            },
        ) { Text(if (testing) "Testing…" else "Save and test connection") }

        val shown = result
        when {
            shown != null -> Text(shown.second, style = MaterialTheme.typography.bodySmall,
                color = if (shown.first) SuccessGreen else ErrorRed)
            saved.isNotEmpty() -> Text(
                if (verified == saved) "Last test: reachable" else "Not tested yet",
                style = MaterialTheme.typography.bodySmall,
                color = if (verified == saved) SuccessGreen else if (isDarkTheme) TextPrimary else TextPrimaryLight,
            )
        }
    }
}
