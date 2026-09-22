import React, { useEffect, useRef, useState } from "react";
import * as api from "./api.js";
import { pendingCalibration, recoverCalibration, startCalibration } from "./calibration-recovery.js";

function SpeedChart({ samples = [] }) {
  const finite = samples.filter(s => [s.left_mps, s.right_mps, s.target_mps].every(Number.isFinite));
  if (finite.length < 2) return null;
  const max = Math.max(0.05, ...finite.flatMap(s => [Math.abs(s.left_mps), Math.abs(s.right_mps), Math.abs(s.target_mps)]));
  const points = key => finite.map((s, i) => `${20 + i / (finite.length - 1) * 440},${90 - s[key] / max * 65}`).join(" ");
  return <figure className="calibration-chart">
    <svg viewBox="0 0 480 180" role="img" aria-label="Target and measured wheel speed over the experiment">
      <line x1="20" x2="460" y1="90" y2="90" stroke="currentColor" opacity=".25" />
      <polyline points={points("target_mps")} stroke="#a8aebd" fill="none" strokeDasharray="4 4" />
      <polyline points={points("left_mps")} stroke="#38bdf8" fill="none" strokeWidth="2" />
      <polyline points={points("right_mps")} stroke="#f59e0b" fill="none" strokeWidth="2" />
      <text x="20" y="16" fill="currentColor" fontSize="11">±{max.toFixed(3)} m/s</text>
      <text x="20" y="175" fill="currentColor" fontSize="11">Start → end of experiment</text>
    </svg>
    <figcaption>Dashed: target · Blue: left · Amber: right. Values retain the session’s measurement provenance.</figcaption>
  </figure>;
}

export default function Calibration({ robot, driving, onEstop, onApplied }) {
  const [session, setSession] = useState(null);
  const [mode, setMode] = useState("straight");
  const [measured, setMeasured] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [signsChecked, setSignsChecked] = useState(false);
  const [restoring, setRestoring] = useState(true);
  const [pending, setPending] = useState(false);
  const activeRobot = useRef(robot.id);
  activeRobot.current = robot.id;
  useEffect(() => {
    let cancelled = false, timer;
    async function restore() {
      try {
        const saved = pendingCalibration(robot.id);
        if (saved) {
          if (!cancelled) { setPending(true); setMode(saved.mode); }
          try {
            const recovered = await recoverCalibration(robot.id, api);
            if (!cancelled) {
              setSession(recovered); setPending(false); setRestoring(false); setError("");
            }
          } catch (e) {
            if (e.status !== 404) throw e;
            if (!cancelled) {
              setRestoring(false);
              setError("Start outcome unknown. Retry uses the saved request; E-STOP remains available.");
            }
          }
          return;
        }
        const history = await api.calibrationHistory(robot.id);
        if (cancelled) return;
        const latest = history.items[0] ?? null;
        setSession(latest); if (latest) setMode(latest.mode);
        setPending(false); setRestoring(false); setError("");
      } catch (e) {
        if (!cancelled) {
          setError(`Restoring calibration: ${e.message}. E-STOP remains available.`);
          timer = setTimeout(restore, 1500);
        }
      }
    }
    setSession(null); setRestoring(true); setPending(false); setBusy(false); setSignsChecked(false);
    restore();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [robot.id]);
  useEffect(() => {
    if (!session || session.status !== "running") return;
    let cancelled = false, timer;
    async function poll() {
      try {
        const value = await api.calibration(robot.id, session.id);
        if (!cancelled) { setSession(value); setError(""); }
      } catch (e) { if (!cancelled) setError(`Calibration status unavailable: ${e.message}. Stop and E-STOP remain available.`); }
      if (!cancelled) timer = setTimeout(poll, 350);
    }
    timer = setTimeout(poll, 350);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [robot.id, session?.id, session?.status]);
  const action = async fn => {
    const owner = robot.id;
    setBusy(true); setError("");
    try {
      const result = await fn();
      if (activeRobot.current !== owner) return;
      setSession(result); setMeasured(""); setPending(false);
      if (result.applied) onApplied?.();
    } catch (e) { if (activeRobot.current === owner) setError(e.message); }
    finally { if (activeRobot.current === owner) setBusy(false); }
  };
  const running = session?.status === "running";
  const stop = async () => {
    try { await api.stopCalibration(robot.id, session.id); }
    catch (e) { setError(e.message); }
  };
  return <details className="calibration-panel">
    <summary>Calibration and wheel response</summary>
    <p className="hint">{robot.fake ? "Simulated experiments — results only update this fake rover’s specification." : "Measured experiments — record tape-measured travel or rotation before applying geometry."} Noise parameters remain unchanged.</p>
    <p className="hint">Sign check: with wheels clear of the floor, verify forward motor motion increases both encoders. Counterclockwise rotation must increase gyro yaw, decrease the left count and increase the right count. Stop if any direction disagrees.</p>
    <label><input type="checkbox" checked={signsChecked} onChange={e => setSignsChecked(e.target.checked)} /> {robot.fake ? "Use simulated sign check" : "I verified motor, encoder and gyro signs"}</label>
    <label>Experiment<select value={mode} disabled={running || pending || busy || restoring} onChange={e => setMode(e.target.value)}>
      <option value="straight">Straight distance (about one metre)</option>
      <option value="spin">Counterclockwise spin (three turns)</option>
      <option value="wheel_step">Wheel step (0.1 m/s for one second)</option>
    </select></label>
    <div className="calibration-actions">
      <button type="button" className="btn btn--ghost" disabled={restoring || driving || running || busy}
        onClick={async () => {
          setBusy(true); setError("");
          try { await api.resetRobot(robot.id); }
          catch (e) { setError(e.message); }
          finally { setBusy(false); }
        }}>Reset rover to idle</button>
      <button type="button" className="btn btn--ghost" disabled={restoring || driving || running || busy || !signsChecked}
        onClick={() => action(async () => {
          setPending(true); setSession(null);
          const result = await startCalibration(robot.id, { mode, duration_s: 1, target_mps: 0.1 }, api);
          return result;
        })}>{pending ? "Retry saved start request" : "Start experiment"}</button>
      <button type="button" className="btn btn--ghost" disabled={!running} onClick={stop}>Stop calibration</button>
      <button type="button" className="btn btn--danger" onClick={onEstop}>E-STOP</button>
    </div>
    <p className="hint">After a mission or E-STOP, reset the rover to idle before starting an experiment. Reset does not start motion.</p>
    {restoring && <p role="status">Checking previous calibration sessions…</p>}
    {session && <>
      <p role="status">{session.mode} · {session.status} · {session.source}{session.reason ? ` · ${session.reason}` : ""}</p>
      {session.start_state === "unknown" && <p role="alert">The start response was not confirmed. Check rover status before another experiment; E-STOP remains available.</p>}
      <p className="hint">Encoder counts: {session.encoder_counts?.map(v => Number(v).toFixed(0)).join(" / ")} · Gyro rotation: {Number(session.gyro_yaw_rad ?? 0).toFixed(3)} rad</p>
      <SpeedChart samples={session.samples} />
      {session.status === "done" && session.mode !== "wheel_step" && <>
        <label>{session.mode === "straight" ? "Measured travel (metres)" : "Measured rotation (radians; three full turns = 18.8496)"}
          <input type="number" min="0" step="any" value={measured} onChange={e => setMeasured(e.target.value)} /></label>
        <button type="button" className="btn btn--ghost" disabled={busy || session.applied || !Number.isFinite(Number(measured)) || Number(measured) <= 0}
          onClick={() => action(() => api.measureCalibration(robot.id, session.id, Number(measured)))}>Calculate proposal</button>
      </>}
      {session.proposal && <>
        <button type="button" className="btn btn--ghost" disabled={busy || driving || running}
          onClick={() => action(() => api.reconcileCalibration(robot.id, session.id))}>Check and reconcile configuration</button>
        <p className="hint">Checks live configuration after an interrupted or older apply. Does not move the rover or resend settings.</p>
        <dl>{Object.entries(session.proposal.settings).map(([key, value]) => <React.Fragment key={key}>
          <dt>{key}</dt><dd>{session.proposal.before[key].toFixed(6)} → {value.toFixed(6)} m</dd>
        </React.Fragment>)}</dl>
        <button type="button" className="btn btn--ghost" disabled={busy || session.applied}
          onClick={() => action(() => api.applyCalibration(robot.id, session.id))}>Apply and verify configuration</button>
        {session.applied && <p role="status">Configuration read-back verified; {session.source} geometry saved.</p>}
      </>}
    </>}
    {error && <p className="error" role="alert">{error}</p>}
  </details>;
}
