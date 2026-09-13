import React, { useEffect, useRef, useState } from "react";
import gsap from "gsap";

import { CLOUD } from "./recon.js";

/**
 * Sensor depth calibration preloader.
 * Clean, unified, telemetry instrument style with zero visual clutter.
 */
export default function Preloader() {
  const rootRef = useRef(null);
  const fillRef = useRef(null);
  const countRef = useRef(null);
  const percentRef = useRef(null);
  const [gone, setGone] = useState(false);

  useEffect(() => {
    const total = CLOUD.length;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    if (reduced) {
      if (countRef.current) countRef.current.textContent = total.toLocaleString();
      if (percentRef.current) percentRef.current.textContent = "100%";
      setGone(true);
      return;
    }

    const state = { count: 0, progress: 0 };
    const tl = gsap.timeline({ onComplete: () => setGone(true) });

    tl.to(
      state,
      {
        count: total,
        progress: 100,
        duration: 1.4,
        ease: "power2.out",
        onUpdate: () => {
          if (countRef.current) {
            countRef.current.textContent = Math.round(state.count).toLocaleString();
          }
          if (percentRef.current) {
            percentRef.current.textContent = `${Math.round(state.progress)}%`;
          }
          if (fillRef.current) {
            fillRef.current.style.width = `${state.progress}%`;
          }
        },
      },
      0
    ).to(rootRef.current, { yPercent: -101, duration: 0.65, ease: "expo.inOut" }, 1.5);

    // The page must never stay covered because the animation was interrupted.
    const bail = setTimeout(() => {
      tl.progress(1);
      setGone(true);
    }, 3500);

    return () => {
      clearTimeout(bail);
      tl.kill();
    };
  }, []);

  if (gone) return null;

  return (
    <div className="preload" ref={rootRef} aria-hidden="true">
      <div className="preload__body">
        <div className="preload__header">
          <span className="preload__eyebrow">Calibrating depth sensor</span>
          <span className="preload__status">
            <span className="preload__dot" />
            Active
          </span>
        </div>

        <div className="preload__track">
          <div className="preload__fill" ref={fillRef} />
          <div className="preload__laser" />
        </div>

        <div className="preload__readout">
          <span className="preload__count">
            <strong ref={countRef}>0</strong> / {CLOUD.length.toLocaleString()} pts acquired
          </span>
          <span className="preload__percent" ref={percentRef}>
            0%
          </span>
        </div>

        <div className="preload__sub">· SYSTEM INIT · 60 FPS SIM GATE READY</div>
      </div>
    </div>
  );
}

