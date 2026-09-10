cmake_minimum_required(VERSION 3.22.1)
project("scanborn_llama")

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# IMPORTANT: Do NOT use -ffast-math — it enables -ffinite-math-only which
# breaks ggml (it uses INFINITY/-INFINITY extensively).
# Use -O3 + explicitly re-enable non-finite math.
set(SAFE_MATH_FLAGS "-O3 -fno-finite-math-only")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${SAFE_MATH_FLAGS}")
set(CMAKE_C_FLAGS   "${CMAKE_C_FLAGS}   ${SAFE_MATH_FLAGS}")

set(LLAMA_DIR "${CMAKE_CURRENT_SOURCE_DIR}/llama")

if(EXISTS "${LLAMA_DIR}/include/llama.h")
    message(STATUS "llama.cpp sources found — building full AI engine")

    # ── ggml base (ggml.c + ggml.cpp need GGML_VERSION/GGML_COMMIT defines) ──
    set(GGML_CPU_DIR "${LLAMA_DIR}/ggml/src/ggml-cpu")

    add_library(ggml_cpu STATIC
        ${LLAMA_DIR}/ggml/src/ggml.c
        ${LLAMA_DIR}/ggml/src/ggml.cpp
        ${LLAMA_DIR}/ggml/src/gguf.cpp
        ${LLAMA_DIR}/ggml/src/ggml-alloc.c
        ${LLAMA_DIR}/ggml/src/ggml-backend.cpp
        ${LLAMA_DIR}/ggml/src/ggml-backend-dl.cpp
        ${LLAMA_DIR}/ggml/src/ggml-backend-meta.cpp
        ${LLAMA_DIR}/ggml/src/ggml-backend-reg.cpp
        ${LLAMA_DIR}/ggml/src/ggml-opt.cpp
        ${LLAMA_DIR}/ggml/src/ggml-quants.c
        ${LLAMA_DIR}/ggml/src/ggml-threading.cpp
        ${GGML_CPU_DIR}/ggml-cpu.c
        ${GGML_CPU_DIR}/ggml-cpu.cpp
        ${GGML_CPU_DIR}/quants.c
        ${GGML_CPU_DIR}/traits.cpp
        ${GGML_CPU_DIR}/ops.cpp
        ${GGML_CPU_DIR}/binary-ops.cpp
        ${GGML_CPU_DIR}/unary-ops.cpp
        ${GGML_CPU_DIR}/vec.cpp
        ${GGML_CPU_DIR}/repack.cpp
        ${GGML_CPU_DIR}/hbm.cpp
        ${GGML_CPU_DIR}/arch/arm/quants.c
        ${GGML_CPU_DIR}/arch/arm/cpu-feats.cpp
        ${GGML_CPU_DIR}/arch/arm/repack.cpp
        # Bug fix: AMX (Advanced Matrix Extensions) is an Intel x86 instruction set.
        # Including amx.cpp / mmq.cpp on ARM64 Android either fails to compile or
        # silently compiles dead code. Exclude them for all Android/ARM builds.
        ${GGML_CPU_DIR}/llamafile/sgemm.cpp
    )

    target_include_directories(ggml_cpu PUBLIC
        ${LLAMA_DIR}/ggml/include
        ${LLAMA_DIR}/ggml/src
        ${LLAMA_DIR}/ggml/src/ggml-cpu
    )

    # Provide the version/commit defines that ggml.c expects
    target_compile_definitions(ggml_cpu PUBLIC
        GGML_VERSION="0.11.1"
        GGML_COMMIT="android-build"
        GGML_USE_CPU
        GGML_BACKEND_DL=0
        NDEBUG
    )

    # ── llama core ────────────────────────────────────────────────────────────
    # Collect all per-model architecture sources
    file(GLOB LLAMA_MODEL_SOURCES "${LLAMA_DIR}/src/models/*.cpp")

    add_library(llama_core STATIC
        ${LLAMA_DIR}/src/llama.cpp
        ${LLAMA_DIR}/src/llama-adapter.cpp
        ${LLAMA_DIR}/src/llama-arch.cpp
        ${LLAMA_DIR}/src/llama-batch.cpp
        ${LLAMA_DIR}/src/llama-chat.cpp
        ${LLAMA_DIR}/src/llama-context.cpp
        ${LLAMA_DIR}/src/llama-cparams.cpp
        ${LLAMA_DIR}/src/llama-grammar.cpp
        ${LLAMA_DIR}/src/llama-graph.cpp
        ${LLAMA_DIR}/src/llama-hparams.cpp
        ${LLAMA_DIR}/src/llama-impl.cpp
        ${LLAMA_DIR}/src/llama-io.cpp
        ${LLAMA_DIR}/src/llama-kv-cache.cpp
        ${LLAMA_DIR}/src/llama-kv-cache-iswa.cpp
        ${LLAMA_DIR}/src/llama-memory.cpp
        ${LLAMA_DIR}/src/llama-memory-hybrid.cpp
        ${LLAMA_DIR}/src/llama-memory-hybrid-iswa.cpp
        ${LLAMA_DIR}/src/llama-memory-recurrent.cpp
        ${LLAMA_DIR}/src/llama-mmap.cpp
        ${LLAMA_DIR}/src/llama-model.cpp
        ${LLAMA_DIR}/src/llama-model-loader.cpp
        ${LLAMA_DIR}/src/llama-model-saver.cpp
        ${LLAMA_DIR}/src/llama-quant.cpp
        ${LLAMA_DIR}/src/llama-sampler.cpp
        ${LLAMA_DIR}/src/llama-vocab.cpp
        ${LLAMA_DIR}/src/unicode.cpp
        ${LLAMA_DIR}/src/unicode-data.cpp
        ${LLAMA_MODEL_SOURCES}
    )

    target_include_directories(llama_core PUBLIC
        ${LLAMA_DIR}/include
        ${LLAMA_DIR}/src
        ${LLAMA_DIR}/src/models
        ${LLAMA_DIR}/ggml/include
        ${LLAMA_DIR}/ggml/src
    )

    target_compile_definitions(llama_core PRIVATE
        GGML_USE_CPU
        GGML_BACKEND_DL=0
        NDEBUG
    )

    target_link_libraries(llama_core ggml_cpu)

    # ── JNI bridge ────────────────────────────────────────────────────────────
    add_library(scanborn_jni SHARED scanborn_jni.cpp)

    target_include_directories(scanborn_jni PRIVATE
        ${LLAMA_DIR}/include
        ${LLAMA_DIR}/src
        ${LLAMA_DIR}/ggml/include
    )

    target_compile_definitions(scanborn_jni PRIVATE
        GGML_USE_CPU
        GGML_BACKEND_DL=0
    )

    target_link_libraries(scanborn_jni llama_core ggml_cpu android log dl)

else()
    message(STATUS "llama.cpp sources NOT found — building stub JNI")
    add_library(scanborn_jni SHARED scanborn_jni_stub.cpp)
    target_link_libraries(scanborn_jni android log)
endif()
