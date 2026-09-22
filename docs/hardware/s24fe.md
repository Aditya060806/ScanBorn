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
| CPU features | `asimddp` (dot product), `i8mm` (int8 matrix multiply), `sve`, `sve2`, `bf16` — identical on all 10 cores | `/proc/cpuinfo` |
| Free storage | 5.2 GB of 105 GB (96 % used) as the first install began; **1.9 GB (99 %)** after its first launch extracted the model; 8.4 GB after the owner freed space; 8.7 GB before installing the repack build | `df -h /data` |
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

`LlamaEngine.inferenceThreads()` returns 6 when `availableProcessors()` is 10 or more — one
thread per core above the efficiency cluster. The threads are not pinned; the 4-versus-6
comparison below is what checks the choice.

## First launch of the debug build

Measured on 2026-09-14 with `am start -W` and logcat, baseline build.

| Step | Result |
|---|---|
| Install (`adb install -r`, streamed) | Success, 65 s for the 1.23 GB APK |
| Cold start to first frame | 1108 ms (`TotalTime`) |
| Model extraction (asset → `files/models/qwen.gguf`) | 1,117,320,736 bytes in about 4 s |
| Model load (`LlamaJniBridge.loadModel`) | 2.6 s, `ctx=4096, threads=6 of 10 cores` |
| llama.cpp notices | `'</s>' was not control-type` (a Qwen GGUF quirk llama.cpp overrides) and `n_ctx_seq (4096) < n_ctx_train (32768)` (informational) — neither is an error |

## Model speed

Chat messages sent by hand on 2026-09-14; timings come from the app's own `onStats`
callback, read from logcat. Raw numbers, memory snapshots and buffer sizes are in
`benchmarks/phone.json`.

| Build | Threads | Prompt | Prefill | First token | Decode (256 tok) | Total |
|---|---|---|---|---|---|---|
| Baseline | 6 | 331 tok | 35.2 tok/s | 9.4 s | 14.5 tok/s | 27.1 s |
| ARM-optimised | 6 | 359 tok | 41.5 tok/s | 8.8 s | 19.1 tok/s | 22.2 s |
| ARM-optimised | 4 | 359 tok | 43.7 tok/s | 8.3 s | 16.8 tok/s | 23.5 s |
| ARM-optimised + repack, **repack on** | 6 | 359 tok | **69.0 tok/s** | **5.3 s** | 18.8 tok/s | **19.0 s** |
| ARM-optimised + repack, repack off | 6 | 359 tok | 46.8 tok/s | 7.7 s | 14.2 tok/s | 25.7 s |

Each row is a single run, phone temperature was not controlled, and every run stopped at
`MAX_TOKENS = 256`. **Decode is noisy:** runs with the same decode kernels and the same prompt
ranged from 14.2 to 19.1 tok/s, so decode differences of that size are not evidence of
anything. Prefill is steadier — 41–47 tok/s in every run without repacking.

### Why the baseline was slow

- **Not threads.** `scanborn_jni.cpp` sets both `n_threads` and `n_threads_batch`, and
  `n_batch = n_ubatch = 512` reads these prompts in one pass.
- **Not optimisation.** `compile_commands.json` shows `-O3` on the llama.cpp sources.
- **No architecture flags.** Nothing was compiled with `-march` or `-mcpu`, so clang targeted
  baseline Armv8.0. ggml chooses its fast ARM kernels at compile time — 138
  `__ARM_FEATURE_DOTPROD` / `__ARM_FEATURE_MATMUL_INT8` / `__ARM_FEATURE_SVE` checks across
  `ggml-cpu` — and a probe compile with the NDK's clang shows those macros are defined only
  when the flag is passed.

### Fix 1 — compile for this CPU

`ggml_cpu` — the kernel sources only — is compiled with `-march=armv8.2-a+dotprod+i8mm`. The
built `libscanborn_jni.so` contains 209 `sdot` and 36 `smmla` instructions, which a baseline
build cannot contain. `scanborn_jni.cpp` stays at baseline and checks `HWCAP_ASIMDDP` and
`HWCAP2_I8MM` before loading the model; on this phone it logs
`CPU features: dotprod + i8mm present`. Its `loadModel` contains none of the new
instructions, so the check itself runs on any arm64 phone and refuses to load where the
kernels would crash with `SIGILL`.

Against the baseline at 6 threads: prefill about +18 % and a full reply about 18 % sooner.

**Threads.** 4 threads read the prompt about 5 % faster but finished the reply about 6 % later.
Six stays the default. Prefill barely moving between 4 and 6 threads showed that prompt
reading was no longer limited by thread count — which pointed at the next fix.

### Fix 2 — weight repacking

ggml can repack quantised weights at load time into interleaved layouts built for its int8
kernels. It was compiled in but never enabled: `ggml-cpu.cpp` registers the repack buffer only
under `#ifdef GGML_USE_CPU_REPACK`, which upstream ggml's own CMake defines and this project's
hand-written `CMakeLists.txt` did not. It is now defined on `ggml_cpu`.

The vendored `repack.cpp` repacks Q4_K, Q5_K and Q6_K when NEON and int8 matmul are available.
From the model's tensor table, 934.1 MiB of its Q4_K and Q6_K matmul weights qualify (the
125.2 MiB token embedding does not), and the device logged exactly
`CPU_REPACK model buffer size = 934.14 MiB` — every eligible weight was repacked.

`scanborn_jni.cpp` reads `debug.scanborn.repack` at model load (unset = on, `0` = off), so both
modes were measured from one installed binary with the same prompt:

| | Repack on | Repack off |
|---|---|---|
| Prefill | **69.0 tok/s** (+47 %) | 46.8 tok/s |
| First token | **5.3 s** (31 % sooner) | 7.7 s |
| Full reply | **19.0 s** (26 % shorter) | 25.7 s |
| Model load | 2.8 s | 2.1 s |

### What repacking costs in memory

Repacked weights live in their own buffer instead of the memory-mapped model file. Memory
from `/proc/<pid>/status` and `dumpsys meminfo`, in GiB:

| | Repack on | Repack off |
|---|---|---|
| After load — total PSS | 2.10 | 1.36 |
| After load — app-owned (`RssAnon`) / model file (`RssFile`) | 1.12 / 0.93 | 0.28 / 1.11 |
| After a reply — total PSS | 1.35 | 1.33 |
| After a reply — app-owned (`RssAnon`) / model file (`RssFile`) | **1.15** / 0.06 | **0.28** / 1.02 |

After a reply the totals are almost the same, but the composition is not. With repacking off,
most of the model is file pages that Android can drop under memory pressure and read back
later. With repacking on, about 0.9 GiB more is app-owned memory Android cannot drop — only
compress into swap (125 MB swapped after a reply, against 83 MB with repacking off). Right after
load the repack build briefly holds both copies (2.10 GiB PSS) until the file pages are released.

**Decision: repacking stays on by default.** A first token 31 % sooner is the most visible speed
difference any change measured here, and ScanBorn's language features run in the foreground.
The cost is that ScanBorn becomes a larger target for Android's background killer and cannot
share RAM with ARCore capture, so the plan adds releasing the model before a scan and on memory
pressure. `debug.scanborn.repack=0` turns it off without a rebuild if that trade stops being
worth it.

## What this means for ScanBorn

- **Storage is the tightest constraint on disk.** Installing the app and extracting the model
  took free space from 5.2 GB to 1.9 GB (99 %). At that level Android can refuse writes, which
  breaks the Room library database, capture uploads and the next install. Keep several GB free
  before scanning or reinstalling.
- **The app keeps two copies of the model on disk** — 1.06 GB inside the APK and 1.06 GB
  extracted. The sideload option in the plan (§5: load `qwen.gguf` directly from
  `getExternalFilesDir("models")`, with a `lite` build that leaves the asset out of the APK)
  removes one copy.
- **The on-device model is usable for ScanBorn's language features.** With both fixes, a
  ~350-token prompt gets its first token in about 5 s and a 256-token reply finishes in about
  19 s. The robot planner's prompts and replies are much shorter; their timing has not been
  measured yet.
- **RAM:** with repacking on, the model holds about 1.15 GiB of app-owned memory while loaded.
  Don't run ARCore capture and generation at the same time; release the model first.
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
adb shell "grep -m1 Features /proc/cpuinfo"
adb shell "for c in /sys/devices/system/cpu/cpu[0-9]*; do echo ${c##*/} $(cat $c/cpufreq/cpuinfo_max_freq); done"
adb shell dumpsys package com.google.ar.core | findstr versionName
adb shell am start -W -n com.scanborn.ai/.MainActivity
adb logcat -d -s LlamaEngine ScanBornLlama:D AIRepository ModelStorage
adb shell dumpsys meminfo com.scanborn.ai
```

Both debug switches are read at the next model load, so set one, then force-stop and reopen
ScanBorn. The load log confirms with `threads overridden by debug.scanborn.threads=4` or
`weight repacking: off (debug.scanborn.repack=0)`. Setting a switch to `"''"` clears it.

```
adb shell setprop debug.scanborn.threads 4
adb shell setprop debug.scanborn.repack 0
adb shell am force-stop com.scanborn.ai
adb shell setprop debug.scanborn.threads "''"
adb shell setprop debug.scanborn.repack "''"
```
