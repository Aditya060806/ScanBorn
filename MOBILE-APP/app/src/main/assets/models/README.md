# Model Setup

The AI model file is NOT included in this repository due to its size (1.04 GB).

## Download Instructions

1. Download the model from HuggingFace:
   - Model: `qwen.gguf`
   - URL: https://huggingface.co/  (add your exact model link here)

2. Place the downloaded file here:
   ```
   app/src/main/assets/models/qwen.gguf
   ```

3. The app will load it automatically at runtime.

## Model Info
- Format: GGUF (llama.cpp compatible)
- Size: ~1.04 GB
- Engine: ScanBorn-X1 via llama.cpp JNI bridge
