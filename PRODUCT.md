# Product

## Register

brand

## Users

Engineers and technical evaluators deciding whether the pipeline is real and worth adopting
or contributing to (it began at the Snapdragon Multiverse Hackathon, July 2026). They
land on `/` cold, scanning for "is this real and does it work end to end" in under a
minute, then either open `/dashboard` (the console, a product-register tool for driving
the actual pipeline) or go read the source. Low patience for marketing fluff, high
tolerance for technical density (endpoint signatures, return shapes, thresholds).

## Product Purpose

ScanBorn turns a phone scan of a room into a twin with a map of where a small two-wheel robot
fits, trains the robot's route-following controller in a simulation of that robot, and lets it
drive only after it passes the simulation gate. Everything runs on a laptop CPU and a Galaxy S24 FE
(no NPU, no cloud service in the loop); the robot is an ESP32 rover that runs the control loop
itself. The loop — capture, reconstruct, segment, generate twin, mission, (plan,) train, optimize,
deploy — is one REST API and one Python SDK.

The landing page (`Landing.jsx`) is the primary surface — it exists to prove the nine-call
pipeline is a real, running system, not a slide deck, and to say plainly which parts have not
yet run on real hardware. Success is a judge
or engineer believing the pipeline works and clicking through to the console or the repo.

## Brand Personality

An instrument, not a costume. Precise, technical, quietly confident — the copy states
mechanism (endpoint signatures, thresholds, file:line-style specifics) instead of
adjectives. Color is diagnostic (a depth-sensor colormap: far=indigo, mid=teal, near=hot),
spent only where the system is actively sensing or working, never as decoration. Motion
should read as instrumentation coming online (a sensor calibrating, a scan sweeping) rather
than a marketing site's entrance choreography.

## Anti-references

Generic SaaS/startup landing-page grammar: gradient-mesh heroes, cream/pastel palettes,
bouncy/elastic motion, rounded-everything cards, hero-metric-with-gradient-text blocks,
tiny uppercase tracked eyebrows over every section, identical icon+heading+text card
grids. If it could be any startup's homepage with the copy swapped, it's wrong for this
brand.

## Design Principles

- **Color carries state, not decoration.** The near/hot end of the depth ramp is spent
  only where the system is actively sensing or computing right now.
- **Mechanism over adjective.** Prefer a concrete endpoint signature, threshold, or file
  reference to a claim like "powerful" or "seamless."
- **The order is enforced by data, not by the interface.** Sequence, gating, and state
  (e.g. `MISSION_GATE`: 27 of 30 noisy runs within 10 cm) should be shown as real constraints, not decorative
  progress bars.
- **Instrument, not costume.** One type family (Archivo, contrasting on its width axis)
  plus JetBrains Mono for anything the machine measured — no serif "for warmth," no second
  display face.
- **Motion reads as calibration.** Reveals and transitions should feel like hardware
  coming online (scanning, settling, locking on) rather than generic entrance animation.

## Accessibility & Inclusion

WCAG AA. `prefers-reduced-motion: reduce` must fully no-op every animation (already the
pattern in `useReveal.js` and `Preloader.jsx` — extend it, don't bypass it). Maintain
4.5:1 contrast for body/placeholder text and 3:1 for large text; the existing `-ink`
color pairs (`--near-ink`, `--mint-ink`) exist specifically because the raw ramp colors
fail small-text contrast — reuse them rather than the raw `--near`/`--mid` for text.
