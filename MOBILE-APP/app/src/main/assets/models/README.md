# The on-device model

`qwen.gguf` is not in the repository (1.04 GiB; `*.gguf` is gitignored). Put it here before
building: the app bundles it as an asset and copies it to internal storage on first launch
(`ai/storage/ModelStorageManager.kt`).

| | |
|---|---|
| Source | [`Qwen/Qwen2.5-1.5B-Instruct-GGUF`](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF), file `qwen2.5-1.5b-instruct-q4_k_m.gguf`, renamed to `qwen.gguf` |
| Size | 1,117,320,736 bytes |
| SHA-256 | `6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e` |
| Model | Qwen2.5-1.5B-Instruct, Q4_K_M (GGUF v3, `general.file_type` 15) |
| Licence | Apache-2.0 (the repository's model card) |
| Runs on | llama.cpp, vendored in `app/src/main/cpp/llama`, on the phone's CPU |

Checked 2026-09-16: the size and SHA-256 above were computed from the file used to build the app,
and both match the listing Hugging Face's API returns for that file in that repository.

To check a copy:

```bash
sha256sum app/src/main/assets/models/qwen.gguf
```

Measured speed on the Galaxy S24 FE is in `benchmarks/phone.json`.
