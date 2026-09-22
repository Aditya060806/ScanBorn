// Same-origin storage holds operator selections, never a command to restart a drive.
export const RECOVERY_KEY = "scanborn.console.v1";
const PENDING_KEY = "scanborn.deploy.pending.v1";
const identifier = value => typeof value === "string" && /^[a-zA-Z0-9_-]{1,128}$/.test(value);

export function readPendingDeployment(storage) {
  try {
    const value = JSON.parse((storage ?? globalThis.localStorage).getItem(PENDING_KEY));
    return value && identifier(value.requestId) && identifier(value.robotId) ? value : null;
  } catch { return null; }
}
export function savePendingDeployment(value, storage) {
  // Fail before starting a rover if its recovery identity cannot be persisted.
  (storage ?? globalThis.localStorage).setItem(PENDING_KEY, JSON.stringify(value));
}
export function clearPendingDeployment(storage) {
  (storage ?? globalThis.localStorage).removeItem(PENDING_KEY);
}

export function readRecovery(storage) {
  try {
    storage ??= globalThis.localStorage;
    const value = JSON.parse(storage.getItem(RECOVERY_KEY));
    if (value?.version !== 1 || !value.pipe || value.pipe.fixture) return null;
    for (const key of ["objects", "poseTrace", "graph", "missionTrace", "staleMissions"])
      if (value.pipe[key] !== undefined && !Array.isArray(value.pipe[key])) return null;
    const point = p => p === null || (Array.isArray(p) && p.length >= 2 && p.length <= 3 && p.every(Number.isFinite));
    if (value.draft && (!point(value.draft.start) || !point(value.draft.goal)
      || !Array.isArray(value.draft.via) || !value.draft.via.every(point))) return null;
    for (const key of ["scanId", "meshId", "twinId", "missionId", "deploymentId"])
      if (value.pipe[key] && !identifier(value.pipe[key])) return null;
    if (value.active && (!identifier(value.active.deploymentId) || !identifier(value.active.robotId))) return null;
    return value;
  } catch { return null; }
}

export function writeRecovery(value, storage) {
  try {
    storage ??= globalThis.localStorage;
    // A fixture must never replace the identity of a rover that could still be moving.
    const pipe = value.pipe.fixture ? {} : value.pipe;
    storage.setItem(RECOVERY_KEY, JSON.stringify({ ...value, pipe, version: 1, savedAt: new Date().toISOString() }));
    return true;
  } catch { return false; }
}

/** Re-fetch mutable mission state and confirm saved artifact lineage before enabling stages. */
export async function validateRecovery(saved, api) {
  const pipe = { ...saved.pipe };
  if (pipe.scanId) await api.scanStatus(pipe.scanId);
  if (pipe.twinId) {
    const twin = await api.twinDetail(pipe.twinId);
    if (twin.scan_id !== pipe.scanId || twin.mesh_id !== pipe.meshId)
      throw new Error("Saved twin lineage no longer matches this scan. Start a new run.");
    pipe.navmesh = await api.navmesh(pipe.twinId);
  }
  if (pipe.missionId) {
    const mission = await api.mission(pipe.missionId);
    if (mission.twin_id !== pipe.twinId) throw new Error("Saved mission belongs to another twin.");
    pipe.mission = mission;
    if (mission.status === "stale") pipe.staleMissions = [{ mission_id: pipe.missionId, stale: true }];
  }
  if (pipe.robot?.id) {
    try {
      const status = await api.robotStatus(pipe.robot.id);
      pipe.robot = { ...pipe.robot, state: status.state, reason: status.reason ?? null };
    } catch (error) {
      if (![404, 502, 503].includes(error.status)) throw error;
      pipe.robot = null; // A restarted server may require rover registration again.
    }
  }
  return pipe;
}
