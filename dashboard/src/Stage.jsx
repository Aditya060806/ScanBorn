import React, { useEffect, useRef, useState } from "react";

function formatOut(text) {
  if (typeof text !== "string") return text;
  // Tokenize numbers, identifiers and key metrics for clear instrument readability
  const parts = text.split(/([0-9]+(?:\.[0-9]+)?(?:%|ms|pts|fps|MB)?|[a-z0-9_-]+_[0-9]+)/gi);
  return parts.map((part, i) => {
    if (/^[0-9]+(?:\.[0-9]+)?(?:%|ms|pts|fps|MB)?$/i.test(part)) {
      return <span key={i} className="out-token-num">{part}</span>;
    }
    if (/^[a-z0-9_-]+_[0-9]+$/i.test(part)) {
      return <span key={i} className="out-token-path">{part}</span>;
    }
    return part;
  });
}

/**
 * One rung of the pipeline. A stage stays locked until the stage before it has produced
 * the id it consumes, which is the same precondition the orchestrator enforces — better
 * to grey the button out than to let the operator earn a 404 mid-demo.
 */
export default function Stage({ n, name, action, ready, busy, locked, error, out, onRun, children }) {
  const state = busy ? "busy" : error ? "failed" : out ? "done" : ready ? "ready" : "waiting";
  const [duration, setDuration] = useState(null);
  const startTimeRef = useRef(null);

  useEffect(() => {
    if (busy) {
      startTimeRef.current = Date.now();
      setDuration(null);
    } else if (startTimeRef.current && out) {
      const elapsed = ((Date.now() - startTimeRef.current) / 1000).toFixed(1);
      setDuration(`${elapsed}s`);
      startTimeRef.current = null;
    }
  }, [busy, out]);

  return (
    <li className={`stage ${state}`}>
      <div className="marker">{state === "done" ? `✓` : n}</div>
      <div className="body">
        <div className="head">
          <h3>
            {name}
            {duration && <span className="stage-duration">({duration})</span>}
          </h3>
          {out && <span className="out">{formatOut(out)}</span>}
        </div>
        <div className="fields">{children}</div>
        <button onClick={onRun} disabled={!ready || locked}>
          {busy ? "Working…" : action}
        </button>
        {error && (
          <div className="error">
            <strong>{error.message}</strong>
            {/* The sim gate refuses with the numbers attached; show them. */}
            {typeof error.detail === "object" && error.detail !== null && (
              <dl>
                {Object.entries(error.detail)
                  .filter(([key]) => key !== "error")
                  .map(([key, value]) => (
                    <React.Fragment key={key}>
                      <dt>{key.replace(/_/g, " ")}</dt>
                      <dd>{String(value)}</dd>
                    </React.Fragment>
                  ))}
              </dl>
            )}
          </div>
        )}
      </div>
    </li>
  );
}

