import React, { useEffect, useState } from "react";

import { benchmarks } from "./api.js";

/**
 * Where each workload runs, on the hardware this project actually uses. None of it relies on
 * an accelerator: the phone's model runs on its CPU, and the laptop's GPU is not needed by the
 * core loop.
 */
const TIERS = [
  {
    tier: "Phone",
    hardware: "Galaxy S24 FE · Exynos 2400e",
    compute: "CPU, 6 threads",
    workload: "ARCore depth capture; Qwen2.5-1.5B Q4_K_M under a grammar",
  },
  {
    tier: "Laptop",
    hardware: "Lenovo LOQ · i5-13450HX",
    compute: "CPU (GPU not needed)",
    workload: "Orchestrator: reconstruction, twin, planning, training, int8 export",
  },
  {
    tier: "Robot",
    hardware: "ESP32 two-wheel rover (being built)",
    compute: "Microcontroller",
    workload: "Odometry and the int8 policy's control loop",
  },
];

const BUILD_LABELS = {
  baseline: "Baseline build",
  arm_optimised: "ARM-optimised build",
  arm_optimised_repack: "ARM-optimised + repack build",
};

const seconds = (ms) => (ms == null ? "—" : `${(ms / 1000).toFixed(1)} s`);
const rate = (value) => (value == null ? "—" : `${value} tok/s`);

function buildLabel(run) {
  const base = BUILD_LABELS[run.build] ?? run.build;
  if (run.repack === true) return `${base}, repack on`;
  if (run.repack === false) return `${base}, repack off`;
  return base;
}

/** benchmarks/phone.json — every value read from the phone's own logs. */
function PhoneRuns({ phone }) {
  const runs = phone.runs ?? [];
  return (
    <>
      <p className="telemetry__meta">
        {phone.device?.model} · {phone.device?.soc} · Android {phone.device?.android} —{" "}
        {phone.model?.name} {phone.model?.quantization}
      </p>
      <div className="telemetry__table-wrap">
        <table className="tiers">
          <thead>
            <tr>
              <th>Build</th>
              <th>Threads</th>
              <th>Prompt</th>
              <th>Prefill</th>
              <th>First token</th>
              <th>Decode</th>
              <th>Full reply</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.measured_at}>
                <td>{buildLabel(run)}</td>
                <td>{run.threads}</td>
                <td>{run.prompt_tokens} tok</td>
                <td>{rate(run.prefill_tok_per_s)}</td>
                <td>{seconds(run.time_to_first_token_ms)}</td>
                <td>{rate(run.decode_tok_per_s)}</td>
                <td>{seconds(run.total_ms)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {phone.conclusions?.length > 0 && (
        <ul className="telemetry__conclusions">
          {phone.conclusions.map((line) => <li key={line}>{line}</li>)}
        </ul>
      )}
      {phone.method && <p className="telemetry__meta">{phone.method}</p>}
    </>
  );
}

/** Qualcomm AI Hub profiles, present only when deployment/aihub_export has been run. */
function ProfiledModels({ models }) {
  return (
    <div className="telemetry__table-wrap">
      <table className="tiers">
        <thead>
          <tr><th>Model</th><th>Device</th><th>Runtime</th><th>Precision</th><th>p50</th></tr>
        </thead>
        <tbody>
          {models.map((record) => {
            const profile = record.npu ?? {};
            return (
              <tr key={record.model}>
                <td>{record.model}</td>
                <td>{profile.device ?? "—"}</td>
                <td>{profile.runtime ?? "—"}</td>
                <td>{profile.precision ?? "—"}</td>
                <td>{profile.latency_p50_ms != null ? `${profile.latency_p50_ms} ms` : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Measured numbers only. When nothing has been measured, it says so rather than showing
 * zeros — an unmeasured model must never read as a fast one.
 */
export default function Telemetry() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    benchmarks().then(setData, (e) => setError(e.message));
  }, []);

  return (
    <section className="telemetry">
      <h3>Where each workload runs</h3>
      <div className="telemetry__table-wrap">
        <table className="tiers">
          <thead>
            <tr><th>Tier</th><th>Hardware</th><th>Compute</th><th>Workload</th></tr>
          </thead>
          <tbody>
            {TIERS.map((t) => (
              <tr key={t.tier}>
                <td>{t.tier}</td>
                <td>{t.hardware}</td>
                <td className="unit-cpu">{t.compute}</td>
                <td>{t.workload}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3>On-device model, measured on the phone</h3>
      {error && <p className="bench-error">Measurements unavailable — {error}</p>}
      {data && !data.phone && (
        <p className="bench-empty">
          No phone measurements yet. <code>docs/hardware/s24fe.md</code> explains how to record
          them into <code>benchmarks/phone.json</code>.
        </p>
      )}
      {data?.phone && <PhoneRuns phone={data.phone} />}

      {data?.models?.length > 0 && (
        <>
          <h3>Profiled models</h3>
          <ProfiledModels models={data.models} />
        </>
      )}
    </section>
  );
}
