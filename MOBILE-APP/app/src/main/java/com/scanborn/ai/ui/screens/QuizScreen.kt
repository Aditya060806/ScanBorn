package com.scanborn.ai.ui.screens

import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.scanborn.ai.ui.components.GlassCard
import com.scanborn.ai.ui.components.GradientBackground
import com.scanborn.ai.ui.theme.*
import com.scanborn.ai.viewmodel.QuizUiState
import com.scanborn.ai.viewmodel.QuizViewModel

private val QuizAccent = Color(0xFF10B981)

@Composable
fun QuizScreen(
    isDarkTheme: Boolean,
    bottomPadding: Dp,
    onNavigateBack: () -> Unit,
    vm: QuizViewModel = viewModel()
) {
    val uiState    by vm.uiState.collectAsState()
    val quizText   by vm.quizText.collectAsState()
    val sourceText by vm.sourceText.collectAsState()
    val showSavedBanner by vm.showSavedBanner.collectAsState()

    val imageLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.GetContent()
    ) { uri: Uri? -> uri?.let { vm.generateFromImage(it) } }

    GradientBackground(darkTheme = isDarkTheme, modifier = Modifier.fillMaxSize()) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .statusBarsPadding()
                .padding(bottom = bottomPadding)
        ) {
            SavedBanner(showSavedBanner)
            FeatureHeader(
                title       = "Quiz Generator",
                isDarkTheme = isDarkTheme,
                uiState     = when (uiState) {
                    is QuizUiState.Idle       -> "Ready"
                    is QuizUiState.Extracting -> "Reading image..."
                    is QuizUiState.Generating -> "Generating..."
                    is QuizUiState.Done       -> "Done"
                    is QuizUiState.Error      -> "Error"
                },
                dotColor    = when (uiState) {
                    is QuizUiState.Error      -> ErrorRed
                    is QuizUiState.Done       -> SuccessGreen
                    is QuizUiState.Generating -> QuizAccent
                    is QuizUiState.Extracting -> WarnAmber
                    else                      -> if (isDarkTheme) TextSecondary else TextSecondaryLight
                },
                onBack      = onNavigateBack,
                showReset   = uiState != QuizUiState.Idle,
                onReset     = { vm.reset() }
            )

            when (uiState) {
                is QuizUiState.Idle -> QuizIdleState(
                    isDarkTheme   = isDarkTheme,
                    onGenerate    = { text -> vm.generateFromText(text) },
                    onPickImage   = { imageLauncher.launch("image/*") }
                )
                is QuizUiState.Extracting -> CenteredSpinner("Reading image...", WarnAmber, isDarkTheme)
                is QuizUiState.Generating, is QuizUiState.Done -> {
                    QuizResultBody(
                        quizText    = quizText,
                        isGenerating = uiState is QuizUiState.Generating,
                        isDarkTheme = isDarkTheme,
                        onStop      = { vm.stop() }
                    )
                }
                is QuizUiState.Error -> FeatureErrorState(
                    isDarkTheme = isDarkTheme,
                    message     = (uiState as QuizUiState.Error).message,
                    onRetry     = { vm.reset() }
                )
            }
        }
    }
}

@Composable
private fun QuizIdleState(
    isDarkTheme : Boolean,
    onGenerate  : (String) -> Unit,
    onPickImage : () -> Unit
) {
    var inputText by remember { mutableStateOf("") }
    val scroll = rememberScrollState()

    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(scroll).padding(horizontal = 24.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Spacer(Modifier.height(12.dp))
        Box(
            modifier = Modifier.size(80.dp).background(QuizAccent.copy(0.12f), CircleShape),
            contentAlignment = Alignment.Center
        ) {
            Icon(Icons.Default.Quiz, null, tint = QuizAccent, modifier = Modifier.size(36.dp))
        }
        Spacer(Modifier.height(16.dp))
        Text("Quiz Generator", style = MaterialTheme.typography.headlineSmall,
            color = if (isDarkTheme) TextPrimary else TextPrimaryLight,
            fontWeight = FontWeight.SemiBold, textAlign = TextAlign.Center)
        Spacer(Modifier.height(6.dp))
        Text("Paste text or pick an image to generate\n5 multiple choice questions instantly.",
            style = MaterialTheme.typography.bodyMedium,
            color = if (isDarkTheme) TextSecondary else TextSecondaryLight,
            textAlign = TextAlign.Center, lineHeight = 22.sp)

        Spacer(Modifier.height(24.dp))

        // Text input
        GlassCard(darkTheme = isDarkTheme, modifier = Modifier.fillMaxWidth()) {
            Text("Paste your text here", style = MaterialTheme.typography.labelSmall,
                color = if (isDarkTheme) TextSecondary else TextSecondaryLight, letterSpacing = 1.sp)
            Spacer(Modifier.height(8.dp))
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(min = 120.dp)
                    .clip(RoundedCornerShape(12.dp))
                    .background(MaterialTheme.colorScheme.surfaceVariant)
                    .padding(12.dp)
            ) {
                BasicTextField(
                    value = inputText,
                    onValueChange = { if (it.length <= 800) inputText = it },
                    modifier = Modifier.fillMaxWidth(),
                    textStyle = MaterialTheme.typography.bodySmall.copy(
                        color = MaterialTheme.colorScheme.onSurface
                    ),
                    cursorBrush = SolidColor(QuizAccent),
                    maxLines = 10,
                    decorationBox = { inner ->
                        if (inputText.isEmpty()) {
                            Text("Type or paste up to 800 characters…",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        inner()
                    }
                )
            }
            Spacer(Modifier.height(4.dp))
            Text("${inputText.length}/800", style = MaterialTheme.typography.labelSmall,
                color = if (inputText.length >= 800) WarnAmber
                else if (isDarkTheme) TextSecondary else TextSecondaryLight,
                modifier = Modifier.align(Alignment.End))
        }

        Spacer(Modifier.height(12.dp))

        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            PickButton("From Image", Icons.Default.Image, QuizAccent, isDarkTheme,
                Modifier.weight(1f), onPickImage)
            Box(
                modifier = Modifier.weight(1f)
                    .clip(RoundedCornerShape(12.dp))
                    .background(if (inputText.isNotBlank()) QuizAccent else QuizAccent.copy(0.3f))
                    .clickable(enabled = inputText.isNotBlank()) { onGenerate(inputText) }
                    .padding(vertical = 14.dp),
                contentAlignment = Alignment.Center
            ) {
                Row(verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Icon(Icons.Default.AutoAwesome, null, tint = Color.White, modifier = Modifier.size(18.dp))
                    Text("Generate Quiz", style = MaterialTheme.typography.labelLarge,
                        color = Color.White, fontWeight = FontWeight.SemiBold)
                }
            }
        }
        Spacer(Modifier.height(24.dp))
    }
}

@Composable
private fun QuizResultBody(
    quizText    : String,
    isGenerating: Boolean,
    isDarkTheme : Boolean,
    onStop      : () -> Unit
) {
    val scroll = rememberScrollState()
    LaunchedEffect(quizText.length) { if (isGenerating) scroll.animateScrollTo(scroll.maxValue) }

    Column(modifier = Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        StreamingResultCard(
            text        = quizText,
            isStreaming = isGenerating,
            isDarkTheme = isDarkTheme,
            scrollState = scroll,
            onStop      = onStop,
            modifier    = Modifier.weight(1f),
            accentColor = QuizAccent,
            streamingLabel = "Generating quiz..."
        )
    }
}
