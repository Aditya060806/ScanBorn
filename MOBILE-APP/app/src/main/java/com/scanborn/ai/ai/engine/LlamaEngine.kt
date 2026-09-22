package com.scanborn.ai.ai.engine

import android.util.Log
import com.scanborn.ai.ai.prompts.PromptFormatter
import com.scanborn.ai.ai.runtime.LlamaCallback
import com.scanborn.ai.ai.runtime.LlamaJniBridge
import com.scanborn.ai.ai.state.AIInferenceState
import com.scanborn.ai.ai.state.InferenceStats
import com.scanborn.ai.model.ChatMessage
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.buffer
import kotlinx.coroutines.flow.callbackFlow
import java.util.concurrent.atomic.AtomicReference

class LlamaEngine : LocalAIEngine {

    companion object {
        private const val TAG = "LlamaEngine"

        /** Room for a scene summary plus few-shot examples, not just a chat turn. */
        private const val N_CTX = 4096

        /**
         * A task graph is a handful of nodes and a profile is four fields, so a long budget
         * only buys a longer worst case when the model rambles. Chat replies are capped by
         * the same number; raise it if that becomes the complaint.
         */
        private const val MAX_TOKENS = 256

        /**
         * Threads for inference, chosen at runtime rather than baked in.
         *
         * This was a hardcoded 4, which is the most likely cause of the three-minute
         * first-token watchdog in AiTextProcessor. The target phone is big.LITTLE with no
         * 4 equal cores: the Galaxy S24 FE's Exynos 2400e is 1x Cortex-X4 + 5x A720 + 4x A520.
         * Four unpinned threads can land partly on A520 efficiency cores, and llama.cpp runs a
         * batch only as fast as its slowest thread — so the little cores set the pace for all
         * of them.
         *
         * Targeting the big-core count is the heuristic that avoids that. Measure on device
         * and pin this if the numbers disagree; the stats callback exists to tell you.
         */
        private fun inferenceThreads(): Int {
            val cores = Runtime.getRuntime().availableProcessors()
            debugThreadOverride(cores)?.let { return it }
            return when {
                cores >= 10 -> 6   // 2400e: X4 + 5x A720. 8 Elite Gen 5: 2 + 6.
                cores >= 8 -> 5
                cores >= 4 -> cores - 1
                else -> 1
            }.coerceAtLeast(1)
        }

        private const val THREADS_PROP = "debug.scanborn.threads"

        /**
         * `adb shell setprop debug.scanborn.threads 4` pins the thread count for the next model
         * load, so comparing thread counts needs no rebuild and no 1.2 GB reinstall.
         *
         * Read through the `getprop` binary rather than the hidden SystemProperties API, which
         * apps targeting API 36 cannot call. Unset, unreadable or out of range means no
         * override — and loadModel logs the count actually used, so an ignored override is
         * visible rather than silently measured as the wrong configuration.
         */
        private fun debugThreadOverride(cores: Int): Int? {
            val raw = runCatching {
                ProcessBuilder("getprop", THREADS_PROP).start().inputStream
                    .bufferedReader().use { it.readText().trim() }
            }.getOrNull()
            val value = raw?.toIntOrNull()?.takeIf { it in 1..cores } ?: return null
            Log.i(TAG, "threads overridden by $THREADS_PROP=$value")
            return value
        }
    }

    // AtomicReference is the CAS source of truth for thread-safe state transitions
    // from C++ JNI threads. MutableStateFlow is kept in sync and used for UI observation.
    // MutableStateFlow has no compareAndSet API, so we need AtomicReference for CAS.
    private val _stateRef = AtomicReference<AIInferenceState>(AIInferenceState.Idle)
    private val _state    = MutableStateFlow<AIInferenceState>(AIInferenceState.Idle)
    override val state: StateFlow<AIInferenceState> = _state.asStateFlow()

    private fun setState(new: AIInferenceState) {
        _stateRef.set(new)
        _state.value = new
    }

    /** Atomic CAS — safe to call from any thread including C++ JNI threads. */
    private fun casState(expected: AIInferenceState, new: AIInferenceState) {
        if (_stateRef.compareAndSet(expected, new)) {
            _state.value = new
        }
    }

    /**
     * Publish a Responding snapshot, but only while generation is genuinely live.
     *
     * The first token promotes Thinking -> Responding; every later token refreshes it in
     * place so partialText and stats stay current. A CAS loop rather than a plain set,
     * because these calls arrive on C++ JNI threads: if stop() or an error has already
     * moved the state to Idle or Error, a blind write would resurrect a finished
     * generation and the UI would stream into a session the user had cancelled.
     */
    private fun publishResponding(snapshot: AIInferenceState.Responding) {
        while (true) {
            val current = _stateRef.get()
            if (current !== AIInferenceState.Thinking && current !is AIInferenceState.Responding) {
                return
            }
            if (_stateRef.compareAndSet(current, snapshot)) {
                _state.value = snapshot
                return
            }
        }
    }

    override suspend fun loadModel(modelPath: String) {
        setState(AIInferenceState.Loading)
        val threads = inferenceThreads()
        Log.i(TAG, "Loading model: $modelPath (ctx=$N_CTX, threads=$threads of " +
            "${Runtime.getRuntime().availableProcessors()} cores)")
        val ok = LlamaJniBridge.loadModel(modelPath, N_CTX, threads)
        setState(if (ok) {
            Log.i(TAG, "Model loaded successfully")
            AIInferenceState.Idle
        } else {
            Log.e(TAG, "Model load failed")
            AIInferenceState.Error("Failed to load AI model")
        })
    }

    override fun generate(history: List<ChatMessage>, userInput: String,
                          grammar: String, systemPrompt: String?): Flow<String> =
        callbackFlow {
            setState(AIInferenceState.Thinking)
            val prompt = PromptFormatter.buildPrompt(history, userInput, systemPrompt)
            Log.d(TAG, "Prompt length: ${prompt.length} chars, grammar: ${grammar.length}")

            // Accumulated on the JNI thread and read back on it; the StateFlow is the only
            // thing crossing to the UI, so a StringBuilder plus an atomic is enough.
            val partial = StringBuilder()
            val latestStats = AtomicReference(InferenceStats())

            LlamaJniBridge.generate(
                prompt    = prompt,
                maxTokens = MAX_TOKENS,
                grammar   = grammar,
                callback  = object : LlamaCallback {
                    override fun onToken(token: String) {
                        partial.append(token)
                        publishResponding(
                            AIInferenceState.Responding(partial.toString(), latestStats.get())
                        )
                        trySend(token)
                    }

                    override fun onStats(promptTokens: Int, genTokens: Int,
                                         prefillMs: Long, decodeMs: Long) {
                        val stats = InferenceStats(promptTokens, genTokens, prefillMs, decodeMs)
                        latestStats.set(stats)
                        // Fires once before the first token, so time-to-first-token is
                        // observable while the model still looks idle. Only refresh the
                        // state if we are already Responding — promoting out of Thinking
                        // here would claim a reply had started before any token arrived.
                        if (_stateRef.get() is AIInferenceState.Responding) {
                            publishResponding(
                                AIInferenceState.Responding(partial.toString(), stats)
                            )
                        }
                        Log.i(TAG, "stats: ${stats.summary()} " +
                            "(prompt=${stats.promptTokens} tok in ${stats.prefillMs}ms, " +
                            "gen=${stats.genTokens} tok in ${stats.decodeMs}ms)")
                    }
                    override fun onComplete() {
                        Log.i(TAG, "Generation complete")
                        setState(AIInferenceState.Idle)
                        // C++ may call onComplete after Kotlin cancellation already closed the
                        // channel. close() is idempotent (a second call does nothing and returns
                        // false), so no isClosedForSend check — a delicate, racy API — is needed.
                        channel.close()
                    }
                    override fun onError(message: String) {
                        Log.e(TAG, "Generation error: $message")
                        setState(AIInferenceState.Error(message))
                        channel.close(Exception(message))
                    }
                }
            )

            awaitClose {
                Log.d(TAG, "Flow closed — signalling stop")
                LlamaJniBridge.stopGeneration()
            }
        }.buffer(Channel.UNLIMITED)

    override fun stop() {
        LlamaJniBridge.stopGeneration()
        setState(AIInferenceState.Idle)
    }

    override fun unload() {
        LlamaJniBridge.unloadModel()
        setState(AIInferenceState.Idle)
    }

    override fun isReady(): Boolean = LlamaJniBridge.isModelLoaded()
}
