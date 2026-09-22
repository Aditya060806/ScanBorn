import React, { useEffect, useRef, useState } from "react";

import * as api from "./api.js";

/**
 * Plain language in, task graph out — the planner the console's Plan stage uses, called
 * through POST /plan/preview so it works without a scanned room.
 *
 * Everything shown is the orchestrator's answer: the steps, the provider that produced them,
 * and whether that ran on this machine or called a network API.
 */
const PRESETS = [
  { label: "Go to the table", text: "Go to the table", lang: "en" },
  { label: "Take the box to the shelf", text: "Take the box to the shelf", lang: "en" },
  { label: "Inspect the door, then wait", text: "Inspect the door, then wait", lang: "en" },
  { label: "Say hello, then go to the chair", text: "Say hello, then go to the chair", lang: "en" },
  { label: "Hindi · मेज़ पर जाओ", text: "मेज़ पर जाओ", lang: "hi" },
];

const DEVANAGARI = /[ऀ-ॿ]/;

function Result({ state }) {
  if (state.status === "planning") return <p className="nlp-note">Planning…</p>;

  if (state.status === "error") {
    // A provider refusal (503 configuration, 502 exhausted) comes from a reachable orchestrator
    // with a coded detail; only a failed request or a bare 5xx means it is not answering.
    const down = state.error.status === 0 ||
      (state.error.status >= 500 && !state.error.detail?.code);
    return (
      <p className="nlp-note nlp-note--error">
        {down
          ? "The orchestrator isn't reachable. Start it with uvicorn orchestrator.service:app --port 8000, then try again."
          : state.error.message}
      </p>
    );
  }

  if (state.status !== "done") return null;

  if (state.nodes.length === 0) {
    return (
      <p className="nlp-note">
        {state.lang === "hi" && state.provider === "local"
          ? "No steps. The offline planner reads English only; Hindi needs a remote planner " +
            "(SCANBORN_PLANNER=groq on the orchestrator) or the phone app's on-device model."
          : "No steps. The planner answers only with navigate_to, pickup, place, inspect, wait " +
            "and speak, and found none of them in that sentence."}
      </p>
    );
  }

  return (
    <>
      {state.planStatus === "clarification_needed" && (
        // The planner asks rather than guesses: a missing, absent or shared target is a question.
        <p className="nlp-note">
          Needs clarification: {state.problems.join("; ")}. Nothing would be stored or driven.
        </p>
      )}
      <div className="nlp-graph-flow">
        {state.nodes.map((node, i) => (
          <React.Fragment key={`${node.action}-${i}`}>
            <div className="nlp-graph-node" style={{ animationDelay: `${i * 60}ms` }}>
              <span className="nlp-graph-node__action">{node.action}</span>
              <span className="nlp-graph-node__target">
                {node.target || node.params?.text || "—"}
              </span>
            </div>
            {i < state.nodes.length - 1 && <span className="nlp-arrow">→</span>}
          </React.Fragment>
        ))}
      </div>
    </>
  );
}

export default function NLPPlayground() {
  const [text, setText] = useState(PRESETS[0].text);
  const [lang, setLang] = useState(PRESETS[0].lang);
  const [state, setState] = useState({ status: "idle" });
  // Only the newest request may write the result; a slow earlier answer must not overwrite it.
  const latest = useRef(0);

  const plan = async (nextText, nextLang) => {
    if (!nextText.trim()) return;
    const request = ++latest.current;
    setState({ status: "planning" });
    try {
      const result = await api.planPreview(nextText, nextLang);
      if (request !== latest.current) return;
      setState({
        status: "done",
        planStatus: result.status ?? "planned",
        problems: result.problems ?? [],
        nodes: JSON.parse(result.graph_json).nodes ?? [],
        provider: result.provider,
        location: result.location ?? (result.on_device ? "orchestrator" : "remote"),
        fallback: result.provenance?.fallback_reason ?? null,
        lang: nextLang,
      });
    } catch (error) {
      if (request === latest.current) setState({ status: "error", error });
    }
  };

  useEffect(() => {
    plan(PRESETS[0].text, PRESETS[0].lang);
  }, []);

  const choose = (preset) => {
    setText(preset.text);
    setLang(preset.lang);
    plan(preset.text, preset.lang);
  };

  const edit = (value) => {
    setText(value);
    setLang(DEVANAGARI.test(value) ? "hi" : "en");
  };

  return (
    <div className="nlp-playground" data-reveal>
      <div className="nlp-header">
        <div>
          <h3 className="nlp-title">Plan a task in plain language</h3>
          <p className="hint">
            The console's planner, called without a scanned room. It can only answer with six
            actions: navigate_to, pickup, place, inspect, wait and speak.
          </p>
        </div>
        {state.status === "done" && (
          <div className="nlp-provider-badge"
               title={state.fallback ? `Fell back: ${state.fallback}` : undefined}>
            {state.provider} · {state.location === "remote" ? "remote API" : "on this machine"}
            {state.fallback && " · after fallback"}
          </div>
        )}
      </div>

      <div className="nlp-prompt-chips">
        {PRESETS.map((preset) => (
          <button
            key={preset.label}
            type="button"
            className={`nlp-chip ${text === preset.text ? "is-active" : ""}`}
            onClick={() => choose(preset)}
          >
            {preset.label}
          </button>
        ))}
      </div>

      <form
        className="nlp-input-row"
        onSubmit={(event) => {
          event.preventDefault();
          plan(text, lang);
        }}
      >
        <input
          type="text"
          className="nlp-input"
          value={text}
          onChange={(event) => edit(event.target.value)}
          placeholder="Type an instruction, e.g. take the box to the shelf"
          aria-label="Task in plain language"
        />
        <button
          type="submit"
          className="btn btn--solid"
          disabled={state.status === "planning" || !text.trim()}
        >
          Plan ↵
        </button>
      </form>

      <div className="nlp-graph-canvas" aria-live="polite">
        <Result state={state} />
      </div>
    </div>
  );
}
