package com.scanborn.ai.ai.runtime

/**
 * LlamaJniBridge
 *
 * This Kotlin object declares the native (C++) functions we wrote in scanborn_jni.cpp.
 *
 * HOW THIS WORKS:
 * - "external fun" tells Kotlin: "this function is implemented in C++, not Kotlin"
 * - When called, Android's JNI system routes the call to our .so library
 * - The C++ function name must match: Java_com_scanborn_ai_ai_runtime_LlamaJniBridge_<methodName>
 *
 * The companion object's init block loads our compiled .so file.
 * "scanborn_jni" matches the library name in CMakeLists.txt: add_library(scanborn_jni ...)
 */
object LlamaJniBridge {

    init {
        // Load our compiled native library. This must happen before any external fun is called.
        System.loadLibrary("scanborn_jni")
    }

    /**
     * Load the GGUF model file into memory.
     * @param modelPath absolute path to the .gguf file on internal storage
     * @param nCtx      context window size (2048 is safe for 6GB RAM devices)
     * @param nThreads  number of CPU threads (4 is a good default)
     * @return true if model loaded successfully
     */
    external fun loadModel(modelPath: String, nCtx: Int, nThreads: Int): Boolean

    /**
     * Generate a response for the given prompt, streaming tokens via callback.
     *
     * @param prompt    the full formatted prompt (system + history + user message)
     * @param maxTokens maximum tokens to generate
     * @param grammar   GBNF source, or "" for normal sampled generation. When supplied,
     *                  the model can only emit strings the grammar accepts and decoding
     *                  switches to greedy so the same input yields the same output twice.
     *                  An invalid grammar fails through onError rather than quietly
     *                  generating unconstrained text — see Grammars.kt.
     * @param callback  receives each token as it's generated
     */
    external fun generate(prompt: String, maxTokens: Int, grammar: String,
                          callback: LlamaCallback)

    /**
     * Signal the generation loop to stop at the next token.
     * Safe to call from any thread.
     */
    external fun stopGeneration()

    /**
     * Free the model and context from memory.
     * Call this in ViewModel.onCleared() to prevent memory leaks.
     */
    external fun unloadModel()

    /**
     * Returns true if a model is currently loaded and ready.
     */
    external fun isModelLoaded(): Boolean
}

/**
 * LlamaCallback — interface that C++ calls back into during generation.
 *
 * Each method is called from the C++ generation thread via JNI.
 * The implementation in LlamaEngine posts results to a Kotlin Flow.
 */
interface LlamaCallback {
    /** Called for each generated token piece (may be partial word) */
    fun onToken(token: String)

    /** Called when generation finishes normally or hits max tokens */
    fun onComplete()

    /** Called if an error occurs during generation */
    fun onError(message: String)

    /**
     * Measured throughput. Fired once when prefill completes (before the first token, so
     * time-to-first-token is observable), then throttled during decode, then once at the
     * end.
     *
     * JNI signature is `(IIJJ)V` — changing these parameter types means changing the
     * `GetMethodID` lookup in scanborn_jni.cpp to match, or the call silently no-ops.
     *
     * Defaulted so existing anonymous implementations do not have to care.
     */
    fun onStats(promptTokens: Int, genTokens: Int, prefillMs: Long, decodeMs: Long) {}
}
