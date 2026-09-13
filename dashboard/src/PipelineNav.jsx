import React, { useEffect, useState } from "react";

const PIPELINE_SECTIONS = [
  { id: "stage-recon", num: "01", label: "Recon & Scan" },
  { id: "stage-pipeline", num: "02-06", label: "Pipeline" },
  { id: "stage-nlp", num: "07", label: "NLP Planner" },
  { id: "stage-silicon", num: "08", label: "Silicon Tiers" },
  { id: "stage-action", num: "RUN", label: "Launch Console" },
];

export default function PipelineNav() {
  const [activeSection, setActiveSection] = useState("stage-recon");

  useEffect(() => {
    const handleScroll = () => {
      const scrollY = window.scrollY;
      const sections = PIPELINE_SECTIONS.map((s) => ({
        id: s.id,
        el: document.getElementById(s.id),
      })).filter((s) => s.el);

      for (let i = sections.length - 1; i >= 0; i--) {
        const top = sections[i].el.getBoundingClientRect().top;
        if (top <= 180) {
          setActiveSection(sections[i].id);
          break;
        }
      }
    };

    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  const scrollToSection = (id) => {
    const el = document.getElementById(id);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  };

  return (
    <nav className="pipeline-nav" aria-label="Pipeline Stages Quick Navigation">
      {PIPELINE_SECTIONS.map((s) => (
        <button
          key={s.id}
          type="button"
          className={`pipeline-pill ${activeSection === s.id ? "is-active" : ""}`}
          onClick={() => scrollToSection(s.id)}
        >
          <span className="pipeline-pill__num">{s.num}</span>
          <span>{s.label}</span>
        </button>
      ))}
    </nav>
  );
}
