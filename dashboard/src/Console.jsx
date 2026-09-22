import React, { useEffect, useRef, useState, lazy, Suspense } from "react";

import * as api from "./api.js";
import { FIXTURE_RECORDED_AT, FIXTURE_SOURCE, replayFixture } from "./fixtures.js";
import JobFeed from "./JobFeed.jsx";
import Calibration from "./Calibration.jsx";
import ProviderSettings from "./ProviderSettings.jsx";
import ResultInspector from "./ResultInspector.jsx";
import { squareDraft } from "./square.js";
import { readRecovery, writeRecovery, validateRecovery, readPendingDeployment, savePendingDeployment, clearPendingDeployment } from "./recovery.js";
import { EMPTY_LIVE, isEstopKey, watchDeployment } from "./live.js";
import MissionMap from "./MissionMap.jsx";
import { defaultStart } from "./navmesh.js";
import {
  EMPTY_PIPE, MISSION_GATE, SIM_GATE, clearFrom, invalidatedBy, toPipe,
} from "./pipeline.js";
const RobotViewer3D = lazy(() => import("./RobotViewer3D.jsx"));
import Stage from "./Stage.jsx";
import Telemetry from "./Telemetry.jsx";
import ViewBoundary from "./ViewBoundary.jsx";

// sarvam/task_engine/schemas.py:ENVIRONMENT_KINDS. Leaving it unset lets the orchestrator's
// profiler choose from the scan.
const ENVIRONMENTS = ["generic", "home", "office", "warehouse", "factory", "hospital", "museum"];

// The adapters in robot/adapters/registry.py, and the two robots that speak the rover protocol
// (docs/robot/rover-protocol.md): the orchestrator's own fake rover and the ESP32 over WiFi.
const ROBOT_KINDS = [
  ["sim", "Simulated robot (one run in the rover model)"],
  ["fake_rover", "Fake rover (the rover protocol, the rover model inside)"],
  ["rover", "ESP32 rover over WiFi"],
];
const ROVER_KINDS = new Set(["fake_rover", "rover"]);

// orchestrator/deployments.py session states, as the live bar says them.
const LIVE_LABELS = {
  starting: "Rover starting",
  running: "Rover driving",
  done: "Drive done: stopped at the goal by its own estimate",
  stopped: "Drive stopped",
  estop: "E-STOP latched",
  error: "Rover error",
  lost: "Telemetry lost: the rover stops on its heartbeat deadman",
  missing: "Deployment not found",
};
const liveLabel = (drive) => LIVE_LABELS[drive.state] ?? drive.state;
const cm = (metres) => `${Math.round(metres * 100)} cm`;

const EMPTY_DRAFT = Object.freeze({ start: null, goal: null, via: [] });
const NO_POINTS = Object.freeze([]);

const fmtBytes = (n) =>
  n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB`
    : n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB`
      : `${Math.max(1, Math.round(n / 1e3))} KB`;

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** The capture stage's result line: what arrived, and whether a physical rover can use it. */
function captureSummary(pipe) {
  const arrived = pipe.scanPoints != null
    ? `${pipe.scanPoints.toLocaleString()} points · ${pipe.scanSource} · ${pipe.scanState}`
    : pipe.scanFrames != null && `${plural(pipe.scanFrames, "frame")} · ${pipe.scanState}`;
  if (!arrived || !pipe.scanRegistration) return arrived;
  return `${arrived} · ${pipe.scanRegistration.physically_registered
    ? "on the home marker" : "registration unknown"}`;
}
const round2 = (v) => Math.round(v * 100) / 100;

/** Why the twin has the grid it has, from /generate-twin's `resolution`. */
const gridNote = (resolution) => {
  if (!resolution) return "";
  if (resolution.chosen === "requested") return " (as requested)";
  const measured = resolution.floor_points_per_10cm_cell_p25;
  return ` (${resolution.reason}${measured != null ? `: ${measured} floor points per 10 cm cell` : ""})`;
};

const reducedMotion = () =>
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

/**
 * A number field that commits on blur or Enter, so typing "-0." is not rejected mid-keystroke.
 * The mission panel's keyboard alternative to clicking the map.
 */
function CoordInput({ label, value, onCommit, disabled, step = 0.05, unit = "m" }) {
  const shown = value == null ? "" : String(round2(value));
  const [text, setText] = useState(shown);
  useEffect(() => setText(shown), [shown]);
  const commit = () => {
    const number = Number.parseFloat(text);
    if (Number.isFinite(number)) {
      if (number !== value) onCommit(number);
    } else {
      setText(shown);
    }
  };
  return (
    <label>
      {label} ({unit})
      <input
        type="number" inputMode="decimal" step={step} value={text} disabled={disabled}
        onChange={(event) => setText(event.target.value)} onBlur={commit}
        onKeyDown={(event) => { if (event.key === "Enter") commit(); }}
      />
    </label>
  );
}

export default function Console() {
  // Each stage's output is the next stage's input — the pipeline's whole state.
  const [saved] = useState(readRecovery);
  const [pipe, setPipe] = useState(() => ({ ...EMPTY_PIPE, ...saved?.pipe }));
  const [recovering, setRecovering] = useState(Boolean(saved));
  const [recoveryError, setRecoveryError] = useState("");
  const [storageFailed, setStorageFailed] = useState(false);
  const [pendingDeploy, setPendingDeploy] = useState(readPendingDeployment);
  const [pendingStatus, setPendingStatus] = useState("");
  const [pendingTerminal, setPendingTerminal] = useState(false);
  const [busy, setBusy] = useState(null);
  const [errors, setErrors] = useState({});
  const [online, setOnline] = useState(null);
  const [upload, setUpload] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [viewTab, setViewTab] = useState("twin"); // "twin" | "map" | "jobs"

  // Form fields, in stage order.
  const [scanId, setScanId] = useState(saved?.pipe.scanId ?? "");
  // An export's units and up axis, as the operator declares them; "" leaves them undeclared.
  const [exportUnits, setExportUnits] = useState("");
  const [exportUp, setExportUp] = useState("");
  const [environment, setEnvironment] = useState("");
  const [gridCell, setGridCell] = useState("");
  const [text, setText] = useState("Go to the table");
  const [lang, setLang] = useState("en");
  const [draft, setDraft] = useState(saved?.draft ?? EMPTY_DRAFT);
  const [deviceLabel, setDeviceLabel] = useState("");
  const [robotKind, setRobotKind] = useState(saved?.pipe.robot?.kind ?? "sim");
  const [roverAddress, setRoverAddress] = useState("http://scanborn-rover.local");
  const [rescanId, setRescanId] = useState("");
  const [sameMarker, setSameMarker] = useState(false);

  // A rover drive (live.js). It outlives re-running earlier stages and starting a new run: the
  // rover is still moving, so its E-STOP stays on screen until the next deployment replaces it.
  const [drive, setDrive] = useState(() => saved?.active ? { ...EMPTY_LIVE, deploymentId: saved.active.deploymentId } : null);
  const watcher = useRef(null);
  const liveRobot = useRef(saved?.active?.robotId ?? null);

  useEffect(() => {
    const ping = () => api.health().then(() => setOnline(true), () => setOnline(false));
    ping();
    const timer = setInterval(ping, 5000);
    return () => clearInterval(timer);
  }, []);

  // A replayed run's ids exist only in the recording. Sending them to the orchestrator would
  // 404, so every stage stays locked until the operator starts a new run.
  const live = !pipe.fixture && !recovering && !recoveryError && !pendingDeploy;

  useEffect(() => {
    if (recovering || recoveryError) return;
    setStorageFailed(!writeRecovery({ pipe, draft, active: drive && liveRobot.current
      ? { deploymentId: drive.deploymentId, robotId: liveRobot.current } : null }));
  }, [pipe, draft, drive?.deploymentId, recovering, recoveryError]);

  /**
   * Run one stage and fold its mapped result into the pipeline state. Starting a stage first
   * clears its previous output and everything downstream of it (pipeline.js:clearFrom), so a
   * failure never sits beside a stale success and nothing downstream can use a result the new
   * run has replaced.
   */
  const run = (key, call) => async () => {
    setBusy(key);
    setPipe((prev) => ({ ...prev, ...clearFrom(key) }));
    // An error belongs to the result it replaced: a new route clears the old route's refusal too.
    setErrors((prev) => ({
      ...prev, ...Object.fromEntries(invalidatedBy(key).map((stage) => [stage, null])),
    }));
    try {
      const patch = await call();
      setPipe((prev) => ({ ...prev, ...patch }));
    } catch (error) {
      setErrors((prev) => ({ ...prev, [key]: error }));
    } finally {
      setBusy(null);
      setUpload(null);
    }
  };

  /**
   * Open a scan from whatever the operator picked.
   *
   * Two transports, because the two capture routes are genuinely different: the phone
   * app produces many .npz depth frames to fuse, while Scaniverse produces one finished
   * .ply/.obj cloud. Branch on extension rather than making the operator declare it.
   */
  const openScan = (picked) => {
    const files = [...picked].sort((a, b) => a.name.localeCompare(b.name));
    if (!files.length) return;

    const extOf = (f) => (f.name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
    const exports_ = files.filter((f) => api.SCAN_EXTS.includes(extOf(f)));
    const frames = files.filter((f) => extOf(f) === ".npz");

    const fail = (message) =>
      setErrors((prev) => ({ ...prev, capture: new Error(message) }));

    // Route on what was actually picked. Falling through to the frame path for
    // anything unrecognised would upload a .usdz as if it were a depth frame and
    // report success — the failure would only surface three stages later.
    if (!exports_.length && !frames.length) {
      const rejected = [...new Set(files.map(extOf))].filter(Boolean);
      const deferred = rejected.filter((e) =>
        [".glb", ".gltf", ".usdz", ".fbx"].includes(e));
      fail(deferred.length
        ? `${deferred.join(", ")} is not supported yet — re-export as ` +
          `${api.SCAN_EXTS.join(" or ")} from Scaniverse.`
        : `Cannot read ${rejected.join(", ") || "that file"}. Pick a ` +
          `${api.SCAN_EXTS.join(" or ")} export, or the phone app's .npz frames.`);
      return;
    }
    if (exports_.length && frames.length) {
      fail("Pick either one scan export or a set of .npz frames, not both.");
      return;
    }

    if (exports_.length) {
      if (exports_.length > 1) {
        fail(`Pick one scan export at a time — got ${exports_.length}. ` +
             `A single .ply or .obj is the whole capture.`);
        return;
      }
      run("capture", async () => {
        const result = await api.importScan(exports_[0], (loaded, total) =>
          setUpload(`${fmtBytes(loaded)} of ${fmtBytes(total)}`),
          { units: exportUnits, upAxis: exportUp });
        setScanId(result.scan_id);
        return toPipe.capture(result);
      })();
      return;
    }

    // Chunk order is capture order, and the picker does not promise either — the sort
    // above is what makes the frames fuse in the order they were shot.
    run("capture", async () => {
      const result = await api.uploadScan(frames, (done, total) =>
        setUpload(`${done}/${total} frames`));
      setScanId(result.scan_id);
      return toPipe.capture(result);
    })();
  };

  const onPick = (event) => {
    const files = [...event.target.files];
    event.target.value = ""; // so re-picking the same file still fires a change
    openScan(files);
  };

  const onDrop = (event) => {
    event.preventDefault();
    setDragging(false);
    if (busy === null && live) openScan(event.dataTransfer.files);
  };

  /** Clear the pipeline so another scan can be run without a page reload. */
  const reset = () => {
    setRecoveryError("");
    setPipe(EMPTY_PIPE);
    setErrors({});
    setUpload(null);
    setScanId("");
    setRescanId("");
    setDraft(EMPTY_DRAFT);
  };

  /** Replay the recorded run (fixtures.js), for when no orchestrator is available. */
  const loadFixture = async () => {
    reset();
    setBusy("fixture");
    try {
      await replayFixture((next) => setPipe(next), { stepMs: reducedMotion() ? 0 : 160 });
      setViewTab("twin");
    } finally {
      setBusy(null);
    }
  };

  /** Plan the draft as a mission once it has a start and a goal (or a plan to take one from). */
  const planMission = (next) => {
    if (!pipe.twinId || !next.start || !(next.goal || next.taskGraphId)) return;
    run("mission", async () => {
      const planned = await api.createMission(pipe.twinId, next);
      if (next.taskGraphId) setDraft({ start: next.start, goal: planned.goal, via: next.via });
      return toPipe.mission(planned);
    })();
  };

  /** Every map edit replaces the draft and, when it is complete, plans it straight away. */
  const onDraft = (next) => {
    setDraft(next);
    planMission(next);
  };

  const setCoord = (key, index, value) => {
    const point = [...(draft[key] ?? [0, 0])];
    point[index] = value;
    setDraft({ ...draft, [key]: point });
  };

  /** Accept the point the orchestrator offered in its refusal. */
  const useNearest = () => {
    const detail = errors.mission?.detail;
    if (!detail?.nearest) return;
    const next = { ...draft, via: [...draft.via] };
    if (detail.code === "start_blocked" || detail.code === "no_start") {
      next.start = draft.start?.length > 2 ? [...detail.nearest, draft.start[2]] : detail.nearest;
    } else if (detail.index != null) {
      next.via[detail.index] = detail.nearest;
    } else {
      next.goal = detail.nearest;
    }
    onDraft(next);
  };

  const stopWatching = () => {
    watcher.current?.close();
    watcher.current = null;
  };
  useEffect(() => stopWatching, []);

  const setRobotState = (robotId, status) =>
    setPipe((prev) => (prev.robot?.id === robotId
      ? { ...prev, robot: { ...prev.robot, state: status.state, reason: status.reason ?? null } }
      : prev));

  /** Follow a rover deployment; once it ends, show the result the orchestrator recorded. */
  const follow = (deploymentId, robotId) => {
    stopWatching();
    liveRobot.current = robotId;
    setDrive({ ...EMPTY_LIVE, deploymentId });
    watcher.current = watchDeployment(deploymentId, (next) => {
      setDrive(next);
      if (!next.finished) return;
      stopWatching();
      api.deployment(deploymentId).then(
        (result) => setPipe((prev) => (prev.deploymentId === deploymentId
          ? { ...prev, ...toPipe.deployment(result) } : prev)),
        (error) => setErrors((prev) => ({ ...prev, deploy: error })),
      );
      api.robotStatus(robotId).then((status) => setRobotState(robotId, status), () => {});
    });
  };

  useEffect(() => {
    if (!pendingDeploy) return;
    let cancelled = false, timer;
    const poll = async () => {
      try {
        const record = await api.deploymentRequest(pendingDeploy.requestId);
        if (cancelled) return;
        if (record.status === "done" && record.response) {
          const answer = record.response;
          const current = await api.deployment(answer.deployment_id);
          if (cancelled) return;
          setPipe(prev => ({ ...prev, ...toPipe.deploy(answer) }));
          liveRobot.current = pendingDeploy.robotId;
          if (current.live) follow(answer.deployment_id, pendingDeploy.robotId);
          else {
            setDrive({ ...EMPTY_LIVE, deploymentId: answer.deployment_id, finished: true,
              state: current.status, reason: current.reason, trace: current.trace ?? [], samples: current.pose_samples ?? [] });
            setPipe(prev => ({ ...prev, ...toPipe.deployment(current) }));
          }
          const checkpoint = readRecovery() ?? { pipe: {} };
          if (writeRecovery({ ...checkpoint, pipe: { ...checkpoint.pipe, ...toPipe.deploy(answer) },
            active: { deploymentId: answer.deployment_id, robotId: pendingDeploy.robotId } })) clearPendingDeployment();
          setPendingDeploy(null); setPendingStatus("");
          return;
        }
        setPendingTerminal(["failed", "interrupted"].includes(record.status));
        setPendingStatus(`Previous deployment request: ${record.status}. No drive will restart automatically.`);
      } catch (error) {
        if (cancelled) return;
        setPendingStatus(`Checking previous deployment request: ${error.message}. E-STOP remains available.`);
      }
      if (!cancelled) timer = setTimeout(poll, 1000);
    };
    timer = setTimeout(poll, 500);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [pendingDeploy]);

  useEffect(() => {
    // Read again when effects reconnect (including hot reload), rather than replaying the
    // mount-time snapshot over stages the operator has completed since then.
    const checkpoint = readRecovery();
    if (!checkpoint) return;
    let cancelled = false, retry;
    setRecovering(true);
    validateRecovery(checkpoint, api).then(next => {
      if (!cancelled) setPipe(prev => ({ ...prev,
        ...(next.navmesh ? { navmesh: next.navmesh } : {}),
        ...(next.mission ? { mission: next.mission } : {}),
        ...("robot" in next ? { robot: next.robot } : {}),
        ...(next.staleMissions ? { staleMissions: next.staleMissions } : {}),
      }));
    }, error => {
      if (!cancelled) setRecoveryError(`Saved workspace could not be verified: ${error.message}. Reload to retry, or start a new run.`);
    }).finally(() => { if (!cancelled) setRecovering(false); });
    // This runs independently of workspace validation: a missing mission must not hide Stop.
    const resumeDrive = async () => {
      if (!checkpoint.active) return;
      const { deploymentId, robotId } = checkpoint.active;
      liveRobot.current = robotId;
      try {
        const result = await api.deployment(deploymentId);
        if (cancelled) return;
        if (result.live) follow(deploymentId, robotId);
        else {
          setDrive({ ...EMPTY_LIVE, deploymentId, state: result.status, finished: true,
            reason: result.reason, trace: result.trace ?? [], samples: result.pose_samples ?? [],
            fake: Boolean(result.result?.fake) });
          setPipe(prev => prev.deploymentId === deploymentId ? { ...prev, ...toPipe.deployment(result) } : prev);
        }
      } catch (error) {
        if (!cancelled) {
          setErrors(prev => ({ ...prev, deploy: error }));
          retry = setTimeout(resumeDrive, 2000);
        }
      }
    };
    resumeDrive();
    return () => { cancelled = true; clearTimeout(retry); stopWatching(); };
  }, [saved]);

  /** Over HTTP, so it works when the live socket does not; it latches on the rover. */
  const emergencyStop = (targetId) => {
    const robotId = typeof targetId === "string" ? targetId : liveRobot.current ?? pipe.robot?.id;
    if (!robotId) return;
    api.estop(robotId).then(
      (answer) => setRobotState(robotId, answer),
      (error) => setErrors((prev) => ({ ...prev, deploy: error })),
    );
  };

  const stopDrive = () => {
    if (!drive?.deploymentId) return;
    api.stopDeployment(drive.deploymentId)
      .catch((error) => setErrors((prev) => ({ ...prev, deploy: error })));
  };

  const resetRover = () => {
    const robotId = pipe.robot?.id ?? liveRobot.current;
    if (!robotId) return;
    api.resetRobot(robotId).then(
      (answer) => setRobotState(robotId, answer),
      (error) => setErrors((prev) => ({ ...prev, robot: error })),
    );
  };

  const driving = Boolean(drive && !drive.finished);
  useEffect(() => {
    if (!driving) return undefined;
    // Space is E-STOP while a rover drives, wherever the focus is.
    const onKey = (event) => {
      if (!isEstopKey(event) || event.repeat) return;
      event.preventDefault();
      emergencyStop();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [driving]);

  const registered = pipe.robot?.kind === robotKind ? pipe.robot : null;
  const result = pipe.deployResult;

  const stageProps = (key) => ({
    busy: busy === key,
    error: errors[key],
    locked: busy !== null || !live,
  });

  const cloudPoints = pipe.points ?? pipe.scanPoints;
  const successPct = pipe.successRate != null ? Math.round(pipe.successRate * 100) : null;
  const staleCount = pipe.staleMissions.filter((m) => m.stale).length;
  const headingDeg = draft.start?.[2] != null ? (draft.start[2] * 180) / Math.PI : null;
  const locked = busy !== null || !live;

  return (
    <div className="console">
      <div className="console__head">
        <div>
          <span className="eyebrow">Operator console</span>
          <h1 className="title">Run the loop, one stage at a time.</h1>
        </div>
        <div className="console__status">
          <button
            type="button"
            className="btn btn--demo"
            onClick={loadFixture}
            disabled={busy !== null}
            title="Replays a real orchestrator run recorded on a synthetic test room. It is not your scan."
          >
            Load offline fixture
          </button>
          {(pipe.scanId || recoveryError) && !pipe.fixture && (
            <button type="button" className="btn btn--ghost" onClick={reset}
                    disabled={busy !== null || recovering}>
              New run
            </button>
          )}
          <span className={`link ${online === null ? "unknown" : online ? "up" : "down"}`}>
            {online === null
              ? "checking orchestrator"
              : online
                ? "orchestrator online"
                : "orchestrator unreachable"}
          </span>
        </div>
      </div>

      {(recovering || recoveryError) && <p role="status" className="console__fixture-banner">
        {recovering ? "Restoring saved workspace and checking server state…" : recoveryError}
      </p>}
      <ProviderSettings disabled={busy !== null || driving || Boolean(pendingDeploy) || pipe.fixture} />
      {storageFailed && <p role="alert">Browser storage is unavailable. Keep this page open during a drive; workspace recovery cannot be saved.</p>}
      {pendingDeploy && <div className="live-bar" role="status">
        <span>{pendingStatus || "Waiting for the deployment response…"}</span>
        <button type="button" className="btn btn--estop" onClick={() => emergencyStop(pendingDeploy.robotId)}>E-STOP pending rover</button>
        {pendingTerminal && <button type="button" className="btn btn--ghost" onClick={() => {
          clearPendingDeployment(); setPendingDeploy(null); setPendingTerminal(false);
        }}>Acknowledge failed request</button>}
      </div>}

      {pipe.fixture && (
        <div className="console__fixture-banner" role="status">
          <strong>Offline fixture</strong>
          <span>
            {FIXTURE_SOURCE} Recorded {FIXTURE_RECORDED_AT}. None of this is from your scan, and
            the stages stay locked until you start a new run.
          </span>
          <button type="button" className="btn btn--ghost" onClick={reset}
                  disabled={busy !== null}>
            New run
          </button>
        </div>
      )}

      {drive && (
        <div className={`live-bar${driving ? " is-driving" : ""}`} role="status" aria-live="polite">
          <span className="live-bar__state">
            {driving && <span className="live-dot" />}
            {liveLabel(drive)}
          </span>
          {drive.reason && <span className="live-bar__chip">reason: {drive.reason}</span>}
          <span className="live-bar__chip">{plural(drive.trace.length, "reported pose")}</span>
          {drive.fake && <span className="live-bar__chip live-bar__chip--fake">fake rover</span>}
          {driving && !drive.connected && <span className="live-bar__chip">reconnecting…</span>}
          <span className="live-bar__actions">
            {driving && (
              <>
                <button type="button" className="btn btn--ghost" onClick={stopDrive}>Stop</button>
                <button type="button" className="btn btn--estop" onClick={emergencyStop}>
                  E-STOP <kbd>Space</kbd>
                </button>
              </>
            )}
            {!driving && ["estop", "error"].includes(drive.state) && (
              <button type="button" className="btn btn--ghost" onClick={resetRover}>Reset rover</button>
            )}
          </span>
        </div>
      )}

      {pipe.scanId && (
        <div className="console__summary-bar">
          <span className="console__summary-pill">
            SCAN: <strong>{pipe.scanId.slice(0, 8)}</strong>
          </span>
          {cloudPoints != null && (
            <span className="console__summary-pill">
              CLOUD: <strong>{cloudPoints.toLocaleString()} pts</strong>
            </span>
          )}
          {pipe.objects.length > 0 && (
            <span className="console__summary-pill">
              OBJECTS: <strong>{pipe.objects.length}</strong>
            </span>
          )}
          {pipe.profile && (
            <span className="console__summary-pill">
              PROFILE: <strong>{pipe.profile.environment}</strong>
            </span>
          )}
          {pipe.mission && (
            <span className={`console__summary-pill${pipe.mission.status === "stale" ? "" : " pass"}`}>
              MISSION: <strong>{pipe.mission.length_m} m · clearance {pipe.mission.min_clearance_m} m
                {pipe.mission.status === "stale" ? " · stale" : ""}</strong>
            </span>
          )}
          {successPct != null && (
            <span className="console__summary-pill pass">
              SIM GATE:{" "}
              <strong>
                {pipe.missionMetrics
                  ? `${pipe.missionMetrics.reached}/${pipe.missionMetrics.runs} runs ≥ ${MISSION_GATE * 100}%`
                  : `${successPct}% ≥ ${SIM_GATE * 100}%`}
              </strong>
            </span>
          )}
          {pipe.tickP50 != null && (
            <span className="console__summary-pill">
              CONTROL TICK: <strong>{pipe.tickP50} ms p50 · {pipe.computeUnit}</strong>
            </span>
          )}
        </div>
      )}

      <div className="console__grid">
        <ol className="rail">
          <Stage
            n="01" name="Capture"
            out={captureSummary(pipe)}
            action="Check scan" ready={scanId.trim().length > 0}
            onRun={run("capture", async () => {
              const id = scanId.trim();
              return toPipe.capture({ ...(await api.scanStatus(id)), scan_id: id });
            })}
            {...stageProps("capture")}
          >
            <div
              className={`dropzone${dragging ? " dropzone--over" : ""}`}
              onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
              onDragLeave={() => setDragging(false)}
              onDrop={onDrop}
            >
              <input id="scan-file" type="file" multiple
                     accept={`${api.SCAN_EXTS.join(",")},.npz`}
                     onChange={onPick} disabled={busy !== null || !live} />
              <label htmlFor="scan-file">
                Drop a scan here, or <span className="dropzone__link">browse</span>
              </label>
            </div>
            <p className="hint">
              {upload
                ? `Uploading ${upload}…`
                : "One .ply or .obj exported from Scaniverse, or the phone app's .npz " +
                  "frames. You can also name a scan already uploaded."}
            </p>
            <label>
              Export units
              <select value={exportUnits} onChange={(e) => setExportUnits(e.target.value)}>
                <option value="">Not declared (metres assumed)</option>
                {api.EXPORT_UNITS.map((u) => <option key={u} value={u}>{u}</option>)}
              </select>
            </label>
            <label>
              Export up axis
              <select value={exportUp} onChange={(e) => setExportUp(e.target.value)}>
                <option value="">Not declared (Z up assumed)</option>
                {api.EXPORT_UP_AXES.map((a) => <option key={a} value={a}>{a.toUpperCase()} up</option>)}
              </select>
            </label>
            {pipe.scanRegistration && (
              <p className="hint">{pipe.scanRegistration.reason}</p>
            )}
            <label>
              Scan id
              <input value={scanId} onChange={(e) => setScanId(e.target.value)}
                     placeholder="from Scaniverse or the phone app" />
            </label>
          </Stage>

          <Stage
            n="02" name="Reconstruct"
            out={pipe.meshId && `${(pipe.points ?? 0).toLocaleString()} points · ${pipe.meshId.slice(0, 8)}`}
            action="Reconstruct" ready={Boolean(pipe.scanId)}
            onRun={run("reconstruct", async () =>
              toPipe.reconstruct(await api.reconstruct(pipe.scanId, "fast")))}
            {...stageProps("reconstruct")}
          >
            <p className="hint">
              Fuses the depth frames on this machine's CPU and keeps surfaces at least two frames
              saw (a one-frame scan keeps everything). An imported .ply or .obj passes through.
            </p>
          </Stage>

          <Stage
            n="03" name="Segment" out={pipe.objects.length > 0 && plural(pipe.objects.length, "object")}
            action="Segment" ready={Boolean(pipe.meshId)}
            onRun={run("segment", async () => toPipe.segment(await api.segment(pipe.meshId)))}
            {...stageProps("segment")}
          >
            <p className="hint">Labels the point cloud by shape and draws the plan view.</p>
          </Stage>

          <Stage
            n="04" name="Generate twin"
            out={pipe.twinId && [
              `${plural(pipe.twinObjects, "object")} placed`,
              pipe.profile &&
                `${pipe.profile.environment} profile · clearance ${pipe.profile.robot_radius} m`,
            ].filter(Boolean).join(" · ")}
            action="Generate twin" ready={Boolean(pipe.objectsId)}
            onRun={run("twin", async () => {
              const twin = await api.generateTwin(pipe.meshId, pipe.objectsId, environment, gridCell, registered?.id);
              const navmesh = await api.navmesh(twin.twin_id);
              setDraft({ ...EMPTY_DRAFT, start: defaultStart(navmesh) });
              setViewTab("map");
              return { ...toPipe.twin(twin), ...toPipe.navmesh(navmesh), mission: null, missionId: null };
            })}
            {...stageProps("twin")}
          >
            <label>
              Robot profile
              <select value={environment} onChange={(e) => setEnvironment(e.target.value)}>
                <option value="">Decide from the scan</option>
                {ENVIRONMENTS.map((kind) => <option key={kind} value={kind}>{kind}</option>)}
              </select>
            </label>
            <label>
              Grid
              <select value={gridCell} onChange={(e) => setGridCell(e.target.value)}>
                <option value="">Decide from the scan's density</option>
                <option value="0.05">5 cm — needs a dense scan</option>
                <option value="0.1">10 cm</option>
              </select>
            </label>
            <p className="hint">
              The profile sets the robot's clearance, sweep height and top speed for this twin.
            </p>
            {pipe.counts && pipe.navmesh && (
              <p className="hint">
                Navmesh at {Math.round(pipe.navmesh.cell * 100)} cm{gridNote(pipe.resolution)}:{" "}
                {pipe.counts.free} cells free, {pipe.counts.blocked} blocked,{" "}
                {pipe.counts.unknown} never scanned.
              </p>
            )}
          </Stage>

          <Stage
            n="05" name="Plan"
            out={pipe.graphId && [
              plural(pipe.graph.length, "step"),
              pipe.provider,
              pipe.onDevice === false ? "via a network API" : "offline",
            ].join(" · ")}
            action="Plan" ready={Boolean(pipe.twinId) && text.trim().length > 0}
            onRun={run("plan", async () => toPipe.plan(await api.plan(pipe.twinId, text, lang)))}
            {...stageProps("plan")}
          >
            <label>
              Instruction
              <input value={text} onChange={(e) => setText(e.target.value)} />
            </label>
            <label>
              Language
              <select value={lang} onChange={(e) => setLang(e.target.value)}>
                <option value="en">English</option>
                <option value="hi">Hindi</option>
              </select>
            </label>
            {lang === "hi" && (
              <p className="hint">
                The offline planner reads English only. Hindi needs a remote planner
                (SCANBORN_PLANNER=groq on the orchestrator) or the phone app.
              </p>
            )}
            {pipe.graph.length > 0 && (
              <ol className="graph">
                {pipe.graph.map((node, i) => (
                  <li key={i}><span>{node.action}</span> {node.target || "—"}</li>
                ))}
              </ol>
            )}
          </Stage>

          <Stage
            n="06" name="Mission"
            out={pipe.mission && [
              `path ${pipe.mission.length_m} m`,
              `clearance ${pipe.mission.min_clearance_m} m`,
              plural(pipe.mission.waypoints.length, "waypoint"),
              pipe.mission.plan_ms != null && `planned in ${pipe.mission.plan_ms} ms`,
              pipe.mission.status === "stale" && "stale",
            ].filter(Boolean).join(" · ")}
            action="Plan route" ready={Boolean(pipe.navmesh && draft.start && draft.goal)}
            onRun={() => planMission(draft)}
            {...stageProps("mission")}
          >
            <div className="mission-fields">
              <fieldset disabled={locked || !pipe.navmesh}>
                <legend>Start</legend>
                <CoordInput label="x" value={draft.start?.[0]} onCommit={(v) => setCoord("start", 0, v)} />
                <CoordInput label="y" value={draft.start?.[1]} onCommit={(v) => setCoord("start", 1, v)} />
                <CoordInput label="heading" unit="°" step={5} value={headingDeg}
                            onCommit={(v) => setCoord("start", 2, (v * Math.PI) / 180)} />
              </fieldset>
              <fieldset disabled={locked || !pipe.navmesh}>
                <legend>Goal</legend>
                <CoordInput label="x" value={draft.goal?.[0]} onCommit={(v) => setCoord("goal", 0, v)} />
                <CoordInput label="y" value={draft.goal?.[1]} onCommit={(v) => setCoord("goal", 1, v)} />
              </fieldset>
            </div>
            <button type="button" className="btn btn--ghost" disabled={locked || !pipe.navmesh || !draft.start}
              onClick={() => onDraft(squareDraft(draft.start))}>Plan one-metre square</button>
            <p className="hint">Square test: choose a clear start, then plan four one-metre sides. Inspect any route detour before training. After driving, compare reported closure with tape-measured position; this does not calibrate the rover.</p>
            {draft.via.length > 0 && (
              <ol className="mission-via-list" aria-label="Via points">
                {draft.via.map(([x, y], i) => (
                  <li key={i}>
                    <button type="button" className="btn btn--ghost" disabled={locked}
                            onClick={() => onDraft({ ...draft, via: draft.via.filter((_, j) => j !== i) })}>
                      {i + 1}: {round2(x)}, {round2(y)} ✕
                    </button>
                  </li>
                ))}
              </ol>
            )}
            <div className="mission-actions">
              <button type="button" className="btn btn--ghost"
                      disabled={locked || !pipe.graphId || !draft.start}
                      title="The spot beside the first target the plan navigates to"
                      onClick={() => planMission({ ...draft, goal: null, taskGraphId: pipe.graphId })}>
                Goal from the plan
              </button>
              {errors.mission?.detail?.nearest && (
                <button type="button" className="btn btn--ghost" disabled={locked} onClick={useNearest}>
                  Use nearest valid point
                </button>
              )}
            </div>
            <p className="hint">
              Click the mission map to set the goal. The start defaults to the scan's home pose;
              without one it is placed where the robot fits nearest the middle of the room.
            </p>
          </Stage>

          <Stage
            n="07" name="Train"
            out={successPct != null && (pipe.missionMetrics
              ? [
                `${pipe.missionMetrics.reached}/${pipe.missionMetrics.runs} runs reached`,
                pipe.missionMetrics.rms_deviation_m != null &&
                  `${Math.round(pipe.missionMetrics.rms_deviation_m * 100)} cm RMS off route`,
                `closest ${pipe.missionMetrics.closest_approach_m} m`,
                `noise ${pipe.missionMetrics.noise.source}`,
              ].filter(Boolean).join(" · ")
              : `${successPct}% success in simulation`)}
            action="Train" ready={Boolean(pipe.twinId)}
            onRun={run("train", async () =>
              toPipe.train(await api.train(pipe.twinId, pipe.graphId, pipe.missionId, registered?.id)))}
            {...stageProps("train")}
          >
            <p className="hint">
              Clones the rover's route-following controller in a model of the reference rover:
              speed, turn and acceleration limits, wheel slip, gyro bias and a one-tick delay,
              with noise assumed until calibration. With a mission, {MISSION_GATE * 100}% of 30
              noisy runs from its start must stop within 10 cm of the goal; without one,
              {" "}{SIM_GATE * 100}% of 20 random starts. Below that the orchestrator refuses with
              HTTP 409. The rover dead-reckons, so routes much longer than about 3 m can fail on
              drift alone.
            </p>
          </Stage>

          <Stage
            n="08" name="Optimize"
            /* op_coverage is null off the AI Hub path — say so, rather than render
               "null% ops" and let an unmeasured bundle read as a profiled one. */
            out={pipe.artifactId && [
              pipe.opCoverage == null ? "ops not profiled" : `${pipe.opCoverage}% ops`,
              `${pipe.latency} ms${pipe.latencySource === "host-cpu" ? " (host CPU)" : ""}`,
              pipe.backend,
            ].join(" · ")}
            action="Optimize" ready={Boolean(pipe.policyId)}
            onRun={run("optimize", async () =>
              toPipe.optimize(await api.optimize(pipe.policyId, deviceLabel.trim())))}
            {...stageProps("optimize")}
          >
            <label>
              Device label
              <input value={deviceLabel} onChange={(e) => setDeviceLabel(e.target.value)}
                     placeholder="blank: host-cpu (or AI Hub's default device)" />
            </label>
            <p className="hint">
              Quantizes the policy to int8. Without an AI Hub token this is the local path: op
              coverage is not measured, and latency is timed on this computer's CPU.
            </p>
          </Stage>

          <Stage
            n="09" name="Deploy"
            out={pipe.deploymentId && [
              pipe.deployStatus,
              pipe.liveUrl ? pipe.deployFake && "fake rover" : plural(pipe.poseTrace.length, "pose"),
              pipe.reached && "goal reached",
              result?.deviation_from_sim_m != null &&
                `${cm(result.deviation_from_sim_m)} RMS from the simulated run`,
              result?.reported_miss_m != null &&
                `stopped ${cm(result.reported_miss_m)} from the goal by its own estimate`,
              // Only the fake rover knows where it really is.
              result?.true_miss_m != null && `${cm(result.true_miss_m)} in truth`,
              // Measured on the int8 policy that actually shipped, per control tick.
              pipe.tickP50 != null &&
                `${pipe.tickP50} ms per control tick (p50, ${pipe.computeUnit}` +
                `${pipe.precision ? `, ${pipe.precision}` : ""})`,
            ].filter(Boolean).join(" · ")}
            action="Deploy"
            // Locked while a rover drives: a second deploy would be refused, and starting it would
            // first clear the record of the drive still under way.
            ready={Boolean(pipe.artifactId) && !driving && !pendingDeploy
              && (!ROVER_KINDS.has(robotKind) || Boolean(registered))}
            onRun={run("deploy", async () => {
              const requestId = crypto.randomUUID();
              if (ROVER_KINDS.has(robotKind)) {
                const pending = { requestId, robotId: registered.id };
                savePendingDeployment(pending); setPendingTerminal(false); setPendingDeploy(pending);
              }
              const answer = await api.deploy(pipe.artifactId, ROVER_KINDS.has(robotKind)
                ? { robotId: registered.id, requestId } : { kind: robotKind, requestId });
              if (answer.live) follow(answer.deployment_id, answer.robot_id);
              const checkpoint = readRecovery() ?? { pipe: {} };
              if (writeRecovery({ ...checkpoint, pipe: { ...checkpoint.pipe, ...toPipe.deploy(answer) },
                active: answer.live ? { deploymentId: answer.deployment_id, robotId: answer.robot_id } : null }))
                clearPendingDeployment();
              setPendingDeploy(null); setPendingStatus("");
              return toPipe.deploy(answer);
            })}
            {...stageProps("deploy")}
          >
            <label>
              Robot
              <select value={robotKind} onChange={(e) => setRobotKind(e.target.value)}>
                {ROBOT_KINDS.map(([kind, label]) => (
                  <option key={kind} value={kind}>{label}</option>
                ))}
              </select>
            </label>
            {robotKind === "rover" && (
              <label>
                Rover address
                <input value={roverAddress} onChange={(e) => setRoverAddress(e.target.value)}
                       placeholder="http://192.168.1.50" />
              </label>
            )}
            {ROVER_KINDS.has(robotKind) && (
              <div className="rover-register">
                <button
                  type="button" className="btn btn--ghost"
                  disabled={locked || (robotKind === "rover" && !roverAddress.trim())}
                  onClick={run("robot", async () => toPipe.robot(await api.registerRobot(
                    robotKind === "rover"
                      ? { kind: "rover", address: roverAddress.trim() }
                      : { kind: "fake_rover" })))}
                >
                  {registered ? "Register again" : "Register rover"}
                </button>
                {registered && (
                  <span className="hint">
                    {registered.fake ? "fake rover" : registered.address}
                    {registered.firmware ? ` · firmware ${registered.firmware}` : ""}
                    {` · ${registered.state}`}{registered.reason ? ` (${registered.reason})` : ""}
                  </span>
                )}
                {registered && !driving && ["estop", "error"].includes(registered.state) && (
                  <button type="button" className="btn btn--ghost" onClick={resetRover}>
                    Reset rover
                  </button>
                )}
                {errors.robot && <div className="error"><strong>{errors.robot.message}</strong></div>}
                {registered && <Calibration key={registered.id} robot={registered} driving={driving || Boolean(pendingDeploy)} onEstop={() => emergencyStop(registered.id)} onApplied={() => setPipe(prev => ({ ...prev, ...clearFrom("train") }))} />}
              </div>
            )}
            <p className="hint">
              The simulated robot drives the route once in the rover model. A rover, fake or ESP32,
              is registered first and needs a policy trained on a mission; it drives that mission
              itself from its start while the map and the 3D view follow it, and Space or E-STOP
              stops it. Deploy stays locked until that drive ends. A mission a rescan made stale is
              refused.
            </p>
          </Stage>

          <Stage
            n="↺" name="Rescan"
            out={pipe.diff && [
              `${pipe.diff.added_voxels} added · ${pipe.diff.removed_voxels} removed`,
              pipe.staleMissions.length > 0 &&
                `${plural(staleCount, "mission")} of ${pipe.staleMissions.length} stale`,
            ].filter(Boolean).join(" · ")}
            action="Rescan and diff" ready={Boolean(pipe.twinId) && rescanId.trim().length > 0 && sameMarker}
            onRun={run("sync", async () => {
              const result = await api.sync(pipe.twinId, rescanId.trim(), sameMarker);
              const patch = toPipe.sync(result);
              const current = result.stale_missions?.find((m) => m.mission_id === pipe.missionId);
              // The map and the summary must show the current mission as stale straight away.
              return current?.stale
                ? { ...patch, ...toPipe.mission(await api.mission(pipe.missionId)) }
                : patch;
            })}
            {...stageProps("sync")}
          >
            <label>
              New scan id
              <input value={rescanId} onChange={(e) => { setRescanId(e.target.value); setSameMarker(false); }}
                     placeholder="scan the room again" />
            </label>
            {pipe.changed?.length > 0 && (
              <p className="hint">Changed: {pipe.changed.join(", ")}</p>
            )}
            <p className="hint">
              A mission goes stale when the rescan puts something within the robot's clearance of
              its route. The rescan must be taken in the same frame as the first scan.
            </p>
            <label><input type="checkbox" checked={sameMarker} onChange={e => setSameMarker(e.target.checked)} />
              I verified the same physical marker, origin and heading. Imported scans need verified alignment.
            </label>
          </Stage>
        </ol>

        <section className="view">
          <nav className="view-tabs" aria-label="Console workspace view">
            <button
              type="button"
              className={`view-tab ${viewTab === "twin" ? "is-active" : ""}`}
              onClick={() => setViewTab("twin")}
            >
              Twin 3D
              {pipe.fixture && <span className="view-tab__badge">fixture</span>}
            </button>
            <button
              type="button"
              className={`view-tab ${viewTab === "map" ? "is-active" : ""}`}
              onClick={() => setViewTab("map")}
            >
              Mission map
              {pipe.mission ? (
                <span className="view-tab__badge">{pipe.mission.length_m} m route</span>
              ) : pipe.objects.length > 0 && (
                <span className="view-tab__badge">{plural(pipe.objects.length, "object")}</span>
              )}
            </button>
            <button
              type="button"
              className={`view-tab ${viewTab === "jobs" ? "is-active" : ""}`}
              onClick={() => setViewTab("jobs")}
            >
              Live jobs
            </button>
          </nav>

          <div className="panel">
            {viewTab === "twin" && (
              <div>
                <h2>Digital twin</h2>
                <ViewBoundary name="3D view">
                <Suspense fallback={<p>Loading 3D workspace…</p>}>
                <RobotViewer3D
                  meshId={pipe.meshId}
                  twinId={pipe.twinId}
                  objects={pipe.objects}
                  poseTrace={pipe.poseTrace}
                  robotRadius={pipe.profile?.robot_radius}
                  navmesh={pipe.navmesh}
                  spec={pipe.robotSpec?.wheel_radius_m ? pipe.robotSpec : undefined}
                  path={pipe.mission?.waypoints ?? NO_POINTS}
                  fixture={pipe.fixture}
                  poseSamples={drive?.finished ? drive.samples : NO_POINTS}
                  livePose={drive && !drive.finished ? drive.pose : null}
                  liveTrace={drive?.trace ?? NO_POINTS}
                  liveState={drive ? liveLabel(drive) : null}
                />
                </Suspense>
                </ViewBoundary>
              </div>
            )}
            {viewTab === "map" && (
              <div>
                <h2>Mission map</h2>
                <ViewBoundary name="mission map">
                <MissionMap
                  objects={pipe.objects}
                  navmesh={pipe.navmesh}
                  draft={live ? draft : null}
                  mission={pipe.mission}
                  simTrace={pipe.poseTrace.length ? pipe.poseTrace : pipe.missionTrace}
                  realTrace={drive?.trace ?? NO_POINTS}
                  roverPose={drive?.pose ?? null}
                  realLabel={drive?.fake ? "driven by the fake rover (reported position)"
                    : "driven by the rover (reported position)"}
                  disabled={locked || !pipe.navmesh}
                  onChange={onDraft}
                />
                </ViewBoundary>
              </div>
            )}
            {viewTab === "jobs" && (
              <div>
                <h2>Orchestrator jobs</h2>
                <JobFeed />
              </div>
            )}
          </div>
          <ResultInspector pipe={pipe} drive={drive} />
        </section>
      </div>

      <section className="panel wide console__telemetry">
        <h2>Measured on this project's hardware</h2>
        <Telemetry />
      </section>
    </div>
  );
}
