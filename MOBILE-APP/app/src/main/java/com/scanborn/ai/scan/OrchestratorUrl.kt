package com.scanborn.ai.scan

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.IOException
import java.net.URI
import java.util.concurrent.TimeUnit

/** The orchestrator's address as the owner types it in Settings, and the connection test. */
object OrchestratorUrl {

    /**
     * `http(s)://host[:port]` with no path or trailing slash, or null when `raw` is not one. A bare
     * `192.168.1.20:8000` gets `http://`: the orchestrator runs plain HTTP on the laptop.
     */
    fun normalise(raw: String): String? {
        val text = raw.trim().trimEnd('/')
        if (text.isEmpty() || text.any { it.isWhitespace() }) return null
        val withScheme = if ("://" in text) text else "http://$text"
        val uri = try {
            URI(withScheme)
        } catch (e: Exception) {
            return null
        }
        val scheme = uri.scheme?.lowercase()
        if (scheme != "http" && scheme != "https") return null
        val host = uri.host ?: return null
        if (!uri.path.isNullOrEmpty() || uri.query != null || uri.fragment != null) return null
        return "$scheme://$host" + if (uri.port != -1) ":${uri.port}" else ""
    }

    private val client = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(5, TimeUnit.SECONDS)
        .callTimeout(6, TimeUnit.SECONDS)
        .build()

    /** `GET /health` on the orchestrator: its answer, or why it could not be reached. */
    suspend fun health(baseUrl: String): Result<String> = withContext(Dispatchers.IO) {
        runCatching {
            client.newCall(Request.Builder().url("$baseUrl/health").build()).execute().use {
                val body = it.body?.string().orEmpty()
                if (!it.isSuccessful) throw IOException("HTTP ${it.code}")
                body.take(80)
            }
        }
    }
}
