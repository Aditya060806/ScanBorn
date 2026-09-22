/**
 * Client for the orchestrator REST surface (orchestrator/service.py).
 *
 * Paths are same-origin: vite.config.ts proxies every route to the orchestrator, so it needs
 * no CORS configuration. Failures throw ApiError, which mirrors
 * sdk/scanborn/client.py:ScanBornError — the HTTP status plus the server's own `detail`. The
 * detail can be an object (the sim gate's 409 carries sim_success_rate and required), and
 * Stage.jsx renders an object detail field by field.
 */

function messageFor(status, detail) {
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => item?.msg ?? String(item)).filter(Boolean);
    if (messages.length) return messages.join("; ");
  }
  if (detail && typeof detail === "object" && typeof detail.error === "string") {
    return detail.error;
  }
  if (status === 0) return "orchestrator unreachable";
  // The dev proxy answers 5xx with an empty body when nothing is listening on :8000.
  if (status >= 500) return `orchestrator error (HTTP ${status}) — is it running?`;
  return `HTTP ${status}`;
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(messageFor(status, detail));
    this.name = "ApiError";
    this.status = status;
    // FastAPI validation errors arrive as a list; Stage.jsx would render that as
    // "0: [object Object]", so a list collapses to its readable message.
    this.detail = Array.isArray(detail) ? this.message : detail;
  }
}

function bodyOf(text) {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function detailOf(body, fallback) {
  if (body && typeof body === "object" && !Array.isArray(body) && "detail" in body) {
    return body.detail;
  }
  return body || fallback;
}

async function request(path, { method = "GET", json } = {}) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: json === undefined ? undefined : { "Content-Type": "application/json" },
      body: json === undefined ? undefined : JSON.stringify(json),
    });
  } catch {
    // fetch rejects only when no HTTP response arrived at all.
    throw new ApiError(0, null);
  }
  const body = bodyOf(await response.text());
  if (!response.ok) throw new ApiError(response.status, detailOf(body, response.statusText));
  return body;
}

const post = (path, json) => request(path, { method: "POST", json });
const id = (value) => encodeURIComponent(value);

/**
 * Multipart upload through XMLHttpRequest rather than fetch: fetch has no upload progress
 * event, and scan exports run to hundreds of megabytes.
 */
function send(path, file, params, onBytes) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", params ? `${path}?${new URLSearchParams(params)}` : path);
    if (onBytes) {
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) onBytes(event.loaded, event.total);
      };
    }
    xhr.onerror = () => reject(new ApiError(0, null));
    xhr.onload = () => {
      const body = bodyOf(xhr.responseText);
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else reject(new ApiError(xhr.status, detailOf(body, xhr.statusText)));
    };
    const form = new FormData();
    form.append("file", file, file.name);
    xhr.send(form);
  });
}

export const SCAN_EXTS = [".ply", ".obj"];

export const health = () => request("/health");
// Each workload's provider chain, where it runs and whether it is ready; never credentials.
export const providers = () => request("/providers");
export const saveProviderSettings = settings => post("/providers/settings", settings);
// Stops a running planner/profiler chain between attempts (the request must carry request_id).
export const cancelProviderRequest = (requestId) =>
  post(`/providers/requests/${id(requestId)}/cancel`, {});

export const scanStatus = (scanId) => request(`/capture/${id(scanId)}`);
export const twinDetail = (twinId) => request(`/twins/${id(twinId)}`);

/** Upload .npz depth frames in order, then close the scan. onProgress(done, total) per frame. */
export async function uploadScan(files, onProgress) {
  let scanId = crypto.randomUUID();
  for (let index = 0; index < files.length; index += 1) {
    const params = { index: String(index), ...(scanId ? { scan_id: scanId } : {}) };
    const result = await send("/capture", files[index], params);
    scanId = result.scan_id;
    onProgress?.(index + 1, files.length);
  }
  if (!scanId) throw new ApiError(400, "pick at least one .npz frame");
  return post(`/capture/${id(scanId)}/complete`);
}

/**
 * One finished .ply/.obj export. onProgress(loadedBytes, totalBytes). An export does not say its
 * units or up axis: pass what the operator declared ({units: "cm", upAxis: "y"}); anything left
 * out is recorded by the server as assumed (metres, Z up).
 */
export const importScan = (file, onProgress, declared = {}) => {
  const params = {
    ...(declared.units ? { units: declared.units } : {}),
    ...(declared.upAxis ? { up_axis: declared.upAxis } : {}),
  };
  return send("/capture/import", file, Object.keys(params).length ? params : null, onProgress);
};

// What an operator can declare for an export (capture/scaniverse.py UNITS and UP_AXES).
export const EXPORT_UNITS = ["m", "cm", "mm", "ft", "in"];
export const EXPORT_UP_AXES = ["z", "y"];

export const reconstruct = (scanId, mode) => post("/reconstruct", { scan_id: scanId, mode });

export const segment = (meshId) => post("/segment", { mesh_id: meshId });

/**
 * `environment` empty lets the orchestrator's profiler choose from the scan; `cellM` empty lets
 * the scan's point density choose the grid (0.05 or 0.1 m).
 */
export const generateTwin = (meshId, objectsId, environment, cellM, robotId) =>
  post("/generate-twin", {
    mesh_id: meshId,
    objects_id: objectsId,
    ...(environment ? { environment } : {}),
    ...(cellM ? { cell_m: Number(cellM) } : {}),
    ...(robotId ? { robot_id: robotId } : {}),
  });

/** The twin's occupancy grid; navmesh.js decodes its base64 cells. */
export const navmesh = (twinId) => request(`/twins/${id(twinId)}/navmesh`);

/**
 * Plan and store a mission: `goal` [x, y] or `taskGraphId`, and `start` [x, y] or [x, y, θ]
 * (omitted when the scan has a home pose). A refusal is a 422 whose detail carries `code` and
 * `nearest`, the point that would work.
 */
export const createMission = (twinId, { start, goal, via = [], taskGraphId } = {}) =>
  post("/missions", {
    twin_id: twinId,
    ...(start ? { start } : {}),
    ...(goal ? { goal } : {}),
    ...(via.length ? { via } : {}),
    ...(taskGraphId ? { task_graph_id: taskGraphId } : {}),
  });

export const mission = (missionId) => request(`/missions/${id(missionId)}`);

export const plan = (twinId, text, lang) => post("/plan", { twin_id: twinId, text, lang });

/** The same planner without a twin: nothing is stored. */
export const planPreview = (text, lang, labels) =>
  post("/plan/preview", { text, lang, ...(labels?.length ? { labels } : {}) });

/** With `missionId` the policy must also drive that mission's route from its start. */
export const train = (twinId, taskGraphId, missionId, robotId) =>
  post("/train", {
    twin_id: twinId,
    task_graph_id: taskGraphId ?? null,
    ...(missionId ? { mission_id: missionId } : {}),
    ...(robotId ? { robot_id: robotId } : {}),
  });

/** `deviceLabel` empty keeps the orchestrator's default label. */
export const optimize = (policyId, deviceLabel) =>
  post("/optimize", { policy_id: policyId, ...(deviceLabel ? { device_label: deviceLabel } : {}) });

/**
 * `robotId` deploys to a registered rover; otherwise `kind` names an adapter. A rover deployment
 * answers at once with status "running" and `live`, the WebSocket path live.js follows.
 */
export const deploy = (artifactId, { kind = "sim", robotId, requestId } = {}) =>
  post("/deploy", { ...(robotId ? { artifact_id: artifactId, robot_id: robotId } : { artifact_id: artifactId, kind }),
    ...(requestId ? { request_id: requestId } : {}) });
export const stageRequest = (stage, requestId) => request(`/stages/${id(stage)}/requests/${id(requestId)}`);

export const deploymentRequest = requestId => request(`/deploy/requests/${id(requestId)}`);

/**
 * Register a protocol rover (docs/robot/rover-protocol.md): an ESP32 at `address`, or with kind
 * "fake_rover" a fake rover the orchestrator runs itself. The answer carries the rover's status.
 */
export const registerRobot = ({ kind, address, timeScale } = {}) =>
  post("/robots", {
    kind,
    ...(address ? { address } : {}),
    ...(timeScale ? { time_scale: timeScale } : {}),
  });

export const robotStatus = (robotId) => request(`/robots/${id(robotId)}/status`);

/** E-STOP. It latches on the rover until reset, and a drive in progress ends as `estop`. */
export const estop = (robotId) => post(`/robots/${id(robotId)}/estop`);

export const resetRobot = (robotId) => post(`/robots/${id(robotId)}/reset`);

const calibrationPath = (robotId, session = "") =>
  `/robots/${id(robotId)}/calibrations${session ? `/${id(session)}` : ""}`;
export const startCalibration = (robotId, options) => post(calibrationPath(robotId), options);
export const calibrationRequest = (robotId, requestId) =>
  request(`/robots/${id(robotId)}/calibration-requests/${id(requestId)}`);
export const calibrationHistory = robotId => request(`${calibrationPath(robotId)}?limit=1`);
export const calibration = (robotId, session) => request(calibrationPath(robotId, session));
export const stopCalibration = (robotId, session) => post(`${calibrationPath(robotId, session)}/stop`);
export const measureCalibration = (robotId, session, measured) =>
  post(`${calibrationPath(robotId, session)}/measurement`, { measured });
export const applyCalibration = (robotId, session) => post(`${calibrationPath(robotId, session)}/apply`);

/** A rover deployment: its state, its trace so far and, once it has ended, its recorded result. */
export const deployment = (deploymentId) => request(`/deployments/${id(deploymentId)}`);

/** The rover halts and keeps its mission; the drive is recorded as stopped. */
export const stopDeployment = (deploymentId) => post(`/deployments/${id(deploymentId)}/stop`);

export const sync = (twinId, newScanId, sameMarkerConfirmed = false) =>
  post("/sync", { twin_id: twinId, new_scan_id: newScanId, same_marker_confirmed: sameMarkerConfirmed });

/** robot/specs/<name>.yaml; its `source` says whether the numbers are measured or placeholders. */
export const robotSpec = (name = "rover") => request(`/robot-specs/${id(name)}`);

/** Measured numbers: `phone` from benchmarks/phone.json, `models` from AI Hub profiles. */
export const benchmarks = () => request("/benchmarks");

export const reconcileCalibration = (robotId, sessionId) => post(`/robots/${id(robotId)}/calibrations/${id(sessionId)}/reconcile`);
