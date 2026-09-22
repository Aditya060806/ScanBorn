import React, { useEffect, useState } from "react";
import * as api from "./api.js";
import { providerPayload } from "./provider-settings.js";

export default function ProviderSettings({ disabled = false }) {
  const [report, setReport] = useState(null), [drafts, setDrafts] = useState({});
  const [consent, setConsent] = useState({}), [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  function loaded(value) {
    setReport(value);
    setDrafts(Object.fromEntries(Object.entries(value.workloads).map(([k, v]) => [k, v.chain.join(", ")])));
    setConsent({});
  }
  useEffect(() => {
    let cancelled = false;
    api.providers().then(value => { if (!cancelled) loaded(value); })
      .catch(e => { if (!cancelled) setMessage(e.message); });
    return () => { cancelled = true; };
  }, []);
  async function refresh() {
    setBusy(true);
    try { loaded(await api.providers()); setMessage(""); }
    catch (e) { setMessage(e.message); }
    finally { setBusy(false); }
  }
  async function save(localOnly = false) {
    setBusy(true);
    try {
      const payload = localOnly ? { workloads: { planner: ["local"], profiler: ["local"], export: ["local"] }, remote_consent: {} }
        : providerPayload(drafts, consent, report);
      loaded(await api.saveProviderSettings(payload));
      setMessage("Saved for subsequent stages. Running requests keep their selected chain.");
    } catch (e) { setMessage(e.message); }
    finally { setBusy(false); }
  }
  return <details className="calibration-panel">
    <summary>Processing providers and privacy</summary>
    <p>Local processing is the default. Remote providers receive only the scope listed below.
      Credentials are configured on the server; this screen never receives their values.</p>
    {report && <>
      <p>Configuration: {report.configuration_source === "saved_settings" ? "saved operator settings" : "server environment"}.</p>
      {Object.entries(report.workloads).map(([name, entry]) => {
        const remote = (drafts[name] ?? "").split(",").some(n => report.provider_locations[n.trim()] === "remote");
        return <fieldset key={name} disabled={disabled || busy}>
          <legend>{name}</legend>
          <label>Providers in order{ name !== "export" && " (comma separated; add local for fallback)"}
            <input value={drafts[name] ?? ""} onChange={e => {
              setDrafts(p => ({ ...p, [name]: e.target.value }));
              setConsent(p => ({ ...p, [name]: false }));
            }} /></label>
          <p className="hint">Available: {entry.allowed.join(", ")}. Current: {entry.chain.join(" → ")} · {entry.ready ? "ready" : entry.error}</p>
          {entry.credential?.map(c => <p key={c.setting} className="hint">{c.setting}: {c.configured ? "configured on server" : "missing on server"}</p>)}
          {remote && <label><input type="checkbox" checked={Boolean(consent[name])}
            onChange={e => setConsent(p => ({ ...p, [name]: e.target.checked }))} />
            Authorize sending {report.remote_data_scopes[name].join(", ")} to the selected remote providers.</label>}
          {name === "export" && <p className="hint">Remote/QAIRT export experiments are not deployable rover policy bundles.</p>}
        </fieldset>;
      })}
      <p className="hint">Remote request ceiling: {report.limits.remote_daily_limit}/UTC day ·
        used: {report.remote_usage_today.reduce((sum, row) => sum + row.calls, 0)} ·
        deadline: {report.limits.deadline_s}s · request timeout: {report.limits.attempt_timeout_s}s.
        These server limits count requests; monetary cost is unknown.</p>
      <button className="btn btn--primary" disabled={disabled || busy} onClick={() => save()}>Save provider settings</button>
    </>}
    <button className="btn btn--ghost" disabled={busy} onClick={refresh}>Refresh provider status</button>
    <button className="btn btn--ghost" disabled={disabled || busy} onClick={() => save(true)}>Use local providers for all stages</button>
    {message && <p role="status">{message}</p>}
  </details>;
}
