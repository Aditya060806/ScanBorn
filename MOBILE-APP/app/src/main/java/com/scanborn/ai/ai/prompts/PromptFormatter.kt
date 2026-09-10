package com.scanborn.ai.ai.prompts

import com.scanborn.ai.model.ChatMessage

/**
 * PromptFormatter
 *
 * Qwen 2.5 uses the ChatML prompt format:
 *
 *   <|im_start|>system
 *   You are a helpful assistant.<|im_end|>
 *   <|im_start|>user
 *   Hello!<|im_end|>
 *   <|im_start|>assistant
 *   Hi there!<|im_end|>
 *   <|im_start|>assistant
 *   ← generation starts here
 *
 * If the format is wrong, the model will produce garbage output.
 * This formatter ensures the exact format Qwen 2.5 expects.
 */
object PromptFormatter {

    private const val SYSTEM_PROMPT = "You are ScanBorn, a helpful, concise, and intelligent AI assistant. " +
        "You run entirely offline on the user's device. " +
        "Be direct and helpful. Keep responses focused and clear. " +
        "Do not mention that you are an AI language model unless asked."

    /**
     * Build the full prompt string from chat history.
     *
     * @param history  list of previous messages (user + assistant)
     * @param newInput the new user message to respond to
     * @return formatted prompt string ready for llama.cpp
     */
    fun buildPrompt(history: List<ChatMessage>, newInput: String): String {
        val sb = StringBuilder()

        // System message — sets the AI's personality
        sb.append("<|im_start|>system\n")
        sb.append(SYSTEM_PROMPT)
        sb.append("<|im_end|>\n")

        // Chat history — last N messages to fit in context window
        // We keep the last 10 exchanges to avoid exceeding 2048 tokens
        val recentHistory = history.takeLast(20)
        for (msg in recentHistory) {
            val role = if (msg.isUser) "user" else "assistant"
            sb.append("<|im_start|>$role\n")
            sb.append(msg.text)
            sb.append("<|im_end|>\n")
        }

        // New user message
        sb.append("<|im_start|>user\n")
        sb.append(newInput)
        sb.append("<|im_end|>\n")

        // Assistant turn start — model generates from here
        sb.append("<|im_start|>assistant\n")

        return sb.toString()
    }
}
