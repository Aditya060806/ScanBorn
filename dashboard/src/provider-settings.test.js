import { expect, it } from "vitest";
import { providerPayload } from "./provider-settings.js";

const report = {
  workloads: { planner: { allowed: ["local", "groq"] }, export: { allowed: ["local", "ai-hub"] } },
  provider_locations: { local: "orchestrator", groq: "remote", "ai-hub": "remote" },
  remote_data_scopes: { planner: ["instruction", "scene_object_labels"], export: ["policy_weights"] },
};
it("requires explicit consent and preserves fallback ordering", () => {
  const drafts = { planner: "groq, local", export: "local" };
  expect(() => providerPayload(drafts, {}, report)).toThrow("Authorize");
  expect(providerPayload(drafts, { planner: true }, report)).toEqual({
    workloads: { planner: ["groq", "local"], export: ["local"] },
    remote_consent: { planner: ["instruction", "scene_object_labels"] },
  });
});
it("refuses invalid chains and does not require consent for local execution", () => {
  for (const planner of ["", "groq,groq", "invented"])
    expect(() => providerPayload({ planner, export: "local" }, {}, report)).toThrow();
  expect(providerPayload({ planner: "local", export: "local" }, {}, report).remote_consent).toEqual({});
  expect(() => providerPayload({ planner: "local", export: "local,ai-hub" }, {}, report)).toThrow();
});
