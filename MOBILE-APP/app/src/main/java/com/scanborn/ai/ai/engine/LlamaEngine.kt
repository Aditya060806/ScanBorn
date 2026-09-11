package com.scanborn.ai.ai.engine

import android.util.Log
import com.scanborn.ai.ai.prompts.PromptFormatter
import com.scanborn.ai.ai.runtime.LlamaCallback
import com.scanborn.ai.ai.runtime.LlamaJniBridge
import com.scanborn.ai.ai.state.AIInferenceState
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
        private const val TAG        = "LlamaEngine"
        private const val N_CTX      = 2048
        private const val N_THREADS  = 4
        private const val MAX_TOKENS = 512
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

    override suspend fun loadModel(modelPath: String) {
        setState(AIInferenceState.Loading)
        Log.i(TAG, "Loading model: $modelPath")
        val ok = LlamaJniBridge.loadModel(modelPath, N_CTX, N_THREADS)
        setState(if (ok) {
            Log.i(TAG, "Model loaded successfully")
            AIInferenceState.Idle
        } else {
            Log.e(TAG, "Model load failed")
            AIInferenceState.Error("Failed to load AI model")
        })
    }

    override fun generate(history: List<ChatMessage>, userInput: String,
                          grammar: String): Flow<String> =
        callbackFlow {
            setState(AIInferenceState.Thinking)
            val prompt = PromptFormatter.buildPrompt(history, userInput)
            Log.d(TAG, "Prompt length: ${prompt.length} chars, grammar: ${grammar.length}")

            LlamaJniBridge.generate(
                prompt    = prompt,
                maxTokens = MAX_TOKENS,
                grammar   = grammar,
                callback  = object : LlamaCallback {
                    override fun onToken(token: String) {
                        // Atomic CAS: only transitions Thinking→Responding once.
                        // Safe from C++ JNI threads. If stop() already set Idle,
                        // the CAS fails and state stays Idle — correct behaviour.
                        casState(AIInferenceState.Thinking, AIInferenceState.Responding())
                        trySend(token)
                    }
                    override fun onComplete() {
                        Log.i(TAG, "Generation complete")
                        setState(AIInferenceState.Idle)
                        // Guard: C++ may call onComplete after Kotlin cancellation
                        // already closed the channel — avoid ClosedSendChannelException.
                        if (!channel.isClosedForSend) channel.close()
                    }
                    override fun onError(message: String) {
                        Log.e(TAG, "Generation error: $message")
                        setState(AIInferenceState.Error(message))
                        if (!channel.isClosedForSend) channel.close(Exception(message))
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
