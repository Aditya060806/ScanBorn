import { describe, expect, it } from "vitest";

import { FIXTURE_ORDER, fixturePipe } from "./fixtures.js";
import recorded from "./fixtures.json";
import {
  EMPTY_PIPE, SIM_GATE, STAGE_KEYS, STAGE_ORDER, clearFrom, invalidatedBy, toPipe,
} from "./pipeline.js";

// Every key Console.jsx reads from a finished run.
const READ_BY_CONSOLE = [
  "scanId", "scanState", "meshId", "points", "objectsId", "twinId", "twinObjects", "profile",
  "counts", "resolution", "navmesh", "graphId", "provider", "onDevice", "missionId", "mission", "policyId",
  "successRate", "missionMetrics", "artifactId", "latency", "latencySource", "backend",
  "deploymentId", "deployStatus", "tickP50", "computeUnit", "diff", "staleMissions",
];

describe("the recorded fixture, through the live mappers", () => {
  const pipe = fixturePipe();

  it("records every stage it replays", () => {
    for (const stage of FIXTURE_ORDER) expect(recorded.responses[stage], stage).toBeDefined();
  });

  it("fills every field the console reads", () => {
    for (const key of READ_BY_CONSOLE) expect(pipe[key], key).not.toBeUndefined();
    expect(pipe.fixture).toBe(true);
    expect(pipe.objects.length).toBeGreaterThan(0);
    expect(pipe.graph.length).toBeGreaterThan(0);
    expect(pipe.poseTrace.length).toBeGreaterThan(0);
  });

  it("trains and deploys on a mission planned on the twin's navmesh", () => {
    expect(pipe.navmesh.cells_b64.length).toBeGreaterThan(0);
    expect(pipe.mission.waypoints.length).toBeGreaterThanOrEqual(2);
    expect(pipe.mission.status).toBe("ready");
    expect(pipe.missionMetrics.passed).toBe(true);
    expect(pipe.missionMetrics.reached).toBeGreaterThanOrEqual(27);
    // The rover model places the rover on its start within the marker's placement error.
    pipe.poseTrace[0].forEach((value, i) =>
      expect(Math.abs(value - pipe.mission.start[i])).toBeLessThanOrEqual(0.02));
    expect(pipe.staleMissions.every((m) => m.stale === false)).toBe(true);
  });

  it("is a run that cleared the gate on the offline, CPU-only path", () => {
    expect(pipe.successRate).toBeGreaterThanOrEqual(SIM_GATE);
    expect(pipe.provider).toBe("local");
    expect(pipe.onDevice).toBe(true);
    expect(pipe.backend).toBe("local");
    expect(pipe.opCoverage).toBeNull();
    expect(pipe.latencySource).toBe("host-cpu");
    expect(pipe.computeUnit).toBe("cpu");
  });
});

describe("re-running a stage clears what it made stale", () => {
  const run = fixturePipe();

  it("lists every key each mapper writes", () => {
    for (const stage of [...STAGE_ORDER, "sync"]) {
      const written = Object.keys(toPipe[stage](recorded.responses[stage])).sort();
      expect(written, stage).toEqual([...STAGE_KEYS[stage]].sort());
    }
  });

  it("a new route clears the policy, the artifact and the deployment, and keeps the twin", () => {
    const next = { ...run, ...clearFrom("mission") };
    expect(next.mission).toBe(EMPTY_PIPE.mission);
    expect(next.policyId).toBeUndefined();
    expect(next.missionMetrics).toBeUndefined();
    expect(next.artifactId).toBeUndefined();
    expect(next.poseTrace).toBe(EMPTY_PIPE.poseTrace);
    expect(next.twinId).toBe(run.twinId);
    expect(next.navmesh).toBe(run.navmesh);
    expect(next.graphId).toBe(run.graphId);
  });

  it("names the stages whose errors go with the results", () => {
    expect(invalidatedBy("mission")).toEqual(["mission", "train", "optimize", "deploy"]);
    expect(invalidatedBy("deploy")).toEqual(["deploy"]);
    expect(invalidatedBy("twin")).not.toContain("capture");
  });

  it("a new twin clears its missions and rescans; a rescan clears only its own report", () => {
    const retwinned = { ...run, ...clearFrom("twin") };
    expect(retwinned.mission).toBeNull();
    expect(retwinned.staleMissions).toEqual([]);
    expect(retwinned.objects).toBe(run.objects);

    const rescanned = { ...run, ...clearFrom("sync") };
    expect(rescanned.diff).toBeUndefined();
    expect(rescanned.policyId).toBe(run.policyId);
  });
});

describe("rover deployments", () => {
  it("keep what a live drive needs, and a new deployment clears it", () => {
    const patch = toPipe.deploy({
      deployment_id: "d1", status: "running", robot_id: "r1", reached: false,
      live: "/ws/deployments/d1", fake: true, pose_trace: [],
    });
    expect(patch).toMatchObject({
      liveUrl: "/ws/deployments/d1", deployFake: true, deployRobotId: "r1", deployResult: null,
    });
    const cleared = clearFrom("deploy");
    for (const key of ["liveUrl", "deployFake", "deployRobotId", "deployResult"]) {
      expect(key in cleared, key).toBe(true);
    }
    const result = { reached: true, deviation_from_sim_m: 0.04, fake: true };
    expect(toPipe.deployment({ status: "done", result })).toEqual({
      deployStatus: "done", reached: true, deployResult: result,
    });
  });

  it("tell a fake rover from an ESP32, and a new run keeps the registered rover", () => {
    const fake = toPipe.robot({
      robot_id: "r1", fake: true, address: "http://127.0.0.1:50000",
      status: { state: "idle", firmware: "fake-0.4.0", reason: null },
    });
    expect(fake.robot).toMatchObject({ id: "r1", kind: "fake_rover", state: "idle" });
    expect(toPipe.robot({ robot_id: "r2", fake: false, address: "http://rover", status: {} }).robot.kind)
      .toBe("rover");
    expect("robot" in clearFrom("capture")).toBe(false);
  });
});

describe("capture has three response shapes", () => {
  it("a frame upload reports frames", () => {
    expect(toPipe.capture({ scan_id: "s", status: "complete", frame_count: 12 }))
      .toMatchObject({ scanFrames: 12, scanPoints: null });
  });

  it("an export import reports its points, not its zero frames", () => {
    expect(toPipe.capture({
      scan_id: "s", status: "complete", frame_count: 0, point_count: 5000, format: "ply",
    })).toMatchObject({ scanPoints: 5000, scanFrames: null, scanSource: "ply" });
  });

  it("a status check uses the id the caller supplied", () => {
    expect(toPipe.capture({ status: "uploading", frame_count: 3, scan_id: "given" }).scanId)
      .toBe("given");
  });

  it("keeps the registration and the import's declared units, so an import reads as unregistered", () => {
    const registration = { state: "unknown", physically_registered: false, reason: "Imported" };
    const record = { units: "cm", units_source: "declared", up_axis: "y" };
    expect(toPipe.capture({ scan_id: "s", status: "complete", point_count: 9, format: "ply",
      registration, import: record })).toMatchObject({ scanRegistration: registration,
      scanImport: record });
    expect(toPipe.capture({ scan_id: "s", status: "complete", frame_count: 2 }))
      .toMatchObject({ scanRegistration: null, scanImport: null });
  });
});
