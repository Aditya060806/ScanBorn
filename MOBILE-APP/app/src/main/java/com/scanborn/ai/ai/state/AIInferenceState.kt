package com.scanborn.ai.ai.state

/**
 * Measured throughput for one generation, reported from the native layer.
 *
 * Prefill and decode are kept apart on purpose. They are different bottlenecks — prefill is
 * compute-bound across the whole prompt, decode is memory-bandwidth-bound one token at a
 * time — so a single tokens-per-second figure cannot tell you which one is slow. On a
 * CPU-only phone the number that matters is [prefillMs]: time-to-first-token is prefill,
 * and it is what a user experiences as the model refusing to answer.
 */
data class InferenceStats(
    val promptTokens: Int = 0,
    val genTokens: Int = 0,
    val prefillMs: Long = 0,
    val decodeMs: Long = 0,
) {
    val prefillTokensPerSec: Double
        get() = if (prefillMs > 0) promptTokens * 1000.0 / prefillMs else 0.0

    val decodeTokensPerSec: Double
        get() = if (decodeMs > 0) genTokens * 1000.0 / decodeMs else 0.0

    /** True once the native layer has reported anything at all. */
    val measured: Boolean get() = prefillMs > 0 || decodeMs > 0

    /** One line for a status row: "142 tok/s prefill · 11.3 tok/s decode". */
    fun summary(): String = if (!measured) "not measured yet" else
        "%.0f tok/s prefill · %.1f tok/s decode".format(prefillTokensPerSec, decodeTokensPerSec)
}

/**
 * AIInferenceState represents every possible state of the local AI engine.
 *
 * This flows from:
 *   LlamaEngine → AIRepository → ChatViewModel → Compose UI + AiBodyOrb
 *
 * The orb animation reacts to each state differently.
 */
sealed class AIInferenceState {

    /** Model is loaded and ready. Orb: slow breathing. */
    object Idle : AIInferenceState()

    /** Model is being copied from assets or loaded into RAM. Orb: soft pulse. */
    object Loading : AIInferenceState()

    /** Prompt is being processed (prefill phase). Orb: rotating energy rings. */
    object Thinking : AIInferenceState()

    /**
     * Tokens are being generated and streamed. Orb: waveform activity.
     *
     * [partialText] accumulates the reply as it arrives. It used to be declared and never
     * populated — LlamaEngine constructed `Responding()` with no argument — which is why
     * VoiceScreen's transcript card could never become visible.
     */
    data class Responding(
        val partialText: String = "",
        val stats: InferenceStats = InferenceStats(),
    ) : AIInferenceState()

    /** Something went wrong. Orb: unstable flicker. */
    data class Error(val message: String) : AIInferenceState()
}
