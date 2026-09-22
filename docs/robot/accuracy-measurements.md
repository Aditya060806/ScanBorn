# Recording accuracy evidence

`scripts/evaluate_measurements.py` evaluates the master plan's proposed A3 and A8 targets from
operator-supplied measurements. It does not measure a room, verify physical provenance, update
calibration, or authorize deployment. Keep the original record alongside the generated report.

Before starting, assign a session ID and write the complete planned trial roster. Keep every
attempt, including failed, stopped and collided attempts. Record the actual robot-specification
file's SHA-256, operator, measurement timestamp and `physical` or `simulated` provenance. A
simulated record can pass its numerical target but cannot become a physical acceptance candidate.
Changing the roster after seeing results invalidates the completeness claim; review the original
session record independently. JSON alone cannot prove that an operator recorded every attempt.

Common record fields:

```json
{
  "version": 1,
  "kind": "goal_error",
  "provenance": "physical",
  "session_id": "your-session-id",
  "operator": "operator-name",
  "measured_at": "ISO-8601 timestamp",
  "spec_sha256": "replace-with-64-character-lowercase-spec-file-sha256",
  "planned_trial_ids": ["trial-01", "trial-02"],
  "trials": []
}
```

This is a template, not evidence. Replace the placeholders and supply every trial before evaluating.

For **A3**, use `kind: dimensions`. Each trial needs `id`, `scan_id`, `tape_m` and
`reconstructed_m`. Measure at least five spans across at least three separately captured scans.
Each span must be within `max(0.05 m, 2% of tape length)`. Preserve units in metres; do not substitute
a displayed bounding box for the measured endpoints. The report includes each error, median,
p95 and maximum.

For **A8**, use `kind: goal_error`. Each attempt needs `id`, `deployment_id`, `completed`,
`collision`, `stop_reason` and `tape_radial_error_m`. The error is the tape-measured radial distance
from the intended goal to the actual endpoint, not the rover's reported position. A failed attempt
may have a null error when it cannot be measured, but still counts in the denominator. Completed
attempts require a finite nonnegative measurement. At least ten attempts, at least 90% completed
within 0.10 m, and zero collisions are required. Error percentiles cover measured attempts only;
the report separately publishes unmeasured attempts so they cannot disappear from the result.

```text
python scripts/evaluate_measurements.py session.json --out reports/session-report.json
```

The command refuses to overwrite a report. Exit 0 means the declared measurements meet the
versioned target; exit 1 means they do not. Invalid/incomplete records are rejected. Inspect the
report's provenance and individual attempts before considering physical acceptance. A4 marker
uncertainty, A5 calibration repeatability and A9 square diagnostics still require their separate
records and review; A3/A8 reports do not close those gates.

## Android recovery instrumentation

`RecoveryHttpTest` runs the real native repository over loopback HTTP with app-private temporary
storage. It verifies unknown-outcome Stop escalation, ViewModel disposal/recreation with an empty
SavedStateHandle, read-only recovery and server switching. The initial six-test run did not cover OS process kill,
camera/ARCore lifecycle, Compose navigation, or a physical drive; follow-up checks are below.

The 20 September run used the existing Pixel 9 Pro API 37 x86_64 emulator with ARM64 translation,
started read-only and without saving snapshots. All six instrumentation tests passed, including
the existing viewer boundary and bundled-asset checks. This is not S24 FE rendering performance.

Additional executable checks:

- `RecoveryScreenTest` hosts the real Spaces Compose screen in a non-exported debug-only activity.
  It injects the viewer's renderer-error message, recreates the activity, and exercises native
  Stop/E-STOP through the loopback HTTP server. This does not simulate a real GPU fault.
- `scripts/check_android_process_recovery.py` drives the `processDeathRecovery` instrumentation
  method in two processes. It waits for the pending-request checkpoint, force-stops the app, and
  starts a new instrumentation process. Recovery must observe the recorded result through GET,
  settle the durable identity, and issue no POST. The test asserts that the process ID changed.
  It covers deployment and calibration pending records, not ARCore or frame uploads.

Build and install the debug APK and instrumentation APK on an emulator first. Wait until
`adb -s emulator-5580 shell getprop sys.boot_completed` prints `1`, then run:

```text
python scripts/check_android_process_recovery.py --serial emulator-5580 --out build/process-recovery
```

The script rejects physical-device serials and verifies the emulator property before any force-stop.
Its interrupted preparation logs are expected; each corresponding recovery log must contain a
passing instrumentation result. Use a dedicated emulator: force-stop intentionally terminates
ScanBorn's process on that selected emulator.

The follow-up run passed eight ordinary tests (one harness-only method skipped in that suite),
plus both separate force-stop/new-process checks. Evidence is in the execution ledger. APK update
failures must stop the workflow: never run a new test APK against a stale app installation.
