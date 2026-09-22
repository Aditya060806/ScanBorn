# ScanBorn — Android app

The phone half of ScanBorn. It scans a room for the orchestrator on the laptop, and it runs a
small language model on the phone's CPU for chat, reading text and summaries. The target phone is
a **Samsung Galaxy S24 FE** (Exynos 2400e). Nothing here uses an NPU.

## What it does

| Feature | Where | State |
|---|---|---|
| Chat with an on-device model | `ai/engine/LlamaEngine.kt`, `ui/screens/ChatScreen.kt` | Runs on the S24 FE; speed measured in `benchmarks/phone.json` |
| Voice: speech in, spoken replies | `ai/voice/SpeechController.kt`, `ui/screens/VoiceScreen.kt` | Android's `SpeechRecognizer` and `TextToSpeech` |
| Text from photos and screenshots, explanations | `ocr/`, `ui/screens/OcrScreen.kt`, `ScreenshotExplainerScreen.kt` | ML Kit text recognition, bundled model (works offline) |
| PDF summaries, quizzes | `pdf/`, `ui/screens/PdfSummaryScreen.kt`, `QuizScreen.kt` | Text PDFs only |
| Circle to learn (screen overlay) | `circle/` | Needs the overlay permission |
| Library of saved results | `ui/screens/LibraryScreen.kt` | Room database on the phone |
| **Scan a space** | `scan/`, `ui/screens/ScanScreen.kt` | Built and unit-tested; **not yet run on the phone** (Phase 5 step 5.5) |
| Orchestrator address | Settings → Orchestrator (`ui/screens/OrchestratorSettings.kt`) | "Save and test connection" |
| Task and environment planning | `ai/planner/` | Grammar-constrained prompts for the same model; no screen calls them yet (Phase 8) |

### Scanning

Tools → Scan a space. ARCore raw depth with its confidence (kept at ≥ 128, 0.25–4 m, full 16-bit
millimetres), gated on tracking, turn rate (45°/s), staleness and sparse depth, about 3 frames a
second, each with a colour sample per depth pixel. The map's origin is the phone at the Start tap
(stand on the printed marker, `docs/capture/home-marker.svg`, pointing along its arrow). Frames go
to the orchestrator's `POST /capture` through a queue that retries and counts every frame it could
not send. The model is released while scanning and reloads on the next AI request.

The phone cannot reach `localhost`: run the orchestrator with
`python -m uvicorn orchestrator.service:app --host 0.0.0.0 --port 8000`, allow port 8000 through
the laptop's firewall, and enter `http://<laptop LAN IP>:8000` in Settings.

## The model

Qwen2.5-1.5B-Instruct, Q4_K_M, 1.04 GiB — source, size and SHA-256 in
[`app/src/main/assets/models/README.md`](app/src/main/assets/models/README.md). It is bundled in
the debug APK (about 1.23 GB) and copied to internal storage on first launch, so the phone holds
two copies (master plan §5 has the fix).

The build compiles llama.cpp's CPU backend with `-march=armv8.2-a+dotprod+i8mm` and weight
repacking, and checks the CPU's `asimddp` and `i8mm` flags before loading. Measured on the S24 FE
with a 359-token prompt at 6 threads: prefill 69 tok/s with repacking (46.8 without), first token
5.3 s, a 256-token reply 19.0 s. Debug switches: `adb shell setprop debug.scanborn.threads N` and
`debug.scanborn.repack 0`.

## llama.cpp

Vendored in `app/src/main/cpp/llama` (`src/`, `ggml/`, `common/`, `include/`), MIT licence in that
folder. The upstream commit it was copied from was not recorded; `cpp/CMakeLists.txt` passes
`GGML_VERSION="0.11.1"`, which is a label written in this repository, not checked against upstream.
To update it, copy the same four folders from an upstream checkout, record the commit here, rebuild,
and re-measure `benchmarks/phone.json`. (`setup_llama.ps1`, which copied an older layout, was
removed: it could not reproduce this tree.)

## Build

Requirements: Android SDK with platform 36, build-tools 36, NDK 28.2.13676358 and CMake 3.22.1;
JDK 17+. `local.properties` points at the SDK (`sdk.dir=...`). Model weights are required for
on-device inference, but not for compilation or the isolated recovery tests.

```bash
./gradlew :app:testDebugUnitTest :app:assembleDebug
./gradlew :app:installDebug
```

On the project's Windows laptop, every JVM needs
`JAVA_TOOL_OPTIONS=-Djdk.net.unixdomain.tmpdir=D:\tmp`; a cold build takes about 15 minutes, a warm
one about 2. `arm64-v8a` only, minimum Android 7.0 (API 24), target API 36.

## Tests

JVM unit tests under `app/src/test/`: the scan math (home frame, axis conversion, depth filter,
turn rate, `.npz` writer, colour sampling, upload queue, orchestrator URL and durable recovery).
Instrumentation covers native HTTP recovery, server switching, viewer message boundaries and a
debug-only Spaces screen host for native Stop/E-STOP through renderer-error state and activity
recreation. The separate emulator-only process harness force-stops unresolved deployment and
calibration requests and requires read-only recovery in a new process. See
[`accuracy-measurements.md`](../docs/robot/accuracy-measurements.md) for commands and scope.
Phone camera/ARCore and real-device rendering still require physical acceptance.

Spaces also provides paginated saved mission and deployment history. Reopen a non-stale mission
after selecting its twin; the app restores its coordinates and route and clears prior training/
artifact selection. Inspecting a deployment record sends no drive command and does not replace
the active drive controls or selected robot.

## Not done yet

- The scan has not been run on the phone (Phase 5 step 5.5).
- One copy of the model on the phone instead of two; sideloading a model with `adb push`.
- The ∞ mark in several screens and the launcher icons are left over from the app's earlier name
  (Phase 9).


### Processing-stage recovery

Reconstruction, segmentation, twin generation, training and optimization now persist their exact
inputs, stable request ID and pipeline checkpoint before sending. An unresolved stage blocks
conflicting space/mission edits. Use **Check saved stage result** to read the server record;
**Retry original stage request** explicitly resends the same identity and inputs. Nothing starts
again merely because the app restarts. Failed/interrupted requests require acknowledgement.
Recovered results require Reconnect before further mission actions. Rebuilding geometry clears
old mission/training selections; retraining clears the old deployment artifact.

The emulator process harness also checks processing-stage recovery and a synthetic capture
queue killed while awaiting an upload acknowledgement. Capture recovery verifies original ID,
metadata and frame bytes, no upload on construction, explicit Finish and completed-file cleanup.
It does not exercise ARCore, real camera frames or model ownership on a phone.
