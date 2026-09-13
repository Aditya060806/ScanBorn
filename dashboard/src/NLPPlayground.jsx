import React, { useState, useEffect } from "react";

const PROMPT_PRESETS = [
  {
    label: "Dock & Pallet Transfer",
    prompt: "Pick package from docking pallet and place on sorting table",
    lang: "en",
    provider: "FunctionGemma · On-Device NPU",
  },
  {
    label: "Cargo Perimeter Patrol",
    prompt: "Inspect perimeter for fallen cargo and clear path",
    lang: "en",
    provider: "FunctionGemma · On-Device NPU",
  },
  {
    label: "Charging Optical Align",
    prompt: "Navigate to charging dock and perform optical self-calibration",
    lang: "en",
    provider: "FunctionGemma · On-Device NPU",
  },
  {
    label: "Hindi · मेज से डिब्बा उठाओ",
    prompt: "मेज से डिब्बा उठाओ और रैक पर रखो",
    lang: "hi",
    provider: "Sarvam AI · Indic Edge VLM",
  },
  {
    label: "Hindi · कमरे का निरीक्षण",
    prompt: "कमरे का निरीक्षण करो और बाधाओं से बचो",
    lang: "hi",
    provider: "Sarvam AI · Indic Edge VLM",
  },
];

function parsePromptToGraph(text) {
  const lower = text.toLowerCase();
  const nodes = [];

  // Always start with navigation/datum
  nodes.push({
    action: "NAVIGATE",
    target: "Starting Datum (0.0, 0.0)",
    constraint: "Hexagon NPU Localizer · 60Hz",
  });

  if (lower.includes("pick") || lower.includes("उठाओ") || lower.includes("cargo") || lower.includes("package")) {
    nodes.push({
      action: "PERCEIVE_6DOF",
      target: lower.includes("cargo") ? "Fallen Cargo BBox" : "Target Package",
      constraint: "YOLO-World INT8 · IoU > 0.85",
    });
    nodes.push({
      action: "GRASP_PICK",
      target: "Manipulator Gripper",
      constraint: "Max Torque < 14N · Slip Guard",
    });
  }

  if (lower.includes("inspect") || lower.includes("निरीक्षण") || lower.includes("patrol")) {
    nodes.push({
      action: "SWEEP_LIDAR",
      target: "360° Safety Envelope",
      constraint: "LiDAR 20Hz · Range 12m",
    });
    nodes.push({
      action: "SEGMENT_OBSTACLES",
      target: "Unmapped Clutter",
      constraint: "MobileSAM · Zero-Shot Mask",
    });
  }

  if (lower.includes("place") || lower.includes("रखो") || lower.includes("table") || lower.includes("रैक")) {
    nodes.push({
      action: "ALIGN_SURFACE",
      target: lower.includes("रैक") ? "Secondary Rack 02" : "Sorting Table Plane",
      constraint: "Depth Normal Z ± 1.5°",
    });
    nodes.push({
      action: "RELEASE_PLACE",
      target: "Contact Damping",
      constraint: "Force Feedback Verified",
    });
  }

  if (lower.includes("dock") || lower.includes("charg") || lower.includes("calibrat")) {
    nodes.push({
      action: "OPTICAL_DOCK",
      target: "Inductive Pad Alpha",
      constraint: "AprilTag Fiducial ± 2mm",
    });
  }

  // Final verification node
  nodes.push({
    action: "SIM_GATE_VERIFY",
    target: "MuJoCo Kinematics Check",
    constraint: "Confidence Score ≥ 0.88",
  });

  return nodes;
}

export default function NLPPlayground() {
  const [selectedPrompt, setSelectedPrompt] = useState(PROMPT_PRESETS[0].prompt);
  const [inputVal, setInputVal] = useState(PROMPT_PRESETS[0].prompt);
  const [provider, setProvider] = useState(PROMPT_PRESETS[0].provider);
  const [isCompiling, setIsCompiling] = useState(false);
  const [graphNodes, setGraphNodes] = useState([]);
  const [activeNodeIndex, setActiveNodeIndex] = useState(0);

  useEffect(() => {
    setIsCompiling(true);
    const timer = setTimeout(() => {
      setGraphNodes(parsePromptToGraph(inputVal));
      setIsCompiling(false);
      setActiveNodeIndex(0);
    }, 280);

    return () => clearTimeout(timer);
  }, [inputVal]);

  const handleSelectPreset = (preset) => {
    setSelectedPrompt(preset.prompt);
    setInputVal(preset.prompt);
    setProvider(preset.provider);
  };

  return (
    <div className="nlp-playground" data-reveal>
      <div className="nlp-header">
        <div>
          <h3 className="nlp-title">07 · Natural Language Policy Compiler</h3>
          <p className="hint">
            Prompt-to-DAG conversion on the edge — runs zero-shot task decomposition without cloud roundtrips.
          </p>
        </div>
        <div className="nlp-provider-badge">
          {provider}
        </div>
      </div>

      <div className="nlp-prompt-chips">
        {PROMPT_PRESETS.map((p) => (
          <button
            key={p.label}
            type="button"
            className={`nlp-chip ${selectedPrompt === p.prompt ? "is-active" : ""}`}
            onClick={() => handleSelectPreset(p)}
          >
            {p.label}
          </button>
        ))}
      </div>

      <div className="nlp-input-row">
        <input
          type="text"
          className="nlp-input"
          value={inputVal}
          onChange={(e) => setInputVal(e.target.value)}
          placeholder="Type an autonomous AMR instruction (English or Hindi)..."
          aria-label="Natural language task prompt"
        />
        <button
          type="button"
          className="btn btn--solid"
          onClick={() => {
            setIsCompiling(true);
            setTimeout(() => {
              setGraphNodes(parsePromptToGraph(inputVal));
              setIsCompiling(false);
            }, 240);
          }}
        >
          Compile DAG ↵
        </button>
      </div>

      <div className="nlp-graph-canvas">
        {isCompiling ? (
          <div style={{ padding: "2rem", textAlign: "center", color: "var(--muted)", fontFamily: "var(--mono)", fontSize: "var(--ui-sm)" }}>
            Tokenizing on NPU tensor cores
            <span className="nlp-cursor" />
          </div>
        ) : (
          <div className="nlp-graph-flow">
            {graphNodes.map((node, i) => (
              <React.Fragment key={node.action + i}>
                <div
                  className={`nlp-graph-node ${activeNodeIndex === i ? "active" : ""}`}
                  onClick={() => setActiveNodeIndex(i)}
                  style={{ animationDelay: `${i * 60}ms`, cursor: "pointer" }}
                >
                  <span className="nlp-graph-node__action">{node.action}</span>
                  <span className="nlp-graph-node__target">{node.target}</span>
                  <span className="nlp-graph-node__constraint">{node.constraint}</span>
                </div>
                {i < graphNodes.length - 1 && <span className="nlp-arrow">→</span>}
              </React.Fragment>
            ))}
          </div>
        )}
      </div>

      {graphNodes[activeNodeIndex] && (
        <div style={{ marginTop: "1rem", display: "flex", gap: "1rem", flexWrap: "wrap", fontSize: "var(--ui-sm)", fontFamily: "var(--mono)", color: "var(--muted)" }}>
          <span>NODE [{activeNodeIndex + 1}/{graphNodes.length}]: <strong style={{ color: "var(--graphite)" }}>{graphNodes[activeNodeIndex].action}</strong></span>
          <span>TARGET: <strong style={{ color: "var(--near-ink)" }}>{graphNodes[activeNodeIndex].target}</strong></span>
          <span>SAFETY CONSTRAINT: <strong style={{ color: "var(--mint-ink)" }}>{graphNodes[activeNodeIndex].constraint}</strong></span>
        </div>
      )}
    </div>
  );
}
