package com.scanborn.ai.ui.navigation

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.*
import com.scanborn.ai.ai.state.AIInferenceState
import com.scanborn.ai.ui.components.toOrbState
import com.scanborn.ai.ui.screens.*
import com.scanborn.ai.viewmodel.ChatViewModel
import com.scanborn.ai.viewmodel.LibraryViewModel
import com.scanborn.ai.viewmodel.VoiceViewModel

sealed class Screen(val route: String, val label: String, val icon: ImageVector) {
    object Dashboard : Screen("dashboard", "Home",     Icons.Default.Home)
    object Chat      : Screen("chat",      "Chat",     Icons.Default.Chat)
    object Tools     : Screen("tools",     "Tools",    Icons.Default.Apps)
    object Library   : Screen("library",   "Library",  Icons.Default.AutoStories)
    object Settings  : Screen("settings",  "Settings", Icons.Default.Settings)
}

private val navItems = listOf(Screen.Dashboard, Screen.Chat, Screen.Tools, Screen.Library, Screen.Settings)

@Composable
fun AppNavigation(isDarkTheme: Boolean, onToggleTheme: () -> Unit) {
    val navController = rememberNavController()
    val backStack     by navController.currentBackStackEntryAsState()
    val currentRoute  = backStack?.destination?.route
    val showNav       = currentRoute in navItems.map { it.route }

    val context = LocalContext.current
    val chatViewModel: ChatViewModel = viewModel()
    val libraryViewModel: LibraryViewModel = viewModel()
    val aiState by chatViewModel.aiState.collectAsState()
    val orbState = aiState.toOrbState()

    // ── Voice ─────────────────────────────────────────────────────────────────
    // The recogniser used to be built and torn down here, inside a composable, with its
    // listener state going nowhere. It now lives in VoiceViewModel, which is a lifetime that
    // actually matches a microphone and survives configuration changes.
    val voiceViewModel: VoiceViewModel = viewModel()
    val voiceState by voiceViewModel.state.collectAsState()

    val micPermLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted -> if (granted) voiceViewModel.startListening() }

    val startListening: () -> Unit = {
        val hasMic = ContextCompat.checkSelfPermission(
            context, Manifest.permission.RECORD_AUDIO
        ) == PackageManager.PERMISSION_GRANTED
        // Start listening as soon as permission is granted, rather than making the user tap
        // the mic a second time after the dialog.
        if (hasMic) voiceViewModel.startListening()
        else micPermLauncher.launch(Manifest.permission.RECORD_AUDIO)
    }

    // A completed utterance is a normal chat turn. It used to call startFromSuggestion,
    // which replaces the message list — so speaking silently deleted the conversation.
    LaunchedEffect(Unit) {
        voiceViewModel.transcripts.collect { chatViewModel.sendVoiceInput(it) }
    }

    // Read the reply aloud once generation finishes. Keyed on aiState so it fires on the
    // Responding -> Idle edge, and guarded on the last message being the assistant's so a
    // rejection notice or a cleared chat is not spoken.
    var wasResponding by remember { mutableStateOf(false) }
    LaunchedEffect(aiState) {
        val responding = aiState is AIInferenceState.Responding
        if (wasResponding && !responding) {
            chatViewModel.messages.value.lastOrNull()
                ?.takeIf { !it.isUser && it.text.isNotBlank() }
                ?.let { voiceViewModel.speak(it.text) }
        }
        wasResponding = responding
    }

    Scaffold(
        containerColor = Color.Transparent,
        contentWindowInsets = WindowInsets(0, 0, 0, 0),
        bottomBar = {
            if (showNav) {
                NavigationBar(
                    containerColor = MaterialTheme.colorScheme.surface,
                    tonalElevation = 0.dp,
                    windowInsets   = WindowInsets.navigationBars
                ) {
                    navItems.forEach { screen ->
                        val selected = currentRoute == screen.route
                        NavigationBarItem(
                            selected = selected,
                            onClick  = {
                                navController.navigate(screen.route) {
                                    popUpTo(navController.graph.findStartDestination().id) {
                                        saveState = true
                                    }
                                    launchSingleTop = true
                                    restoreState    = true
                                }
                            },
                            icon  = { Icon(screen.icon, screen.label, modifier = Modifier.size(22.dp)) },
                            label = { Text(screen.label, style = MaterialTheme.typography.labelSmall) },
                            colors = NavigationBarItemDefaults.colors(
                                selectedIconColor   = Color(0xFF4F8CFF),
                                selectedTextColor   = Color(0xFF4F8CFF),
                                indicatorColor      = Color(0xFF4F8CFF).copy(alpha = 0.10f),
                                unselectedIconColor = MaterialTheme.colorScheme.onSurfaceVariant,
                                unselectedTextColor = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        )
                    }
                }
            }
        }
    ) { innerPadding ->
        NavHost(
            navController    = navController,
            startDestination = "splash",
            enterTransition  = { fadeIn(tween(220)) },
            exitTransition   = { fadeOut(tween(220)) }
        ) {
            composable("splash") {
                SplashScreen {
                    navController.navigate(Screen.Dashboard.route) {
                        popUpTo("splash") { inclusive = true }
                    }
                }
            }
            composable(Screen.Dashboard.route) {
                DashboardScreen(
                    isDarkTheme            = isDarkTheme,
                    orbState               = orbState,
                    bottomPadding          = innerPadding.calculateBottomPadding(),
                    onNavigateToChat       = { navController.navigate(Screen.Chat.route) },
                    onNavigateToVoice      = { navController.navigate("voice") },
                    onOrbTap               = { navController.navigate(Screen.Chat.route) },
                    onNavigateToCircle     = { navController.navigate("circle_learn") },
                    onNavigateToOcr        = { navController.navigate("ocr") },
                    onNavigateToPdf        = { navController.navigate("pdf_summary") },
                    onNavigateToQuiz       = { navController.navigate("quiz") },
                    onNavigateToScreenshot = { navController.navigate("screenshot") }
                )
            }
            composable(Screen.Chat.route) {
                ChatScreen(
                    isDarkTheme       = isDarkTheme,
                    bottomPadding     = innerPadding.calculateBottomPadding(),
                    onNavigateToVoice = { navController.navigate("voice") },
                    chatViewModel     = chatViewModel
                )
            }
            composable(Screen.Tools.route) {
                ToolsScreen(
                    isDarkTheme            = isDarkTheme,
                    bottomPadding          = innerPadding.calculateBottomPadding(),
                    onNavigateToPdf        = { navController.navigate("pdf_summary") },
                    onNavigateToOcr        = { navController.navigate("ocr") },
                    onNavigateToScreenshot = { navController.navigate("screenshot") },
                    onNavigateToQuiz       = { navController.navigate("quiz") },
                    onNavigateToCircle     = { navController.navigate("circle_learn") }
                )
            }
            composable("pdf_summary") {
                PdfSummaryScreen(
                    isDarkTheme    = isDarkTheme,
                    bottomPadding  = innerPadding.calculateBottomPadding(),
                    onNavigateBack = { navController.popBackStack() }
                )
            }
            composable("ocr") {
                OcrScreen(
                    isDarkTheme    = isDarkTheme,
                    bottomPadding  = innerPadding.calculateBottomPadding(),
                    onNavigateBack = { navController.popBackStack() }
                )
            }
            composable("screenshot") {
                ScreenshotExplainerScreen(
                    isDarkTheme    = isDarkTheme,
                    bottomPadding  = innerPadding.calculateBottomPadding(),
                    onNavigateBack = { navController.popBackStack() }
                )
            }
            composable("quiz") {
                QuizScreen(
                    isDarkTheme    = isDarkTheme,
                    bottomPadding  = innerPadding.calculateBottomPadding(),
                    onNavigateBack = { navController.popBackStack() }
                )
            }
            composable("circle_learn") {
                CircleLearnEntryScreen(
                    isDarkTheme    = isDarkTheme,
                    bottomPadding  = innerPadding.calculateBottomPadding(),
                    onNavigateBack = { navController.popBackStack() }
                )
            }
            composable(Screen.Settings.route) {
                SettingsScreen(
                    isDarkTheme   = isDarkTheme,
                    bottomPadding = innerPadding.calculateBottomPadding(),
                    onToggleTheme = onToggleTheme
                )
            }
            composable(Screen.Library.route) {
                LibraryScreen(
                    isDarkTheme   = isDarkTheme,
                    bottomPadding = innerPadding.calculateBottomPadding(),
                    onOpenEntry   = { /* detail view future */ },
                    vm            = libraryViewModel
                )
            }
            composable("voice") {
                VoiceScreen(
                    isDarkTheme    = isDarkTheme,
                    orbState       = orbState,
                    aiState        = aiState,
                    voice          = voiceState,
                    onSetListening = startListening,
                    onSetIdle      = {
                        // Stop everything the button could plausibly mean: the mic, the
                        // speaker, and the generation in between.
                        voiceViewModel.stopAll()
                        chatViewModel.stopGeneration()
                    },
                    onDismiss      = {
                        voiceViewModel.stopAll()
                        navController.popBackStack()
                    }
                )
            }
        }
    }
}
