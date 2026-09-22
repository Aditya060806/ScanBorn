# Approved plan audit — 17 September 2026

Compared the supplied Downloads/PLAN.md and pasted plan with the current implementation and
the shared-viewer/calibration handoff. This is the current status index; older milestone tables
are historical. No overall completion percentage is assigned: implementation is not acceptance.

## Implemented and software-verified

| Plan requirement | Evidence / scope |
|---|---|
| Root ignore and source completeness | `/data/` is scoped to the root. `scripts/check_source_completeness.py` passes: 118 application files, none ignored. Required data sources are present but remain untracked along with other new work; no staging or commits requested. |
| Library detail, combined search/filter, punctuation, migration; prompt override; branding/storage text | Implemented in Android data, navigation, screens and AI repository/engine; Android unit tests/build recorded. Actual upgrade and About/Library interaction on the phone remain separate. |
| Server capture validation and atomic storage | Depth/camera validation, bounded uploads/imports, safe paths and commit/Finish coordination implemented; Python capture tests recorded. |
| Immutable artifacts and pipeline ownership | Reconstruction/twin/export directories, lineage refusals and int8 exported-policy validation/checksum/spec checks implemented; integrity tests recorded. Repeated segmentation reuses its existing object set; historical duplicate rows are not repaired. |
| Deployment integrity and job persistence | Robot reservation, request deduplication, telemetry validation/order, persistent results and restart interruption implemented. Dashboard lost deployment-response/reload recovery browser-verified. Drives never restart automatically. |
| Rescan registration | Explicit same-marker confirmation required. Unknown imported registration cannot silently invalidate missions. |
| Real reconstruction visualization | Versioned ID-scoped manifest/assets, deterministic 100k/500k RGB previews, floor-relative metres/Z-up, optional imported faces, semantic/occupancy distinction implemented and tested. |
| Shared renderer | Scene/camera/geometry/rover/layers/picking/lifecycle modules; six material batches for detailed true-scale rover; views, lighting, layers, markers, dimensions and legend. Correct color conversion, bounded reusable traces, timed playback and legacy untimed labels. |
| Renderer lifecycle | Obsolete asset responses ignored; disposal and idle/visibility handling; context restoration rebuilds environment lighting; dashboard retry/2D fallback. Eight mount/dispose cycles stable at 12 geometries/4 textures with canvases removed and contexts released. This is not a browser heap profile. |
| Laptop rendering target | Recorded 500k-point, 1240x396, DPR 1.25, Intel UHD ANGLE orbit/live-trace sequence: 14.1 ms p95, below 33 ms. Phone target not measured. |
| Dashboard core loop | Reconstruction visible before segmentation; mission/train/export/fake drive/result loop exercised. External Stop/E-STOP, SVG outcomes/spatial trace comparison, sticky status, lazy workspace/landing and ESM Vite configuration implemented. |
| Calibration software | Async firmware/fake/orchestration sessions, capability checks, stop/deadman, arithmetic, proposals, read-back/rollback and simulated provenance implemented. Fake wheel/straight/spin and failure tests recorded. Dashboard active-session reload and Stop browser-verified. |
| Square workflow on dashboard | Unit square planning and result report implemented and tested. Fake run: 4 m planned, 228 poses, 0.020 m reported closure. This is simulated odometry, not a physical accuracy measurement. |
| Public API/SDK/schema | Discovery, visualization, timestamped poses, jobs/validation/calibration migrations and SDK/OpenAPI additions implemented. Latest snapshot: 42 paths / 45 operations. |

## Implemented, but acceptance is still open

| Area | Remaining proof / limitation |
|---|---|
| Native capture | Disk queue, stable ID, serialized lifecycle, explicit partial Finish and model ownership gate exist. Need slow/offline network, lost first response, process-kill/restart, queue exhaustion, Finish/Cancel races and tracking interruption on Android. Need actual model release/reload with ARCore. |
| Android Spaces / missions | Native actions, coordinate entry/picking, cached offline view, saved identities and independent stop requests exist. Need full phone workflow, rotation/process restoration, offline reconnect and renderer failure instrumentation. Current instrumentation covers origin/message boundary and bundled assets, not all lifecycle cases. |
| Android discovery | This audit adds explicit Load more for scans/twins/robots using server next_offset, deduplicates returned IDs and retains selection. Build verification does not replace a >100-item device exercise. Offset-based browsing is not a frozen snapshot if records are inserted concurrently. |
| Android playback | This audit wires viewer progress/completion back to native controls; previously native progress could remain stale. Phone playback acceptance still required. |
| Android calibration | Native actions and saved-session polling exist; this audit adds explicit reset to idle. Lost first calibration response can still lose native session identity; selected-robot/history reconciliation remains open. |
| Rendering parity | Shared geometry/coordinate engine is present, but native controls still lack full imported-surface/point-size/legend-selection parity. Browser known-axis/context/resource evidence does not prove phone WebView performance. |
| Dashboard layout/recovery | Responsive CSS and recovery exist; broader narrow-screen visual audit, camera preservation and trusted Space-stop regression remain open. Non-deploy stage request recovery and paginated saved-session discovery UI remain open. |
| Calibration hardware | Firmware compiles and host/golden controller tests pass. More direct calibration state-machine sensor/timeout fault evidence and physical motor/encoder/gyro verification remain required. |
| CI | Dashboard/viewer/Android checks are configured. A clean remote runner has not been executed in this session. New untracked files are not yet part of a committed deliverable. |

## Not yet completed

1. Native square result report parity and persisted tape-measured closure records.
2. Dashboard paginated discovery for saved pipeline work and non-deploy request recovery.
3. Native calibration unknown-outcome recovery and broader lifecycle/navigation instrumentation.
4. Complete native renderer control/surface parity and retry/fallback verification.
5. Capture fault-injection and phone acceptance listed above.
6. Final regression sweep against the final tree: full pytest, repository flake8/mypy, Vitest/build,
   Android tests/build and firmware host/golden/ESP32 checks. Prior results are dated evidence,
   not a claim that all checks ran after every later edit.
7. S24 FE 100k-point p95 <=33 ms, room scan, two-metre axis walk, model ownership and About check.
8. Physical rover calibration/trials and tape-measured goal error. Keep legacy capture app until
   the agreed phone acceptance passes. Docker runtime validation remains a separate task.

## Current verification

- Fresh source completeness: 118 source files, none ignored.
- Fresh dashboard: 59 tests and production build pass. Three.js chunk-size warning remains;
  module-format warning is resolved.
- Earlier full Python run: 445 passed; subsequent focused integrity/orchestration (13) and
  calibration/OpenAPI (7) passed. Full suite has not been repeated for this audit.
- Android audit changes: unit/debug/instrumentation-APK build result recorded in the handoff.
  Instrumentation APK compilation is not device execution.

Next implementation order: finish native result/recovery parity, finish dashboard discovery and
request recovery, add targeted fault/instrumentation proof, then run final regression and the
explicit physical acceptance session. Preserve CPU pipeline/current model and all uncommitted work.

Follow-on dependency plan: [scan-to-hardware completion](2026-09-17-scan-to-hardware-completion.md). Fresh spec identity/calibration binding defects take priority over the earlier next-work ordering.
