package com.scanborn.ai.ui.screens

import android.Manifest
import android.content.pm.PackageManager
import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.lifecycle.viewmodel.compose.viewModel
import com.scanborn.ai.ai.state.AIInferenceState
import com.scanborn.ai.ai.state.InferenceStats
import com.scanborn.ai.ui.components.GradientBackground
import com.scanborn.ai.ui.components.GlassCard
import com.scanborn.ai.ui.theme.*
import com.scanborn.ai.viewmodel.ChatViewModel

@Composable
fun SettingsScreen(isDarkTheme: Boolean, bottomPadding: Dp, onToggleTheme: () -> Unit) {
    val chatViewModel: ChatViewModel = viewModel()
    val aiState by chatViewModel.aiState.collectAsState()
    val scroll = rememberScrollState()
    val context = LocalContext.current

    // Stats live on the Responding state, so they vanish the moment generation ends. Hold
    // the last measured set so the panel keeps reporting real numbers between runs rather
    // than snapping back to "not measured yet".
    var lastStats by remember { mutableStateOf(InferenceStats()) }
    LaunchedEffect(aiState) {
        (aiState as? AIInferenceState.Responding)?.stats
            ?.let { if (it.measured) lastStats = it }
    }

    val micGranted = remember {
        ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
                PackageManager.PERMISSION_GRANTED
    }
    val notifGranted = if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU) {
        remember {
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) ==
                    PackageManager.PERMISSION_GRANTED
        }
    } else {
        true
    }
    val storageGranted = remember {
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU) true
        else ContextCompat.checkSelfPermission(context, Manifest.permission.READ_EXTERNAL_STORAGE) ==
                PackageManager.PERMISSION_GRANTED
    }

    GradientBackground(darkTheme = isDarkTheme, modifier = Modifier.fillMaxSize()) {
        Column(modifier = Modifier.fillMaxSize().statusBarsPadding().verticalScroll(scroll)) {
            Spacer(Modifier.height(16.dp))

            Column(modifier = Modifier.padding(horizontal = 20.dp)) {
                Text("Settings", style = MaterialTheme.typography.headlineMedium,
                    color = if (isDarkTheme) TextPrimary else TextPrimaryLight,
                    fontWeight = FontWeight.Bold)
            }

            Spacer(Modifier.height(24.dp))

            // Profile card
            GlassCard(darkTheme = isDarkTheme, modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                    Box(
                        modifier = Modifier.size(50.dp)
                            .background(
                                Brush.linearGradient(listOf(Blue500, Blue400)),
                                CircleShape
                            ),
                        contentAlignment = Alignment.Center
                    ) {
                        Text("∞", fontSize = 21.sp, color = Color.White, fontWeight = FontWeight.Bold)
                    }
                    Column {
                        Text("ScanBorn User", style = MaterialTheme.typography.titleMedium,
                            color = if (isDarkTheme) TextPrimary else TextPrimaryLight,
                            fontWeight = FontWeight.SemiBold)
                        Text("AI Command Center", style = MaterialTheme.typography.bodySmall,
                            color = if (isDarkTheme) TextSecondary else TextSecondaryLight)
                    }
                    Spacer(Modifier.weight(1f))
                    Icon(Icons.Default.ChevronRight, null,
                        tint = if (isDarkTheme) TextSecondary else TextSecondaryLight,
                        modifier = Modifier.size(17.dp))
                }
            }

            Spacer(Modifier.height(20.dp))

            SettingsSection("Appearance", isDarkTheme) {
                SettingsToggle(Icons.Default.DarkMode, "Dark Mode",
                    if (isDarkTheme) "Dark theme active" else "Light theme active",
                    isDarkTheme, onToggleTheme, isDarkTheme)
            }

            Spacer(Modifier.height(12.dp))

            SettingsSection("AI Engine", isDarkTheme) {
                val (modelLabel, modelColor) = when (aiState) {
                    is AIInferenceState.Idle       -> "Ready" to SuccessGreen
                    is AIInferenceState.Loading    -> "Loading..." to WarnAmber
                    is AIInferenceState.Thinking   -> "Thinking..." to Blue500
                    is AIInferenceState.Responding -> "Responding..." to Blue500
                    is AIInferenceState.Error      -> "Error" to ErrorRed
                }
                SettingsRow(Icons.Default.Memory, "Local Model", modelLabel, modelColor, isDarkTheme)
                SettingsDivider(isDarkTheme)

                // Measured, not claimed. The rows here used to read "Response Mode:
                // Balanced" and "Language: English", neither of which was wired to
                // anything — so the one screen that looks like a diagnostics panel showed
                // no diagnostics. Throughput is the number that decides whether this
                // device needs a bigger machine in the loop, so it belongs on screen
                // rather than only in logcat. lastStats is captured above via
                // LaunchedEffect — never assigned during composition.
                val cores = remember { Runtime.getRuntime().availableProcessors() }

                if (lastStats.measured) {
                    SettingsRow(Icons.Default.Speed, "Prefill",
                        "%.0f tok/s · %d tok in %d ms".format(
                            lastStats.prefillTokensPerSec, lastStats.promptTokens,
                            lastStats.prefillMs),
                        Blue500, isDarkTheme)
                    SettingsDivider(isDarkTheme)
                    SettingsRow(Icons.Default.Bolt, "Decode",
                        "%.1f tok/s · %d tok in %d ms".format(
                            lastStats.decodeTokensPerSec, lastStats.genTokens,
                            lastStats.decodeMs),
                        Blue500, isDarkTheme)
                } else {
                    // Never show zeros — an unmeasured engine must not read as a slow one.
                    SettingsRow(Icons.Default.Speed, "Throughput", "not measured yet",
                        if (isDarkTheme) TextSecondary else TextSecondaryLight, isDarkTheme)
                }
                SettingsDivider(isDarkTheme)
                SettingsRow(Icons.Default.DeveloperBoard, "Compute",
                    "CPU · $cores cores", Blue500, isDarkTheme)
            }

            Spacer(Modifier.height(12.dp))

            SettingsSection("Permissions", isDarkTheme) {
                SettingsRowBadge(Icons.Default.Mic, "Microphone", "Required for voice", micGranted, isDarkTheme)
                SettingsDivider(isDarkTheme)
                SettingsRowBadge(Icons.Default.Notifications, "Notifications", "For AI alerts", notifGranted, isDarkTheme)
                SettingsDivider(isDarkTheme)
                SettingsRowBadge(Icons.Default.FolderOpen, "Storage", "For file analyzer", storageGranted, isDarkTheme)
            }

            Spacer(Modifier.height(12.dp))

            SettingsSection("About", isDarkTheme) {
                SettingsRow(Icons.Default.Info, "Version", "1.0.0",
                    if (isDarkTheme) TextSecondary else TextSecondaryLight, isDarkTheme)
                SettingsDivider(isDarkTheme)
                SettingsRow(Icons.Default.Code, "Build", "Production Foundation",
                    if (isDarkTheme) TextSecondary else TextSecondaryLight, isDarkTheme)
                SettingsDivider(isDarkTheme)
                SettingsRow(Icons.Default.Memory, "Engine", "ScanBorn-X1",
                    if (isDarkTheme) TextSecondary else TextSecondaryLight, isDarkTheme)
            }

            Spacer(Modifier.height(48.dp))

            Column(modifier = Modifier.fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally) {
                Text("∞", fontSize = 24.sp,
                    color = if (isDarkTheme) TextDisabled else TextSecondaryLight.copy(0.4f))
                Spacer(Modifier.height(4.dp))
                Text("ScanBorn AI · v1.0.0", style = MaterialTheme.typography.labelSmall,
                    color = if (isDarkTheme) TextDisabled else TextSecondaryLight.copy(0.4f))
            }

            Spacer(Modifier.height(bottomPadding + 16.dp))
        }
    }
}

@Composable
private fun SettingsSection(title: String, isDarkTheme: Boolean, content: @Composable ColumnScope.() -> Unit) {
    Column(modifier = Modifier.padding(horizontal = 20.dp)) {
        Text(title.uppercase(), style = MaterialTheme.typography.labelSmall,
            color = if (isDarkTheme) TextSecondary else TextSecondaryLight,
            fontWeight = FontWeight.Medium, letterSpacing = 1.sp,
            modifier = Modifier.padding(bottom = 8.dp, start = 4.dp))
        GlassCard(darkTheme = isDarkTheme, modifier = Modifier.fillMaxWidth()) {
            content()
        }
    }
}

@Composable
private fun SettingsDivider(isDarkTheme: Boolean) {
    HorizontalDivider(
        modifier = Modifier.padding(vertical = 4.dp),
        color = if (isDarkTheme) Color.White.copy(0.05f) else Color.Black.copy(0.05f)
    )
}

@Composable
private fun SettingsToggle(
    icon: ImageVector, title: String, subtitle: String,
    checked: Boolean, onToggle: () -> Unit, isDarkTheme: Boolean
) {
    Row(modifier = Modifier.fillMaxWidth().clickable(onClick = onToggle),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Box(modifier = Modifier.size(36.dp)
            .background(Blue500.copy(0.15f), RoundedCornerShape(10.dp)),
            contentAlignment = Alignment.Center) {
            Icon(icon, null, tint = Blue500, modifier = Modifier.size(18.dp))
        }
        Column(modifier = Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyLarge,
                color = if (isDarkTheme) TextPrimary else TextPrimaryLight, fontWeight = FontWeight.Medium)
            Text(subtitle, style = MaterialTheme.typography.bodySmall,
                color = if (isDarkTheme) TextSecondary else TextSecondaryLight)
        }
        Switch(checked = checked, onCheckedChange = { onToggle() },
            colors = SwitchDefaults.colors(
                checkedTrackColor = Blue500,
                checkedThumbColor = Color.White
            ))
    }
}

@Composable
private fun SettingsRow(icon: ImageVector, title: String, value: String,
                        iconTint: Color, isDarkTheme: Boolean) {
    Row(modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Box(modifier = Modifier.size(36.dp)
            .background(iconTint.copy(0.12f), RoundedCornerShape(10.dp)),
            contentAlignment = Alignment.Center) {
            Icon(icon, null, tint = iconTint, modifier = Modifier.size(18.dp))
        }
        Text(title, style = MaterialTheme.typography.bodyLarge,
            color = if (isDarkTheme) TextPrimary else TextPrimaryLight,
            fontWeight = FontWeight.Medium, modifier = Modifier.weight(1f))
        Text(value, style = MaterialTheme.typography.bodyMedium,
            color = if (isDarkTheme) TextSecondary else TextSecondaryLight)
    }
}

@Composable
private fun SettingsRowBadge(icon: ImageVector, title: String, subtitle: String,
                              granted: Boolean, isDarkTheme: Boolean) {
    Row(modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Box(modifier = Modifier.size(36.dp)
            .background(
                if (granted) SuccessGreen.copy(0.12f) else ErrorRed.copy(0.12f),
                RoundedCornerShape(10.dp)),
            contentAlignment = Alignment.Center) {
            Icon(icon, null,
                tint = if (granted) SuccessGreen else ErrorRed,
                modifier = Modifier.size(18.dp))
        }
        Column(modifier = Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyLarge,
                color = if (isDarkTheme) TextPrimary else TextPrimaryLight, fontWeight = FontWeight.Medium)
            Text(subtitle, style = MaterialTheme.typography.bodySmall,
                color = if (isDarkTheme) TextSecondary else TextSecondaryLight)
        }
        Box(modifier = Modifier
            .background(
                if (granted) SuccessGreen.copy(0.12f) else ErrorRed.copy(0.12f),
                RoundedCornerShape(6.dp))
            .padding(horizontal = 8.dp, vertical = 3.dp)) {
            Text(if (granted) "Granted" else "Denied",
                style = MaterialTheme.typography.labelSmall,
                color = if (granted) SuccessGreen else ErrorRed)
        }
    }
}
