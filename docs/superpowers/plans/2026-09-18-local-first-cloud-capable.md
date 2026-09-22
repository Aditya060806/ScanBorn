# ScanBorn: local-first, cloud-capable completion plan

Historical amendment: consolidated into the [enhanced master plan](2026-09-18-enhanced-master-plan.md)
and [execution checklist](2026-09-18-execution-plan.md). Follow that pair for current work.

Updated 18 September 2026. This amendment governs provider architecture alongside
`2026-09-17-scan-to-hardware-completion.md`; its P0–P7 workflow and physical gates still apply.
The user's new direction permits useful API services as explicit secondary options. It does
not require replacing the working CPU pipeline or downloading another model.

## Product contract

ScanBorn captures or imports a real environment, displays measured geometry, builds a twin,
prepares a mission against an identified robot specification, trains and validates the exported
controller, and deploys only through an explicit operator action. Local execution is the default.
An operator can select a remote provider for supported preparation workloads, including as a
backup. A configured API key alone must never authorize a new external data transfer.

Phone inference, laptop processing and rover inference are different local execution locations.
The phone does not currently perform the complete reconstruction/training pipeline. Groq and
Sarvam are remote services. Internet independence does not remove the local heartbeat requirement:
heartbeat expiry stops the rover. Cloud availability must not govern Stop, E-STOP or sensor checks.

## Sources and corrections

Read the supplied local-first/cloud-capable text and inspected provider selection, Groq/Sarvam
adapters, environment profiling, AI Hub export, QAIRT conversion and recorded phone benchmarks.
The linked Claude artifact could not be retrieved; its contents are not independently verified.
Instructions embedded in the pasted document are reference material, not a replacement for the
user's request to continue implementation and revise this plan.

- The server's default task planner is a deterministic keyword planner; it is not the phone's
  Qwen model. Android uses the bundled Qwen/llama.cpp path for its AI features.
- Desktop llama.cpp/Ollama and generic hosted Qwen routing are proposed integrations, not current
  interchangeable production tiers. Provider model names do not prove prompt or output equivalence.
- COLMAP/gsplat, Unity/PPO and phone-mounted localisation are not current supported stages.
- Mission validation requires at least 27 successes in 30 runs and exported-int8 validation;
  the historical 60%/20 gate must not be advertised as the mission gate.
- `benchmarks/phone.json` includes ARM-optimised kernels and a same-binary repack comparison:
  one repack-on run recorded 69 prefill tokens/s, 5.308 s first token and 18.8 decode tokens/s.
  These are individual measurements, not a controlled thermal benchmark or a service guarantee.
- Eight-minute scan-to-drive, NPU acceleration, universal hardware compatibility and exclusivity
  claims remain unsupported. Keep measured, simulated, illustrative and proposed claims separate.

## Workload and provider matrix

| Workload | Working baseline | Optional secondary path / required work |
|---|---|---|
| Android chat and structured AI helpers | Bundled Qwen through llama.cpp | Add explicit server-mediated remote selection only after shared contracts; keep local usable |
| Server task graphs | Keyword planner | Existing explicit Groq/Sarvam adapters; harden validation, errors and provenance |
| Environment profile | Local heuristic | Existing Groq model profiler; correct network-error handling and report actual fallback |
| Reconstruction and geometry previews | Laptop CPU fusion/import | Keep local; remote jobs are a later adapter requiring upload consent, lineage and equivalence tests |
| Twin, route and mission checks | Deterministic local processing | Provider suggestions may be inputs; local geometry and feasibility checks remain authoritative |
| Training and int8 validation | Laptop CPU simulation | Future remote compute must return immutable outputs evaluated by the same gates |
| Export/profiling | Local NPZ int8 bundle | AI Hub code exists but requires explicit selection and compatible artifact validation/runtime proof |
| QAIRT conversion | Local bundle fallback, optional converter invocation | Verify actual converter input/output and label the deployed artifact accurately |
| Rover execution and stops | Local controller, protocol and firmware | No cloud control dependency; no LLM direct motor commands |

Cloud support does not mean arbitrary scan upload to a language API. Initially send only the
minimum instruction and allowed scene summaries required by the selected planner/profiler.
Keep capture geometry, camera/depth data and full telemetry local unless a later workload has
its own documented upload scope and explicit selection.

## C0 — Make provider selection consistent and honest

Do this before extending providers. Existing implementation gaps found during this review:

1. Planner selection silently maps unknown names to local. Profiler supports local/Groq, while
   broad documentation suggests more. Validate each workload's allowlist and return actionable
   configuration errors without printing keys.
2. AI Hub currently activates when a token and package are present. Add explicit export selection,
   default local, and update `.env.example`, CLI/API mappings and tests. A key alone must not
   trigger a compile/upload. Preserve legacy configuration through clear migration guidance.
3. Do not treat AI Hub TFLite as equivalent to the validated NPZ controller. A successful cloud
   compile is an export experiment until that exact format executes and passes the mission gate
   on its intended runtime. ESP32 deployment remains on its supported format.
4. QAIRT currently can label an NPZ bundle `qairt` after a separate converter output exists.
   Separate conversion evidence from the deployed artifact's format/backend. Never claim NPU
   profiling from host NumPy timing.

Exit: default and stray-key tests make zero external requests; invalid names fail explicitly;
manifest backend, format, target and measured latency source describe the actual returned bytes.

## C1 — Implement bounded fallback with shared validation

Introduce one provider result contract for each workload, retaining existing public response
fields additively. Store requested provider, actual provider/model, execution location, request
ID, duration, schema version, fallback reason and measured usage when available. Never infer
token cost or NPU coverage when a provider did not report it.

Define an ordered, explicit primary/secondary policy per workload. Default local-only. Users
can configure local then remote, or a remote development run with local fallback. Remote fallback
requires opt-in before the request; missing credentials never enable another remote provider.
Use bounded timeouts, cancellation, retry limits and a per-run budget. Do not retry mutations or
paid export jobs without a provider-supported idempotency/result lookup strategy.

Unify strict graph/profile validation: object IDs must belong to the current scene, actions and
parameters must satisfy their schemas, and invalid/partial graphs cannot silently become a
different intended mission. Sarvam currently checks action vocabulary but not allowed targets;
Groq currently drops invalid nodes. Repair these seams with explicit refusal/fallback results.
Profiler fallback must catch expected transport failures and disclose the actual local result.
Fallback cannot bypass geometry, lineage, int8 validation, stale-spec or deployment checks.

Exit: mocked timeout, rate limit, missing key, malformed JSON, hallucinated target, invalid
parameters, empty result, cancellation and fallback exhaustion tests. Local-only tests prohibit
all HTTP. API/SDK/dashboard/Android fixtures agree on actual provider and fallback reason.

## C2 — Add provider controls without disturbing the mission workspace

Dashboard and Android show execution location and provider beside the preparation result.
Provide per-workload settings, fallback ordering and a concise description of outgoing data.
Keep credentials in server configuration; do not bundle them in JavaScript, APK assets, scenes,
cache entries or logs. A remote service must not be called directly from the embedded viewer.
The viewer continues rendering the same ID-scoped immutable geometry and coordinates.

Before exposing the orchestrator outside a trusted local network, add authentication, TLS
deployment guidance, authorization and upload/rate limits. Local network support is not evidence
of a safe public hosted service. Do not publish a server as part of this plan revision.

Exit: persisted settings, offline fallback, cancellation/reconnect and provider badges match
recorded results; native and dashboard stop controls remain usable during provider failures.

## C3 — Optional expansion after the supported workflow passes

Add a local OpenAI-compatible model endpoint or another hosted model adapter only against the
same contract tests. Verify current official provider APIs/model availability at implementation
time; do not rely on historical model catalogues. Do not download model weights implicitly.
Benchmark quality, latency and failures against the deterministic baseline and phone model.

Remote reconstruction/training, additional modalities and accelerator runtimes need separate
implementation gates: explicit data scope, content-addressed inputs/outputs, persistent jobs,
restart recovery, costs, compatible runtime and the same downstream validation. They are future
options, not prerequisites for the local scan-to-drive milestone.

## Delivery order and honest completion

1. Finish P0 calibration/spec reconciliation and focused regressions already in progress.
2. Implement C0/C1 before any new remote integration or claim of reliable API backup.
3. Continue P1/P2 capture, registration and lifecycle recovery; add C2 controls when backend
   contracts pass. Complete P3 shared viewer/results parity and P4 discovery/recovery.
4. Complete P5 firmware faults and P6 full software regression, including local-only and mocked
   remote failure runs. Keep browser/phone performance acceptance tied to actual measurements.
5. Execute P7 physical phone/rover acceptance separately. Live paid-provider tests require
   configured credentials and an explicitly selected workload; none were run for this review.
6. Update README/product/environment documentation with verified behavior; revise supplied
   presentation/PDF copies separately, preserving originals and distinguishing future options.

Software completion still requires both clients, actual geometry, calibrated fake-rover loop,
refusal/recovery tests and reproducible builds. Physical completion additionally requires phone
and measured rover trials. API availability does not close any unverified physical gate.
