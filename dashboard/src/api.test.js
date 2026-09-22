import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "./api.js";

/** A fetch that answers once with `status` and a JSON body (or an empty body). */
const reply = (status, body) =>
  vi.fn().mockResolvedValue(
    new Response(body === undefined ? "" : JSON.stringify(body), { status }),
  );

/** Stub fetch with a 200 and return a reader for what the client sent. */
function recordRequest() {
  const fetchMock = reply(200, {});
  vi.stubGlobal("fetch", fetchMock);
  return () => {
    const [path, init = {}] = fetchMock.mock.calls[0];
    return {
      path,
      method: init.method ?? "GET",
      body: init.body ? JSON.parse(init.body) : undefined,
    };
  };
}

afterEach(() => vi.unstubAllGlobals());

it("recovers a calibration request with a read-only lookup", async () => {
  const sent = recordRequest();
  await api.calibrationRequest("rover 1", "request-1");
  expect(sent()).toEqual({ path: "/robots/rover%201/calibration-requests/request-1",
    method: "GET", body: undefined });
});

it("preserves the caller's calibration request identity", async () => {
  const sent = recordRequest();
  await api.startCalibration("rover-1", { mode: "straight", request_id: "saved-before-send" });
  expect(sent().body).toEqual({ mode: "straight", request_id: "saved-before-send" });
});

describe("errors carry the orchestrator's own explanation", () => {
  it("rejects when no response arrives, which is what turns the status pill red", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    const error = await api.health().catch((e) => e);
    expect(error).toBeInstanceOf(api.ApiError);
    expect(error.status).toBe(0);
    expect(error.message).toBe("orchestrator unreachable");
  });

  it("keeps an object detail whole, so the sim gate's numbers reach Stage.jsx", async () => {
    const detail = { error: "policy failed sim validation", sim_success_rate: 0.35, required: 0.6 };
    vi.stubGlobal("fetch", reply(409, { detail }));
    const error = await api.train("twin-1", null).catch((e) => e);
    expect(error.status).toBe(409);
    expect(error.message).toBe("policy failed sim validation");
    expect(error.detail).toEqual(detail);
  });

  it("uses a string detail as the message", async () => {
    vi.stubGlobal("fetch", reply(404, { detail: "scans 'x' not found" }));
    const error = await api.reconstruct("x", "fast").catch((e) => e);
    expect(error.status).toBe(404);
    expect(error.message).toBe("scans 'x' not found");
  });

  it("flattens FastAPI validation errors instead of rendering [object Object]", async () => {
    vi.stubGlobal("fetch", reply(422, { detail: [{ loc: ["body", "mesh_id"], msg: "Field required" }] }));
    const error = await api.segment(undefined).catch((e) => e);
    expect(error.message).toBe("Field required");
    expect(error.detail).toBe("Field required");
  });

  it("keeps a mission refusal's nearest point, which the console offers as a fix", async () => {
    const detail = { error: "The goal is on an obstacle.", code: "goal_blocked", nearest: [1.25, 0.35] };
    vi.stubGlobal("fetch", reply(422, { detail }));
    const error = await api.createMission("twin-1", { start: [0, 0], goal: [1, 0] }).catch((e) => e);
    expect(error.status).toBe(422);
    expect(error.message).toBe("The goal is on an obstacle.");
    expect(error.detail.nearest).toEqual([1.25, 0.35]);
  });

  it("explains the empty 5xx the dev proxy returns when nothing is listening", async () => {
    vi.stubGlobal("fetch", reply(500));
    const error = await api.health().catch((e) => e);
    expect(error.message).toMatch(/is it running/);
  });
});

describe("requests match what orchestrator/service.py reads", () => {
  it("sends environment only when a profile was chosen", async () => {
    let sent = recordRequest();
    await api.generateTwin("mesh-1", "mesh-1", "");
    expect(sent()).toEqual({
      path: "/generate-twin", method: "POST", body: { mesh_id: "mesh-1", objects_id: "mesh-1" },
    });

    vi.unstubAllGlobals();
    sent = recordRequest();
    await api.generateTwin("mesh-1", "mesh-1", "warehouse");
    expect(sent().body.environment).toBe("warehouse");
  });

  it("sends a grid size only when one was chosen, as a number", async () => {
    let sent = recordRequest();
    await api.generateTwin("mesh-1", "mesh-1", "", "");
    expect(sent().body).toEqual({ mesh_id: "mesh-1", objects_id: "mesh-1" });

    vi.unstubAllGlobals();
    sent = recordRequest();
    await api.generateTwin("mesh-1", "mesh-1", "", "0.05");
    expect(sent().body).toEqual({ mesh_id: "mesh-1", objects_id: "mesh-1", cell_m: 0.05 });
  });

  it("leaves device_label to the orchestrator's default when blank", async () => {
    const sent = recordRequest();
    await api.optimize("policy-1", "");
    expect(sent().body).toEqual({ policy_id: "policy-1" });
  });

  it("previews a plan without a twin", async () => {
    const sent = recordRequest();
    await api.planPreview("go to the table", "en");
    expect(sent()).toEqual({
      path: "/plan/preview", method: "POST", body: { text: "go to the table", lang: "en" },
    });
  });

  it("reads provider settings and cancels a provider request by its id", async () => {
    let sent = recordRequest();
    await api.providers();
    expect(sent()).toEqual({ path: "/providers", method: "GET", body: undefined });
    vi.unstubAllGlobals();
    sent = recordRequest();
    await api.cancelProviderRequest("plan/7");
    expect(sent()).toEqual({ path: "/providers/requests/plan%2F7/cancel", method: "POST",
                             body: {} });
  });

  it("sends a mission's fields only when they are set", async () => {
    let sent = recordRequest();
    await api.createMission("twin-1", { start: [0, 0, 1.5], goal: [2, 1] });
    expect(sent()).toEqual({
      path: "/missions", method: "POST",
      body: { twin_id: "twin-1", start: [0, 0, 1.5], goal: [2, 1] },
    });

    vi.unstubAllGlobals();
    sent = recordRequest();
    await api.createMission("twin-1", { start: [0, 0], via: [[1, 1]], taskGraphId: "graph-1" });
    expect(sent().body).toEqual({
      twin_id: "twin-1", start: [0, 0], via: [[1, 1]], task_graph_id: "graph-1",
    });
  });

  it("trains on a mission when one is chosen", async () => {
    const sent = recordRequest();
    await api.train("twin-1", null, "mission-1");
    expect(sent().body).toEqual({ twin_id: "twin-1", task_graph_id: null, mission_id: "mission-1" });
  });

  it("deploys to a registered rover by its id, and to an adapter by kind", async () => {
    let sent = recordRequest();
    await api.deploy("artifact-1", { robotId: "robot-1" });
    expect(sent().body).toEqual({ artifact_id: "artifact-1", robot_id: "robot-1" });

    vi.unstubAllGlobals();
    sent = recordRequest();
    await api.deploy("artifact-1", { kind: "sim" });
    expect(sent().body).toEqual({ artifact_id: "artifact-1", kind: "sim" });
  });

  it("registers an ESP32 by address and a fake rover without one", async () => {
    let sent = recordRequest();
    await api.registerRobot({ kind: "rover", address: "http://192.168.1.50" });
    expect(sent()).toEqual({
      path: "/robots", method: "POST", body: { kind: "rover", address: "http://192.168.1.50" },
    });

    vi.unstubAllGlobals();
    sent = recordRequest();
    await api.registerRobot({ kind: "fake_rover" });
    expect(sent().body).toEqual({ kind: "fake_rover" });
  });

  it("sends E-STOP to the robot and Stop to the deployment", async () => {
    let sent = recordRequest();
    await api.estop("robot/1");
    expect(sent()).toEqual({ path: "/robots/robot%2F1/estop", method: "POST", body: undefined });

    vi.unstubAllGlobals();
    sent = recordRequest();
    await api.stopDeployment("dep-1");
    expect(sent()).toEqual({ path: "/deployments/dep-1/stop", method: "POST", body: undefined });
  });

  it("escapes ids placed in a path", async () => {
    const sent = recordRequest();
    await api.scanStatus("a/b");
    expect(sent()).toMatchObject({ path: "/capture/a%2Fb", method: "GET" });
  });
});


describe("an export import declares only what the operator chose", () => {
  function recordUpload() {
    const opened = [];
    class FakeXhr {
      constructor() { this.upload = {}; }
      open(method, url) { opened.push({ method, url }); }
      send() { this.status = 200; this.responseText = JSON.stringify({ scan_id: "s" }); this.onload(); }
    }
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    return opened;
  }
  const file = new File(["ply"], "room.ply");

  it("sends units and up axis when declared", async () => {
    const opened = recordUpload();
    await api.importScan(file, null, { units: "cm", upAxis: "y" });
    expect(opened).toEqual([{ method: "POST", url: "/capture/import?units=cm&up_axis=y" }]);
  });

  it("sends neither when nothing was declared, so the server records them as assumed", async () => {
    const opened = recordUpload();
    await api.importScan(file, null, { units: "", upAxis: "" });
    await api.importScan(file);
    expect(opened.map((o) => o.url)).toEqual(["/capture/import", "/capture/import"]);
  });
});


it("binds twin generation and training to the selected calibrated robot", async () => {
  let sent = recordRequest();
  await api.generateTwin("mesh-1", "mesh-1", "home", "0.1", "robot-1");
  expect(sent().body.robot_id).toBe("robot-1");
  sent = recordRequest();
  await api.train("twin-1", null, "mission-1", "robot-1");
  expect(sent().body.robot_id).toBe("robot-1");
});


it("looks up a stage result without starting work", async () => {
  const sent = recordRequest();
  await api.stageRequest("generate-twin", "request 1");
  expect(sent()).toEqual({ path: "/stages/generate-twin/requests/request%201", method: "GET", body: undefined });
});
