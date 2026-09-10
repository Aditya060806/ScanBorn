# setup_llama.ps1
# Run this script ONCE from the project root to set up llama.cpp source files.
# Usage: Right-click → "Run with PowerShell" OR run in terminal:
#   powershell -ExecutionPolicy Bypass -File setup_llama.ps1

$ErrorActionPreference = "Stop"

$LLAMA_REPO  = "https://github.com/ggerganov/llama.cpp.git"
$LLAMA_TAG   = "b4570"   # Stable tag — change to latest if needed
$CLONE_DIR   = "$env:TEMP\llama_cpp_src"
$DEST_DIR    = "app\src\main\cpp\llama"

Write-Host "=== ScanBorn AI — llama.cpp Setup ===" -ForegroundColor Cyan
Write-Host "Cloning llama.cpp @ $LLAMA_TAG ..."

# Clone (shallow, just the tag we need)
if (Test-Path $CLONE_DIR) { Remove-Item $CLONE_DIR -Recurse -Force }
git clone --depth 1 --branch $LLAMA_TAG $LLAMA_REPO $CLONE_DIR

Write-Host "Copying source files to $DEST_DIR ..." -ForegroundColor Yellow

# Files we need from llama.cpp root
$cppFiles = @(
    "llama.cpp", "llama.h",
    "llama-vocab.cpp", "llama-vocab.h",
    "llama-sampling.cpp", "llama-sampling.h",
    "llama-context.cpp", "llama-context.h",
    "llama-kv-cache.cpp", "llama-kv-cache.h",
    "llama-model.cpp", "llama-model.h",
    "llama-model-loader.cpp", "llama-model-loader.h",
    "llama-arch.cpp", "llama-arch.h",
    "llama-batch.cpp", "llama-batch.h",
    "llama-chat.cpp", "llama-chat.h",
    "llama-mmap.cpp", "llama-mmap.h",
    "llama-adapter.cpp", "llama-adapter.h",
    "llama-graph.cpp", "llama-graph.h",
    "llama-io.cpp", "llama-io.h",
    "llama-memory.cpp", "llama-memory.h",
    "ggml.c", "ggml.h",
    "ggml-alloc.c", "ggml-alloc.h",
    "ggml-backend.cpp", "ggml-backend.h",
    "ggml-backend-reg.cpp",
    "ggml-cpu.c", "ggml-cpu.cpp", "ggml-cpu.h",
    "ggml-cpu-aarch64.c", "ggml-cpu-aarch64.cpp", "ggml-cpu-aarch64.h",
    "ggml-cpu-quants.c", "ggml-cpu-quants.h",
    "ggml-cpu-traits.cpp", "ggml-cpu-traits.h",
    "ggml-opt.cpp", "ggml-opt.h",
    "ggml-threading.cpp", "ggml-threading.h",
    "ggml-quants.c", "ggml-quants.h",
    "ggml-common.h",
    "unicode.cpp", "unicode.h",
    "unicode-data.cpp", "unicode-data.h"
)

foreach ($file in $cppFiles) {
    $src = Join-Path $CLONE_DIR $file
    $dst = Join-Path $DEST_DIR  $file
    if (Test-Path $src) {
        Copy-Item $src $dst -Force
        Write-Host "  Copied: $file" -ForegroundColor Green
    } else {
        Write-Host "  MISSING: $file (check llama.cpp version)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "=== Done! ===" -ForegroundColor Cyan
Write-Host "Next steps:"
Write-Host "  1. Download Qwen 2.5 1.5B Q4_K_M GGUF from HuggingFace"
Write-Host "  2. Rename it to: qwen.gguf"
Write-Host "  3. Place it at:  app/src/main/assets/models/qwen.gguf"
Write-Host "  4. Sync Gradle in Android Studio"
Write-Host "  5. Build and run!"
