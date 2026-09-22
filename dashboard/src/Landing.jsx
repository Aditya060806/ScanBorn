import React, { useEffect, useRef } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

import Preloader from "./Preloader.jsx";
import PipelineNav from "./PipelineNav.jsx";
import ReconCanvas from "./ReconCanvas.jsx";
import NLPPlayground from "./NLPPlayground.jsx";
import { Link } from "./router.jsx";
import { useReveal } from "./useReveal.js";

gsap.registerPlugin(ScrollTrigger);

const STEPS = [
  ["01", "Capture",
   "ARCore raw depth from the ScanBorn app, registered to a printed marker on the floor and uploaded frame by frame — or one finished .ply or .obj from Scaniverse. The app's scan is built but has not yet run on the phone.",
   "POST /capture → scan_id, frame_count"],
  ["02", "Reconstruct",
   "The depth frames fuse into one point cloud on the laptop's CPU, keeping only surfaces at least two frames saw (a one-frame scan keeps everything). An imported scan passes straight through.",
   "POST /reconstruct → mesh_id, point_count, fusion"],
  ["03", "Segment",
   "Geometry first — the floor splits off as a slab, the rest clusters by voxel connectivity and is labelled by shape. No weights to download, which is what keeps the stage offline.",
   "POST /segment → objects_id, objects[]"],
  ["04", "Generate twin",
   "The point cloud becomes a free / blocked / unknown grid sized for the robot, at 5 or 10 cm depending on how densely the floor was scanned. Never-seen floor is not drivable.",
   "POST /generate-twin → twin_id, profile, resolution"],
  ["05", "Mission",
   "Pick a start and a goal on the map. The planner keeps the robot's clearance from everything blocked or unknown, and names the reason when there is no route.",
   "POST /missions → mission_id, path, length_m"],
  ["06", "Plan",
   "Optional: type the task instead. The offline planner turns it into steps over six actions on this machine; a network planner is used only when one is named.",
   "POST /plan → task_graph_id, provider, on_device"],
  ["07", "Train",
   "Behaviour cloning of a pure-pursuit controller, then 30 runs of the mission in a model of the two-wheel rover with slip, gyro drift, latency and placement error.",
   "POST /train → policy_id, sim_success_rate, mission_metrics"],
  ["08", "Optimize",
   "The policy is quantized to int8 and timed on the host CPU. Qualcomm AI Hub is used only when a token is configured, and the response says which path ran.",
   "POST /optimize → artifact_id, backend, est_latency"],
  ["09", "Deploy",
   "To the simulated robot, which returns the route it drove; or to a rover over the rover protocol — the fake rover today, the ESP32 once it is built — streamed live with an E-STOP.",
   "POST /deploy → deployment_id, pose_trace | live"],
];

const TIERS = [
  {
    role: "Phone",
    body: "ARCore depth capture (built, not yet run on the phone), and a small language model running on the phone's own CPU.",
    specs: [
      ["Device", "Galaxy S24 FE · Exynos 2400e"],
      ["Model", "Qwen2.5-1.5B Q4_K_M"],
      ["Measured", "5.3 s to first token, 359-token prompt"],
      ["Network", "Not needed for the model"],
    ],
  },
  {
    role: "Laptop",
    body: "Reconstruction, segmentation, twin generation, route planning, simulation and training — all on the CPU.",
    specs: [
      ["Machine", "Lenovo LOQ · i5-13450HX"],
      ["Twin", "Navmesh grid + scene manifest"],
      ["Route", "p95 37 ms, 5 cm grid, synthetic 12 × 10 m room"],
      ["GPU", "Not required"],
    ],
  },
  {
    role: "Robot",
    body: "The rover runs the int8 policy's control loop on its own microcontroller; the laptop never steers it.",
    specs: [
      ["Target", "ESP32 two-wheel rover"],
      ["Policy", "Int8 linear controller"],
      ["Gate", "27 of 30 noisy runs within 10 cm"],
      ["Status", "Firmware compiles and passes its self-test in an emulator; not yet on a board"],
    ],
  },
];

const OPERATOR_STEPS = [
  ["01", "Scan the room", "Tape the printed marker to the floor, stand on it pointing along its arrow, and scan with the ScanBorn app — or export a .ply or .obj from Scaniverse.", "App: Tools → Scan a space"],
  ["02", "Open the scan", "In the console, drop the Scaniverse file or type the scan id the app uploaded.", "Console: Capture"],
  ["03", "Check the geometry", "Reconstruct and segment, then look at the floor and the labelled clusters in the 3D view.", "Console: Reconstruct → Segment"],
  ["04", "Build the twin", "Name the environment or let the profiler read the scan; the grid size and its evidence are shown.", "Console: Generate twin"],
  ["05", "Set the mission", "Click a goal on the map (the start defaults to the marker when the scan has one); alt-click adds a point the route must pass.", "Console: Mission"],
  ["06", "Train and optimize", "The policy must reach the goal in 27 of 30 noisy runs; otherwise the console shows why it was refused.", "Console: Train → Optimize"],
  ["07", "Deploy", "Run it on the simulated robot or a fake rover now; the ESP32 rover when it exists. Space is E-STOP.", "Console: Deploy"],
];

export default function Landing() {
  const pipelineRef = useReveal();
  const gateRef = useReveal();
  const edgeRef = useReveal();
  const startRef = useReveal();
  const workflowRef = useReveal();
  const meterFillRef = useRef(null);
  const gateScoreRef = useRef(null);

  // The reading sweeps in once the gate section is reached — an instrument taking a
  // measurement, not a decorative bar filling up. useReveal already handles the section's
  // fade-in; this is the one earned moment layered on top of it.
  useEffect(() => {
    const fill = meterFillRef.current;
    if (!fill) return;

    const mm = gsap.matchMedia();
    mm.add("(prefers-reduced-motion: no-preference)", () => {
      gsap.from(fill, {
        scaleX: 0,
        duration: 0.9,
        ease: "power2.out",
        scrollTrigger: { trigger: fill, start: "top 85%" },
      });

      const obj = { val: 0.0 };
      gsap.to(obj, {
        val: 0.90,
        duration: 1.1,
        ease: "power2.out",
        scrollTrigger: { trigger: fill, start: "top 85%" },
        onUpdate: () => {
          if (gateScoreRef.current) {
            gateScoreRef.current.textContent = obj.val.toFixed(2);
          }
        },
      });
    });

    return () => mm.revert();
  }, []);

  return (
    <>
      <Preloader />

      <PipelineNav />

      <ReconCanvas />

      <section className="band" id="stage-pipeline" ref={pipelineRef}>
        <aside className="notes" data-reveal>
          <p>Z is up, metres.</p>
          <p>Each step consumes the id the step before it returned.</p>
          <p>One REST surface, one Python SDK.</p>
        </aside>

        <div className="band__body">
          <h2 className="title" data-reveal>Nine calls, scan to robot.</h2>
          <p className="prose" data-reveal>
            The order is not a diagram. Every stage takes the identifier the last one
            returned, so the sequence is enforced by the data, not by the interface.
          </p>

          <ol className="steps">
            {STEPS.map(([n, name, body, sig]) => (
              <li className="step" key={n} data-reveal>
                <span className="step__n">{n}</span>
                <div className="step__body">
                  <h3>{name}</h3>
                  <p>{body}</p>
                  <code>{sig}</code>
                </div>
              </li>
            ))}
          </ol>

          <p className="steps__loop" data-reveal>
            <span className="steps__loopmark" aria-hidden="true">↺</span>
            <span>
              The room changes. <code>POST /sync</code> re-scans it and returns the voxel
              diff — added, removed, and which objects moved — so the twin follows the space
              instead of aging out of it.
            </span>
          </p>
        </div>
      </section>

      <section className="band band--gate" ref={gateRef}>
        <aside className="notes" data-reveal>
          <p>MISSION_GATE, policy/evaluate.py</p>
          <p>30 noisy runs, 10 cm goal</p>
          <p>HTTP 409 on failure</p>
        </aside>
        <div className="band__body">
          <h2 className="title" data-reveal>An unvalidated policy never reaches hardware.</h2>
          <p className="prose" data-reveal>
            <code>/train</code> drives the mission 30 times in the rover model, with the slip,
            drift, latency and placement error the robot's spec assumes, and refuses a policy
            that stops within 10 cm of the goal in fewer than 90 % of them. There is no
            override flag and no way to hand <code>/deploy</code> an artifact that skipped it;
            a mission the room has since blocked is refused too.
          </p>
          <div className="simgate" data-reveal>
            <div className="simgate__meter" aria-hidden="true">
              <i ref={meterFillRef} style={{ "--pass": 0.9 }} />
            </div>
            <p className="simgate__legend">
              <b ref={gateScoreRef}>0.90</b> of 30 noisy runs must reach the goal. Without a
              mission: 0.60 of 20 random starts.
            </p>
          </div>
        </div>
      </section>

      <section className="band" id="stage-nlp">
        <aside className="notes">
          <p>Six actions</p>
          <p>Targets from the scene</p>
          <p>Offline planner</p>
        </aside>
        <div className="band__body">
          <NLPPlayground />
        </div>
      </section>

      <section className="band" id="stage-silicon" ref={edgeRef}>
        <aside className="notes" data-reveal>
          <p>No cloud in the loop.</p>
          <p>No internet needed.</p>
        </aside>
        <div className="band__body">
          <h2 className="title" data-reveal>A phone and a laptop, end to end.</h2>
          <p className="prose" data-reveal>
            Every stage runs on hardware you already own, with nothing leaving the local
            network. A remote planner is an option you name, never a dependency.
          </p>
          <ul className="edge-tiers">
            {TIERS.map((tier) => (
              <li className="edge-tier" key={tier.role} data-reveal>
                <span className="edge-tier__role">{tier.role}</span>
                <p>{tier.body}</p>
                <dl className="tier-spec-list">
                  {tier.specs.map(([lbl, val]) => (
                    <React.Fragment key={lbl}>
                      <dt>{lbl}</dt>
                      <dd>{val}</dd>
                    </React.Fragment>
                  ))}
                </dl>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section className="band band--start" id="stage-action" ref={startRef}>
        <aside className="notes" data-reveal>
          <p>pip install -e .</p>
          <p>orchestrator :8000</p>
        </aside>
        <div className="band__body">
          <h2 className="title" data-reveal>The whole loop is one method.</h2>
          <pre className="code" data-reveal>
            <code>
              <span className="c-k">from</span> scanborn <span className="c-k">import</span> ScanBorn
              {"\n\n"}
              tf = ScanBorn(<span className="c-s">"http://localhost:8000"</span>)
              {"\n"}
              result = tf.run_pipeline(
              {"\n    "}frames,
              {"\n    "}text=<span className="c-s">"Take the box to the table"</span>,
              {"\n    "}mode=<span className="c-s">"fast"</span>,
              {"\n"})
              {"\n"}
              <span className="c-f">print</span>(result[<span className="c-s">"deployment_id"</span>])
            </code>
          </pre>

          <div className="cta" data-reveal>
            <Link className="btn btn--solid" to="/dashboard">
              Open the console
            </Link>
          </div>

          <p className="prose prose--small" data-reveal>
            Run it locally with <code>make install</code>, then{" "}
            <code>uvicorn orchestrator.service:app --port 8000</code>. The console drives the
            same endpoints, one stage at a time.
          </p>
        </div>
      </section>

      <section className="band" id="workflow" ref={workflowRef}>
        <aside className="notes" data-reveal>
          <p>docs/capture/home-marker.svg</p>
          <p>The marker is the map origin</p>
          <p>The same marker places the rover</p>
        </aside>
        <div className="band__body">
          <h2 className="title" data-reveal>From a room to a drive.</h2>
          <p className="prose" data-reveal>
            What an operator does, in order. The scan's origin is the marker, so a route drawn on
            the map starts where the rover will actually stand.
          </p>

          <ol className="steps">
            {OPERATOR_STEPS.map(([n, name, body, sig]) => (
              <li className="step" key={n} data-reveal>
                <span className="step__n">{n}</span>
                <div className="step__body">
                  <h3>{name}</h3>
                  <p>{body}</p>
                  <code>{sig}</code>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </section>
    </>
  );
}
