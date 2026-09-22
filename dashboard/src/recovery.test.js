import { describe, expect, it, vi } from "vitest";
import { readRecovery, writeRecovery, validateRecovery, RECOVERY_KEY,
  readPendingDeployment, savePendingDeployment, clearPendingDeployment } from "./recovery.js";

const memory = () => {
  const values = new Map();
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key) };
};
describe("workspace recovery", () => {
  it("persists a pending drive before submission independently of workspace writes", () => {
    const storage = memory(), pending = { requestId: "request-1", robotId: "robot-1" };
    savePendingDeployment(pending, storage);
    writeRecovery({ pipe: {} }, storage);
    expect(readPendingDeployment(storage)).toEqual(pending);
    clearPendingDeployment(storage);
    expect(readPendingDeployment(storage)).toBeNull();
    expect(() => savePendingDeployment(pending, { setItem() { throw new Error("quota"); } })).toThrow("quota");
  });
  it("retains drive identity separately when switching to a fixture", () => {
    const storage = memory(), active = { deploymentId: "drive1", robotId: "robot1" };
    expect(writeRecovery({ pipe: { fixture: true, scanId: "fixture" }, active }, storage)).toBe(true);
    expect(readRecovery(storage)).toMatchObject({ pipe: {}, active });
  });
  it("rejects corrupt selections and tolerates unavailable storage", () => {
    const storage = memory();
    storage.setItem(RECOVERY_KEY, JSON.stringify({ version: 1, pipe: { objects: null } }));
    expect(readRecovery(storage)).toBeNull();
    storage.setItem(RECOVERY_KEY, "broken");
    expect(readRecovery(storage)).toBeNull();
    expect(writeRecovery({ pipe: {} }, { setItem() { throw new Error("quota"); } })).toBe(false);
  });
  it("refetches mission staleness and rejects mismatched twin lineage", async () => {
    const saved = { pipe: { scanId: "scan", meshId: "mesh", twinId: "twin", missionId: "mission" } };
    const api = {
      scanStatus: vi.fn().mockResolvedValue({}),
      twinDetail: vi.fn().mockResolvedValue({ scan_id: "scan", mesh_id: "mesh" }),
      navmesh: vi.fn().mockResolvedValue({ cells: [] }),
      mission: vi.fn().mockResolvedValue({ twin_id: "twin", status: "stale" }),
    };
    expect(await validateRecovery(saved, api)).toMatchObject({
      mission: { status: "stale" }, staleMissions: [{ mission_id: "mission", stale: true }],
    });
    api.twinDetail.mockResolvedValue({ scan_id: "other", mesh_id: "mesh" });
    await expect(validateRecovery(saved, api)).rejects.toThrow("lineage");
  });
  it("preserves the workspace when a restarted fake rover needs registration again", async () => {
    const saved = { pipe: { robot: { id: "robot1" }, artifactId: "artifact1" } };
    const api = { robotStatus: vi.fn().mockRejectedValue({ status: 502 }) };
    expect(await validateRecovery(saved, api)).toEqual({ robot: null, artifactId: "artifact1" });
  });
});
