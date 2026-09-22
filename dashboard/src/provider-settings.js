export function providerPayload(drafts, consent, report) {
  const workloads = {}, remote_consent = {};
  for (const [name, entry] of Object.entries(report.workloads)) {
    const chain = (drafts[name] ?? "").split(",").map(n => n.trim()).filter(Boolean);
    if (!chain.length || chain.some(n => !entry.allowed.includes(n)) ||
        new Set(chain).size !== chain.length || (name === "export" && chain.length !== 1))
      throw new Error(`Choose a valid ${name} chain from ${entry.allowed.join(", ")}`);
    workloads[name] = chain;
    if (chain.some(n => report.provider_locations[n] === "remote")) {
      if (!consent[name]) throw new Error(`Authorize the outgoing ${name} data first`);
      remote_consent[name] = report.remote_data_scopes[name];
    }
  }
  return { workloads, remote_consent };
}
