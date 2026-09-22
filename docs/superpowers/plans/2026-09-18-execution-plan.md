# ScanBorn — execution checklist and evidence ledger

**Revision:** 18 September 2026  
**Requirements:** [Enhanced master plan](2026-09-18-enhanced-master-plan.md)

This document turns the master plan into implementation batches. It is an execution specification,
not a claim that its unchecked work has run. Each batch ends with code, contract updates, tests,
recorded evidence and a remaining-defects entry. Keep this file current instead of creating another
competing plan. `[x]` means the stated evidence exists; `[ ]` means closure is still required.

## Execution rules

1. Read the affected code and existing tests; reproduce the gap before changing its owning layer.
2. Preserve uncommitted work and staging. Make compatibility changes additive; back up test databases
   before migration experiments. No production data cleanup or model downloads are implied.
3. Change API/SDK/client models/OpenAPI/fixtures together. Give new behavior focused failure tests.
4. Run focused checks first. Run broader checks once the batch stabilizes; do not run heavy suites
   during performance measurement or browser acceptance.
5. Record attempted commands and their actual results. Never mark a device test passed from an APK
   build. Record fixture/fake/provider provenance and distinguish targets from measurements.
6. When blocked on phone, rover or external credentials, complete independent software work and
   leave the dependent gate visibly open. Do not simulate an external result and label it physical.

## Baseline ledger

| Evidence | Last recorded result | Scope |
|---|---|---|
| Full Python | 453 passed, 204.25 s | `build/regression-20260918.txt`, prior implementation pass |
| Dashboard | 60 tests passed; production build passed | Existing Three.js chunk warning remains |
| Android | Unit tests, debug APK, instrumentation APK build passed | Instrumentation was not executed on the phone |
| Changed Python modules | flake8/mypy passed for calibration/specification/SDK | Not a fresh whole-repository static-analysis result |
| Desktop renderer | Earlier 14.1 ms p95 at 500k; eight mount/dispose cycles stable | Prior recorded setup only |
| Physical accuracy / phone rendering | Unverified | E7 required |

## E0 — Consolidate state, specification and recovery foundations

**Owns:** `orchestrator/specifications.py`, `calibrations.py`, `db.py`, `jobs.py`,
`idempotency.py`, `service.py`, `artifact_validation.py`, calibration/integrity/deployment tests.

- [x] Bind accepted robot snapshot/revision through twin, train, exported validation and deploy.
- [x] Preserve immutable historical twin visualization and isolate fake calibrated specifications.
- [x] Add explicit config reconciliation with crash-window and legacy-adoption tests.
- [x] Exercise multiple legacy applied sessions, competing uncertain sessions, superseded proposals,
  read-back mismatch, retry after publication and failed recording. Reconciliation must have a
  deterministic way forward without applying an older proposal or silently clearing uncertainty.
- [x] Prove all stage jobs terminate on failure and restart as interrupted; connections/resources
  close; identical request IDs with different input refuse; deployment recording failures surface.
- [x] Test schema upgrade from a populated older database; retain IDs/history, repeat safely, and
  document rollback compatibility before accepting new fields.
- [x] Verify every supported stage refuses crossed scan/mesh/object/twin/mission/spec/artifact
  lineage; test concurrent robot reservation and duplicate deployment against a single drive.

```text
Batch / requirement IDs: E0 open items (master plan §5 recovery matrix, §6 C0 state seams)
Changed behavior and affected files:
  orchestrator/specifications.py: uncertain_sessions() lists every uncertain session, skipping
    ones recorded as superseded; resolve() names the newest (calibration_id) and all of them
    (pending), and while reconciling one session ignores the older ones it settles.
  orchestrator/calibrations.py: reconcile works newest first from live read-back only (no config
    or motion is sent): a match publishes and supersedes older uncertainty; a newer applied state
    supersedes an older proposal instead of refusing forever; a session that never reached the
    rover adopts the newest older live proposal (adopted_calibration_id) or supersedes the rest.
    start_calibration records an unmonitored 'running' row as interrupted instead of blocking.
  orchestrator/db.py: backup_before_migration() copies a file holding data to
    <db>.v<old>.bak (SQLite backup API, once per starting version) before the first migration.
  orchestrator/service.py: the "validated for another robot" refusal now carries
    code artifact_validation like the other gate-binding refusals (additive).
  docs/api/database.md (new): versions 1-5, upgrade/backup behaviour, which older code opens a
    v5 file (v3/v4 yes; v1/v2 no, restore the .bak).
  tests: test_calibration_reconciliation.py (7), test_recovery.py (13), test_migrations.py (7),
    test_lineage.py (11).
Baseline defect reproduced: two legacy applied sessions each blocked the other (older refused by
  "a newer calibration was applied", newer by the older's uncertainty) with no way forward; a
  'running' calibration row whose monitor died blocked the robot until restart; the v3 rename
  left no way back to v1/v2 code and no copy was taken.
Commands, test counts and pass/fail:
  focused: test_calibration_reconciliation, test_recovery, test_migrations, test_lineage,
    test_rover_deploy: 47 passed in 69.57 s; race tests repeated 5x: 2 passed each time.
  full: python -m pytest -q -o addopts="": 606 passed, 1 skipped in 214.05 s
    (build/regression-e0.txt; the skip is the real ONNX write, onnx not installed).
  flake8 (repository list) clean; mypy 121 source files clean.
Build / environment: Windows 11, Python 3.11.9. Rover is the in-process fake (simulated
  provenance); no phone, rover or provider was used.
Recovery matrix covered: an injected unexpected exception in each of reconstruct, segment,
  twin_generate, plan, train, optimize and the simulated drive ends the job failed and closes every
  connection; a restart marks jobs, request_results, deployments and calibration sessions
  interrupted and starts nothing; a request_id reused with different input is refused (plan,
  train, optimize, deploy); a drive-recording failure marks the deployment recording_failed
  (test_rover_deploy). Lineage: objects from another mesh, a plan or mission from another twin,
  a calibration read through another robot, changed artifact bytes, changed twin geometry, a policy
  pointed at another mission, another robot and a recalibrated specification are all refused
  before anything runs. Two different deploy requests racing for one fake rover: one drive, the
  other robot_busy naming it; the same request_id twice at once: one drive, one deployment row.
Compatibility / migration / rollback: populated v1-v4 files upgrade with every ID kept, foreign
  keys clean, old rows served; repeat start is a no-op; the .bak is never overwritten.
Remaining defects and unexecuted checks: several orchestrator processes on one database file are
  unsupported (documented); physical rover reconciliation is E7.
Status: software verified. Next dependency: E2.
```

**Exit:** E0 recovery matrix passes and no unknown calibration outcome can appear ready to train
or drive. Preserve no-auto-restart behavior. Do not repeat already completed implementation.

## E1 — Make API support explicit, bounded and trustworthy

**Owns:** `sarvam/task_engine/provider.py`, `groq_provider.py`, `sarvam_provider.py`,
`profile_planner.py`, `schemas.py`; `deployment/aihub_export/export_script.py`,
`deployment/qairt/convert.py`; `.env.example`, orchestrator response mappings, SDK and fixtures.

### E1.1 Selection and export truthfulness — software verified 18 September

- [x] Introduce validated workload-specific provider selection; reject unknown names before work.
- [x] Add an explicit export provider setting with local default. A stray AI Hub token must cause
  zero upload/compile/profile calls. Document how prior token-only configurations opt in.
- [x] Label artifact format/backend from the returned bytes. Separate optional QAIRT conversion
  output from the NPZ bundle; do not report accelerator execution from host timing.
- [x] Refuse unsupported export/runtime combinations with an actionable message. Keep existing
  validated ESP32/local artifact path working and public response changes additive.
- [x] Add mocked tests for no key, stray key, absent package, unsupported provider, conversion
  failure, malformed profile and mismatched runtime. No live paid service needed for this batch.

```text
Batch / requirement IDs: E1.1 (master plan §6 C0 seams: token-triggered AI Hub, QAIRT label,
  unknown-name silent fallback)
Changed behavior and affected files:
  deployment/artifact_format.py (new): format read from bytes (npz-int8/npz-float/npz-other/
    tflite/unknown); one runtime scanborn-int8-linear-v1 executed by simulator, fake rover, ESP32.
  deployment/aihub_export/export_script.py: export_model(provider=local|ai-hub|qairt), default
    local; requirements() refuses before any file; artifact is always the verified npz-int8
    bundle; AI Hub is an experiment record (own format/device/latency source, deployable false,
    failures and malformed profiles recorded, not raised).
  deployment/qairt/convert.py: converts an ONNX export (was: the NPZ checkpoint it cannot read);
    DLC output is an experiment, format read from bytes; never relabels the bundle.
  sarvam/task_engine/provider.py, profile_planner.py: PLANNER_PROVIDERS / PROFILER_PROVIDERS
    allowlists; unknown names raise ProviderSelectionError (was: silent local).
  orchestrator/providers.py (new): SCANBORN_PLANNER/PROFILER/EXPORT checked (name, credential,
    packages) before a job or file; GET /providers report without credential values; startup log.
  orchestrator/service.py: /plan, /plan/preview, /generate-twin (profiler only when used),
    /optimize (stale/snapshot checks moved before export; additive format, runtime,
    export_provider, experiments) and /deploy (unsupported_runtime refusal; always drives the
    int8 bytes, float-checkpoint fallback removed).
  sdk/scanborn/client.py: providers(). .env.example, README, CLAUDE.md, CHANGELOG: SCANBORN_EXPORT
    and the token-only opt-in (set SCANBORN_EXPORT=ai-hub).
  tests/conftest.py (new): provider settings cleared per test; any HTTP request to a non-local
    host fails the test.
Baseline defect reproduced: read in code — export_model uploaded when AI_HUB_API_TOKEN and qai_hub
  were present; /optimize then loaded the result with load_int8_policy; convert_to_qairt labelled
  the NPZ bundle format "qairt"; SCANBORN_PLANNER=anything-else returned the keyword planner.
Commands, test counts and pass/fail:
  pytest tests/test_deployment.py tests/test_providers.py: 36 passed, 1 skipped (real ONNX check:
    onnx not installed); planner/profiler/groq suites: 76 passed; affected orchestrator, rover,
    integrity, calibration, SDK, OpenAPI, mission, visualization suites: 63 passed.
  Full: python -m pytest -q --no-cov: 486 passed, 1 skipped in 247.00 s
    (build/regression-e11.txt). flake8 (repository list) clean; mypy 111 source files clean;
    dashboard vitest 60 passed; source completeness 118 files, none ignored.
Build / environment: Windows 11, Python 3.11.9, torch 2.5.1 present, onnx/qai_hub absent.
Actual provider and provenance: no live provider called; AI Hub, torch and qairt-converter are
  stand-ins in tests; tests/conftest.py forbids non-local HTTP.
Evidence paths: build/regression-e11.txt; docs/api/openapi.yaml (44 paths / 47 operations);
  dashboard/src/fixtures.json re-recorded (export_provider local, experiments []).
Compatibility: response changes additive; artifacts table unchanged; existing int8 artifacts keep
  deploying. Breaking only for configurations that relied on the token alone (documented).
Remaining defects and unexecuted checks: real AI Hub compile/profile, real qairt-converter and a
  real ONNX write were not run (no token, SDK or onnx; installing needs the owner's yes).
Status: software verified. Next dependency: E1.2.
```

**Exit:** source, tests, OpenAPI/SDK/fixtures and configuration guidance agree. No cloud request
occurs unless the workload explicitly selected it. Experimental export cannot become deployable
merely because its download succeeded.

### E1.2 Shared validation and fallback — software verified 18 September

- [x] Define result metadata: requested/actual provider, model, location, schema version, input
  identity, request ID, duration, attempts, fallback reason and nullable measured usage.
- [x] Validate complete task graphs/profiles against scene targets and finite/ranged parameters;
  refuse ambiguity and invalid partial results instead of silently deleting steps.
- [x] Implement ordered opt-in fallback, total deadline, bounded retries/backoff, response-size and
  concurrency limits, cancellation and configured budget enforcement. Keep keys server-side.
- [x] Do not retry a paid/mutating request with unknown outcome without idempotency/lookup support.
- [x] Make actual local fallback visible, including transport exceptions and invalid model replies.
- [x] Cover timeout, rate limit, 5xx, missing credentials, malformed/non-JSON/oversized replies,
  hallucinated targets, unsupported actions, invalid numeric values, cancellation and exhaustion.
- [x] Add a versioned intent evaluation set with supported, ambiguous, unsupported and multilingual
  cases. Require all safety/refusal invariants to pass; publish semantic success and abstention by
  category. Freeze the set before comparing providers; do not equate schema validity with accuracy.

```text
Batch / requirement IDs: E1.2 (master plan §6; C1 seams: Sarvam target validation, Groq partial
  graph filtering, profiler transport handling and silent fallback)
Changed behavior and affected files:
  sarvam/task_engine/contract.py (new): task-graph-v1 / environment-profile-v1; whole-reply
    judgement (planned, clarification_needed, unsupported, invalid); params per action (wait
    seconds 0..600, speak text <=120); targets must name exactly one scene object.
  sarvam/task_engine/execution.py (new): run_chain — ordered providers, total deadline, per-request
    timeout, retries only for not-processed outcomes (unreachable, 429, 503; Retry-After honoured
    within the deadline), no retry after read timeout / 5xx, reply-size cap while streaming,
    per-provider concurrency, Budget.reserve, cancellation between attempts and during backoff;
    provenance-v1 record; post_json transport classification without secrets.
  sarvam/task_engine/runner.py (new): plan_instruction / profile_scene; a remote off-scene target
    is a hallucination (fallback), a local one is a clarification (echoes the operator's words).
  groq_provider.py, sarvam_provider.py: transport only (Sarvam now receives the same targets
    prompt); lenient GroqPlanner.parse removed. fallback.py: interpret() — verbless clauses that
    name an object repeat the previous action, other clauses are reported not understood; speak
    text moved to params. profile_planner.py: ModelProfiler records last_fallback.
  orchestrator/providers.py: ordered chains, limit settings (validated), PersistentBudget (DB v5
    provider_usage, per UTC day), cancellation registry, report with limits and today's usage.
  orchestrator/db.py: v5 — provider_usage table, task_graphs.provenance_json,
    twins.profile_provenance_json (additive).
  orchestrator/service.py: /plan and /plan/preview idempotent, chain-run, 422 for clarification /
    unsupported, 502 exhausted, 409 cancelled; provenance stored; /generate-twin profiler chain
    (resolves spec before any file, removes the twin folder on footprint refusal);
    POST /providers/requests/{id}/cancel; missions from plans: last navigate_to = goal, earlier =
    via, non-driving steps refused, absent / shared targets refused with candidates; /train and
    /deploy without a mission no longer aim at the room centre for an unresolvable target.
  dashboard: NLPPlayground shows status, reasons and fallback; Stage hides machine-only detail
    fields; api.providers / cancelProviderRequest; /providers dev proxy. SDK: plan(request_id=),
    cancel_provider_request. datasets/intents/v1 (frozen 78b33be0...), intent_eval.py,
    scripts/evaluate_intents.py, benchmarks/intents-local-v1.json.
Baseline defect reproduced: GroqPlanner.parse dropped invalid nodes and returned the rest;
  SarvamPlanner kept off-scene targets; ModelProfiler let httpx errors escape (500) and hid
  fallback; /missions and _goal_for aimed at the room centre for an absent target and took the
  last of several same-label objects; KeywordPlanner dropped "then the door" and "and dance".
Commands, test counts and pass/fail: tests/test_provider_execution.py 36, test_groq_provider.py
  rewritten, test_providers.py 32, test_intents.py 10, planner/profile/parity suites 198 + 30;
  full python -m pytest -q --no-cov: 568 passed, 1 skipped in 175.48 s
  (build/regression-e12.txt). flake8 clean; mypy 117 files clean; vitest 61 passed; dashboard
  build passed; source completeness 118 files.
Accuracy metrics: local keyword planner on intent set v1 (38 cases): 16/16 expected plans correct
  (English supported + manipulation), 18 correct refusals, 1 refused differently (Hindi ambiguous
  answered unsupported), 3 abstained (Hindi), 0 wrong_plan, 0 unsafe_plan. Multilingual semantic
  success 0.0: the keyword planner does not read Hindi.
Actual provider and provenance: local only; Groq and Sarvam are stand-ins in tests; conftest
  forbids non-local HTTP. Remote planner accuracy has not been measured (no key used).
Evidence paths: build/regression-e12.txt, benchmarks/intents-local-v1.json,
  docs/api/openapi.yaml (45 paths / 48 operations), dashboard/src/fixtures.json.
Compatibility / migration: DB v5 additive; existing task graphs keep NULL provenance; plans stored
  before E1.2 with absent targets now refuse at mission / train time instead of driving elsewhere.
  SCANBORN_PROFILER=groq alone now fails when Groq fails (use groq,local to keep the fallback).
Remaining defects and unexecuted checks: live Groq/Sarvam runs and their intent scores; provider
  settings UI and badges are E3; phone-side LocalTaskPlanner is unchanged (Phase 8).
Status: software verified. Next dependency: E0 open items, then E2.
```

**Exit:** local-only tests forbid external HTTP; fallback preserves intent/schema and cannot bypass
mission gates. Live provider accuracy remains separately unverified until explicitly measured.

## E2 — Complete capture and import reliability

**Owns:** Android `scan/`, capture store/validation/imports, reconstruction and visualization;
capture/axis/visualization tests; input controls in both clients.

- [ ] Exercise slow/offline network, first-response loss, queue capacity, disk-full, corrupted queue
  entry and process restart. No acknowledged frame or stable scan identity is lost.
- [ ] Test Start/Finish/Cancel/pause races and interrupted tracking. Define each counter and assert
  its accounting invariant; pending/failed frames cannot masquerade as uploaded completion.
- [ ] Verify model release completes before capture and competing inference stays blocked; reload
  after leaving capture. Cover denial/permission and ARCore unavailable paths.
- [ ] Bound compressed and decompressed inputs before large allocation; validate depth, intrinsics,
  poses, indices, finite values, upload size and paths before committing files.
- [ ] Persist units/up-axis/registration metadata for imports, preserve originals and valid faces,
  and distinguish inspectable scans from physically registered drive-ready scans.
- [ ] Build shared known-axis/nonzero-floor/sparse/large/missing-color/imported-face fixtures.
- [ ] Generate deterministic 100k/500k previews and ID-scoped cached assets without filesystem paths.

**Exit:** synthetic A1 passes for phone-format and imported fixtures; all fault outcomes leave
consistent recoverable records. Real A2/A3 and model lifecycle measurements remain E7.

### 19 September continuation — queue identity and queued Finish regression

Reviewed the updated E0/E1 evidence and E2 capture/import code before continuing; retained the
user's provider, migration, registration, body-limit and scene-axis implementation.

Implemented in `UploadQueue.kt`: remember settled frame indices in memory and reconstruct them
from durable uploaded/rejected receipts. Repeated submissions cannot overwrite an acknowledged
frame or increase accepted/uploaded/rejected counts, including after restart. Finish now checks
closure before draining/retrying/completing, so a Finish waiting behind an upload does not complete
after shutdown. An upload already acknowledged while closing still gets its receipt.

Regression proof: two new duplicate-identity tests failed before the fix; a latch-controlled
upload/Finish/shutdown test also failed before its fix. Added byte-capacity, failed disk admission
and unreadable pending-entry tests. Corrected the existing restart test to require explicit partial
completion when a dropped frame survives restart; normal completion correctly refuses that case.

Fresh checks: Android full unit suite **35 passed**, including **9 UploadQueue tests**; debug APK
and instrumentation APK builds passed offline (final Gradle run 11 s). Results:
`MOBILE-APP/app/build/test-results/testDebugUnitTest/`. Server capture/import/axis checks:
`python -m pytest tests/test_import_safety.py tests/test_scene_axes.py tests/test_capture_validation.py
tests/test_capture_meta.py -q -o addopts=''` — **50 passed in 17.89 s**. No external provider,
physical phone session, device instrumentation execution or rover measurements were performed.

E2 remains partial. Still required: actual lost-response/process-kill lifecycle coverage, complete
Start/Finish/Cancel/discard concurrency coverage, model/ARCore handoff and permissions. Shutdown
cannot revoke completion already sent to the server; unknown outcomes still require reconciliation.
Failed disk admission uses a deterministic unwritable destination, not a real full-device test.
Shared viewer packaging still emits its existing size warning. No whole-stage completion is claimed.

## E3 — Complete native and dashboard state recovery

**Owns:** Android `spaces/`, viewer bridge/cache/navigation; dashboard API/pipeline/recovery/Console;
SDK discovery and persistent job/request endpoints.

- [x] Android physical rover registration, latest calibration/status loading and mission path
  restoration implemented and compiled.
- [ ] Persist calibration-start and stage request identity before POST. Recover unknown outcomes
  by lookup; server changes must not reuse IDs or sessions from the previous server.
- [ ] Distinguish reachable, reconciling, ready, offline and active-session state. Readiness requires
  current lineage/spec/artifact verification; a failed restore leaves mutation controls disabled.
- [ ] Test robot switching, including an unreachable selected robot, old watcher callbacks, active
  sessions and stale calibration results. Permit recovery without transferring another robot's state.
- [ ] Restore selection, draft, active deployment and scene identity after rotation/process death;
  resume safety observation independently of discovery. No automatic drive resumption.
- [ ] Complete paginated saved scan/twin/robot/mission/deployment discovery (>100 records) and stage
  result restoration, avoiding normal ID copying in either client.
- [ ] Add provider settings and requested/actual-provider badges after E1 contracts stabilize.
- [ ] Keep native Stop/E-STOP and dashboard equivalents operational during busy work, network
  reconnect and renderer failure; show stop acknowledgement/uncertainty rather than assuming success.
- [ ] Add instrumentation for navigation, restricted viewer messaging, rotation/state restore and
  failure paths; build separately from executing on the device.

**Exit:** scripted full mission and refusal/reconnect flows pass on dashboard; Android unit/bridge
checks and instrumentation build pass, with actual instrumentation execution recorded separately.

## E4 — Finish shared 3D, responsive layout and measured results

19 September native mesh/cache batch: `SpacesRepository` now selects the 100k geometry asset by
its ID-scoped URL rather than array position, streams downloads through `BoundedAsset`, and
publishes uniquely named geometry/surface files before switching `last-scene.json`. Download or
publication failure removes newly created files while retaining the previous scene/assets. PLY/OBJ
surfaces carry the manifest's `to_map` into the shared renderer; Android exposes an Imported mesh
toggle. Only same-mesh ID-scoped server routes and local cache URLs are accepted. Geometry is capped
at 1.6 MB with 16-byte stride checks, surfaces at 16 MiB. Oversized surfaces are explicitly omitted
while measured points remain available; the UI explains the limit. This bounds downloads, not all
temporary memory used by mesh parsers.

Verification: 74 dashboard/viewer tests passed, including local-origin restrictions, surface
transform forwarding and stale-response suppression. Android bounded-stream tests cover declared
and streamed oversize, truncated responses, invalid stride, temporary cleanup and preservation of
an existing asset. Full Android unit suite: 36 tests; final debug and instrumentation APK builds
passed offline in 32 s. Dashboard production build and shared-viewer
packaging passed; existing chunk-size warnings remain. Actual phone/offline/visual acceptance and
whole-cache retention/eviction remain open. Unique successful generations are currently retained to
avoid deleting a scene still displayed by a WebView. No server API or robot control behavior changed.

19 September rendering continuation (partial): imported PLY surfaces now use their vertex colors
with a white material instead of a uniform tint. Filtering non-indexed OBJ faces retains matching
color attributes; malformed shortened color streams fall back to a neutral material rather than
misassigning colors. Three.js loaders already convert colors to the working space, so no second
conversion is applied. New regression tests reproduced both original color-loss cases and cover
malformed color alignment. Native Android now exposes Perspective and a persisted 1–8 px point
size control through the origin-restricted viewer message channel. Real scan geometry, navigation
coordinates and robot motion dimensions are unchanged.

Verification: 71 dashboard/shared-viewer tests passed; production build passed; shared Android
viewer packaged; Android unit/debug/instrumentation APK builds passed (42 s). Browser bootstrap
failed because its native bridge was unavailable/untrusted; no fresh screenshot, visual acceptance
or performance claim. Native surface loading/cache, full control/result parity and phone checks
remain open. Existing dashboard/shared-viewer chunk-size warnings remain. This is a focused
rendering fidelity improvement, not closure of E4 or a hardware accuracy measurement.

**Owns:** shared viewer modules, Android scene/cache adapter, dashboard workspace/results, SVG charts.

- [ ] Finish native imported surfaces and viewer controls: point size, layers, selection/dimensions,
  camera views, scale/legend, start/home/goal and correct bound specification.
- [ ] Bound cache downloads before allocation, preserve prior cache on replacement failure and
  label offline timestamp/identity. Block remote navigation and untrusted bridge messages.
- [ ] Test actual geometry after reconstruction before segmentation; distinguish semantic/inferred,
  occupancy, measured and illustrative layers without changing coordinate interpretation.
- [ ] Verify color space, depth-tested circular points, floor-relative transform and true rover scale.
  Preserve camera through route/layer/telemetry updates; do not recreate live tubes every frame.
- [ ] Complete pause/scrub/speed and timestamp/heading replay; label legacy untimed traces.
- [ ] Complete Android result/square report parity and persistent trial measurement records. Compare
  reported, simulated and tape-measured values separately; never infer physical truth from odometry.
- [ ] Test narrow dashboard workspace-first layout, collapsible controls, touch targets, status and
  Stop/E-STOP visibility. Preserve landing identity and its illustrative status.
- [ ] Review the remaining chunk warning; reduce unnecessary startup work and measure the effect
  rather than hiding the warning through a larger configured threshold.

**Performance protocol (A10):** record OS/GPU/browser/build, viewport, DPR, fixture hash and point
count. Warm up 10 s; record three 60 s runs with the same orbit and growing live trace. Include
geometry-ready time, frame p50/p95/max, calls/geometries/textures and long stalls. Exclude deliberate
idle/hidden periods from frame latency; test their reduced rendering separately. Do not run builds
or pytest concurrently. Run laptop 500k and phone 100k independently. Follow with at least 20 scene
switches and mount/unmount cycles, context loss/restore and failed fetch. Counters must return to a
stable post-disposal baseline; inspect retained resources if they trend upward. Record memory
separately; counters alone do not prove whole-process memory stability.

**Exit:** both clients pass A1 and share scene/picks/routes/results. Laptop performance and resource
checks pass; phone measurement remains explicitly open until E7, even when desktop passes.

## E5 — Calibration, firmware faults and accuracy evaluation

**Owns:** firmware calibration state machine, fake rover, calibration arithmetic/read-back,
controller/simulator/evaluator, golden tests and persistent measurement reports.

- [ ] Host-test async calibration ticks under Stop/E-STOP/heartbeat expiry, timeout, invalid command,
  missing IMU/range data, obstacle, duplicate starts and terminal-state reuse. Record tick/time bounds.
- [ ] Validate stopping behavior during calibration and drive against configured firmware deadlines;
  physical braking distance remains measured separately. Never infer motor stopping from HTTP alone.
- [ ] Guided motor/encoder/gyro signs, bounded wheel-step target/measured speed traces and explicit
  calibration apply/read-back/rollback all work against fake rover with simulated provenance.
- [ ] Record repeated straight/spin measurements, before/after residuals and repeatability; explain
  rejected measurements. Counts-per-metre and effective wheelbase use correct units/signs.
- [ ] Preserve previous accepted settings and verify coordinated config/local snapshot publication;
  fake measurements cannot update a physical spec. Do not relabel unrelated noise as calibrated.
- [ ] Add accuracy report A1–A9 with input identities, measurements/assumptions and error budget.
- [ ] Version the stronger release gate; add held-out 100-run representative evaluations, explicit
  collision veto for release acceptance and pre-/post-int8 comparison. Preserve legacy results with
  their original gate version. Test routes that must refuse, not only easy passing scenes.
- [ ] Exercise calibration -> twin -> mission -> train -> int8 gate -> deploy -> report on fake rover,
  then change calibration and prove the old artifact refuses while history remains inspectable.

**Exit:** deterministic fault traces and golden math pass; fake complete loop and held-out release
suite pass. Assumed noise and the short-route operating envelope remain visible until measured.

## E6 — Software release candidate

- [ ] Full pytest, whole-repository flake8/mypy, dashboard tests/build, shared viewer packaging,
  Android unit/debug/instrumentation builds, firmware host/golden and ESP32 normal/self-test builds.
- [ ] Browser tests cover both scan entrances, geometry loading, saved discovery, gate refusal,
  trusted Stop/E-STOP input, calibration, lost responses, reconnect and responsive layout.
- [ ] Clean CI checks source completeness and builds without bundled model weights; explicitly
  distinguish buildable no-model APK from an APK ready for local inference.
- [ ] API/SDK/OpenAPI/fixtures and migration compatibility pass; source and artifact manifests match.
- [ ] A complete local-only run succeeds with all external API traffic prohibited. Configured remote
  failure runs fall back or refuse as specified, without altering deployment safeguards.
- [ ] Update README/product/setup claims, current limitations and evidence ledger. Preserve supplied
  slide/PDF originals; any revised copies describe implemented versus future capabilities clearly.

Useful commands (run from repo root unless the block sets another directory):

```powershell
python -m pytest -q --no-cov
python -m flake8 sdk orchestrator capture reconstruction semantic robot sarvam twin policy deployment tests scripts noxfile.py
python -m mypy sdk orchestrator capture reconstruction semantic robot sarvam twin policy deployment tests
python scripts/export_openapi.py
```

```powershell
Set-Location dashboard
npm test -- --run
npm run build
```

```powershell
Set-Location MOBILE-APP
$env:JAVA_TOOL_OPTIONS='-Djdk.net.unixdomain.tmpdir=D:/tmp'
.\gradlew.bat :app:testDebugUnitTest :app:assembleDebug :app:assembleDebugAndroidTest --offline
```

```powershell
$env:SCANBORN_CXX='C:/MinGW/bin/g++.exe'
python -m nox -s firmware_host
$env:SCANBORN_ARDUINO_DATA='D:/Arduino15'
python -m nox -s firmware
```

Run `scripts/check_live_drive.py` only with the matching backend/dashboard started and isolated
test data. Regenerate recorded fixtures when response shapes change, and golden vectors when
controller/spec/format changes. Inspect generated diffs; passing regenerated expectations alone
does not establish correctness. Device instrumentation and QEMU have separate environment gates.

## E7 — Phone and physical acceptance

- [ ] Arrange the daily-phone session; install the agreed build only when the device is available.
  Verify About/Library upgrade, capture permissions and AI release/reload using the actual phone.
- [ ] Record a room scan and 2 m axis walk; run `scripts/check_scan_axes.py`; retain data provenance.
  Measure A3 dimensions across repeated scans. Refuse physical-ready status if scale/frame is unknown.
- [ ] Run Android instrumentation and A10 on S24 FE; repeat offline/restart/context-failure flows.
- [ ] With hardware available, complete bench sign/sensor/deadman checks before wheels-on-floor work;
  measure calibration A5, marker placement A4, and update only measured spec/noise parameters.
- [ ] Freeze the short-route test layout and mission/spec version, record all 10 A8 trial attempts,
  tape-measured x/y goal offsets, radial error, reported error, orientation if measured, and failures.
- [ ] Run the square diagnostic separately. Compare true physical measurements with reported and
  simulated traces; investigate disagreement rather than relabelling reported pose as ground truth.
- [ ] Retain legacy capture app until phone acceptance passes. Do not claim general autonomy,
  longer routes, arbitrary surfaces or accelerator performance from this bounded demonstration.

**Exit:** all applicable master-plan accuracy targets are met with retained raw evidence, or the
release remains incomplete with an explicit defect and revised supported envelope. Scope changes
are documented before rerunning acceptance; failed attempts remain in the record.

## Evidence record for every completed batch

```text
Batch / requirement IDs:
Changed behavior and affected files:
Baseline defect / regression reproduced:
Commands, test counts and pass/fail:
Build / environment / fixture hashes / random seeds:
Actual provider and physical-or-simulated provenance:
Accuracy metrics, units, sample count and measurement method:
Evidence paths (logs, screenshots, traces, manifests):
Compatibility / migration / rollback result:
Remaining defects and unexecuted checks:
Status: implemented | software verified | device verified | blocked
Next dependency:
```

## Current execution position

### 19–20 September implementation and broad verification

Changes completed in this pass:

- E2: `PendingScans.create` is idempotent only for the same server/metadata and refuses identity
  reuse that would overwrite a saved capture. New restart, server isolation, corrupt-manifest,
  completion-cleanup and discard-scope tests. Upload HTTP 408/425/429 now remain retryable instead
  of permanently rejecting the durable frame; response classification has a regression test.
- E3: Android separates server reachability from pipeline readiness. Failed saved-twin/mission
  reconciliation and stale missions do not enable mission stages; stage methods also enforce the
  gate. Browsing can recover by selecting a different saved space/robot. Restored training carries
  its bound spec back into the scene, and selecting a saved twin clears an unrelated cached mesh ID.
  These paths compile; full instrumented lifecycle and unknown-POST-outcome coverage remain open.
- E4: renderer acknowledgements now trigger deletion of obsolete UUID-named cache assets. Only an
  acknowledgement matching the currently published scene can prune; late loads return no success
  acknowledgement. Publication/pruning are serialized on native IO. A 128 MiB generation budget
  reserves room before download, refusing growth when scenes never finish loading. Pure retention
  tests preserve current assets and unrelated files and verify quota behavior. This closes the
  previously noted unbounded successful-generation retention; physical cache/lifecycle checks remain.
- E6: CI now compiles the Android instrumentation APK as well as unit tests/debug APK. No remote
  CI execution was performed and no claim of a clean checkout build is made for uncommitted sources.
- Firmware verification uncovered a QEMU idle-task cache panic before SELFTEST. Symbolized stack:
  `esp_cpu_wait_for_intr -> esp_vApplicationIdleHook -> prvIdleTask`. The identical binary passed
  with `-icount 3`; nox now uses instruction-count timing and snapshot flash. No firmware logic,
  motor controls or safety checks changed. This is functional emulation, not real-time performance.

Fresh evidence:

| Check | Result / evidence |
|---|---|
| Full Python suite | 636 passed, 1 skipped, 356.71 s; `build/regression-20260919.txt` |
| Whole-repository flake8 | Passed |
| Whole-repository mypy | Passed, 126 source files; changed noxfile checked separately afterward |
| Dashboard/shared viewer | 74 tests passed; production/shared-viewer builds passed |
| Android | 40 unit tests passed; debug/instrumentation APK builds passed, final run 28 s |
| Firmware host | 7 golden rows, 40 odometry ticks, zero failed checks |
| ESP32 builds | Normal and SELFTEST passed; `build/firmware-20260919.txt` |
| QEMU functional checks | Passed after timing fix, 15 s; `build/qemu-20260920.txt` |

QEMU SELFTEST first failed under wall-clock timing; both the failure log and controlled `-icount`
experiment are retained under `build/firmware/`. The normal image reaches missing-sensor refusals,
then hits the known emulated Wi-Fi PHY assertion; this session does not verify networking, sensors
or motors. The pytest skip remains the unavailable real ONNX export dependency. An initial noxfile
lint invocation used the dashboard directory and could not find the file; rerun from root passed.

Remaining software work is not reduced to hardware alone: calibration-start unknown outcomes,
full native recovery instrumentation, mission/deployment discovery, provider settings UI, persistent
physical trial reports, complete firmware calibration-state fault tests and stronger held-out
accuracy evaluation still need implementation/closure. Fresh browser visual/recovery acceptance,
S24 FE frame-time/memory tests and physical scans/drives are also open. Existing bundle-size warnings
remain. No commits, pushes, model downloads or physical-device actions in this pass.

E1.1, E1.2 and E0 retain their recorded software verification. Latest broad run: 636 passed,
1 skipped (`build/regression-20260919.txt`). E2–E4 have the partial progress detailed above;
next priority is **E3 unknown mutation outcomes and native lifecycle recovery**, alongside remaining
E2 fault cases. E7 stays open until the phone and rover sessions happen; no whole stage is closed
merely by the baseline test totals.

### 20 September — calibration request durability and firmware safety decisions

Implemented:

- Calibration orchestration accepts an optional stable `request_id`, commits the session and
  request fingerprint before sending the rover's motion command, and returns that same session
  on retry. Changed parameters with the same identity are refused. Transport failure records an
  interrupted session with `start_state: unknown`; it does not claim successful motion or completion.
  Heartbeats are closed on failure and firmware deadman protection remains required.
- Added read-only `/robots/{robot_id}/calibration-requests/{request_id}` recovery, Python SDK and
  dashboard API methods, and regenerated OpenAPI. Legacy requests without an identity still work.
  Tests check persistence before motion, loss after rover acceptance, no replay, completed retries,
  mismatched/invalid identities and read-only SDK lookup.
- Extracted the firmware calibration fault decision into `calibration_safety.h`, used by the real
  tick function. Preserved safety priority and strict deadline comparisons. Twelve cases now run
  in both host and ESP32 SELFTEST code: heartbeat/timeout boundaries, IMU loss, fault priority and
  unsigned clock rollover. These are decision tests, not motor-stop latency measurements.

Fresh checks: calibration/reconciliation batch 24 passed; final recovery tests 5 passed;
SDK/OpenAPI/recovery batch 16 passed; dashboard 76 tests passed; production and Android viewer
asset builds passed. Changed Python flake8 and mypy passed (3 source files). Firmware host passed
before and after extraction: 12 new safety cases plus 7 golden rows and 40 odometry ticks.
The existing 568 kB/619 kB bundle warnings remain. Additional combined regression and ESP32 build
results are recorded below when those commands finish.

Dashboard follow-through: the calibration panel now persists its request before POST, restores via
GET only, and offers an explicit retry that retains the original parameters and identity. Storage
failure refuses the motion request. A lost response triggers only a read-only lookup. Robot changes
clear sign confirmation and prevent late action results from replacing the new robot's session.
Four regression tests cover persistence ordering, response loss, restoration and storage failure.
Final dashboard suite: **80 passed**, production build passed. Native recovery is still outstanding.

Combined Python regression: **40 passed**, 105.46 s (`build/recovery-20260920.txt`). Normal ESP32 and
SELFTEST builds passed (`build/firmware-20260920-recovery.txt`, six minutes). QEMU functional checks
passed (`build/qemu-20260920-recovery.txt`, 15 s), including the new safety cases in SELFTEST.
The normal-image QEMU gate still does not verify physical networking or sensors. Source completeness
passed: 125 application files, none ignored. Android sources were not changed or rebuilt in this batch.

**Still open:** native screens must persist the calibration request before submission and recover
it through the new read-only endpoint. Browser interaction/reload tests must exercise the new panel;
helper tests and a production build alone do not close E3.
Full calibration tick/actuator fault coverage, provider settings UI, held-out accuracy release
evaluation, physical trial reports, native lifecycle instrumentation, fresh browser acceptance and
phone/rover acceptance remain open. No physical result, new renderer performance measurement or
whole-plan completion is claimed. Existing staging is preserved; no commits or pushes.

### 20 September — reconcile Claude handoff and finish capture-screen recovery

Compared the supplied Claude transcript with the current source, preserving all overlapping work.
The transcript is evidence of reported authorship, not a separate checkout: exact percentages or
per-author line counts cannot be recovered from the combined uncommitted working tree.

| Handoff work | Current verification |
|---|---|
| Import units/up-axis, provenance and SDK `import_scan` | Present in capture imports, dashboard controls and Python SDK; import/coordinate tests rerun |
| Bounded import validation and preserved source geometry | Present; targeted safety/axis tests rerun |
| Intent evaluation fixtures and provider contracts | Present; intent/provider suites rerun |
| Durable upload queue and saved-scan store | Present and retained; later identity/closure fixes remain in place |
| Saved-scan restore/discard in ScanController | Present, but discard was not reachable from ScanScreen before this batch |
| Confirmed discard, release error, permanent permission denial UI | Missing at the handoff boundary; implemented in this batch |

Changes:

- ScanScreen exposes confirmed deletion of locally saved frames, accurately explaining that server
  frames remain. Capture actions stack vertically instead of overflowing a narrow row; dropped
  frames are included in visible counters.
- Model-release failure becomes a visible error with Retry and Back. Cancellation is propagated;
  capture ownership is released on exit. Camera permission is rechecked on resume, and permanent
  denial offers the Android app-settings screen.
- Found and fixed a late upload/discard race: after queue closure an in-flight response cannot
  write receipts into the discarded directory or emit late counter callbacks. On ordinary exit its
  frame remains pending for safe retry under the existing stable scan/index. This supersedes the
  earlier 19 September note saying an in-flight acknowledgement always writes a receipt on close.
- Discarding a scan with an existing ARCore session now publishes Ready after resume; it previously
  could remain at Checking. Failed local deletion hides the unusable Retry-upload action and offers
  explicit discard retry or reopening for recovery.

Fresh evidence: **128 Python tests passed** in 48.20 s across import safety, scene axes, capture
validation/metadata, intents and provider execution (`build/claude-handoff-audit-20260920.txt`).
Android: **41 unit tests passed**, debug and instrumentation APK builds passed offline; final run
20 s (`build/android-20260920-capture-final.txt`). New latch regression covers discard while an
upload is in flight. Source completeness: 125 application files, none ignored.

E2 remains partial: actual camera-denial/settings navigation, ARCore lifecycle, model release/reload,
process-kill/network recovery and physical capture require instrumentation/device evidence.
Native calibration request recovery, provider settings UI, accuracy release evaluation and the
remaining E3–E7 gates are still open. No device actions, downloads, staging, commits or pushes.

### 20 September — native calibration request recovery implementation

- Added app-private, versioned calibration request records before POST. They retain server, robot,
  request ID, mode, duration and speed. Recreating the store returns the same identity; another
  robot or mode cannot silently replace an unresolved request. Damaged or unwritable storage
  refuses a new start instead of losing its recovery identity.
- Spaces restores the pending robot on app/server initialization and uses the existing GET request
  lookup to recover the session. A failed POST triggers only a GET lookup. Reissuing POST requires
  the explicit Retry saved calibration request button, with the persisted original parameters.
  Records remain durable throughout running sessions and clear only on observed terminal state.
- Recovered results must match both request and robot identities. Stale settlement callbacks cannot
  remove another pending request. Ordinary calibration polling resumes after recovery; motion does
  not restart automatically. Native Stop sends E-STOP while the session ID is unknown and reports
  that escalation; the dedicated E-STOP path remains independent of the viewer and busy work.
- Refresh invalidates previous pipeline readiness before its health probe, preventing a failed
  restore plus a later successful status callback from re-enabling stale pipeline controls.

Verification: three new durable-store tests cover recreation/retry, server and robot isolation,
changed modes, stale settlement, damaged records and failed persistence. Android unit suite:
**44 passed**, debug and instrumentation APK builds passed offline. Backend recovery contract:
**5 passed**, `build/native-calibration-contract-20260920.txt`. Android log:
`build/android-20260920-calibration-recovery.txt`. Source completeness: 127 application files,
none ignored. No physical device or external provider was contacted.

Native calibration recovery is now implemented, but E3 is not closed: the new Compose controls,
process recreation against HTTP, unknown-outcome Stop and server switching still require Android
instrumentation execution. Stage/deployment recovery, provider settings UI, accuracy evaluation,
remaining firmware fault coverage and E6/E7 acceptance remain as listed above. Do not convert the
unit tests or APK build into a claim of physical acceptance.


### 20 September — providers, release evaluation, firmware faults and native deployment recovery

Implemented E3/E4/E5 continuation (not closure of the entire plan):

- Dashboard and native Spaces provider panels now expose ordered planner/profiler chains, export
  selection, credential readiness, request ceilings and explicit per-workload remote-data consent.
  POST /providers/settings validates the complete configuration before atomic persistence in the
  configured data directory. Saved settings override environment provider defaults, contain no
  credentials and affect subsequent requests. Local reset also repairs damaged settings. Python
  SDK and OpenAPI updated; dashboard fixture regenerated. No paid provider was contacted.
- Added release-v1-100-zero-collision evaluation, separate from the historical mission gate.
  Three traversable scenes use 100 paired reserved-seed runs for both float and the actual int8
  artifact, plus blocked-goal and unknown-barrier refusal cases. Reports retain suite hash, policy
  and artifact checksums, spec snapshot, outcome samples and p95 errors. A reference controller
  trained on a separate synthetic route passed all 600 simulated runs with zero collisions;
  int8 p95 miss: straight 0.03368 m, corner 0.04562 m, detour 0.06249 m. Assumed rover/noise
  parameters remain simulated provenance. This is not measured physical accuracy, and the release
  suite must not become training/tuning data. Command: scripts/evaluate_release.py; report:
  build/release-evaluation/d6d7e25dcfbb4ac0a1d7300fd9676427/release-report.json.
- Host tests now compile the production calibration state machine against deterministic hardware
  stubs: 49 checks cover invalid admission, duplicate start, Stop, heartbeat identity/deadline,
  IMU loss, external safety latches, timeout, bounded wheel-step settling and obsolete API refusal.
  Fixed stale ToF health: initialization alone is not ready, and readings older than 250 ms refuse
  motion / trigger tof_fault. Host checks cover first measurement, expiry and millis rollover.
  External safety latch tests do not pretend to exercise real sensor electronics. CI includes the
  host tests with pinned ArduinoJson 7.4.3 headers; hosted CI itself has not run in this session.
- Android deployment requests now persist robot/artifact/request identity before POST. App restart
  uses GET /deploy/requests/{id}, never automatic replay. Records remain until a terminal deployment
  is observed. Explicit retry reuses the original request; failed/interrupted requests require
  acknowledgement. Unknown deployment Stop escalates to E-STOP. Durable-store tests cover restart,
  identity conflicts, server isolation, stale settlement, damaged storage and failed persistence.
  Deployment detail responses now include robot_id/artifact_id for recovery lineage checks.
- Browser acceptance was stale after the shared viewer extraction. Updated canvas/status selectors,
  viewport-relative sampling and lit true-scale rover checks. Added provider consent/local-save,
  WebGL context loss during a drive, restoration, explicit failed-request acknowledgement and
  390 px layout assertions. The narrow-screen check found real header overflow; status controls
  now wrap, telemetry tables wrap on phones, and the benchmark grid can shrink.

Fresh checks:

| Check | Result / evidence |
|---|---|
| Full Python | 652 passed, 1 skipped; 365.07 s; build/regression-20260920.txt (before final additive deployment fields) |
| Final deployment/OpenAPI follow-up | 11 passed; build/deployment-lineage-20260920.txt |
| Provider tests | 76 passed; build/providers-ui-20260920.txt |
| Dashboard | 82 passed; production/shared-viewer builds pass; build/dashboard-final-20260920.txt |
| Android | 48 unit tests, debug + instrumentation APK builds pass; build/android-20260920-deployment-final.txt |
| Source completeness | 130 application files, none ignored |
| Firmware | golden math/freshness + 49 calibration checks pass; normal and SELFTEST ESP32 builds pass; build/firmware-20260920-freshness.txt |
| Emulated ESP32 | SELFTEST and normal boot checks pass; build/qemu-20260920.txt |
| Browser/fake rover | 18 checks pass, no page errors; build/live-check-20260920/report.json |
| Static analysis | Final full flake8 and mypy (130 files) clean; logs build/lint-final-20260920.txt and build/types-final-20260920.txt |

Browser runs use an isolated data directory and temporary browser profile. Native instrumented
APK compilation is not instrumentation execution. No new physical actions, external API calls,
model/toolchain downloads, staging, commits or pushes occurred. Existing large Three.js chunk
warnings remain; this run does not replace the defined desktop/phone performance acceptance run.

Still open: Android HTTP/process-kill lifecycle instrumentation (capture, deployment and
calibration), generic native stage-result recovery/discovery beyond the stored selections, broader
accuracy/clearance measurement records and physical A1–A9 acceptance, live remote-provider accuracy,
remaining sensor/hardware fault coverage and the remaining E6/E7 evidence gates. The two source
plans remain authoritative; do not turn these implementation results into blanket checked boxes.


### 20 September — executed Android recovery instrumentation and sensor safety coverage

- Added a constructor seam to SpacesViewModel for an isolated storage root and server URL flow.
  The normal two-argument Android factory constructor retains production preferences/storage.
  RecoveryHttpTest uses the real native HTTP repository and Android filesystem, disposing the
  ViewModel and recreating it with an empty SavedStateHandle. Deployment and calibration recovery
  issue only GETs; unknown-outcome Stop sends the native rover E-STOP request. A third case switches
  servers, verifies pending drive isolation, then restores the original server identity.
- Executed the instrumentation APK on the already-installed Pixel 9 Pro API 37 x86_64 emulator
  (ARM64 translation). Started with -read-only, -no-snapshot and -no-window; no emulator image was
  downloaded and no physical phone was attached. **6 instrumentation tests passed**, including
  the existing viewer-origin/asset tests and application-context test. Logs:
  build/android-instrumentation-final-20260920.txt. This is fresh ViewModel recreation evidence,
  not an OS process kill, Compose rotation/navigation, ARCore or S24 FE rendering evidence.
- Added a host harness compiling actual firmware safety.cpp against Arduino/VL53L0X boundary
  stubs. **23 checks pass**: unmeasured/stale/timeout/failed range sensor, exact freshness and
  obstacle boundaries, out-of-range readings, rollover, debounced button edges, sustained low
  battery and missing battery during motion. Existing golden and 49 production-calibration
  checks also pass. Evidence: build/firmware-faults-20260920.txt. Added the sensor-safety host
  session to CI; hosted CI has not run here. Production firmware code did not change this batch.
- Added robot/accuracy_report.py and scripts/evaluate_measurements.py for proposed A3/A8 evidence.
  Reports require a complete predeclared attempt roster, retain failures/unmeasured attempts,
  reject duplicate/dropped/invalid measurements, bind a declared spec checksum and timestamp,
  report individual errors plus median/p95/max, and veto collisions. A3 requires five spans
  across three scans; A8 requires at least ten attempts and 90% completed within 10 cm. Simulated
  records cannot become physical acceptance candidates. Physical provenance remains an operator
  assertion requiring independent review; no report changes calibration or enables deployment.
  Usage and limitations: docs/robot/accuracy-measurements.md.

Fresh validation: **38 Python accuracy/calibration/spec tests pass**, build/accuracy-records-20260920.txt;
flake8 and mypy pass for new Python files/nox. **48 Android unit tests** and debug/instrumentation
APK builds pass, build/android-lifecycle-20260920.txt and android-lifecycle-final-20260920.txt.
Source completeness: **131 application source files, none ignored**. The prior full Python,
dashboard and ESP32 compilation results remain historical; no unrelated full suites were rerun.

No provider keys were configured in the current process environment, so no live provider was
contacted or scored. No physical phone/rover was operated. Remaining: actual process-kill and
Compose lifecycle/navigation tests, capture camera/model/ARCore tests, physical A2–A5/A8/A9 data,
live remote-provider evaluation, remaining generic native stage recovery/discovery and E6/E7
acceptance. A3/A8 reporting support is implemented; physical accuracy gates remain unverified.


### 20 September — process interruption, native screen safety and saved history

Built and executed the next E3 recovery slice:

- Added an emulator-only two-phase process harness. It waits until a durable pending request is
  being looked up, force-stops ScanBorn, then starts a new instrumentation process. The test
  verifies a different PID, resolves the recorded result by GET, settles the durable identity and
  observes no POST. Deployment and calibration both pass. The harness checks emulator identity,
  boot completion and installed APKs before acting; it never targets a physical-device serial.
  Evidence: build/android-process-final-verified.txt and build/android-process-final-verified/.
  Preparation logs deliberately report a crashed process; recovery logs require OK (1 test).
- Added a non-exported debug-only RecoveryTestActivity hosting the real Spaces Compose screen.
  RecoveryScreenTest injects the renderer-error message, exercises native Stop, recreates the
  activity, and exercises E-STOP. Buttons remain visible/enabled and reach the expected native
  HTTP endpoint. This tests the renderer-error state, not actual Android GPU context loss.
- Added paginated native saved mission/deployment discovery and a collapsible history panel.
  Reopening a mission checks its twin and stale status before restoring start/goal/via and the
  route. It clears old training/artifact selection and the previous completed drive trace.
  Viewing a deployment is read-only and separate from active-drive state and robot selection.
  HTTP instrumentation covers pagination, successful reopen, stale/cross-twin refusal, coordinate
  restoration, old trace removal and inspecting results without issuing motion requests.

Verification: Android unit tests **48 passed**; debug and instrumentation APK builds passed
(build/android-history-final-build.txt). Normal instrumentation: **8 passed, 1 skipped**
(build/android-history-final-instrumentation.txt reports OK (9 tests); the skipped case is the
special two-phase process method, executed separately for both request kinds). Both actual
force-stop/new-process checks passed. New Python harness flake8/mypy pass. Source completeness:
**135 application source files, none ignored**. No backend API changes in this slice.

Test-environment corrections: the first install was attempted before the emulator package service
was ready; a later replacement install failed for insufficient temporary storage because the debug
APK includes existing model weights. A stale-app/new-test mismatch was detected, not accepted as
product evidence. Removed only the app installed on this temporary read-only emulator and installed
both current APKs with exit-code checks; all results above are from that matching build. The emulator
was started without saved-snapshot changes and is shut down after testing. No phone, physical rover,
provider API, model download, staging, commit or push was involved.

Remaining: generic native stage request/result recovery, capture process interruption and camera/
model/ARCore lifecycle, full navigation and real WebView/GPU lifecycle coverage, live API-provider
accuracy and physical A2–A5/A8/A9/A10 measurements. Pending deployment/calibration OS process
interruption and the stated Spaces screen recreation gate are now evidenced; do not leave those
specific checks described as wholly unexecuted. E3/E6/E7 are not blanket-complete.


### 2026-09-20 — durable generic stage recovery and capture queue process interruption

Implemented:
- Read-only `GET /stages/{stage}/requests/{request_id}` exposes persisted status/result for
  reconstruct, segment, generate-twin, train, optimize, plan and plan-preview. The allowlist
  excludes deployment/motion. Stage identities remain isolated; GET never creates jobs.
  Python SDK and dashboard mapping/proxy/OpenAPI updated.
- Android StageJournal atomically records server, original inputs, stable request identity and
  pipeline checkpoint before submission. Successful result acceptance publishes the checkpoint
  and settles the pending identity together. Disk/corrupt-record failures refuse admission.
- Native recovery controls check by GET, explicitly retry the original request, or acknowledge a
  server-confirmed failed/interrupted result. Startup never POSTs a stage. Pending stage blocks
  conflicting scene/coordinate edits; recovered work requires pipeline revalidation. Old mission/
  policy selection clears when replacing geometry/twin; old artifact clears when retraining.
- Segmentation completion is checkpointed so twin creation can continue after interruption.
- Emulator harness now force-stops and verifies a different PID for pending processing stages
  in addition to deployment/calibration. A separate synthetic capture test kills an upload before
  acknowledgment, restores the same scan ID/metadata/bytes, confirms no automatic upload, then
  explicitly finishes and verifies queue settlement and cleanup. This is durable queue evidence,
  not camera, ARCore, physical geometry or model-release evidence.

Checks: 31 backend/SDK recovery tests; 9 OpenAPI/dashboard contract tests; targeted flake8/mypy;
50 Android JVM tests; debug/instrumentation builds; 83 Vitest tests and production build pass.
Ordinary Android instrumentation: 9 passed, 2 harness-only methods skipped (OK 11 tests).
All four separate force-stop/new-process cases pass: deployment, calibration, stage, capture.
Evidence: `build/stage-recovery-python.txt`, `build/stage-contracts.txt`,
`build/stage-recovery-final-build.txt`, `build/capture-process-build.txt`,
`build/capture-stage-final-instrumentation.txt`, `build/capture-stage-process-final.txt` and
`build/capture-stage-process-final/`, `build/stage-dashboard-tests.txt`, `build/stage-dashboard-build.txt`.
Earlier APK instrumentation preceded the downstream-selection fix and is superseded by these
final checks. No model downloads, provider calls, physical-device operation, staging or commits.

Remaining: real capture-screen lifecycle/navigation, camera/ARCore interruption and model handoff,
real WebView/GPU lifecycle and phone performance, live-provider accuracy, and physical acceptance.
Generic stage recovery is implemented for the five native processing actions; dashboard-wide
stage recovery and remaining full-navigation/recovery acceptance should not be inferred from
this native slice. E2/E3/E6/E7 remain partial where those gates apply.

Final regression: **675 pytest passed, 1 skipped** (full suite, 362.08s;
`build/stage-full-pytest.txt`). Canonical `python -m nox -s lint` passes flake8 and mypy
(132 source files; `build/stage-project-lint.txt`); the initial unscoped lint commands were
superseded by this repository-defined scope, excluding bundled third-party llama code.
Source completeness: **139 application files, none ignored**. Final installed/debug APK SHA-256
matches (`build/stage-installed-sha256.txt`, `build/stage-built-sha256.txt`). Temporary read-only
emulator shut down. This batch does not claim phone or physical accuracy acceptance.
