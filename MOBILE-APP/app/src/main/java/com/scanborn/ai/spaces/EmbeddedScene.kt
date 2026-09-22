package com.scanborn.ai.spaces

import android.annotation.SuppressLint
import android.net.Uri
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView
import androidx.webkit.WebViewAssetLoader
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.io.File

private const val ORIGIN = VIEWER_ORIGIN

@SuppressLint("SetJavaScriptEnabled")
@Composable
fun EmbeddedScene(scene: JSONObject?, telemetry: JSONObject?, mode: String, modifier: Modifier,
                  onMessage: (String) -> Unit, layers: JSONObject? = null, playback: JSONObject? = null,
                  pointSize: Float = 3f) {
    val callback by rememberUpdatedState(onMessage)
    var webView by remember { mutableStateOf<WebView?>(null) }
    var ready by remember { mutableStateOf(false) }
    AndroidView(modifier = modifier, factory = { context ->
        WebView(context).apply {
            settings.javaScriptEnabled = true
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            settings.domStorageEnabled = false
            settings.mixedContentMode = android.webkit.WebSettings.MIXED_CONTENT_NEVER_ALLOW
            val cache = File(context.filesDir, "viewer-cache").apply { mkdirs() }
            val loader = WebViewAssetLoader.Builder()
                .addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(context))
                .addPathHandler("/cache/", WebViewAssetLoader.InternalStoragePathHandler(context, cache))
                .build()
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest) = true
                override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse {
                    return loader.shouldInterceptRequest(request.url) ?: WebResourceResponse(
                        "text/plain", "UTF-8", 403, "Blocked", emptyMap(), ByteArrayInputStream(ByteArray(0)))
                }
                override fun onRenderProcessGone(view: WebView, detail: android.webkit.RenderProcessGoneDetail): Boolean {
                    callback(JSONObject().put("type", "renderer").put("error", "Renderer stopped. Native mission controls remain active.").toString())
                    ready = false
                    (view.parent as? android.view.ViewGroup)?.removeView(view)
                    view.destroy()
                    webView = null
                    return true
                }
            }
            if (WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) {
                WebViewCompat.addWebMessageListener(this, "scanborn", setOf(ORIGIN)) { _, message, source, mainFrame, _ ->
                    if (acceptsViewerMessage(source, mainFrame, message.data)) {
                        val raw = message.data ?: return@addWebMessageListener
                        if (runCatching { JSONObject(raw).optString("type") == "ready" }.getOrDefault(false)) ready = true
                        else callback(raw)
                    }
                }
                loadUrl("$ORIGIN/assets/viewer/viewer.html")
            } else callback(JSONObject().put("type", "renderer").put("error", "Update Android System WebView to enable 3D messaging.").toString())
            webView = this
        }
    })
    LaunchedEffect(ready, scene) {
        if (ready && scene != null) webView?.postWebMessage(android.webkit.WebMessage(scene.toString()), Uri.parse(ORIGIN))
    }
    LaunchedEffect(ready, telemetry) {
        if (ready && telemetry != null) {
            val message = JSONObject().put("type", "telemetry").put("pose", telemetry.opt("last"))
                .put("trace", telemetry.opt("trace"))
                .put("finished", !telemetry.optBoolean("live"))
                .put("samples", telemetry.opt("pose_samples"))
            webView?.postWebMessage(android.webkit.WebMessage(message.toString()), Uri.parse(ORIGIN))
        }
    }
    LaunchedEffect(ready, mode) {
        if (ready) webView?.postWebMessage(android.webkit.WebMessage(JSONObject().put("type", "view").put("mode", mode).toString()), Uri.parse(ORIGIN))
    }
    LaunchedEffect(ready, layers) {
        if (ready && layers != null) webView?.postWebMessage(android.webkit.WebMessage(
            JSONObject().put("type", "layers").put("layers", layers).toString()), Uri.parse(ORIGIN))
    }
    LaunchedEffect(ready, playback) {
        if (ready && playback != null) webView?.postWebMessage(android.webkit.WebMessage(
            JSONObject().put("type", "playback").put("options", playback).toString()), Uri.parse(ORIGIN))
    }
    DisposableEffect(Unit) {
        // Renderer controls never affect native mission state.
        onDispose {
            ready = false
            webView?.apply { stopLoading(); loadUrl("about:blank"); removeAllViews(); destroy() }
            webView = null
        }
    }
    LaunchedEffect(ready, pointSize) {
        if (ready) webView?.postWebMessage(android.webkit.WebMessage(
            JSONObject().put("type", "pointSize").put("size", pointSize.toDouble()).toString()), Uri.parse(ORIGIN))
    }
}
