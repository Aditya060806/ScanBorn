/**
 * scanborn_jni_stub.cpp
 *
 * This stub is compiled when llama.cpp sources are not yet present.
 * All functions return safe "not available" values so the app builds
 * and runs — AI features will show an error state instead of crashing.
 *
 * Once you run setup_llama.ps1 and place the llama.cpp sources,
 * CMakeLists.txt will automatically switch to the real scanborn_jni.cpp.
 */

#include <jni.h>
#include <android/log.h>

#define LOG_TAG "ScanBornStub"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO,  LOG_TAG, __VA_ARGS__)

extern "C" {

JNIEXPORT jboolean JNICALL
Java_com_scanborn_ai_ai_runtime_LlamaJniBridge_loadModel(
        JNIEnv*, jobject, jstring, jint, jint) {
    LOGI("STUB: loadModel called — llama.cpp not set up yet. Run setup_llama.ps1");
    return JNI_FALSE;
}

JNIEXPORT void JNICALL
Java_com_scanborn_ai_ai_runtime_LlamaJniBridge_generate(
        JNIEnv* env, jobject, jstring, jint, jstring, jobject callback) {
    LOGI("STUB: generate called — llama.cpp not set up yet");
    jclass cb_class = env->GetObjectClass(callback);
    jmethodID on_error = env->GetMethodID(cb_class, "onError", "(Ljava/lang/String;)V");
    jstring msg = env->NewStringUTF("AI engine not set up. Run setup_llama.ps1 and place the model file.");
    env->CallVoidMethod(callback, on_error, msg);
    env->DeleteLocalRef(msg);
}

JNIEXPORT void JNICALL
Java_com_scanborn_ai_ai_runtime_LlamaJniBridge_stopGeneration(JNIEnv*, jobject) {}

JNIEXPORT void JNICALL
Java_com_scanborn_ai_ai_runtime_LlamaJniBridge_unloadModel(JNIEnv*, jobject) {}

JNIEXPORT jboolean JNICALL
Java_com_scanborn_ai_ai_runtime_LlamaJniBridge_isModelLoaded(JNIEnv*, jobject) {
    return JNI_FALSE;
}

} // extern "C"
