import React from "react";
import { squareReport } from "./square.js";

const valid = points => (points ?? []).filter(p => Number.isFinite(p[0]) && Number.isFinite(p[1]));

export default function ResultInspector({ pipe, drive }) {
  const metrics = pipe.missionMetrics;
  const result = pipe.deployResult;
  const simulation = valid(pipe.missionTrace.length ? pipe.missionTrace : pipe.poseTrace);
  const reported = valid(drive?.trace);
  const all = [...simulation, ...reported];
  const bounds = all.reduce((b,p) => [Math.min(b[0],p[0]),Math.min(b[1],p[1]),Math.max(b[2],p[0]),Math.max(b[3],p[1])], [Infinity,Infinity,-Infinity,-Infinity]);
  const scale = Math.min(440 / Math.max(.1,bounds[2]-bounds[0]), 210 / Math.max(.1,bounds[3]-bounds[1]));
  const polyline = trace => trace.filter((_,i) => i % Math.max(1,Math.floor(trace.length/2000)) === 0 || i === trace.length-1)
    .map(p => `${20+(p[0]-bounds[0])*scale},${235-(p[1]-bounds[1])*scale}`).join(" ");
  const outcomes = Object.entries(metrics?.outcomes ?? {});
  const square = drive?.finished && drive.deploymentId === pipe.deploymentId
    ? squareReport(pipe.mission, drive.trace, drive.samples) : null;
  return <aside className="result-inspector" aria-label="Mission and result inspector">
    <h2>Mission and result</h2>
    {square && <section aria-label="One-metre square odometry report">
      <h3>One-metre square · {drive.state}</h3>
      <p>{drive.fake ? "Simulated rover odometry" : "Reported rover odometry"} · closure {square.closure_m.toFixed(3)} m
        · ΔX {square.dx.toFixed(3)} m · ΔY {square.dy.toFixed(3)} m</p>
      <p>Reported travel {square.reported_travel_m.toFixed(3)} m · planned {square.planned_length_m.toFixed(3)} m
        {square.final_heading_change_deg != null && ` · final heading change ${square.final_heading_change_deg.toFixed(1)}°`}</p>
      {square.detoured && <p role="status">Planner detoured from the four-metre square. This is not a clean square trial.</p>}
      <p className="hint">Closure uses the rover’s own estimate. Tape-measure the final X/Y offsets from the start marker separately. The route does not command a final in-place turn; heading change is descriptive. Stopped runs are incomplete trials.</p>
    </section>}
    <p>{pipe.mission ? `${pipe.mission.length_m} m route · ${pipe.mission.status ?? "ready"}` : "Choose a start and goal to inspect a mission."}</p>
    {metrics && <>
      <p>{metrics.reached}/{metrics.runs} simulated runs reached the goal · gate {Math.round(metrics.gate*100)}%</p>
      <svg viewBox={`0 0 480 ${outcomes.length * 28 + 8}`} role="img" aria-label="Measured simulated mission outcomes">
        {outcomes.map(([label,count],i) => <g key={label} transform={`translate(0,${i*28})`}>
          <text x="0" y="18" fill="currentColor" fontSize="11">{label.replaceAll("_"," ")}</text>
          <rect x="135" y="5" width={Math.max(0,count/Math.max(1,metrics.runs))*290} height="17" fill={label === "reached" ? "#14b09a" : "#ff5b2e"} />
          <text x="438" y="18" fill="currentColor" fontSize="12">{count}</text>
        </g>)}
      </svg>
      <p className="hint">Noise: {metrics.noise?.source ?? "unknown"}. These are simulated trials.</p>
    </>}
    {all.length > 0 && <figure>
      <svg viewBox="0 0 480 260" role="img" aria-label="Simulated and reported traces in map coordinates">
        <polyline points={polyline(simulation)} fill="none" stroke="#ff5b2e" strokeWidth="2" />
        <polyline points={polyline(reported)} fill="none" stroke="#7b2cbf" strokeWidth="2" />
        <text x="20" y="255" fontSize="11" fill="currentColor">Map X/Y · metres · equal scale</text>
      </svg>
      <figcaption>Orange: simulation · Purple: {drive?.fake ? "fake rover’s reported" : "rover’s reported"} position. Spatial comparison; no invented time alignment.</figcaption>
    </figure>}
    {result && <dl>
      {[["Goal reached",String(result.reached)],["Reported goal error",result.reported_miss_m],
        ["Simulation deviation",result.deviation_from_sim_m],["Simulated true goal error",result.true_miss_m]]
        .filter(([,v])=>v != null).map(([name,value])=><React.Fragment key={name}><dt>{name}</dt><dd>{typeof value === "number" ? `${value.toFixed(3)} m` : value}</dd></React.Fragment>)}
    </dl>}
  </aside>;
}
