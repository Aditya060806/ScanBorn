package com.scanborn.ai.ai.repository

import android.content.Context
import android.util.Log
import com.scanborn.ai.ai.engine.LlamaEngine
import com.scanborn.ai.ai.engine.LocalAIEngine
import com.scanborn.ai.ai.state.AIInferenceState
import com.scanborn.ai.ai.storage.ModelStorageManager
import com.scanborn.ai.ai.streaming.TokenStreamBuffer
import com.scanborn.ai.model.ChatMessage
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.emitAll
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeout

/**
 * AIRepository
 *
 * The single source of truth for all AI operations.
 * The ViewModel talks only to this class — never to LlamaEngine or ModelStorageManager directly.
 *
 * Responsibilities:
 * 1. Coordinate model extraction (storage) + model loading (engine)
 * 2. Expose AI state to ViewModel
 * 3. Route generate/stop/unload calls to the engine
 * 4. Ensure thread-safe initialization (idempotent, no double-loads)
 * 5. Handle errors gracefully with proper state management
 *
 * Lifecycle: one instance per app session (created in ViewModel, lives as long as ViewModel)
 */
class AIRepository(context: Context) {

    companion object {
        private const val TAG = "AIRepository"
        private const val INIT_TIMEOUT_MS = 120_000L

        @Volatile private var INSTANCE: AIRepository? = null

        /**
         * Single shared instance across all ViewModels.
         * Prevents multiple loadModel() calls on the same JNI context.
         */
        fun getInstance(context: Context): AIRepository =
            INSTANCE ?: synchronized(this) {
                INSTANCE ?: AIRepository(context.applicationContext).also { INSTANCE = it }
            }
    }

    private val storageManager = ModelStorageManager(context)
    private val engine: LocalAIEngine = LlamaEngine()

    // Mutex to ensure initialize() is idempotent (thread-safe, no double-loads)
    private val initializationLock = Mutex()
    private val operationLock = Mutex()
    @Volatile private var capturing = false
    private var initialized = false
    private var initializationError: Throwable? = null

    /** Observe AI state changes — ViewModel collects this */
    val aiState: StateFlow<AIInferenceState> = engine.state

    /**
     * Initialize the AI engine.
     * This is the full startup sequence:
     * 1. Check if model is already on disk
     * 2. If not, copy from APK assets (first launch only)
     * 3. Load model into RAM via llama.cpp
     *
     * IDEMPOTENT: Safe to call multiple times. Only performs initialization once.
     * Subsequent calls return cached result immediately.
     *
     * @param onExtractionProgress called during first-launch copy (0.0 to 1.0)
     * @return Result.success if initialized, Result.failure if error occurs
     */
    suspend fun initialize(onExtractionProgress: (Float) -> Unit = {}): Result<Unit> =
        initializationLock.withLock {
            if (capturing) return@withLock Result.failure(IllegalStateException("AI is paused while scanning"))
            if (initialized) return@withLock Result.success(Unit)
            initializationError?.let { return@withLock Result.failure(it) }

            try {
                withTimeout(INIT_TIMEOUT_MS) {
                    Log.i(TAG, "Initializing AI repository")

                    val modelPath = storageManager.extractModelIfNeeded(onExtractionProgress)
                        .getOrElse { e ->
                            Log.e(TAG, "Model extraction failed", e)
                            initializationError = e
                            throw e
                        }

                    Log.i(TAG, "Loading model from: $modelPath")
                    engine.loadModel(modelPath)

                    val state = engine.state.value
                    if (state is AIInferenceState.Error) {
                        val err = Exception(state.message)
                        initializationError = err
                        Log.e(TAG, "Model loading failed: ${state.message}")
                        throw err
                    }

                    initialized = true
                    Log.i(TAG, "AI initialization complete")
                }
                Result.success(Unit)
            } catch (e: Exception) {
                if (initializationError == null) initializationError = e
                Log.e(TAG, "Initialization failed", e)
                Result.failure(e)
            }
        }

    /**
     * Generate a response for the user's message.
     * Returns a Flow<String> that emits tokens as they're generated.
     * Collect this flow in the ViewModel to build the response incrementally.
     *
     * Pass a GBNF grammar (see Grammars.kt) to constrain the output to a schema. Chat and
     * the summarisation features leave it empty and behave exactly as before.
     */
    fun generate(history: List<ChatMessage>, userInput: String,
                 grammar: String = "", systemPrompt: String? = null): Flow<String> = flow {
        // The model may have been released (a scan started, or Android asked for memory): load it
        // again first. When it is already loaded this returns at once.
        operationLock.withLock {
            check(!capturing) { "AI is paused while scanning" }
            initialize().getOrThrow()
            emitAll(TokenStreamBuffer.clean(engine.generate(history, userInput, grammar, systemPrompt)))
        }
    }

    /**
     * Free the model's memory now; the next [generate] loads it again. Called when a scan starts
     * (ARCore needs the memory) and when Android trims the app's memory. Safe mid-generation: the
     * generation is stopped first.
     */
    suspend fun release() = initializationLock.withLock {
        if (initialized || engine.isReady()) {
            engine.stop()
            engine.unload()
            Log.i(TAG, "Model released")
        }
        initialized = false
        initializationError = null
    }

    suspend fun beginCapture() {
        capturing = true
        engine.stop()
        try {
            operationLock.withLock { release() }
        } catch (e: Throwable) {
            capturing = false
            throw e
        }
    }

    fun endCapture() { capturing = false }

    /** Stop the current generation */
    fun stop() = engine.stop()

    /** Free model memory — call from ViewModel.onCleared() */
    fun unload() {
        engine.unload()
        initialized = false
        initializationError = null
    }

    /** True if model is loaded and ready */
    fun isReady(): Boolean = engine.isReady()

    /** True if model file exists on disk (already extracted) */
    fun isModelOnDisk(): Boolean = storageManager.isModelExtracted()
}
