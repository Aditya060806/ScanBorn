# Reliable scan-to-drive and shared viewer implementation

This is the active continuation of the owner's approved dashboard/Android plan. It preserves
the existing CPU pipeline, Qwen model and visual identity. No commits or pushes were requested.
Earlier phase completion labels do not close the new acceptance gates below.

## Milestones and evidence

| Milestone | Implemented | Acceptance still open |
|---|---|---|
| Foundation | Root `/data/` ignore; source completeness checks; Library detail, combined search/filter, quoted tokens, Room migration; planner system prompt override; document-picker status; legacy branding replaced while retaining formula infinity | Phone About/Library migration and visual checks |
| Capture | Stable IDs; bounded disk-backed upload admission; recovery and receipt counts; serialized native lifecycle; explicit partial Finish; model release before AR; finite camera validation, array-header limits, atomic writes and server commit/Finish locks | Process-kill/slow-network instrumentation, model release/reload on S24 FE, imported-file limits and atomic import transaction |
| Pipeline | Immutable stage directories; pipeline ownership checks; exported-int8 validation/checksums/spec snapshot; persistent jobs/request IDs; restart interruption; telemetry ordering; atomic deployment reservation; explicit same-marker rescan confirmation | Expand fault injection around recording failure, malformed external requests and artifact lineage; dashboard request identity/recovery UX |
| Shared renderer | Framework-independent scene/camera/geometry/rover/layers/picking/lifecycle modules; RGB reconstruction previews at 100k/500k; optional imported faces; color conversion; floor-relative coordinates; true-scale rover; fit/inspect/top/perspective/follow; bounded trace buffers; timed playback with untimed legacy fallback; disposal/idle rendering/context-loss fallback | Full browser context-loss/remount/resource-growth evidence; p95 laptop/phone targets; actual mobile playback controls and layer parity |
| Dashboard | Dominant workspace; responsive workspace-first layout and collapsible stages; external sticky drive controls; persistent result inspector and SVG outcomes/spatial traces; calibration controls and wheel speed chart; lazy landing/workspace; ESM Vite config; point-cloud fixture | Complete live browser workflow and responsive visual audit; session discovery/recovery controls; playback completion/scrub UI synchronization |
| Native Android | Spaces and Mission controls, post-capture inspection, start/goal/via entry/picking, training/export/deploy, native Stop/E-STOP; bundled WebViewAssetLoader viewer and restricted message origin; offline scene cache; saved selections/draft/deployment; retry IDs; short independent stop request timeout | Instrumentation for bridge/navigation/lifecycle and renderer crash; physical phone acceptance; paginated browsing beyond first 100 items; full calibration native UI |
| Calibration | Capability-advertised asynchronous firmware/fake sessions; Stop/E-STOP/deadman remain active; bounded wheel-step; counts/travel and wheelbase/angle arithmetic; explicit measurement/apply/read-back/rollback; simulated provenance and separate local spec; sign-check guidance | Firmware host tests for calibration state machine itself; sensor timeout/readback faults beyond fake coverage; guided one-metre-square report; physical measurements |
| Public contracts/CI | Additive SQLite migration v4; discovery/visualization/asset/calibration endpoints; SDK additions; refreshed OpenAPI and synthetic fixture; dashboard/shared viewer/Android CI job | CI execution on clean Linux runner; Android instrumentation CI strategy |

## Verification record

- The first full Python run in this continuation: **436 passed, 2 failed**. Fixed the SDK empty
  capture guard and obsolete-calibration expectation; all 13 SDK/rover-deploy tests then passed.
- Calibration arithmetic and rover protocol/adapter checks: **19 passed**.
- Calibration safety, rollback and deployment checks after timestamp persistence: **17 passed**.
- Repository-scoped flake8 and mypy passed (**105 Python source files**) before the last capture
  hardening edits; repeat status is recorded in the handoff.
- Dashboard **49 Vitest tests** and both production viewer bundles passed before result-inspector
  addition. Remaining Three.js bundle-size warning is visible; no module-format warning.
- Android unit tests and `assembleDebug --offline` passed after native mission/retry changes.
  Branding/capture changes require the final rerun recorded in the handoff.
- Firmware host: **7 golden rows, 40 odometry ticks, zero failed checks**. Both normal and
  SELFTEST ESP32 builds pass. Normal image: **1,073,355 bytes**, globals **61,412 bytes**.
- Browser loaded the recorded synthetic scene with **16,384 reconstructed points**, bounds
  approximately **4.96 × 4.96 m**, and preserved its explicit synthetic-fixture label.
  This is geometry inspection evidence, not a frame-time or GPU-leak benchmark.

## Boundaries

Latest continuation evidence is recorded in the shared-viewer/calibration handoff: dashboard reload
and lost-deployment-response recovery, independent Android watcher restoration, browser Stop/E-STOP
and fake calibration/read-back checks, 445 full Python tests plus 20 recovery regressions, 54 dashboard
tests, Android builds, and a fixed 1240x396 laptop 500k-point run at 14.0 ms p95. The earlier renderer
timing anomaly remains documented. Dashboard discovery, non-deploy request recovery, calibration
reload restoration, square report, lifecycle instrumentation and physical phone acceptance remain open.

The S24 FE room scan, two-metre axis walk, model release/reload and About check remain physical
acceptance. The legacy capture app stays until that session passes. Actual rover tape-measured
goal error and calibration remain separate. No new model weights or Docker runtime downloads
were performed. AndroidX WebKit was downloaded following the owner's instruction to continue;
subsequent Android builds use the cache.

Continuation: calibration reload recovery, dashboard square report, native square preparation, detailed batched rover, async geometry ownership, context recovery and eight mount/dispose checks are implemented. See the handoff for measured 14.1 ms laptop p95, 59 dashboard tests and remaining native/physical acceptance gates. This supersedes the older open-item list above for those specific items.

Current status: [approved plan audit](2026-09-17-plan-audit.md). This requirement-level audit supersedes stale open-item lists above; it includes native pagination/playback/reset changes and clearly distinguishes implementation from device acceptance.
