package com.scanborn.ai.ai.engine

import com.scanborn.ai.ai.state.AIInferenceState
import com.scanborn.ai.model.ChatMessage
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.StateFlow

/**
 * LocalAIEngine
 *
 * This interface defines the contract for local AI inference.
 * The Repository talks to this interface — it never knows about llama.cpp directly.
 *
 * This design means:
 * - We can swap llama.cpp for another engine without touching the ViewModel
 * - We can create a FakeAIEngine for testing
 * - The architecture stays clean
 */
interface LocalAIEngine {

    /** Current state of the engine (Idle, Loading, Thinking, Responding, Error) */
    val state: StateFlow<AIInferenceState>

    /**
     * Load the model from the given file path into memory.
     * Must be called before generate().
     */
    suspend fun loadModel(modelPath: String)

    /**
     * Generate a response for the given prompt.
     * Returns a Flow<String> that emits tokens one by one as they're generated.
     * The flow completes when generation is done.
     *
     * @param history   previous chat messages for context
     * @param userInput the new user message
     * @param grammar   GBNF source constraining the output, or "" for free generation.
     *                  Defaulted so every existing chat caller is unaffected.
     */
    fun generate(history: List<ChatMessage>, userInput: String,
                 grammar: String = ""): Flow<String>

    /**
     * Stop the current generation immediately.
     * The generate() flow will complete after the current token.
     */
    fun stop()

    /**
     * Unload the model from memory.
     * Call in ViewModel.onCleared() to prevent memory leaks.
     */
    fun unload()

    /** Returns true if the model is loaded and ready to generate */
    fun isReady(): Boolean
}
