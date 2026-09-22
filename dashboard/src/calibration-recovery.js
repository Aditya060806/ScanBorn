const key = robotId => `scanborn.calibration.pending.v1:${robotId}`;

export function pendingCalibration(robotId, storage = globalThis.localStorage) {
  const raw = storage.getItem(key(robotId));
  if (!raw) return null;
  const value = JSON.parse(raw);
  if (!value || typeof value.request_id !== "string" || !value.request_id ||
      !["straight", "spin", "wheel_step"].includes(value.mode))
    throw new Error("Saved calibration request is invalid; inspect rover status before continuing");
  return value;
}

export async function recoverCalibration(robotId, api, storage = globalThis.localStorage) {
  const pending = pendingCalibration(robotId, storage);
  if (!pending) return null;
  const result = await api.calibrationRequest(robotId, pending.request_id);
  storage.removeItem(key(robotId));
  return result;
}

// Only called by an explicit operator click. Restoration uses GET, never this function.
export async function startCalibration(robotId, options, api, storage = globalThis.localStorage) {
  const pending = pendingCalibration(robotId, storage) ??
    { ...options, request_id: globalThis.crypto.randomUUID() };
  // A failed persistence write must prevent any motion request.
  storage.setItem(key(robotId), JSON.stringify(pending));
  let result;
  try { result = await api.startCalibration(robotId, pending); }
  catch (error) {
    // A response can be lost after acceptance; only read its outcome here.
    try { return await recoverCalibration(robotId, api, storage); }
    catch { throw error; }
  }
  storage.removeItem(key(robotId));
  return result;
}
