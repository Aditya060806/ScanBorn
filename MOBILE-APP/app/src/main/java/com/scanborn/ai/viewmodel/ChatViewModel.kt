package com.scanborn.ai.viewmodel

import android.app.Application
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.scanborn.ai.ai.repository.AIRepository
import com.scanborn.ai.ai.state.AIInferenceState
import com.scanborn.ai.model.ChatMessage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.onCompletion
import kotlinx.coroutines.launch

class ChatViewModel(app: Application) : AndroidViewModel(app) {

    companion object {
        private const val TAG = "ChatViewModel"
    }

    private val repository = AIRepository.getInstance(app)

    // ── Chat state ─────────────────────────────────────────────────────────────
    private val _messages = MutableStateFlow<List<ChatMessage>>(emptyList())
    val messages: StateFlow<List<ChatMessage>> = _messages.asStateFlow()

    private val _input = MutableStateFlow("")
    val input: StateFlow<String> = _input.asStateFlow()

    private val _showSuggestions = MutableStateFlow(true)
    val showSuggestions: StateFlow<Boolean> = _showSuggestions.asStateFlow()

    // ── AI engine state (forwarded from repository) ────────────────────────────
    val aiState: StateFlow<AIInferenceState> = repository.aiState

    // ── Extraction progress (first launch only) ────────────────────────────────
    private val _extractionProgress = MutableStateFlow(0f)
    val extractionProgress: StateFlow<Float> = _extractionProgress.asStateFlow()

    private val _isExtracting = MutableStateFlow(false)
    val isExtracting: StateFlow<Boolean> = _isExtracting.asStateFlow()

    // ── Generation job ─────────────────────────────────────────────────────────
    private var generationJob: Job? = null
    private var messageIdCounter = 0L
    private fun nextId() = ++messageIdCounter

    // Bug fix: track whether stop was user-initiated so onCompletion doesn't
    // replace a valid partial response with an error message.
    private var userStoppedGeneration = false

    init {
        initializeAI()
    }

    // ── AI Initialization ──────────────────────────────────────────────────────

    private fun initializeAI() {
        viewModelScope.launch(Dispatchers.IO) {
            _isExtracting.value = !repository.isModelOnDisk()
            repository.initialize { progress ->
                _extractionProgress.value = progress
            }
            _isExtracting.value = false
        }
    }

    // ── Input handling ─────────────────────────────────────────────────────────

    fun onInputChange(value: String) {
        _input.value = value
    }

    fun sendMessage() {
        val text = _input.value.trim()
        if (text.isBlank()) return
        if (aiState.value is AIInferenceState.Thinking ||
            aiState.value is AIInferenceState.Responding) return

        if (aiState.value is AIInferenceState.Loading ||
            aiState.value is AIInferenceState.Error) {
            val userMsg = ChatMessage(nextId(), text, isUser = true)
            val errMsg  = ChatMessage(nextId(),
                if (aiState.value is AIInferenceState.Loading)
                    "Model is still loading, please wait a moment and try again."
                else
                    "AI engine error. Please restart the app.",
                isUser = false)
            _messages.value = _messages.value + userMsg + errMsg
            _input.value = ""
            _showSuggestions.value = false
            return
        }

        _showSuggestions.value = false
        _input.value = ""

        val userMsg = ChatMessage(nextId(), text, isUser = true)
        _messages.value = _messages.value + userMsg

        generateReply(text)
    }

    /**
     * Start a fresh conversation from a tapped suggestion chip.
     *
     * Replacing the list is correct here — the chips only show on an empty chat, so there is
     * nothing to lose. It is NOT correct for voice; see [sendVoiceInput].
     */
    fun startFromSuggestion(prompt: String) {
        _showSuggestions.value = false
        val welcome = ChatMessage(nextId(), "Hello! I'm ScanBorn. How can I help you today?", isUser = false)
        val userMsg = ChatMessage(nextId(), prompt, isUser = true)
        _messages.value = listOf(welcome, userMsg)
        if (rejectedWhileUnready()) return
        generateReply(prompt)
    }

    /**
     * Handle a spoken instruction.
     *
     * Voice used to route through [startFromSuggestion], which replaces the entire message
     * list — so every utterance silently deleted the conversation so far. Speaking is a normal
     * turn, not a new session, so this appends exactly like [sendMessage] does.
     */
    fun sendVoiceInput(text: String) {
        val spoken = text.trim()
        if (spoken.isBlank()) return
        // Ignore an utterance that lands mid-generation rather than interleaving two replies.
        if (aiState.value is AIInferenceState.Thinking ||
            aiState.value is AIInferenceState.Responding) return

        _showSuggestions.value = false
        _messages.value = _messages.value + ChatMessage(nextId(), spoken, isUser = true)
        if (rejectedWhileUnready()) return
        generateReply(spoken)
    }

    /**
     * Append the "not ready" reply when the engine cannot answer, and report whether it did.
     *
     * Shared by every entry point so a loading model reads the same whether the user typed,
     * tapped or spoke.
     */
    private fun rejectedWhileUnready(): Boolean {
        val state = aiState.value
        if (state !is AIInferenceState.Loading && state !is AIInferenceState.Error) return false
        _messages.value = _messages.value + ChatMessage(nextId(),
            if (state is AIInferenceState.Loading)
                "Model is still loading, please wait a moment and try again."
            else
                "AI engine error. Please restart the app.",
            isUser = false)
        return true
    }

    fun stopGeneration() {
        userStoppedGeneration = true
        // Bug fix: stop C++ thread FIRST, then cancel the coroutine job.
        // Previously cancel() fired awaitClose (which called stopGeneration) then
        // repository.stop() called it again — harmless but wrong order semantically.
        // More importantly: stopping C++ first reduces the window where a new
        // generation could start while the old thread is still running.
        repository.stop()
        generationJob?.cancel()
    }

    fun clearChat() {
        stopGeneration()
        _messages.value = emptyList()
        _input.value = ""
        _showSuggestions.value = true
    }

    // ── Streaming generation ───────────────────────────────────────────────────

    private fun generateReply(userInput: String) {
        val aiMsgId = nextId()
        val placeholder = ChatMessage(aiMsgId, "", isUser = false)
        _messages.value = _messages.value + placeholder

        // Stop previous generation before starting a new one
        userStoppedGeneration = false
        repository.stop()
        generationJob?.cancel()

        generationJob = viewModelScope.launch(Dispatchers.IO) {
            val historyForPrompt = _messages.value
                .dropLast(1)
                .takeLast(20)

            try {
                repository.generate(historyForPrompt, userInput)
                    .catch { e ->
                        Log.e(TAG, "Generation failed: ${e.message}", e)
                        updateMessage(aiMsgId, "Sorry, I encountered an error: ${e.message}")
                    }
                    .onCompletion { cause ->
                        if (cause != null) {
                            Log.e(TAG, "Generation cancelled or failed", cause)
                        }
                        // Bug fix: only replace blank message with error if the stop was NOT
                        // user-initiated. A user-stopped generation may have a valid partial
                        // response that should be preserved.
                        if (!userStoppedGeneration) {
                            val current = _messages.value.find { it.id == aiMsgId }
                            if (current?.text.isNullOrBlank()) {
                                updateMessage(aiMsgId, "I couldn't generate a response. Please try again.")
                            }
                        }
                    }
                    .collect { token -> appendToken(aiMsgId, token) }
            } catch (e: Exception) {
                Log.e(TAG, "Unexpected error during generation", e)
                updateMessage(aiMsgId, "An unexpected error occurred. Please try again.")
            }
        }
    }

    private fun appendToken(msgId: Long, token: String) {
        // Bug fix: removed `if (updated !== current)` reference-equality guard.
        // List.map{} always returns a new object so the check was always true —
        // a misleading no-op. Just assign directly.
        _messages.value = _messages.value.map { msg ->
            if (msg.id == msgId) msg.copy(text = msg.text + token) else msg
        }
    }

    private fun updateMessage(msgId: Long, text: String) {
        _messages.value = _messages.value.map { msg ->
            if (msg.id == msgId) msg.copy(text = text) else msg
        }
    }

    // ── Lifecycle cleanup ──────────────────────────────────────────────────────

    override fun onCleared() {
        super.onCleared()
        stopGeneration()
        repository.unload()
    }
}
