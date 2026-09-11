#include <jni.h>
#include <android/log.h>
#include <string>
#include <vector>
#include <atomic>
#include <pthread.h>

#include "llama.h"
#include "ggml-backend.h"
#include "ggml-cpu.h"

#define LOG_TAG "ScanBornLlama"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO,  LOG_TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)

// ── Global state ──────────────────────────────────────────────────────────────
// g_state_mutex  : guards g_model / g_ctx pointer reads+writes
// g_gen_mutex    : ensures only one generation thread runs at a time
// g_stop         : atomic flag — set by stopGeneration(), cleared at gen start
static pthread_mutex_t   g_state_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t   g_gen_mutex   = PTHREAD_MUTEX_INITIALIZER;
static llama_model*      g_model       = nullptr;
static llama_context*    g_ctx         = nullptr;
static std::atomic<bool> g_stop{false};
static bool              g_backend     = false;
static JavaVM*           g_jvm         = nullptr;

JNIEXPORT jint JNI_OnLoad(JavaVM* vm, void*) {
    g_jvm = vm;
    return JNI_VERSION_1_6;
}

static std::string jstr(JNIEnv* env, jstring s) {
    if (!s) return "";
    const char* c = env->GetStringUTFChars(s, nullptr);
    std::string r(c);
    env->ReleaseStringUTFChars(s, c);
    return r;
}

struct GenContext {
    std::string prompt;
    int         maxTokens;
    // GBNF source, or empty for unconstrained generation. When set, the model can only
    // emit strings the grammar accepts — which is what lets a 1.5B model produce reliable
    // task-graph and environment-profile JSON without a larger model or a retry loop.
    std::string grammar;
    jobject     callback;
    jmethodID   onToken;
    jmethodID   onComplete;
    jmethodID   onError;
};

static void run_generation(JNIEnv* env, GenContext* ctx) {
    // ── Reset stop flag FIRST, before any early-return path ──────────────────
    // Bug fix: previously reset AFTER the null check, so a prior stopGeneration()
    // call would leave g_stop=true and the generation loop would exit immediately.
    g_stop.store(false);

    auto fireError = [&](const char* msg) {
        LOGE("%s", msg);
        jstring jmsg = env->NewStringUTF(msg);
        env->CallVoidMethod(ctx->callback, ctx->onError, jmsg);
        env->DeleteLocalRef(jmsg);
    };

    // ── Snapshot model/ctx pointers under lock ────────────────────────────────
    // Bug fix: previously vocab was obtained under lock then used after unlock,
    // creating a dangling pointer if unloadModel() ran concurrently.
    // Solution: snapshot both pointers once. unloadModel() sets them to nullptr
    // under the same lock, so if we got non-null here the objects are valid for
    // the duration of this generation (unloadModel blocks on g_gen_mutex).
    pthread_mutex_lock(&g_state_mutex);
    llama_model*   model = g_model;
    llama_context* ctx_  = g_ctx;
    pthread_mutex_unlock(&g_state_mutex);

    if (!model || !ctx_) {
        fireError("Model not loaded");
        return;
    }

    const llama_vocab* vocab = llama_model_get_vocab(model);

    // ── Tokenize ──────────────────────────────────────────────────────────────
    const int n = -llama_tokenize(vocab, ctx->prompt.c_str(),
                                  (int32_t)ctx->prompt.size(), nullptr, 0, true, true);
    if (n <= 0) { fireError("Tokenize size failed"); return; }

    std::vector<llama_token> tokens(n);
    if (llama_tokenize(vocab, ctx->prompt.c_str(), (int32_t)ctx->prompt.size(),
                       tokens.data(), n, true, true) < 0) {
        fireError("Tokenization failed");
        return;
    }
    LOGI("Prompt tokens: %d", n);

    // ── Prompt prefill ────────────────────────────────────────────────────────
    // Bug fix: previously the mutex was held across the ENTIRE decode loop,
    // blocking unloadModel() for the full prefill duration (seconds for large prompts).
    // Fix: lock per-chunk so unloadModel() can acquire the lock between chunks.
    llama_memory_clear(llama_get_memory(ctx_), true);

    for (int i = 0; i < n; i += 512) {
        if (g_stop.load()) return;

        // Re-check ctx_ is still valid before each chunk
        pthread_mutex_lock(&g_state_mutex);
        llama_context* live_ctx = g_ctx;
        pthread_mutex_unlock(&g_state_mutex);
        if (!live_ctx) return;

        int chunk = std::min(512, n - i);
        llama_batch batch = llama_batch_get_one(tokens.data() + i, chunk);
        if (llama_decode(live_ctx, batch) != 0) {
            fireError("Prompt decode failed");
            return;
        }
    }

    // ── Sampler chain ─────────────────────────────────────────────────────────
    auto sparams        = llama_sampler_chain_default_params();
    llama_sampler* smpl = llama_sampler_chain_init(sparams);
    if (!smpl) { fireError("Failed to initialize sampler"); return; }

    if (!ctx->grammar.empty()) {
        // The grammar goes in FIRST: it masks every token the grammar cannot accept, and
        // whatever follows must only ever choose among what is left. Adding it after a
        // truncating sampler would let top_p discard the only legal continuation and leave
        // the grammar nothing to pick.
        llama_sampler* gr = llama_sampler_init_grammar(vocab, ctx->grammar.c_str(), "root");
        if (!gr) {
            // A caller that asked for constrained output must never silently receive
            // unconstrained output — that is how invalid JSON reaches the parser while
            // the logs claim a grammar was applied.
            llama_sampler_free(smpl);
            fireError("Invalid GBNF grammar — refusing to generate unconstrained");
            return;
        }
        llama_sampler_chain_add(smpl, gr);

        // Greedy, not sampled. Structured extraction should be reproducible: the same
        // scene must yield the same profile twice, or a bad demo cannot be diagnosed.
        // This matches the temperature-0 choice on the remote providers.
        llama_sampler_chain_add(smpl, llama_sampler_init_greedy());
        LOGI("Grammar-constrained generation (%zu bytes of GBNF)", ctx->grammar.size());
    } else {
        llama_sampler_chain_add(smpl, llama_sampler_init_top_p(0.9f, 1));
        llama_sampler_chain_add(smpl, llama_sampler_init_temp(0.7f));
        llama_sampler_chain_add(smpl, llama_sampler_init_dist(LLAMA_DEFAULT_SEED));
    }

    bool stopped_by_flag = false;

    // ── Token generation loop ─────────────────────────────────────────────────
    for (int i = 0; i < ctx->maxTokens; i++) {
        if (g_stop.load()) {
            stopped_by_flag = true;
            break;
        }

        // Re-check ctx_ is still valid (unloadModel sets g_ctx=nullptr under lock)
        pthread_mutex_lock(&g_state_mutex);
        llama_context* live_ctx = g_ctx;
        pthread_mutex_unlock(&g_state_mutex);
        if (!live_ctx) { stopped_by_flag = true; break; }

        llama_token tok = llama_sampler_sample(smpl, live_ctx, -1);
        if (llama_vocab_is_eog(vocab, tok)) break;

        char buf[256] = {};
        int  len = llama_token_to_piece(vocab, tok, buf, sizeof(buf), 0, true);
        if (len < 0) break;

        jstring jtok = env->NewStringUTF(std::string(buf, len).c_str());
        env->CallVoidMethod(ctx->callback, ctx->onToken, jtok);
        env->DeleteLocalRef(jtok);
        if (env->ExceptionCheck()) { env->ExceptionClear(); break; }

        pthread_mutex_lock(&g_state_mutex);
        live_ctx = g_ctx;
        pthread_mutex_unlock(&g_state_mutex);
        if (!live_ctx) { stopped_by_flag = true; break; }

        llama_batch nb = llama_batch_get_one(&tok, 1);
        if (llama_decode(live_ctx, nb) != 0) break;
    }

    llama_sampler_free(smpl);

    // Bug fix: previously onComplete() was called even when stopped via g_stop.
    // This caused ChatViewModel's onCompletion handler to replace valid partial
    // responses with an error message. Only call onComplete for natural endings.
    if (!stopped_by_flag) {
        LOGI("Generation complete");
        env->CallVoidMethod(ctx->callback, ctx->onComplete);
    }
    // If stopped_by_flag: the Kotlin callbackFlow's awaitClose already fired
    // (job was cancelled), so the channel is already closed — no callback needed.
}

static void* generation_thread(void* arg) {
    GenContext* ctx = static_cast<GenContext*>(arg);

    // Bug fix: g_gen_mutex ensures only one generation thread runs at a time.
    // Previously two threads could share g_ctx simultaneously causing corruption.
    pthread_mutex_lock(&g_gen_mutex);

    JNIEnv* env = nullptr;
    JavaVMAttachArgs attachArgs = { JNI_VERSION_1_6, "llama-inference", nullptr };
    if (g_jvm->AttachCurrentThread(&env, &attachArgs) != JNI_OK || !env) {
        LOGE("Failed to attach inference thread to JVM");
        pthread_mutex_unlock(&g_gen_mutex);
        // env is null here — cannot call DeleteGlobalRef, so we leak the global ref.
        // This is an unrecoverable JVM error; leaking one ref is acceptable.
        delete ctx;
        return nullptr;
    }

    run_generation(env, ctx);

    env->DeleteGlobalRef(ctx->callback);
    delete ctx;
    g_jvm->DetachCurrentThread();
    pthread_mutex_unlock(&g_gen_mutex);
    return nullptr;
}

extern "C" {

JNIEXPORT jboolean JNICALL
Java_com_scanborn_ai_ai_runtime_LlamaJniBridge_loadModel(
        JNIEnv* env, jobject, jstring modelPath, jint nCtx, jint nThreads) {

    // Bug fix: model+context assignment now fully under mutex so isModelLoaded()
    // never sees a partially-initialised state (model set, ctx not yet set).
    pthread_mutex_lock(&g_state_mutex);
    if (g_ctx)   { llama_free(g_ctx);         g_ctx   = nullptr; }
    if (g_model) { llama_model_free(g_model); g_model = nullptr; }
    pthread_mutex_unlock(&g_state_mutex);

    if (!g_backend) {
        llama_backend_init();
        ggml_backend_register(ggml_backend_cpu_reg());
        g_backend = true;
        LOGI("llama backend + CPU registered");
    }

    llama_log_set([](ggml_log_level level, const char* text, void*) {
        if (level == GGML_LOG_LEVEL_ERROR || level == GGML_LOG_LEVEL_WARN)
            __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, "[llama] %s", text);
        else
            __android_log_print(ANDROID_LOG_DEBUG, LOG_TAG, "[llama] %s", text);
    }, nullptr);

    std::string path = jstr(env, modelPath);
    LOGI("Loading model: %s", path.c_str());

    llama_model_params mp = llama_model_default_params();
    mp.n_gpu_layers = 0;

    llama_model* new_model = llama_model_load_from_file(path.c_str(), mp);
    if (!new_model) { LOGE("Failed to load model"); return JNI_FALSE; }

    llama_context_params cp = llama_context_default_params();
    cp.n_ctx           = (uint32_t)nCtx;
    cp.n_batch         = 512;
    cp.n_ubatch        = 512;
    cp.n_threads       = (uint32_t)nThreads;
    cp.n_threads_batch = (uint32_t)nThreads;

    llama_context* new_ctx = llama_init_from_model(new_model, cp);
    if (!new_ctx) {
        LOGE("Failed to create context");
        llama_model_free(new_model);
        return JNI_FALSE;
    }

    // Assign both atomically under the lock
    pthread_mutex_lock(&g_state_mutex);
    g_model = new_model;
    g_ctx   = new_ctx;
    pthread_mutex_unlock(&g_state_mutex);

    LOGI("Model loaded OK. ctx=%d tokens", nCtx);
    return JNI_TRUE;
}

JNIEXPORT void JNICALL
Java_com_scanborn_ai_ai_runtime_LlamaJniBridge_generate(
        JNIEnv* env, jobject, jstring prompt, jint maxTokens, jstring grammar,
        jobject callback) {

    jclass    cls        = env->GetObjectClass(callback);
    jmethodID onToken    = env->GetMethodID(cls, "onToken",    "(Ljava/lang/String;)V");
    jmethodID onComplete = env->GetMethodID(cls, "onComplete", "()V");
    jmethodID onError    = env->GetMethodID(cls, "onError",    "(Ljava/lang/String;)V");

    GenContext* ctx = new GenContext();
    ctx->prompt     = jstr(env, prompt);
    ctx->maxTokens  = (int)maxTokens;
    ctx->grammar    = jstr(env, grammar);   // "" means unconstrained
    ctx->callback   = env->NewGlobalRef(callback);
    ctx->onToken    = onToken;
    ctx->onComplete = onComplete;
    ctx->onError    = onError;

    pthread_t      thread;
    pthread_attr_t attr;
    pthread_attr_init(&attr);
    pthread_attr_setdetachstate(&attr, PTHREAD_CREATE_DETACHED);
    if (pthread_create(&thread, &attr, generation_thread, ctx) != 0) {
        LOGE("Failed to create inference thread");
        jstring jmsg = env->NewStringUTF("Failed to start inference thread");
        env->CallVoidMethod(callback, onError, jmsg);
        env->DeleteLocalRef(jmsg);
        env->DeleteGlobalRef(ctx->callback);
        delete ctx;
    }
    pthread_attr_destroy(&attr);
}

JNIEXPORT void JNICALL
Java_com_scanborn_ai_ai_runtime_LlamaJniBridge_stopGeneration(JNIEnv*, jobject) {
    g_stop.store(true);
}

JNIEXPORT void JNICALL
Java_com_scanborn_ai_ai_runtime_LlamaJniBridge_unloadModel(JNIEnv*, jobject) {
    // Wait for any running generation to finish before freeing memory.
    // g_gen_mutex is held by the generation thread for its entire lifetime.
    pthread_mutex_lock(&g_gen_mutex);
    pthread_mutex_lock(&g_state_mutex);
    if (g_ctx)   { llama_free(g_ctx);         g_ctx   = nullptr; }
    if (g_model) { llama_model_free(g_model); g_model = nullptr; }
    pthread_mutex_unlock(&g_state_mutex);
    pthread_mutex_unlock(&g_gen_mutex);
    LOGI("Model unloaded");
}

JNIEXPORT jboolean JNICALL
Java_com_scanborn_ai_ai_runtime_LlamaJniBridge_isModelLoaded(JNIEnv*, jobject) {
    pthread_mutex_lock(&g_state_mutex);
    jboolean result = (g_model && g_ctx) ? JNI_TRUE : JNI_FALSE;
    pthread_mutex_unlock(&g_state_mutex);
    return result;
}

} // extern "C"
