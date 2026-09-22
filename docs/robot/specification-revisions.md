# Accepted robot specifications

The reference specification ID is `rover`; its human-readable name is `reference-rover`.
Names are labels, not filesystem paths. Calibration does not overwrite the shared reference YAML.

After successful configuration read-back, calibration writes a content-addressed YAML under
the configured data directory and publishes the accepted snapshot and session result in one
SQLite transaction. Physical and simulated files have separate directories. The robot's
`config_json.accepted_spec` holds `spec_id`, SHA-256 `revision`, `snapshot` and `calibration_id`.
Existing robot rows without this optional field still resolve the configured reference spec.
Existing artifacts without a robot binding retain the previous snapshot/geometry checks.

Pass the registered `robot_id` to `/generate-twin` and `/train` to use its accepted calibration.
The dashboard and Android do this for the selected robot. The Python SDK supports the same
optional keyword on `generate_twin` and `train`:

```python
twin = sb.generate_twin(mesh_id, objects_id, environment="home", robot_id=robot_id)
# Plan the mission against twin['twin_id'] using the existing mission API.
policy = sb.train(twin["twin_id"], mission_id=mission_id, robot_id=robot_id)
artifact = sb.optimize(policy["policy_id"])
deployment = sb.deploy(artifact["artifact_id"], robot_id=robot_id)
```

Training stores the full snapshot, stable spec ID, content revision and optional robot ID in
policy metadata. Optimization evaluates the exported int8 bytes against that frozen snapshot;
the validation record retains the binding. Deployment resolves the robot's current accepted
spec and refuses changed revisions/snapshots, a different bound robot, changed artifact/navmesh
bytes, insufficient footprint clearance, stale missions or mismatching rover config read-back.
This does not bypass the existing simulation gate.

New twins retain a `robot-spec.json` sidecar; their visualization manifest returns its frozen
snapshot and binding. Legacy twins are explicitly labelled as using a reference spec rather
than a historical pipeline snapshot. Once training completes, both viewers use the trained
snapshot for the rover. Cosmetic hardware dimensions remain illustrative.

Calibration applies wheel geometry only. Noise provenance remains unchanged. A successful apply
clears local training/export selections; train and optimize again. Footprint changes may require
a larger twin profile and a new route. Fake calibration cannot modify the physical reference
or supply a simulated accepted snapshot to a physical robot.

Tests cover a nontrivial fake calibration followed by training, export and completed drive;
wrong-robot and changed-calibration refusals; immutable prior twin visualization; the physical
name/filename alias using a mocked transport; bad read-back and local-write rollback. These are
software tests, not measurements from a physical rover.

Interrupted apply and legacy applied sessions can be checked explicitly through
`POST /robots/{robot_id}/calibrations/{id}/reconcile` or the corresponding dashboard/Android
action and SDK `reconcile_calibration`. This reads live configuration without resending settings
or motion. Matching proposed settings publish the accepted snapshot; unchanged previous settings
record that application did not occur; other settings remain an explicit reconciliation failure.
Uncertain sessions block specification-dependent work until reconciled. Superseded sessions
cannot replace a newer accepted calibration. Injected interruption tests cover before configure,
before publication and after immutable file writing, plus legacy adoption and repeat requests.
These injections are software crash-window tests, not physical process-kill measurements.
Fake rover processes themselves are not automatically restarted after an orchestrator restart.
