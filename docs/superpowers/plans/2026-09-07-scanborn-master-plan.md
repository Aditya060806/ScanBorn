# ScanBorn Master Plan — on-device pipeline, unified design system, full rebrand

> **For agentic workers:** steps use checkbox (`- [ ]`) syntax. Phases are ordered by
> dependency, not by importance. Do not start a phase whose predecessor is unchecked
> unless the phase header says it is independent.

**Goal:** take ScanBorn from "two disconnected apps plus a mocked dashboard" to one
phone-first product with a single visual identity, a real on-device model under
grammar-constrained decoding, and a laptop compute tier — on the hardware actually
available, with nothing claimed that has not been measured.

**Progress (2026-09-07).** Phase 0 done except 0.1 (prior-work rule — needs an answer from
the organisers, not a commit). Phase 1.1, 1.2 and 1.3 done: grammar reaches the model,
prefill and decode are timed separately and surfaced in Settings, threads are chosen at
runtime, and the three-minute first-token watchdog is gone. Phase 1.4 (on-device planner and
profiler) is next. **204 tests, flake8 clean, mypy clean across 68 files.**

The one thing still unmeasured is the number everything else waits on: real prefill and
decode tok/s on the S24 FE. The instrumentation to read it now exists; it needs a device.

**Hardware this plan targets.** Every decision below is constrained by these three, not by
the Snapdragon reference hardware the repo was originally written against.

| Tier | Device | Real capability | Hard limits |
|---|---|---|---|
| Phone | **Galaxy S24 FE**, Exynos 2400e, 8 GB | ARCore depth, Qwen 1.5B on CPU under llama.cpp, ML Kit OCR, `SpeechRecognizer` | **No Hexagon NPU.** No QNN / Genie / QAIRT / AI Hub. 8 GB caps the model at ~2B alongside ARCore. |
| Laptop | **Lenovo LOQ**, i5-13450HX (6P+4E), 16 GB, **RTX 4050 6 GB** | CUDA. Qwen 3 4B Q4 comfortably, 7B Q4 tight. YOLO-World. Full Python pipeline. | 6 GB VRAM ceiling. |
| Remote | Groq / Gemini / Claude | Toolchain and optional runtime tier | Off in the demo path. Never a silent fallback. |

**Non-negotiable invariants.** These are the things this plan must not break.

1. `pytest` stays green at every checkpoint. Currently **186 passing**.
2. No claim without a measurement. `op_coverage=None` and `"compute_unit": "cpu"` stay
   honest; "NPU" is never printed on Exynos.
3. Local-first. `SCANBORN_PLANNER` must be named to reach the network; a key alone does
   nothing (already enforced in `sarvam/task_engine/provider.py`).
4. Every heavy dependency lazy-imports behind a working fallback, matching the existing
   `open3d` / `ultralytics` / `pyserial` / `qai_hub` pattern.

---

## Current state — verified, not assumed

| Area | State |
|---|---|
| Python pipeline | **Real.** 186 tests. Depth fusion, voxel clustering, navmesh from cloud, BFS + costmap inflation, int8 quantization, `SIM_GATE` 409. |
| `reconstruction/fidelity_path/pipeline.py` | **The only true stub.** Two hardcoded returns. Throws `KeyError` → 500 if COLMAP is on PATH. |
| `MOBILE-APP/` (`com.scanborn.ai`) | **Real app.** llama.cpp vendored + building, Qwen 2.5 1.5B GGUF, JNI streaming, ML Kit OCR, Room+FTS, Circle Learn via MediaProjection, 54 Kotlin files. **CPU only.** |
| `capture/android/` (`com.scanborn.capture`) | **Second, separate app.** ARCore 16-bit depth, coordinate conversions, hand-rolled `.npy`. No UI. |
| `dashboard/src/api.js` | **Entirely mocked.** Zero `fetch`. Claims `compute_unit: "Hexagon NPU"`, `op_coverage: 98.4`. |
| `dashboard/src/Telemetry.jsx` | `ModelCard` / `Compare` / `Tail` fully written, **never execute** — `benchmarks()` returns `{}`. `TIERS` hardcodes OnePlus 15 and X Elite. |
| Design systems | **Two, unrelated.** `dashboard/src/styles.css` = "Depth Sheet", light vellum `#eef0ea` + depth ramp (far `#22307e` / mid `#14b09a` / near `#ff5b2e`), Archivo + JetBrains Mono. `MOBILE-APP .../ui/theme/Color.kt` = dark navy `#0A0E1A` + blue `#4F8CFF`. |
| GBNF grammar | **Compiled into the binary** (`llama-grammar.cpp` in `llama_core`) and **not exposed** through `LlamaJniBridge`. |
| Voice | `SpeechRecognizer` real. **No TTS anywhere.** `VoiceScreen` transcript is dead code — `partialText` never populated. |
| Unity | **Does not exist.** README still instructs installing Unity Hub and opening `unity/`. |
| `assets/logo.png` | Uncommitted. LFS-tracked. Two judging PNGs are broken 131-byte pointers. |

---

## Architecture — where every piece runs

```
PHONE (S24 FE)                          LAPTOP (RTX 4050)               REMOTE (optional)
──────────────────────────────          ─────────────────────────       ─────────────────
ARCore depth capture      ─── LAN ───►  reconstruct / segment           Groq  (dev tier)
Qwen 1.5B + GBNF (CPU)                  twin generate + profile         Gemini (vision)
AR twin overlay           ◄── LAN ───   train + int8 quantize           Claude (toolchain)
Voice (SpeechRecognizer)                Qwen 3 4B on CUDA
Robot drive (USB-OTG)                   YOLO-World labels
```

No Office Kit. The bridge is plain LAN HTTP, which `capture/android/.../Uploader.kt` and
`orchestrator/service.py` already implement — nothing to build.

---

## Phase 0 — Unblock (independent, do first)

- [ ] **0.1 Confirm the prior-work rule in writing** if the hackathon is still a target.
      Two substantial pre-existing codebases. This gates nothing technical but everything
      strategic.
- [x] **0.2 Fix `.gitattributes`.** It is two concatenated configs: duplicated
      `* text=auto`, a stale Unity block (`*.cs`, `*.unity`, `*.prefab`, `*.meta`, `*.mat`)
      for a project that does not exist, and LFS rules for `*.png`/`*.jpg`/`*.psd`/`*.wav`/
      `*.mp3`. **Delete the LFS lines and the Unity block.** For a 900 KB logo, LFS buys
      nothing and costs broken pointers.
      **Then:** `git add assets/logo.png "Eligible solution.png" "Judging Creteria.png"` —
      all three commit as normal blobs.
      *Accept:* `git cat-file -s HEAD:assets/logo.png` returns ~899740, not 131.
- [x] **0.3 Install the lint toolchain.** `flake8` and `mypy` are absent, so `make lint`
      cannot run and this plan cannot self-verify style. `pip install -r requirements-dev.txt`.
      *Accept:* `make lint` completes (findings are fine; the command running is the bar).
- [x] **0.4 Reconcile the mypy conflict.** `.pre-commit-config.yaml` runs `--strict` while
      `pyproject.toml` deliberately disables it with a documented 206-error note. Pick one.
      Recommend: drop `--strict` from the hook to match pyproject.

---

## Phase 1 — On-device AI (the core)

**Why first:** every downstream decision depends on how fast the phone actually is, and on
whether grammar-constrained decoding makes the 1.5B reliable. Both are unmeasured.

### 1.1 Instrumentation — measure before changing anything

- [x] `MOBILE-APP/app/src/main/cpp/scanborn_jni.cpp` — time the two phases separately.
      Prefill (prompt eval) and decode have different bottlenecks and one number hides that.
      Emit via a new `onStats(prefillMs, decodeMs, promptTokens, genTokens)` callback.
- [x] `.../ai/runtime/LlamaJniBridge.kt` — add `onStats` to the `LlamaCallback` interface.
- [x] `.../ai/state/AIInferenceState.kt` — add a `stats` field to `Responding`.
      **Note:** this also fixes the dead `partialText` — see 1.5.
- [x] `.../ai/engine/LlamaEngine.kt` — surface tokens/sec on the state flow.
- [x] Settings screen — show live prefill/decode tok/s. This is the number that decides
      Phase 4's necessity.
      *Accept:* real tok/s visible on device for an 800-char prompt.

### 1.2 Thread and context tuning

- [x] `.../ai/engine/LlamaEngine.kt` — `N_THREADS = 4` is a constant; make it runtime.
      Exynos 2400e is 10-core big.LITTLE (1×X4 + 5×A720 + 4×A520); four unpinned threads
      can land on A520s, which is the likely cause of the **three-minute first-token
      watchdog** in `ocr/AiTextProcessor.kt`. Try 4 and 6, measure both.
- [x] `N_CTX` 2048 → 4096 (scene summaries + few-shot need the room).
- [x] `MAX_TOKENS` 512 → 256 (task graphs are short; caps worst-case latency).
- [x] Once measured, lower `FIRST_TOKEN_TIMEOUT_MS` from 180 s to something honest
      (~20 s) and raise `MAX_INPUT_CHARS` from 800 if throughput allows.
      *Accept:* first token under 5 s on device; watchdogs reflect reality.

### 1.3 GBNF through the JNI bridge — the highest-leverage change

`llama-grammar.cpp` is already in `llama_core`. The generators already exist in
`sarvam/task_engine/schemas.py` (`task_graph_grammar`, `environment_profile_grammar`).
Only the bridge is missing.

- [x] `scanborn_jni.cpp` — accept a `jstring grammar`; when non-empty, build
      `llama_sampler_init_grammar(vocab, grammar_str, "root")` and chain it into the
      sampler before the existing samplers. Empty string keeps current behaviour.
- [x] `scanborn_jni_stub.cpp` — mirror the new signature so the no-llama build still links.
- [x] `LlamaJniBridge.kt` — `generate(prompt, maxTokens, grammar, callback)`.
- [x] `LlamaEngine.kt` / `LocalAIEngine.kt` — thread `grammar: String = ""` through.
- [x] `ai/repository/AIRepository.kt` — same, defaulted so no existing caller changes.
- [x] **New** `.../ai/grammar/Grammars.kt` — ship the two grammars. Generate them from
      `schemas.py` at build time or vendor them with a test asserting parity, so the phone
      and the server cannot drift.
      *Accept:* an instrumentation test (or manual run) shows the model emitting only valid
      task-graph JSON for 20 varied prompts, including deliberately confusing ones.

### 1.4 On-device planner and profiler

- [ ] **New** `.../ai/planner/LocalTaskPlanner.kt` — sentence → task graph via Qwen under
      `task_graph_grammar`. Mirrors `sarvam/task_engine/` semantics.
- [ ] **New** `.../ai/planner/LocalProfiler.kt` — scene summary → profile JSON under
      `environment_profile_grammar`. Mirrors `ModelProfiler`, same fallback discipline.
- [ ] Reuse `sarvam/task_engine/profile_planner.py:summarize()` wording verbatim so
      distilled examples transfer.
      *Accept:* phone produces the same graph as the Python path for the 10 canonical
      instructions in `tests/test_task_engine.py`.

### 1.5 Voice — finish what is half-built

- [x] `.../ui/screens/VoiceScreen.kt:35` reads `aiState.partialText`, which
      `LlamaEngine.kt:73` constructs as `Responding()` with **no argument** — so the
      transcript card can never render. Populate it (1.1 already touches this state).
- [ ] Add listening feedback: `isListening` is derived from `aiState`, but
      `sr.startListening()` never touches `aiState`, so the mic looks dead until generation
      starts. Wire `onReadyForSpeech` / `onEndOfSpeech`.
- [ ] Implement `onPartialResults` — `EXTRA_PARTIAL_RESULTS` is requested and the override
      is empty.
- [ ] Voice currently routes through `chatViewModel.startFromSuggestion()`, which
      **replaces the whole message list** and wipes history. Give voice its own entry path.
- [ ] Add `TextToSpeech`. There is none anywhere; `VoiceScreen`'s "Speaking" indicator is
      decorative.
      *Accept:* speak Hindi → transcript visible while speaking → task graph → spoken reply.

---

## Phase 2 — One app (merge the two Android projects)

**Why:** the phone is currently a sensor feeding a laptop. It must become the product.

- [ ] **2.1** Port pure logic unchanged — these have no UI and no Android UI deps:
      `capture/android/.../Frames.kt` → `MOBILE-APP/.../com/scanborn/ai/scan/Frames.kt`
      `capture/android/.../NpzWriter.kt` → `.../ai/scan/NpzWriter.kt`
      `capture/android/.../Uploader.kt` → `.../ai/scan/Uploader.kt`
- [ ] **2.2** `MOBILE-APP/app/build.gradle.kts` — add `com.google.ar:core:1.44.0` and
      `com.squareup.okhttp3:okhttp:4.12.0`. Neither is currently present.
- [ ] **2.3** `AndroidManifest.xml` — add `INTERNET`, the `camera.ar` uses-feature, and the
      `com.google.ar.core` meta-data from `capture/android`'s manifest.
- [ ] **2.4** **New** `.../ui/screens/ScanScreen.kt` — `capture/android`'s `MainActivity` is
      an `AppCompatActivity` + `GLSurfaceView.Renderer`; rebuild as a Compose screen hosting
      the ARCore session via `AndroidView`.
- [ ] **2.5** **Guard depth support.** `session.isDepthModeSupported(Config.DepthMode.AUTOMATIC)`
      before use — currently `acquireDepthImage16Bits()` is called unguarded and throws.
      **The S24 FE has no ToF sensor**, so this is depth-from-motion: lower quality, needs
      deliberate sweeping. Verify early; it is the largest single unknown in this plan.
- [ ] **2.6** Wire into `ui/navigation/AppNavigation.kt` and `ui/screens/ToolsScreen.kt`
      (the dead `onClick = {}` placeholder at line 140 becomes "Scan a space").
- [ ] **2.7** Fix `AppNavigation.kt` — `circle_learn` is **registered twice** (identical
      bodies); the second overwrites the first. Remove it.
- [ ] **2.8** `MainActivity.kt` ignores `intent` entirely, so Circle Learn's
      `onOpenInApp(route)` extra is silently dropped. Read it.
- [ ] **2.9** Retire `capture/android/` once parity is confirmed. Do not delete until 2.5
      passes on hardware.
      *Accept:* one APK scans a room, uploads, and shows the twin.

---

## Phase 3 — Environment profile: finish the wiring

Phase already partly done (`twin/profile.py`, profile in `navmesh.json`, `inflate()` reads
it). What remains is the part that is carried but not consumed.

- [ ] **3.1 Per-label keep-out.** `EnvironmentProfile.clearance_for()` exists and nothing
      calls it. Apply it in `twin/generator.py:_navmesh()` on the **bbox path**, where
      labels are known. **Honest limitation:** the point-cloud path has unlabelled points,
      so it uses the uniform radius — document this, do not paper over it.
- [ ] **3.2 Profile-driven label vocabulary.** `EnvironmentProfile.labels()` exists and
      nothing calls it. Feed it to `semantic/service/inference.py:segment_image(labels=...)`
      → `model.set_classes()`. Needs Phase 4.2.
- [ ] **3.3 Profile-driven speed.** `robot/adapters/base.py:MAX_LINEAR_SPEED` is a module
      constant clamped in `unoq.py`. Take it from the profile.
- [ ] **3.4 Two-navmesh comparison endpoint or UI toggle** — same scan, two profiles, side
      by side. `tests/test_profile.py::test_a_bigger_robot_gets_less_walkable_floor` already
      proves the mechanism; this exposes it.
      *Accept:* toggling profile visibly changes walkable area in the UI.

---

## Phase 4 — Laptop tier (independent of Phases 1–3)

- [ ] **4.1 CUDA llama.cpp** serving Qwen 3 4B Q4_K_M (~2.5 GB) on the RTX 4050. Expose
      behind the existing `TaskPlanner` ABC as a `LocalServerPlanner` pointing at LAN.
      This is the "big model" tier — **not** a cloud API. No keys, no cost, works on venue
      WiFi, still counts as offline.
- [ ] **4.2 YOLO-World on CUDA.** `semantic/service/inference.py:segment_image()` already
      lazy-imports `ultralytics` and returns `{"backend": "none"}` without it. Install,
      fuse RGB labels with the geometric clusters. This replaces height-based
      classification (`_classify()`: under 0.6 m is a chair, under 1.2 m a table) — the
      softest part of the pipeline.
- [ ] **4.3 Distillation dataset** using Claude / Gemini as teachers:
      - `(scene summary → profile JSON)` across all 7 environment kinds
      - `(natural language → task graph)` in English, Hindi, Tamil
      - Fill `examples/{warehouse,factory,hospital,museum}/scenario.yaml` — currently 8-line
        placeholders no code reads
      Store under a new `datasets/` directory. Validate every example against
      `schemas.validate_task_graph` / `validate_environment_profile` in CI.
- [ ] **4.4** Optionally LoRA-tune Qwen 1.5B on 4.3 using the RTX 4050. Only if the
      grammar-constrained base model measurably underperforms.
      *Accept:* every generated example validates; local 1.5B matches teacher labels on a
      held-out split at an agreed rate.

---

## Phase 5 — One design system ("Depth Sheet" everywhere)

**The decision:** the dashboard's Depth Sheet is the identity. The app adopts it. Right now
the two surfaces share no colour, no type, and no logic — dashboard is light vellum
`#eef0ea` with a depth ramp, the app is dark navy `#0A0E1A` with blue `#4F8CFF`.

Depth Sheet is the stronger system because its palette **carries meaning**: it is the
colormap a depth sensor actually outputs (far indigo → mid teal → near hot), and "near" is
spent only where the system is sensing *right now*. That is a rule the app can inherit.

- [ ] **5.1** Extract canonical tokens to a single source: **new** `design/tokens.json`
      — palette, type ramps, spacing, hairline width, motion durations/easings.
- [ ] **5.2** Generate `dashboard/src/tokens.css` from it (replacing the hand-maintained
      `:root` block in `styles.css`, keeping the retargeted legacy names).
- [ ] **5.3** Generate `MOBILE-APP/.../ui/theme/Tokens.kt` from the same file.
- [ ] **5.4** Rewrite `.../ui/theme/Color.kt` onto the depth ramp. Keep a dark variant —
      a phone held up in a room wants dark; the projector wants light. Same hues, both modes.
- [ ] **5.5** `.../ui/theme/Type.kt` — Archivo + JetBrains Mono, matching the dashboard's
      two-ramp rule (fixed for instrument data, fluid for display). Bundle the fonts.
- [ ] **5.6** Keep the **accessibility discipline** already in `styles.css`: `--near-ink`
      `#a82c0c` and `--mint-ink` `#06584e` exist because the graphic hues measure 2.69:1 and
      3.49:1 as small text and fail. Port those text-safe pairs to Compose, do not let the
      graphic hues become body text.
- [ ] **5.7** Keep `--hair: 1.5px`. 1px hairlines vanish on a projector.
- [ ] **5.8** Remove `@paper-design/shaders` and `@paper-design/shaders-react` from
      `dashboard/package.json` — declared, **never imported** anywhere.
- [ ] **5.9** Delete `dashboard/src/modify_console.js` — a committed one-off codemod with a
      hardcoded `/Users/deepesh/...` path.
      *Accept:* a token change in one file visibly moves both surfaces.

---

## Phase 6 — Dashboard UI/UX upgrade

### 6.1 Make it real (prerequisite for everything else here)

- [ ] Replace `dashboard/src/api.js` with a real client. It currently has **zero `fetch`**
      and returns hardcoded `compute_unit: "Hexagon NPU"`, `op_coverage: 98.4`,
      `sim_success_rate: 0.84 // Higher than 0.60 sim gate`. `vite.config.ts` already
      proxies the full real route list, so the transport exists.
- [ ] Git history shows a real client was replaced by this mock (`1980607` on top of
      `68503a6`); recover the shape from `68503a6` rather than rewriting blind.
      *Accept:* every stage in `Console.jsx` drives the live orchestrator.

### 6.2 Charts and data visualisation

- [ ] **Unlock `Telemetry.jsx`.** `ModelCard`, `Compare` (NPU-vs-CPU bars), and `Tail`
      (p50/p95/p99) are fully written and **never run**, because `benchmarks()` returns `{}`.
      Real data lights them up with no new components.
- [ ] **Fix the `TIERS` table** — it hardcodes "OnePlus 15 · 8 Elite" and "X Elite AI PC",
      neither of which you own. Replace with the real three tiers: S24 FE (Exynos, CPU),
      RTX 4050 laptop (CUDA), UNO Q (CPU, no NPU). Keep the honesty of the "no NPU" row.
- [ ] **New** `dashboard/src/charts/` — a small chart set built on SVG, no new dependency:
      - `TokenThroughput.jsx` — prefill vs decode tok/s over time, from Phase 1.1
      - `LatencyTail.jsx` — p50/p95/p99 distribution, reusing `Tail`'s semantics
      - `NavmeshDelta.jsx` — walkable-area comparison across profiles (Phase 3.4)
      - `VoxelDiff.jsx` — added/removed/unchanged from `/sync`
      - `SimGate.jsx` — success rate against the 0.6 gate; `Landing.jsx` already animates a
        `--pass: 0.6` meter, so reuse that visual language
- [ ] **Charts must render an explicit empty state**, never zeros. `Telemetry.jsx` already
      does this correctly ("Not profiled yet") — that is the pattern to copy.

### 6.3 Motion and texture

- [ ] `gsap` is already a dependency and `useReveal.js` already wraps everything in
      `gsap.matchMedia("(prefers-reduced-motion: no-preference)")`. **Every new animation
      must go through that hook** — the reduced-motion path is already correct, do not
      bypass it.
- [ ] Stage transitions in `Console.jsx`: the rail's nine stages currently pop between
      states. Add enter/settle motion driven by the `busy`/`failed`/`done`/`ready`/`waiting`
      states `Stage.jsx` already derives.
- [ ] Live job feed motion — `JobFeed.jsx` opens a real WebSocket and upserts by
      `job_id`; animate insert and status change.
- [ ] Texture: the landing's `aperture.js` is a 2D-canvas logarithmic-spiral halftone with
      ordered dithering against a Bayer 4×4 matrix. That halftone is the brand's texture —
      reuse it as a subtle panel treatment in the console rather than inventing a new one.
- [ ] Do **not** re-add post-processing to `recon/pipeline.js`. Its 15-line header documents
      that SSAO and bloom washed the scene near-white and SMAA smeared the 1–2px fat-line
      accents from ~140 visible pixels to ~3, and that removing EffectComposer restored
      hardware MSAA. That is a measured decision; respect it.

### 6.4 UX correctness

- [ ] `api.health()` currently always resolves, so the "orchestrator online" pill is green
      whether or not anything is running. Make it reflect reality.
- [ ] `Console.jsx:openScan()` file routing is genuinely good (`.ply`/`.obj` → import,
      `.npz` → chunked, `.glb`/`.usdz` explicitly rejected, mixing rejected). Keep it
      exactly as is.
- [ ] Add a **provider indicator**. `/plan` now returns `provider` and `on_device`; the UI
      must say when an answer came from the network. `api.js` currently hardcodes
      `provider: "Local Gemma 4"`.
- [ ] Add a **profile selector** to the twin stage, driving Phase 3.4.
- [ ] `Floorplan.jsx` projection is correct (`place(x,y) => [x - minX, maxY - y]` so map-Y-up
      becomes screen-Y-down without mirroring labels). Leave the maths alone; only restyle.

---

## Phase 7 — App UI/UX upgrade

- [ ] **7.1** Apply Phase 5 tokens across all 12 screens.
- [ ] **7.2** `.../ui/components/AiBodyOrb.kt` already exists as the app's signature
      visual. Re-key it to the depth ramp and drive it from real inference state (Phase 1.1
      gives it tok/s), so it reports rather than decorates.
- [ ] **7.3 New** `.../ui/components/charts/` in Compose Canvas, mirroring Phase 6.2:
      throughput sparkline, latency tail, navmesh delta, voxel diff. Same empty-state rule.
- [ ] **7.4 Scan coverage feedback** — live indication of under-sampled surfaces during
      capture. This is the highest-value new UX in the app: it uses the depth stream in real
      time and makes the difference between a good scan and a bad one visible *before* the
      pipeline runs.
- [ ] **7.5 AR twin overlay** — register the twin onto the real room through the camera.
      Best visual in the product and it justifies the hardware.
      **Fallback if it slips:** port `Floorplan.jsx`'s 2D plan view to Compose Canvas; the
      projection is already solved and a top-down plan reads a robot route more clearly than
      3D anyway.
- [ ] **7.6 Voice label correction** — tap a wrong label, say "that's a sofa". Cheap,
      memorable, and human-in-the-loop.
- [ ] **7.7** Motion: Compose `AnimatedContent` for stage transitions, shared-element for
      scan→twin. Respect the system reduced-motion setting, matching the web's discipline.
- [ ] **7.8 Fix the placeholders** currently shipping as if real:
      - `ToolsScreen.kt:140` "Smart Notes" → `onClick = {}`
      - `LibraryScreen` entry tap → `/* detail view future */`, no detail screen exists
      - `SettingsScreen` "Response Mode: Balanced" and "Language: English" are cosmetic
      - Settings "Storage" permission badge hardcodes `true` on API 33+ and otherwise checks
        `READ_EXTERNAL_STORAGE`, which is **not in the manifest**, so it always reads
        "Denied" on API < 33
- [ ] **7.9** `PdfTextExtractor` reads the whole PDF into memory as ISO-8859-1; with
      `largeHeap` this survives but a big file risks OOM. Stream it.
- [ ] **7.10** `OcrScreen` creates a temp file in `cacheDir` on every camera tap and never
      cleans up. Fix the leak.

---

## Phase 8 — Rebrand completeness

Dashboard, README, Python package, Kotlin packages, and `Infinity`→`ScanBorn` are **done**
(commits `8a2c85d`, `499c48c`). What remains:

- [ ] **8.1** `assets/logo.png` — new ScanBorn logo, committed (needs Phase 0.2).
- [ ] **8.2** Regenerate `dashboard/index.html`'s inline SVG favicon if the logo changes.
      It currently encodes the depth ramp `#ff5b2e`/`#14b09a`/`#22307e` — which is on-brand
      and may be worth keeping regardless.
- [ ] **8.3** App launcher icon — `ic_launcher_foreground.xml` still carries the Infinity
      mark.
- [ ] **8.4** `MOBILE-APP/README.md` still describes "Infinity — AI Command Center" with a
      "deep brown/black + amber/orange" palette that **does not match** `Color.kt`'s navy
      and blue. Rewrite for ScanBorn and the Phase 5 palette.
- [ ] **8.5** `detailed implementation doc.md` — 58 renamed references, but the content
      still describes the v2/v3 Snapdragon architecture. Add a header noting it is
      historical, or update it.
- [ ] **8.6** `CLAUDE.md` — its central claim ("nearly every module is a stub") is false,
      and all three of its "known inconsistencies" are fixed. Rewrite against reality.

---

## Phase 9 — Honesty pass (do before any public demo or screening)

These are all currently-shipping false claims. Each is a credibility risk with technical
judges, and each is a small edit.

- [ ] **9.1 Unity.** `README.md` lists Unity Hub + Unity 2022 LTS as prerequisites (`:648`),
      says to open `unity/` (`:717`, `:806`), documents a `unity/Assets/MLAgents/Scenes/`
      tree (`:469`), and tells you to run `mlagents-learn configs/ppo.yaml` (`:935`).
      **Neither `unity/` nor `configs/ppo.yaml` exists.** A reader following the README hits
      a wall in five minutes. Remove.
- [ ] **9.2 PPO.** README claims PPO via ML-Agents (`:158`, `:252`). The code does
      closed-form ridge-regression behaviour cloning (`train_bc.py:86`). Say so.
- [ ] **9.3 Whisper / speech.** README advertises multilingual speech understanding
      (`:213`), lists Whisper in the stack (`:599`, `:1402`), and says cloud may handle
      speech (`:1234`). There is no audio in the Python tree at all. Either build it
      (Phase 1.5) or drop the claim.
- [ ] **9.4 NPU.** Delete the mocked `"Hexagon NPU"` / `98.4%` (Phase 6.1). On Exynos these
      are not merely mocked, they are unreachable.
- [ ] **9.5 `configs/default.yaml`** is loaded by nothing and contradicts the shipped code
      (`yoloworld+mobilesam` where segmentation is geometric; `fbx` where the twin emits
      JSON). Either wire it or delete it.
- [ ] **9.6 `CAPTURE_URL` / `SEMANTIC_URL`** in `docker/docker-compose.yml` are read by
      zero lines of Python — the orchestrator imports stage modules in-process by design.
      Remove them and the misleading three-service split, or implement it.
- [ ] **9.7 `Dockerfile.orchestrator`** copies only `orchestrator/` while
      `orchestrator/service.py` imports eight sibling trees. It only works because compose
      bind-mounts the repo over `/app`; a plain `docker build && docker run` fails at import.
- [ ] **9.8 `k8s/`** contains a README promising manifests and no manifests.
- [ ] **9.9 `reconstruction/fidelity_path/pipeline.py`** — two hardcoded returns. On a
      machine with COLMAP on PATH it returns a dict without `point_count` and
      `service.py:160` raises `KeyError` → HTTP 500. Either implement, or make the guard
      return a clean 501.
- [ ] **9.10 `twin/inference.py`** cannot be imported — module-scope
      `serial.Serial("/dev/ttyUSB0")`, a bare `raise`, and `onnxruntime_qnn` absent from
      requirements. Its 14-dim observation space is also incompatible with the
      orchestrator's `OBS_DIM = 2`. Quarantine it clearly as a Snapdragon-only experiment.

---

## Phase 10 — Robot (finale, independent)

- [ ] **10.1** Phone drives the buggy over **USB-OTG serial** using
      `usb-serial-for-android`. The firmware already speaks newline-delimited
      `steer,throttle` at 115200, so **zero firmware changes**.
- [ ] **10.2** Wire `deployment/qairt/convert.py` into `/optimize` — it is fully implemented
      with honest backend labelling and **never called**.
- [ ] **10.3** `reconstruction/fast_path/fusion.py:tsdf_fusion()` is a real Open3D
      integration that **nothing calls**; `reconstruct()` uses only the numpy path. Either
      wire it or mark it explicitly unused.

---

## Verification matrix

| Checkpoint | Command | Bar |
|---|---|---|
| Python | `python -m pytest -o addopts=""` | 186+ pass |
| Lint | `make lint` | runs clean (after 0.3) |
| Format | `make format-check` | CI has this **commented out** because the tree was never black-formatted (22 files). Decide: format once, or delete the hook. |
| Dashboard | `npm run build` in `dashboard/` | exit 0 |
| App | `./gradlew assembleDebug` | exit 0 |
| App tests | `./gradlew testDebugUnitTest` | only `ExampleUnitTest` exists — real coverage is a gap |
| Grammar parity | new test | phone grammar == `schemas.py` output |
| Dataset | new CI step | every example validates against the schemas |

**Untestable in this environment, and must be said so:** anything NPU (no Hexagon on
Exynos), ARCore depth quality (needs the device), the physical robot, and Office Kit.

---

## Explicitly out of scope

- **Unity.** No project exists, it is desktop-only, and it cannot run during a phone-only
  session. Remove the claims (9.1) rather than building it.
- **NPU / QNN / Genie / QAIRT / AI Hub.** Impossible on Exynos. Build every accelerated
  path behind a runtime capability check so the same APK lights up on Snapdragon later.
- **Office Kit.** Deferred until an iQOO device exists.
- **Cloud in the demo path.** Toolchain and optional tier only.
- **A model swap.** Qwen 2.5 1.5B is Apache 2.0, handles Hindi, and under a grammar is
  reliably structured. Revisit only if Phase 1.3 measurably fails.

---

## Sequencing summary

```
Phase 0  ──┬─► Phase 1 ──► Phase 2 ──► Phase 7
           │      │
           │      └──────► Phase 3
           ├─► Phase 4  (independent)
           ├─► Phase 5 ──► Phase 6 ──► Phase 7
           ├─► Phase 8
           ├─► Phase 9  (independent, do before any demo)
           └─► Phase 10 (independent)
```

Phase 1 is the critical path: it produces the measurement every other decision rests on.
Phase 9 is independent and cheap and should be done early, because it is the difference
between a repo that reads as honest and one that reads as inflated.
