package com.scanborn.ai.spaces

import android.content.Intent
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Rule
import org.junit.Test
import java.io.File
import java.util.UUID

class RecoveryScreenTest {
    @get:Rule val compose = createEmptyComposeRule()

    @Test fun nativeStopSurvivesRendererErrorAndActivityRecreation() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val token = UUID.randomUUID().toString()
        val root = File(context.cacheDir, "screen-recovery-$token")
        RecoveryHttpTest.Server().use { server ->
            PendingDeploymentRequests(File(root, "pending-deployments"))
                .begin(server.url, "robot-1", "artifact-1")
            val intent = Intent(context, RecoveryTestActivity::class.java)
                .putExtra("test_root", token).putExtra("test_server", server.url)
            try {
                ActivityScenario.launch<RecoveryTestActivity>(intent).use { scenario ->
                    compose.waitUntil(15000) { server.calls.any { it.contains("/deploy/requests/") } }
                    scenario.onActivity { it.spaces.viewerMessage("""{"type":"renderer","error":"Injected context loss"}""") }
                    compose.onNodeWithText("Stop").assertIsDisplayed().assertIsEnabled().performClick()
                    compose.waitUntil(10000) { server.calls.contains("POST /robots/robot-1/estop") }
                    scenario.recreate()
                    compose.onNodeWithText("Stop").assertIsDisplayed().assertIsEnabled()
                    compose.onNodeWithText("E-STOP").assertIsDisplayed().assertIsEnabled().performClick()
                    compose.waitUntil(10000) { server.calls.count { it == "POST /robots/robot-1/estop" } >= 2 }
                    org.junit.Assert.assertTrue(server.calls.filter { it.startsWith("POST") }
                        .all { it.endsWith("/estop") })
                }
            } finally { root.deleteRecursively() }
        }
    }
}
