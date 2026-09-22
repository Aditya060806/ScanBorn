package com.scanborn.ai.scan

import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/** Temporary server throttling/timeouts keep the durable frame available for retry. */
internal fun permanentlyRejectedChunk(code: Int): Boolean =
    code in 400..499 && code !in setOf(408, 425, 429)

/**
 * Scan chunks to the orchestrator: `POST /capture` per chunk and `POST /capture/{id}/complete`.
 *
 * The orchestrator keys chunks by index, so re-sending an index overwrites it: a retry after a
 * dropped connection is safe.
 */
class Uploader(
    private val baseUrl: String,
    private val device: String,
    private val client: OkHttpClient = CLIENT,
    scanIdentifier: String = java.util.UUID.randomUUID().toString(),
) : ChunkSink {

    @Volatile
    override var scanId: String? = scanIdentifier
        private set

    override fun upload(index: Int, npz: ByteArray, meta: String?) {
        val url = "$baseUrl/capture".toHttpUrl().newBuilder()
            .addQueryParameter("index", index.toString())
            .addQueryParameter("device", device)
            .apply {
                scanId?.let { addQueryParameter("scan_id", it) }
                meta?.let { addQueryParameter("meta", it) }
            }
            .build()
        val body = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("file", "frame_$index.npz",
                npz.toRequestBody("application/octet-stream".toMediaType()))
            .build()
        client.newCall(Request.Builder().url(url).post(body).build()).execute().use { response ->
            val text = response.body?.string().orEmpty()
            if (permanentlyRejectedChunk(response.code)) {
                throw ChunkRejected("chunk $index refused: ${response.code} ${text.take(200)}")
            }
            if (!response.isSuccessful) throw IOException("chunk $index failed: ${response.code}")
            check(JSONObject(text).getString("scan_id") == scanId) { "Server returned a different scan ID" }
        }
    }

    override fun complete(meta: String): Int {
        val id = scanId ?: throw IllegalStateException("nothing uploaded yet")
        val body = JSONObject().put("meta", JSONObject(meta)).toString()
            .toRequestBody("application/json".toMediaType())
        client.newCall(Request.Builder().url("$baseUrl/capture/$id/complete").post(body).build())
            .execute().use { response ->
                val text = response.body?.string().orEmpty()
                if (!response.isSuccessful) throw IOException("HTTP ${response.code} ${text.take(200)}")
                return JSONObject(text).getInt("frame_count")
            }
    }

    companion object {
        val CLIENT: OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(5, TimeUnit.SECONDS)
            .callTimeout(30, TimeUnit.SECONDS)
            .build()
    }
}
