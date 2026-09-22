package com.scanborn.ai.ui.screens

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import android.content.pm.ActivityInfo
import android.content.pm.PackageManager
import android.opengl.GLSurfaceView
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.TextButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import com.scanborn.ai.ai.repository.AIRepository
import com.scanborn.ai.data.OrchestratorPreference
import com.scanborn.ai.scan.ScanController
import com.scanborn.ai.ui.components.GradientBackground
import com.scanborn.ai.ui.theme.TextPrimary
import com.scanborn.ai.ui.theme.TextPrimaryLight
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlin.math.roundToInt

/**
 * Scan a space: registered on the home marker, uploaded to the orchestrator set in Settings
 * (docs/superpowers/plans/2026-09-15-phase-5-capture.md).
 */
@Composable
fun ScanScreen(isDarkTheme: Boolean, onNavigateBack: () -> Unit, onOpenSettings: () -> Unit, onInspect: (String) -> Unit = {}) {
    val context = LocalContext.current
    val activity = context as? ComponentActivity ?: return
    val preference = remember { OrchestratorPreference(context.applicationContext) }
    val url by preference.url.collectAsState(initial = null)
    val verified by preference.verifiedUrl.collectAsState(initial = null)
    var cameraGranted by remember {
        mutableStateOf(ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED)
    }
    var cameraRequested by rememberSaveable { mutableStateOf(false) }
    val askCamera = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        cameraRequested = true
        cameraGranted = it
    }
    DisposableEffect(activity) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                cameraGranted = ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
                    PackageManager.PERMISSION_GRANTED
            }
        }
        activity.lifecycle.addObserver(observer)
        onDispose { activity.lifecycle.removeObserver(observer) }
    }

    // A loaded model holds ~1.15 GiB that ARCore needs while scanning; it reloads on the next
    // AI request (plan decision 12).
    var captureReady by remember { mutableStateOf(false) }
    var captureError by remember { mutableStateOf<String?>(null) }
    var captureAttempt by remember { mutableStateOf(0) }
    LaunchedEffect(captureAttempt) {
        val repository = AIRepository.getInstance(context.applicationContext)
        captureError = null
        try {
            withContext(Dispatchers.IO) { repository.beginCapture() }
            captureReady = true
            kotlinx.coroutines.awaitCancellation()
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (e: Exception) {
            captureError = "Could not release the AI model for scanning: ${e.message ?: "unknown error"}"
        } finally {
            captureReady = false
            repository.endCapture()
        }
    }

    val savedUrl = url
    when {
        savedUrl == null || verified == null -> Unit // preferences still loading
        savedUrl.isEmpty() || verified != savedUrl -> Blocked(
            isDarkTheme,
            "Set the orchestrator's address in Settings and tap Save and test connection first. " +
                "Scans upload there.",
            "Open Settings", onOpenSettings, onNavigateBack,
        )
        !cameraGranted -> Blocked(
            isDarkTheme, "Scanning needs camera access. If permission was disabled, enable it in app settings.",
            if (cameraRequested && !activity.shouldShowRequestPermissionRationale(Manifest.permission.CAMERA))
                "Open app settings" else "Allow camera",
            {
                if (cameraRequested && !activity.shouldShowRequestPermissionRationale(Manifest.permission.CAMERA)) {
                    context.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                        Uri.parse("package:${context.packageName}")))
                } else askCamera.launch(Manifest.permission.CAMERA)
            }, onNavigateBack,
        )
        captureError != null -> Blocked(isDarkTheme, captureError!!, "Retry model release",
            { captureAttempt++ }, onNavigateBack)
        !captureReady -> Text("Releasing model memory for the camera…")
        else -> ScanView(activity, savedUrl, onNavigateBack, onInspect)
    }
}

@Composable
private fun Blocked(
    isDarkTheme: Boolean, message: String, action: String, onAction: () -> Unit, onBack: () -> Unit,
) {
    GradientBackground(darkTheme = isDarkTheme, modifier = Modifier.fillMaxSize()) {
        Column(
            modifier = Modifier.fillMaxSize().statusBarsPadding().padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp, Alignment.CenterVertically),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(message, style = MaterialTheme.typography.bodyLarge,
                color = if (isDarkTheme) TextPrimary else TextPrimaryLight)
            Button(onClick = onAction) { Text(action) }
            OutlinedButton(onClick = onBack) { Text("Back") }
        }
    }
}

@Composable
private fun ScanView(activity: ComponentActivity, baseUrl: String, onNavigateBack: () -> Unit, onInspect: (String) -> Unit) {
    val controller = remember(baseUrl) { ScanController(activity.applicationContext, baseUrl) }
    val status by controller.status.collectAsState()
    var confirmDiscard by remember(status.savedScan) { mutableStateOf(false) }
    if (confirmDiscard) {
        AlertDialog(onDismissRequest = { confirmDiscard = false },
            title = { Text("Discard saved scan?") },
            text = { Text("This deletes this scan's pending frames from this phone. Frames already uploaded remain on the server.") },
            confirmButton = { TextButton(onClick = {
                confirmDiscard = false
                controller.discard(activity)
            }) { Text("Discard pending frames") } },
            dismissButton = { TextButton(onClick = { confirmDiscard = false }) { Text("Keep scan") } })
    }
    // ARCore and the camera follow the activity: they pause when the app leaves the screen.
    // Leaving the route instead is handled by onDispose below.
    val lifecycleOwner = activity
    val surface = remember { arrayOfNulls<GLSurfaceView>(1) }

    DisposableEffect(lifecycleOwner, controller) {
        val orientation = activity.requestedOrientation
        activity.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
        val observer = LifecycleEventObserver { _, event ->
            when (event) {
                Lifecycle.Event.ON_RESUME -> {
                    controller.resume(activity)
                    surface[0]?.onResume()
                }
                Lifecycle.Event.ON_PAUSE -> {
                    surface[0]?.onPause()
                    controller.pause()
                }
                else -> Unit
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose {
            lifecycleOwner.lifecycle.removeObserver(observer)
            surface[0]?.onPause()
            controller.pause()
            controller.close()
            activity.requestedOrientation = orientation
        }
    }

    Box(modifier = Modifier.fillMaxSize().background(Color.Black)) {
        AndroidView(
            modifier = Modifier.fillMaxSize(),
            factory = { context ->
                GLSurfaceView(context).apply {
                    preserveEGLContextOnPause = true
                    setEGLContextClientVersion(2)
                    setEGLConfigChooser(8, 8, 8, 8, 16, 0)
                    setRenderer(controller)
                    renderMode = GLSurfaceView.RENDERMODE_CONTINUOUSLY
                    keepScreenOn = true
                    surface[0] = this
                }
            },
        )

        Column(
            modifier = Modifier
                .fillMaxWidth()
                .statusBarsPadding()
                .padding(16.dp)
                .background(Color.Black.copy(alpha = 0.6f), RoundedCornerShape(14.dp))
                .padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            val white = MaterialTheme.typography.bodyMedium
            Text(status.message, style = white, color = Color.White)
            val active = status.phase == ScanController.Phase.SCANNING ||
                status.phase == ScanController.Phase.FINISHING
            if (active || status.phase == ScanController.Phase.DONE || status.phase == ScanController.Phase.FAILED) {
                Text("${status.kept} accepted · ${status.uploaded} uploaded" +
                    (if (status.pending > 0) " · ${status.pending} waiting" else "") +
                    (if (status.rejected > 0) " · ${status.rejected} refused" else "") +
                    (if (status.dropped > 0) " · ${status.dropped} dropped" else ""),
                    style = MaterialTheme.typography.bodySmall, color = Color.White)
                val skips = status.skipped.filterValues { it > 0 }.entries
                    .joinToString(" · ") { "${it.value} ${it.key}" }
                if (skips.isNotEmpty()) {
                    Text("skipped: $skips", style = MaterialTheme.typography.bodySmall, color = Color.White)
                }
            }
            if (active) {
                Text("depth kept ${(status.keptShare * 100).roundToInt()} % · ${status.tracking}" +
                    (status.depthMode?.let { " · $it" } ?: ""),
                    style = MaterialTheme.typography.bodySmall, color = Color.White)
            } else if (status.tracking.isNotEmpty() && status.phase == ScanController.Phase.READY) {
                Text(status.tracking, style = MaterialTheme.typography.bodySmall, color = Color.White)
            }
        }

        Column(
            modifier = Modifier.align(Alignment.BottomCenter).navigationBarsPadding().padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            when (status.phase) {
                ScanController.Phase.READY -> Button(onClick = controller::requestStart) { Text("Start") }
                ScanController.Phase.SCANNING -> Button(onClick = controller::finish) { Text("Finish scan") }
                ScanController.Phase.FINISHING -> Button(onClick = {}, enabled = false) { Text("Uploading…") }
                ScanController.Phase.DONE -> Button(onClick = { status.scanId?.let(onInspect) }) { Text("Reconstruct and inspect") }
                ScanController.Phase.FAILED -> if (status.scanId != null) {
                    Button(onClick = controller::finish) { Text("Retry upload") }
                    if (status.uploaded > 0 && (status.pending > 0 || status.rejected > 0 || status.dropped > 0)) {
                        OutlinedButton(onClick = { controller.finish(true) }) { Text("Finish with uploaded frames") }
                    }
                }
                else -> Unit
            }
            if (status.savedScan != null && status.phase != ScanController.Phase.FINISHING) {
                OutlinedButton(onClick = { confirmDiscard = true }) { Text("Discard saved scan", color = Color.White) }
            }
            val done = status.phase == ScanController.Phase.DONE || status.phase == ScanController.Phase.FAILED
            OutlinedButton(onClick = onNavigateBack, enabled = status.phase != ScanController.Phase.FINISHING) {
                Text(if (done) "Close" else "Cancel", color = Color.White)
            }
        }
    }
}
