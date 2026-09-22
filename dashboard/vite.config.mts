import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const ORCHESTRATOR = process.env.ORCHESTRATOR_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Localhost only by default. This Vite dev server has known advisories (`npm audit`) that
    // let a client read files through it, so listening on the LAN is opt-in — for when the
    // phone needs to open the dashboard:
    //   PowerShell:  $env:DASHBOARD_LAN = "1"; npm run dev
    //   bash:        DASHBOARD_LAN=1 npm run dev
    host: process.env.DASHBOARD_LAN === "1",
    // Same-origin in the browser, so the orchestrator needs no CORS config. Keys are path
    // prefixes: "/plan" also covers "/plan/preview".
    proxy: Object.fromEntries(
      ["/meshes", "/scans", "/health", "/capture", "/reconstruct", "/segment", "/generate-twin", "/twins", "/plan",
       "/missions", "/robot-specs", "/train", "/optimize", "/deploy", "/sync", "/status",
       "/benchmarks", "/robots", "/deployments", "/providers", "/stages"]
        .map((route) => [route, { target: ORCHESTRATOR }])
        .concat([
          ["/ws/status", { target: ORCHESTRATOR, ws: true }],
          ["/ws/deployments", { target: ORCHESTRATOR, ws: true }],
        ]),
    ),
  },
});
