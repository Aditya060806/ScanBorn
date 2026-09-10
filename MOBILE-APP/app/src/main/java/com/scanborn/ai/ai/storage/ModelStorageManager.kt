package com.scanborn.ai.ai.storage

import android.content.Context
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.io.File

class ModelStorageManager(private val context: Context) {

    companion object {
        private const val TAG            = "ModelStorage"
        const val ASSET_MODEL_PATH       = "models/qwen.gguf"
        const val MODEL_FILENAME         = "qwen.gguf"
        private const val MODELS_DIR     = "models"
    }

    private val extractionLock = Mutex()

    val modelFilePath: String
        get() = File(context.filesDir, "$MODELS_DIR/$MODEL_FILENAME").absolutePath

    fun isModelExtracted(): Boolean {
        val file = File(modelFilePath)
        val exists = file.exists() && file.length() > 0
        Log.d(TAG, "Model extracted: $exists (path: $modelFilePath)")
        return exists
    }

    suspend fun extractModelIfNeeded(
        onProgress: (Float) -> Unit = {}
    ): Result<String> {
        // Bug fix: fast-path check OUTSIDE the mutex.
        // Previously the mutex wrapped the entire withContext block, meaning
        // concurrent callers would block for the full extraction duration (~minutes)
        // even though they only needed to read a boolean. Now they return immediately.
        if (isModelExtracted()) {
            Log.i(TAG, "Model already extracted, skipping copy")
            return Result.success(modelFilePath)
        }

        return extractionLock.withLock {
            withContext(Dispatchers.IO) {
                // Re-check inside the lock — another coroutine may have extracted
                // while we were waiting to acquire the lock.
                if (isModelExtracted()) {
                    Log.i(TAG, "Model extracted by concurrent caller, skipping")
                    return@withContext Result.success(modelFilePath)
                }

                try {
                    Log.i(TAG, "Starting model extraction from assets...")

                    val modelsDir = File(context.filesDir, MODELS_DIR)
                    if (!modelsDir.exists()) modelsDir.mkdirs()

                    val destFile = File(modelFilePath)
                    val tempFile = File("$modelFilePath.tmp")

                    if (tempFile.exists()) {
                        Log.w(TAG, "Cleaning up leftover temp file from previous extraction")
                        tempFile.delete()
                    }

                    // Bug fix: AssetFileDescriptor was never closed — leaked file descriptor.
                    // Fixed by wrapping in .use{} so it closes after reading .length.
                    val totalBytes = context.assets.openFd(ASSET_MODEL_PATH).use { it.length }

                    context.assets.open(ASSET_MODEL_PATH).use { input ->
                        tempFile.outputStream().use { output ->
                            val buffer = ByteArray(8 * 1024 * 1024) // 8 MB
                            var bytesCopied = 0L
                            var bytesRead: Int
                            while (input.read(buffer).also { bytesRead = it } != -1) {
                                output.write(buffer, 0, bytesRead)
                                bytesCopied += bytesRead
                                if (totalBytes > 0) {
                                    onProgress(bytesCopied.toFloat() / totalBytes.toFloat())
                                }
                            }
                            output.flush()
                        }
                    }

                    val renamed = tempFile.renameTo(destFile)
                    if (!renamed) {
                        Log.e(TAG, "Failed to rename temp file to destination")
                        tempFile.delete()
                        return@withContext Result.failure(Exception("Failed to finalize model extraction"))
                    }

                    Log.i(TAG, "Model extraction complete: ${destFile.length()} bytes")
                    Result.success(modelFilePath)

                } catch (e: Exception) {
                    Log.e(TAG, "Model extraction failed", e)
                    File("$modelFilePath.tmp").delete()
                    Result.failure(e)
                }
            }
        }
    }

    fun deleteModel() {
        File(modelFilePath).delete()
        Log.i(TAG, "Model deleted from internal storage")
    }
}
