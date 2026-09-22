# README visual assets

Captured 22 September 2026 from the current ScanBorn Vite application, using a temporary headless Chrome profile at 1440 × 1000 CSS pixels (430 × 932 for the responsive image). Screenshots are original application output, not generated mockups.

| File | Source / state |
|---|---|
| website-overview.png | `/`; current landing animation, illustrative procedural geometry |
| workspace-3d.png | `/dashboard`; Load offline fixture → Fit room |
| reference-rover.png | Same fixture → Inspect rover; canvas close-up of `dashboard/shared-viewer/rover.js` |
| rover-inspection.png | Same view including controls, scale and provenance |
| mission-workspace.png | Same fixture → Mission map; recorded simulation outcomes |
| responsive-console.png | Same console at 430 CSS pixels; website, not Android |

The fixture is `dashboard/src/fixtures.json`, recorded 20 September 2026 from real in-process orchestrator responses on synthetic geometry. It contains 16,384 reconstructed points and a recorded 29/30 mission simulation result. No physical scan/drive or fresh accuracy benchmark is represented. The server was intentionally absent; the offline fixture's unreachable status is preserved rather than concealed.

Rover cosmetic hardware is illustrative. Motion-related dimensions follow the reference spec, which remains placeholder/assumed until physical calibration. The screenshot is not a hardware photograph, wiring diagram or fabrication-ready CAD export.

The SVG charts use `benchmarks/phone.json`, `benchmarks/renderer.json` and calculated coefficient representation sizes from `policy/finetune/train_bc.py`. Numeric controller payload is 48 float64 bytes versus 6 int8 bytes plus 16 scale bytes, excluding container/manifest overhead. They do not establish full-file compression or runtime speedup.

Reproduce manually: start the dashboard with `npm run dev`, open the listed routes, load the offline fixture and select the listed views. Capture at the stated viewport after geometry loads. Current capture produced no browser console/runtime errors. Website screenshots are presentation evidence, not performance acceptance.
