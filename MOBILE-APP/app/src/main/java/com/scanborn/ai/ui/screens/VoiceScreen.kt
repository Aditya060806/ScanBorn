package com.scanborn.ai.ui.screens

import androidx.compose.animation.*
import androidx.compose.animation.core.*
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.scanborn.ai.ai.state.AIInferenceState
import com.scanborn.ai.ai.voice.SpeechController
import com.scanborn.ai.ui.components.*
import com.scanborn.ai.ui.theme.*

@Composable
fun VoiceScreen(
    isDarkTheme: Boolean,
    orbState: OrbState,
    aiState: AIInferenceState,
    voice: SpeechController.VoiceUiState,
    onSetListening: () -> Unit,
    onSetIdle: () -> Unit,
    onDismiss: () -> Unit
) {
    // Recognition state, not inference state. These were conflated: isListening was derived
    // from aiState, so the mic read as idle for the whole window between opening it and the
    // model starting to reply — exactly when a user needs to see they are being heard.
    val isListening = voice.listening

    // The live guess while speaking, the final transcript once done, and the model's reply
    // while it streams. All three belong in the same card, in that order of precedence.
    val transcript = when {
        voice.transcript.isNotEmpty() -> voice.transcript
        aiState is AIInferenceState.Responding -> aiState.partialText
        else -> ""
    }

    GradientBackground(darkTheme = isDarkTheme, modifier = Modifier.fillMaxSize()) {
        Column(
            modifier = Modifier.fillMaxSize().statusBarsPadding().padding(horizontal = 24.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Spacer(Modifier.height(24.dp))

            // ── Top bar ───────────────────────────────────────────────
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Box(
                    modifier = Modifier.size(36.dp)
                        .background(if (isDarkTheme) DarkGlass else LightGlass, CircleShape)
                        .border(0.5.dp,
                            if (isDarkTheme) Color.White.copy(0.08f) else Color.White.copy(0.6f),
                            CircleShape)
                        .clickable(onClick = onDismiss),
                    contentAlignment = Alignment.Center
                ) {
                    Icon(Icons.Default.Close, null,
                        tint = if (isDarkTheme) TextSecondary else TextSecondaryLight,
                        modifier = Modifier.size(16.dp))
                }

                Text("∞", fontSize = 22.sp, color = Blue500, fontWeight = FontWeight.Light)

                // Driven by TTS now. This read "Speaking" whenever the model was generating
                // text, while the app had no text-to-speech at all — a label for a feature
                // that did not exist.
                AnimatedVisibility(visible = voice.speaking) {
                    Row(verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text("Speaking", style = MaterialTheme.typography.labelSmall,
                            color = if (isDarkTheme) TextSecondary else TextSecondaryLight)
                        WaveformAnimation(isActive = true,
                            modifier = Modifier.height(16.dp).width(48.dp), color = Blue500)
                    }
                }
            }

            Spacer(Modifier.weight(0.25f))

            AiBodyOrb(orbState = orbState, isDarkTheme = isDarkTheme, size = 220.dp)

            Spacer(Modifier.height(24.dp))

            // ── Status label ──────────────────────────────────────────
            // Recognition status outranks inference status. While the mic is open the model
            // is idle, so keying only on aiState left this saying "Tap mic to speak" over a
            // live microphone.
            val status: Pair<String, Color> = when {
                voice.error != null -> voice.error to ErrorRed
                voice.listening -> "Listening…" to Blue500
                voice.speaking -> "Speaking" to Blue500
                aiState is AIInferenceState.Loading -> "Loading model…" to Blue500
                aiState is AIInferenceState.Thinking -> "Processing…" to Blue500
                aiState is AIInferenceState.Responding -> "ScanBorn is responding" to Blue500
                aiState is AIInferenceState.Error -> "Something went wrong" to ErrorRed
                else -> "Tap mic to speak" to
                    (if (isDarkTheme) TextSecondary else TextSecondaryLight)
            }

            AnimatedContent(
                targetState = status,
                transitionSpec = { fadeIn(tween(300)) togetherWith fadeOut(tween(300)) },
                label = "voiceStatus"
            ) { (label, tint) ->
                Text(
                    text = label,
                    style = MaterialTheme.typography.titleMedium,
                    color = tint,
                    textAlign = TextAlign.Center,
                    fontWeight = FontWeight.Medium
                )
            }

            Spacer(Modifier.height(20.dp))

            // ── Transcript card ───────────────────────────────────────
            AnimatedVisibility(
                visible = transcript.isNotEmpty(),
                enter = fadeIn(tween(400)) + slideInVertically(tween(400)) { it / 3 },
                exit  = fadeOut(tween(300))
            ) {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(20.dp))
                        .background(if (isDarkTheme) DarkGlass else LightGlass)
                        .border(0.5.dp,
                            if (isDarkTheme) Color.White.copy(0.08f) else Color.White.copy(0.7f),
                            RoundedCornerShape(20.dp))
                        .padding(20.dp)
                ) {
                    Text(transcript, style = MaterialTheme.typography.bodyLarge,
                        color = if (isDarkTheme) TextPrimary else TextPrimaryLight,
                        textAlign = TextAlign.Center, lineHeight = 26.sp)
                }
            }

            Spacer(Modifier.weight(0.35f))

            // ── Bottom controls ───────────────────────────────────────
            Row(
                modifier = Modifier.fillMaxWidth().navigationBarsPadding().padding(bottom = 24.dp),
                horizontalArrangement = Arrangement.SpaceEvenly,
                verticalAlignment = Alignment.CenterVertically
            ) {
                ControlButton(
                    icon = Icons.Default.Close, isDarkTheme = isDarkTheme,
                    tint = if (isDarkTheme) TextSecondary else TextSecondaryLight,
                    onClick = onSetIdle
                )

                Box(modifier = Modifier.width(120.dp).height(40.dp), contentAlignment = Alignment.Center) {
                    if (isListening) {
                        WaveformAnimation(isActive = true, modifier = Modifier.fillMaxSize(), color = Blue500)
                    }
                }

                Box(
                    modifier = Modifier
                        .size(56.dp)
                        .background(
                            if (isListening)
                                Brush.linearGradient(listOf(Blue500, Color(0xFF8B5CF6)))
                            else
                                Brush.linearGradient(listOf(
                                    if (isDarkTheme) DarkSurface else LightSurface,
                                    if (isDarkTheme) DarkSurfaceElevated else LightSurfaceElevated
                                )),
                            CircleShape
                        )
                        .clickable { if (isListening) onSetIdle() else onSetListening() },
                    contentAlignment = Alignment.Center
                ) {
                    Icon(
                        if (isListening) Icons.Default.Pause else Icons.Default.Mic,
                        null,
                        tint = if (isListening) Color.White
                               else if (isDarkTheme) TextPrimary else TextPrimaryLight,
                        modifier = Modifier.size(24.dp)
                    )
                }
            }
        }
    }
}

@Composable
private fun ControlButton(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    isDarkTheme: Boolean,
    tint: Color,
    onClick: () -> Unit
) {
    Box(
        modifier = Modifier
            .size(48.dp)
            .background(if (isDarkTheme) DarkGlass else LightGlass, CircleShape)
            .border(0.5.dp,
                if (isDarkTheme) Color.White.copy(0.08f) else Color.White.copy(0.6f),
                CircleShape)
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center
    ) {
        Icon(icon, null, tint = tint, modifier = Modifier.size(20.dp))
    }
}
