package com.scanborn.ai.spaces

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.File
import java.util.concurrent.TimeUnit
import com.scanborn.ai.scan.UploadQueue

/** Native networking only. The embedded renderer cannot make orchestrator requests. */
class SpacesRepository(private val root: File) {
    private val client = OkHttpClient.Builder().connectTimeout(5, TimeUnit.SECONDS)
        .callTimeout(120, TimeUnit.SECONDS).build()
    private val safetyClient = client.newBuilder().callTimeout(5, TimeUnit.SECONDS).build()

    fun request(base: String, path: String, body: JSONObject? = null): JSONObject {
        require(base.startsWith("http://") || base.startsWith("https://")) { "Set the orchestrator URL in Settings" }
        val request = Request.Builder().url(base.trimEnd('/') + path)
        if (body != null) request.post(body.toString().toRequestBody("application/json".toMediaType()))
        val transport = if (path.endsWith("/stop") || path.endsWith("/estop")) safetyClient else client
        return transport.newCall(request.build()).execute().use {
            val text = it.body?.string().orEmpty()
            check(it.isSuccessful) { "HTTP ${it.code}: ${runCatching { JSONObject(text).opt("detail") }.getOrNull() ?: text.take(500)}" }
            JSONObject(text)
        }
    }

    @Synchronized fun cacheScene(base: String, manifest: JSONObject, navmesh: JSONObject?): JSONObject {
        val id = manifest.getString("mesh_id")
        require(id.matches(Regex("[a-zA-Z0-9_-]+")))
        val levels = manifest.getJSONArray("geometry")
        val asset = (0 until levels.length()).map { levels.getJSONObject(it) }
            .first { it.getString("url") == "/meshes/$id/geometry/100000" }
        val path = asset.getString("url")
        root.mkdirs()
        AssetRetention.requireSpace(root, 100000L * 16 + 16L * 1024 * 1024)
        // A replacement never overwrites assets referenced by the last published scene.
        val token = java.util.UUID.randomUUID().toString()
        val created = mutableListOf<File>()
        fun download(route: String, suffix: String, limit: Long, stride: Int = 1): String {
            val file = File(root, "$token.$suffix")
            client.newCall(Request.Builder().url(base.trimEnd('/') + route).build()).execute().use {
                check(it.isSuccessful) { "Could not fetch $suffix" }
                val body = checkNotNull(it.body) { "Empty asset response" }
                body.byteStream().use { stream -> saveBoundedAsset(stream, file, limit, body.contentLength(), stride) }
            }
            created.add(file)
            return "https://appassets.androidplatform.net/cache/${file.name}"
        }
        try {
            val geometryUrl = download(path, "bin", 100000L * 16, 16)
            val scene = JSONObject().put("manifest", manifest).put("meshId", id)
                .put("objects", manifest.getJSONArray("objects"))
                .put("spec", manifest.getJSONObject("robot_spec"))
            if (navmesh != null) scene.put("navmesh", navmesh)
            val message = JSONObject().put("type", "scene").put("scene", scene)
                .put("cache_id", token)
                .put("geometry", geometryUrl)
                .put("cached_at", System.currentTimeMillis()).put("server", base)
            manifest.optJSONObject("surface")?.let { surface ->
                val format = surface.getString("format")
                require(format in listOf("ply", "obj")) { "Unsupported mesh format" }
                val route = surface.getString("url")
                require(route == "/meshes/$id/surface/$format") { "Surface belongs to another mesh" }
                try {
                    message.put("surface", JSONObject().put("url", download(route, format, 16L * 1024 * 1024))
                        .put("format", format).put("to_map", surface.getJSONArray("to_map")))
                } catch (_: AssetTooLarge) {
                    message.put("surface_warning", "Imported mesh exceeds the 16 MiB mobile limit. Measured points remain available; inspect the full mesh on the dashboard.")
                }
            }
            UploadQueue.atomicWrite(File(root, "last-scene.json"), message.toString().toByteArray())
            return message
        } catch (error: Exception) {
            created.forEach { it.delete() }
            throw error
        }
    }

    /** Called only after the current WebView has consumed both assets. Late acknowledgements
     *  cannot evict a newer pending scene. Run on IO, like cacheScene. */
    @Synchronized fun acknowledgeScene(id: String) {
        val record = runCatching { JSONObject(File(root, "last-scene.json").readText()) }.getOrNull() ?: return
        if (record.optString("cache_id") != id) return
        val keep = mutableSetOf(record.getString("geometry").substringAfterLast('/'))
        record.optJSONObject("surface")?.let { keep.add(it.getString("url").substringAfterLast('/')) }
        AssetRetention.prune(root, keep)
    }

    fun cached(base: String): JSONObject? = runCatching {
        JSONObject(File(root, "last-scene.json").readText()).takeIf { it.optString("server") == base }
    }.getOrNull()
}
