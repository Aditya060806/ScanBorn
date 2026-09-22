/**
 * Orchestrator response -> console state, one mapper per stage.
 *
 * The live console and the offline fixture both go through these, so a replayed run cannot
 * drift from the API's real field names. Before this module the demo and the live stages
 * wrote seven different keys (pointCount vs points, simRate vs successRate, ...), and the
 * demo rendered "undefined placed" and "NaN% in sim".
 *
 * Keys on the left are what Console.jsx reads; on the right, what orchestrator/service.py
 * returns.
 */

/** Mirrors policy/evaluate.py:SIM_GATE — without a mission, below this POST /train answers 409. */
export const SIM_GATE = 0.6;

/** Mirrors policy/evaluate.py:MISSION_GATE — the share of a mission's noisy runs that must arrive. */
export const MISSION_GATE = 0.9;

export const EMPTY_PIPE = Object.freeze({
  objects: [], poseTrace: [], graph: [], navmesh: null, mission: null, missionTrace: [],
  staleMissions: [], fixture: false,
});

export const STAGE_ORDER = Object.freeze([
  "capture", "reconstruct", "segment", "twin", "navmesh", "plan", "mission", "train",
  "optimize", "deploy",
]);

/** The console state each stage's mapper writes. */
export const STAGE_KEYS = Object.freeze({
  capture: ["scanId", "scanState", "scanPoints", "scanFrames", "scanSource", "scanRegistration",
    "scanImport"],
  reconstruct: ["meshId", "points"],
  segment: ["objectsId", "objects"],
  twin: ["twinId", "twinObjects", "sceneUrl", "profile", "floorZ", "counts", "resolution"],
  navmesh: ["navmesh"],
  plan: ["graphId", "provider", "onDevice", "graph"],
  mission: ["missionId", "mission"],
  train: ["policyId", "successRate", "missionMetrics", "missionTrace", "robotSpec"],
  optimize: ["artifactId", "opCoverage", "latency", "latencySource", "backend"],
  deploy: ["deploymentId", "deployStatus", "reached", "poseTrace", "ticks", "tickP50",
    "computeUnit", "precision", "deployRobotId", "liveUrl", "deployFake", "deployResult"],
  sync: ["diff", "changed", "staleMissions"],
  robot: ["robot"],
});

/**
 * What re-running a stage makes stale. A new route invalidates the policy trained on the old one
 * and everything exported and deployed from it; a new twin invalidates every mission on the old
 * one. Without this, a failed retrain sat next to the previous route's success, and Deploy would
 * still ship the previous policy.
 */
const INVALIDATES = Object.freeze({
  capture: ["reconstruct", "segment", "twin", "navmesh", "plan", "mission", "train", "optimize",
    "deploy", "sync"],
  reconstruct: ["segment", "twin", "navmesh", "plan", "mission", "train", "optimize", "deploy",
    "sync"],
  segment: ["twin", "navmesh", "plan", "mission", "train", "optimize", "deploy", "sync"],
  twin: ["navmesh", "plan", "mission", "train", "optimize", "deploy", "sync"],
  navmesh: [],
  plan: ["train", "optimize", "deploy"],
  mission: ["train", "optimize", "deploy"],
  train: ["optimize", "deploy"],
  optimize: ["deploy"],
  deploy: [],
  sync: [],
  // A registered rover is hardware on the network, not a result: nothing else goes stale with it.
  robot: [],
});

/** `stage` and every stage whose result it makes stale. */
export const invalidatedBy = (stage) => [stage, ...(INVALIDATES[stage] ?? [])];

/** State to merge when `stage` starts: its own previous output and everything downstream, emptied. */
export function clearFrom(stage) {
  const cleared = {};
  for (const affected of invalidatedBy(stage)) {
    for (const key of STAGE_KEYS[affected] ?? []) {
      cleared[key] = key in EMPTY_PIPE ? EMPTY_PIPE[key] : undefined;
    }
  }
  return cleared;
}

export const toPipe = {
  /** POST /capture/{id}/complete, POST /capture/import, or GET /capture/{id} plus scan_id. */
  capture: (r) => ({
    scanId: r.scan_id,
    scanState: r.status,
    // An import has a point count and no frames; a frame upload has frames and no points.
    scanPoints: r.point_count ?? null,
    scanFrames: r.point_count == null ? (r.frame_count ?? null) : null,
    scanSource: r.format ?? null,
    // Whether a physical rover can share this scan's frame (capture/service/registration.py),
    // and for an import its declared or assumed units and up axis.
    scanRegistration: r.registration ?? null,
    scanImport: r.import ?? null,
  }),

  reconstruct: (r) => ({ meshId: r.mesh_id, points: r.point_count }),

  segment: (r) => ({ objectsId: r.objects_id, objects: r.objects ?? [] }),

  twin: (r) => ({
    twinId: r.twin_id,
    twinObjects: r.object_count,
    sceneUrl: r.scene_url,
    profile: r.profile ?? null,
    floorZ: r.floor_z ?? null,
    counts: r.counts ?? null,
    resolution: r.resolution ?? null,
  }),

  /** GET /twins/{id}/navmesh, kept whole: the map decodes its cells. */
  navmesh: (r) => ({ navmesh: r }),

  plan: (r) => ({
    graphId: r.task_graph_id,
    provider: r.provider,
    onDevice: r.on_device ?? null,
    graph: JSON.parse(r.graph_json).nodes ?? [],
  }),

  /** POST /missions or GET /missions/{id}. */
  mission: (r) => ({ missionId: r.mission_id, mission: r }),

  /** With a mission: outcome counts, drift and the median run's trace from the rover model. */
  train: (r) => ({
    policyId: r.policy_id,
    successRate: r.sim_success_rate,
    missionMetrics: r.mission_metrics ?? null,
    missionTrace: r.mission_trace ?? [],
    robotSpec: r.robot_spec ?? null,
  }),

  optimize: (r) => ({
    artifactId: r.artifact_id,
    opCoverage: r.op_coverage ?? null,
    latency: r.est_latency,
    latencySource: r.latency_source ?? null,
    backend: r.backend,
  }),

  deploy: (r) => ({
    deploymentId: r.deployment_id,
    deployStatus: r.status,
    reached: r.reached ?? null,
    // A rover deploy answers with no trace: keep the shared empty one, so starting a live drive
    // does not hand the 3D view a new array and rebuild its scene.
    poseTrace: r.pose_trace?.length ? r.pose_trace : EMPTY_PIPE.poseTrace,
    ticks: r.ticks ?? null,
    tickP50: r.inference_p50_ms ?? null,
    computeUnit: r.compute_unit ?? null,
    precision: r.precision ?? null,
    deployRobotId: r.robot_id ?? null,
    // Only a rover deployment has a live socket; it drives on, and its result arrives later.
    liveUrl: r.live ?? null,
    deployFake: r.fake ?? false,
    deployResult: null,
  }),

  /** GET /deployments/{id} once a rover drive has ended: the result the orchestrator recorded. */
  deployment: (r) => ({
    deployStatus: r.status,
    reached: r.result?.reached ?? null,
    deployResult: r.result ?? null,
  }),

  /** POST /robots. `kind` follows from `fake`: the orchestrator stores both as "rover". */
  robot: (r) => ({
    robot: {
      id: r.robot_id,
      kind: r.fake ? "fake_rover" : "rover",
      fake: Boolean(r.fake),
      address: r.address,
      firmware: r.status?.firmware ?? null,
      state: r.status?.state ?? null,
      reason: r.status?.reason ?? null,
    },
  }),

  sync: (r) => ({
    diff: r.diff_summary,
    changed: r.changed_objects ?? [],
    staleMissions: r.stale_missions ?? [],
  }),
};
