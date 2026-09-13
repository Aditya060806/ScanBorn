# Galaxy S24 FE — measured

Read over `adb shell` on 2026-09-14 from the phone this project is built on. Every value in
the first two tables was read from the device, not a spec sheet.

| Property | Value | Read from |
|---|---|---|
| Model | SM-S721B (`r12s`) | `getprop ro.product.model`, `ro.product.device` |
| Android | 16 (API 36) | `ro.build.version.release`, `ro.build.version.sdk` |
| SoC | Samsung `s5e9945` (Exynos 2400e), platform `erd9945` | `ro.soc.model`, `ro.board.platform` |
| ABI | `arm64-v8a` — matches `abiFilters += "arm64-v8a"` in `MOBILE-APP/app/build.gradle.kts` | `ro.product.cpu.abi` |
| RAM | 7.05 GB visible to the kernel (`MemTotal: 7397724 kB`) | `/proc/meminfo` |
| Free storage | **5.2 GB** of 105 GB (96 % used) while the debug APK install was starting; **1.9 GB (99 % used)** after the app's first launch extracted its model | `df -h /data` |
| ARCore | `com.google.ar.core` 1.56.262080393 installed | `dumpsys package com.google.ar.core` |

## CPU layout

| Cores | Max frequency | Cluster |
|---|---|---|
| cpu0–3 | 1.959 GHz | 4 efficiency cores |
| cpu4–6 | 2.592 GHz | 3 performance cores |
| cpu7–8 | 2.900 GHz | 2 performance cores |
| cpu9 | 3.110 GHz | 1 prime core |

Ten cores in four frequency domains, read from each core's `cpufreq/cpuinfo_max_freq`. The
frequencies are consistent with the Exynos 2400e's published configuration of 1× Cortex-X4,
5× Cortex-A720 and 4× Cortex-A520; the core names come from that published configuration,
the frequencies from the device.

The **six** cores above the efficiency cluster (cpu4–9) are where llama.cpp's threads belong.
llama.cpp runs a batch only as fast as its slowest thread, so one thread landing on a 1.96 GHz
core sets the pace for all of them.

`LlamaEngine.inferenceThreads()` returns 6 when `availableProcessors()` is 10 or more, so on
this phone the engine already asks for six threads. The threads are not pinned, so whether
the scheduler actually keeps them on cpu4–9 is what the 4-versus-6-thread throughput
measurement checks.

## First launch of the debug build

Measured on 2026-09-14 with `am start -W` and logcat.

| Step | Result |
|---|---|
| Install (`adb install -r`, streamed) | Success, 65 s for the 1.23 GB APK |
| Cold start to first frame | 1108 ms (`TotalTime`) |
| Model extraction (asset → `files/models/qwen.gguf`) | 1,117,320,736 bytes in about 4 s |
| Model load (`LlamaJniBridge.loadModel`) | 2.6 s, `ctx=4096, threads=6 of 10 cores` |
| llama.cpp notices | `'</s>' was not control-type` (Qwen GGUF quirk, overridden by llama.cpp); `n_ctx_seq (4096) < n_ctx_train (32768)` (informational) — neither is an error |

## Model speed

One chat message sent by hand on 2026-09-14; timings come from the app's own `onStats`
callback, read from logcat. Raw numbers are in `benchmarks/phone.json`.

| Phase | Result |
|---|---|
| Prompt (after the chat template) | 331 tokens (1,840 characters) |
| Prefill — reading the prompt | 35.2 tok/s; first token after 9.4 s |
| Decode — writing the reply | 19.1 tok/s over the first 8 tokens; 14.5 tok/s over all 256 |
| Total | 27.1 s; stopped at `MAX_TOKENS = 256` |

Reply speed is fine for a 1.5B model on CPU. **Prompt reading is the bottleneck, and the likely
cause is how the app is compiled, not the chip:**

- Threads are not the cause: `scanborn_jni.cpp` sets both `n_threads` and `n_threads_batch` to
  6, and `n_batch = n_ubatch = 512` reads this prompt in one pass.
- Optimisation is not the cause: `compile_commands.json` shows `-O3` on the llama.cpp sources.
- **The architecture flags are missing.** No source is compiled with `-march` or `-mcpu`, so
  clang targets baseline Armv8.0. ggml chooses its fast ARM kernels at compile time — 138
  `__ARM_FEATURE_DOTPROD` / `__ARM_FEATURE_MATMUL_INT8` / `__ARM_FEATURE_SVE` checks across
  `ggml-cpu` — and those macros exist only when the flags are passed. This phone reports
  `asimddp` (dot product) and `i8mm` on **all 10 cores** in `/proc/cpuinfo`, so the faster
  quantised maths is supported by the chip but compiled out of the app.

The fix is tracked in the plan (Step 0): compile with `-march=armv8.2-a+dotprod+i8mm`, check
for both extensions at model load so an older phone gets a clear error instead of an
illegal-instruction crash, then repeat this exact measurement. How much faster it gets is not
known until it is measured.

## What this means for ScanBorn

- **Storage is the tightest constraint, not RAM — and it is already critical.** Installing the
  app and extracting the model took free space from 5.2 GB to **1.9 GB**. At 99 % full,
  Android can refuse writes, which breaks the Room library database, ARCore capture uploads and
  the next `adb install`. Free space on the phone before scanning.
- **The app keeps two copies of the model** — 1.06 GB inside the APK and 1.06 GB extracted.
  The sideload option in the plan (§5: load `qwen.gguf` directly from
  `getExternalFilesDir("models")`, with a `lite` build that leaves the asset out of the APK)
  removes one copy and is now a priority, not a nicety.
- **RAM:** generation needs roughly 1.5 GB (plan §5). Don't run ARCore capture and generation
  at the same time.
- **ARCore is installed**, so depth capture can be attempted. Depth API support still has to be
  checked at runtime with `session.isDepthModeSupported(Config.DepthMode.AUTOMATIC)` (plan
  Phase 5); being ARCore-capable does not guarantee it.

## Re-measuring

Run from PowerShell or `cmd`. In Git Bash, prefix with `MSYS_NO_PATHCONV=1`, or Git Bash
rewrites `/data` and `/sdcard` into Windows paths before `adb` sees them.

```
adb shell getprop ro.soc.model
adb shell head -1 /proc/meminfo
adb shell df -h /data
adb shell "for c in /sys/devices/system/cpu/cpu[0-9]*; do echo ${c##*/} $(cat $c/cpufreq/cpuinfo_max_freq); done"
adb shell dumpsys package com.google.ar.core | findstr versionName
adb shell am start -W -n com.scanborn.ai/.MainActivity
adb logcat -d -s LlamaEngine ScanBornLlama AIRepository ModelStorage
```
