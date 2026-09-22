package com.scanborn.ai.spaces

import android.net.Uri
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ViewerBoundaryTest {
    @Test fun untrustedOriginsAndSubframesCannotSelectMissionCoordinates() {
        val selection = """{"type":"selection","kind":"point","position":[1,2]}"""
        assertTrue(acceptsViewerMessage(Uri.parse(VIEWER_ORIGIN), true, selection))
        listOf("http://appassets.androidplatform.net", "https://appassets.androidplatform.net.attacker.test",
            "https://attacker.test", "file:///android_asset/viewer.html", "null").forEach {
            assertFalse(it, acceptsViewerMessage(Uri.parse(it), true, selection))
        }
        assertFalse(acceptsViewerMessage(Uri.parse(VIEWER_ORIGIN), false, selection))
        assertFalse(acceptsViewerMessage(Uri.parse(VIEWER_ORIGIN), true, "x".repeat(16385)))
        assertFalse(acceptsViewerMessage(Uri.parse(VIEWER_ORIGIN), true, null))
    }

    @Test fun sharedViewerIsBundledAndRequiresNoRemoteScript() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val html = context.assets.open("viewer/viewer.html").bufferedReader().use { it.readText() }
        assertTrue(html.contains("id=\"viewer\""))
        assertFalse(html.contains("src=\"http"))
        assertTrue(context.assets.list("viewer/assets")!!.any { it.endsWith(".js") })
    }
}
