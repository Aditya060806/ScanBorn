import { describe, expect, it, vi } from "vitest";
import { pendingCalibration, recoverCalibration, startCalibration } from "./calibration-recovery.js";

function storage() {
  const rows = new Map();
  return { getItem: k => rows.get(k) ?? null, setItem: (k, v) => rows.set(k, v),
    removeItem: k => rows.delete(k) };
}

describe("durable calibration starts", () => {
  it("persists before POST and retries the exact saved request", async () => {
    const disk = storage();
    const api = { startCalibration: vi.fn(async (robot, body) => {
      expect(pendingCalibration(robot, disk)).toEqual(body);
      throw new Error("offline");
    }), calibrationRequest: vi.fn().mockRejectedValue(new Error("offline")) };
    await expect(startCalibration("r", { mode: "straight" }, api, disk)).rejects.toThrow("offline");
    const first = pendingCalibration("r", disk);
    await expect(startCalibration("r", { mode: "spin" }, api, disk)).rejects.toThrow("offline");
    expect(api.startCalibration.mock.calls[1][1]).toEqual(first);
    expect(pendingCalibration("other", disk)).toBeNull();
  });

  it("recovers accepted response loss using GET without replaying POST", async () => {
    const disk = storage();
    const result = { id: "session", status: "running" };
    const api = { startCalibration: vi.fn().mockRejectedValue(new Error("lost")),
      calibrationRequest: vi.fn().mockResolvedValue(result) };
    expect(await startCalibration("r", { mode: "spin" }, api, disk)).toEqual(result);
    expect(api.startCalibration).toHaveBeenCalledTimes(1);
    expect(api.calibrationRequest).toHaveBeenCalledTimes(1);
    expect(pendingCalibration("r", disk)).toBeNull();
  });

  it("restores without issuing a motion command", async () => {
    const disk = storage();
    const api = { startCalibration: vi.fn().mockRejectedValue(new Error("offline")),
      calibrationRequest: vi.fn().mockRejectedValue(new Error("offline")) };
    await expect(startCalibration("r", { mode: "spin" }, api, disk)).rejects.toThrow();
    api.startCalibration.mockClear();
    api.calibrationRequest.mockResolvedValue({ id: "restored" });
    expect(await recoverCalibration("r", api, disk)).toEqual({ id: "restored" });
    expect(api.startCalibration).not.toHaveBeenCalled();
  });

  it("refuses motion if storage cannot retain the identity", async () => {
    const disk = storage();
    disk.setItem = () => { throw new Error("quota"); };
    const api = { startCalibration: vi.fn() };
    await expect(startCalibration("r", { mode: "spin" }, api, disk)).rejects.toThrow("quota");
    expect(api.startCalibration).not.toHaveBeenCalled();
  });
});
