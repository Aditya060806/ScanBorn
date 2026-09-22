"""End-to-end check of a live rover drive in a real browser, through Chrome's DevTools protocol.

It covers what the in-app browser pane cannot: a real WebGL context, so the 3D view's live rover is
checked by pixels, and trusted input through the browser's own input pipeline, so Space as E-STOP
is checked as a key press and the E-STOP button as a mouse click, not as synthesized DOM events.

The run: seed the tests' synthetic room → capture … optimize in the console → register the
in-process fake rover → drive 1 to `done` while the 3D view is sampled → drive 2 stopped by Space →
a deploy to the latched rover is refused → reset → drive 3 stopped by the button → reset.

Needs the orchestrator (:8000), the dashboard dev server (:5173) and Chrome or Edge. The browser
runs with its own temporary profile, so it never touches the user's browser or sessions.

    python scripts/check_live_drive.py [--browser PATH] [--out build/live_check] [--headed]

Prints a JSON report and writes screenshots to --out. Exits 0 only when every check passed.
"""
import argparse
import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "google-chrome", "chromium", "chromium-browser", "microsoft-edge",
]
LIVE_VIOLET = (123, 44, 191)   # RobotViewer3D COLORS.live and the map's .trace--real
START, GOAL = ("-1.2", "-1.2"), ("0.6", "0.6")  # a 2.55 m route in the synthetic room

# Helpers installed in the page. Stages are found by their heading; results are read from the DOM.
PAGE_HELPERS = r"""
window.__sb = {
  sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
  stage: (name) => [...document.querySelectorAll('.stage')]
    .find((s) => s.querySelector('h3')?.textContent.startsWith(name)),
  state(name) {
    const s = this.stage(name);
    return { stage: name, cls: s.className.replace('stage ', ''),
      out: s.querySelector('.out')?.textContent ?? null,
      error: s.querySelector('.error')?.innerText.replace(/\n+/g, ' | ') ?? null };
  },
  async until(test, ms) {
    const end = Date.now() + ms;
    while (Date.now() < end) { if (test()) return true; await this.sleep(100); }
    return false;
  },
  async run(name, ms) {
    this.stage(name).querySelector('.body > button').click();
    await this.sleep(300);
    await this.until(() => /done|failed/.test(this.stage(name).className), ms);
    return this.state(name);
  },
  type(input, value) {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  },
  choose(select, value) {
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(select, value);
    select.dispatchEvent(new Event('change', { bubbles: true }));
  },
  bar: () => document.querySelector('.live-bar')?.innerText.replace(/\n+/g, ' | ') ?? null,
  driving: () => Boolean(document.querySelector('.live-bar.is-driving')),
  poses() { return Number((this.bar() || '').match(/(\d+) reported pose/)?.[1] ?? 0); },
  robot() {
    return this.stage('Deploy').querySelector('.rover-register')?.innerText
      .replace(/\n+/g, ' | ') ?? null;
  },
  tab: (label) => [...document.querySelectorAll('.view-tab')]
    .find((b) => b.textContent.startsWith(label)).click(),
  canvas: () => document.querySelector('.scan-workspace__canvas canvas'),
};
true
"""


class Page:
    """One browser tab over the DevTools protocol."""

    def __init__(self, ws_url: str):
        import websocket

        self.ws = websocket.create_connection(ws_url, timeout=180, suppress_origin=True)
        self.next_id = 0
        self.errors: list = []

    def call(self, method: str, **params):
        self.next_id += 1
        message_id = self.next_id
        self.ws.send(json.dumps({"id": message_id, "method": method, "params": params}))
        while True:
            message = json.loads(self.ws.recv())
            if message.get("id") == message_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})
            self._event(message)

    def _event(self, message: dict) -> None:
        method, params = message.get("method"), message.get("params", {})
        if method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails", {})
            self.errors.append(details.get("exception", {}).get("description")
                               or details.get("text"))
        elif method == "Runtime.consoleAPICalled" and params.get("type") == "error":
            self.errors.append(" ".join(str(a.get("value", a.get("description", "")))
                                        for a in params.get("args", [])))

    def js(self, expression: str):
        result = self.call("Runtime.evaluate", expression=expression, awaitPromise=True,
                           returnByValue=True)
        if "exceptionDetails" in result:
            raise RuntimeError(f"page script failed: {result['exceptionDetails']}")
        return result.get("result", {}).get("value")

    def screenshot(self, rect: dict) -> bytes:
        shot = self.call("Page.captureScreenshot", format="png", captureBeyondViewport=True,
                         clip={**rect, "scale": 1})
        return base64.b64decode(shot["data"])

    def key(self, key: str, code: str, key_code: int) -> None:
        common = {"key": key, "code": code, "windowsVirtualKeyCode": key_code,
                  "nativeVirtualKeyCode": key_code}
        self.call("Input.dispatchKeyEvent", type="keyDown", text=key, unmodifiedText=key, **common)
        self.call("Input.dispatchKeyEvent", type="keyUp", **common)

    def click(self, x: float, y: float) -> None:
        for kind in ("mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left",
                      clickCount=1)


def find_browser(given: str | None) -> str:
    for candidate in ([given] if given else BROWSERS):
        found = candidate if os.path.isfile(candidate) else shutil.which(candidate)
        if found:
            return found
    sys.exit("no Chrome or Edge found; pass --browser")


def launch(browser: str, profile: str, headed: bool):
    args = [browser, f"--user-data-dir={profile}", "--remote-debugging-port=0",
            "--no-first-run", "--no-default-browser-check", "--window-size=1440,1000",
            # Chrome no longer falls back to software WebGL on its own; allow it where there
            # is no usable GPU, and report which renderer was used.
            "--enable-unsafe-swiftshader", "about:blank"]
    if not headed:
        args.insert(1, "--headless=new")
    process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    port_file = os.path.join(profile, "DevToolsActivePort")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if os.path.exists(port_file):
            with open(port_file, encoding="utf-8") as f:
                lines = f.read().split()
            if lines:
                port = int(lines[0])
                targets = json.load(urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10))
                pages = [t for t in targets if t.get("type") == "page"]
                if pages:
                    return process, pages[0]["webSocketDebuggerUrl"]
        time.sleep(0.2)
    process.kill()
    sys.exit("the browser did not open its DevTools port")


def seed_scan(orchestrator: str) -> str:
    import httpx

    sys.path.insert(0, ROOT)
    from tests.test_orchestrator import frame_bytes, scan_frame

    first = httpx.post(f"{orchestrator}/capture", params={"index": 0},
                       files={"file": ("frame.npz", frame_bytes(scan_frame()))}, timeout=60)
    first.raise_for_status()
    scan_id = first.json()["scan_id"]
    httpx.post(f"{orchestrator}/capture/{scan_id}/complete", timeout=60).raise_for_status()
    return scan_id


def pixels(png: bytes):
    import numpy as np
    from PIL import Image

    return np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(int)


def near(image, rgb, tolerance: int = 30):
    import numpy as np

    return (np.abs(image - np.array(rgb)) <= tolerance).all(axis=2)


def dark(image):
    # The physically lit rover uses grey metal, not the old almost-black proxy.
    return (image.max(axis=2) < 180) & ~near(image, LIVE_VIOLET)


def centroid(mask):
    import numpy as np

    points = np.argwhere(mask)
    return None if len(points) == 0 else [round(float(v), 1) for v in points.mean(axis=0)[::-1]]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--orchestrator", default="http://localhost:8000")
    parser.add_argument("--dashboard", default="http://localhost:5173")
    parser.add_argument("--browser")
    parser.add_argument("--out", default=os.path.join(ROOT, "build", "live_check"))
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)
    sys.stdout.reconfigure(encoding="utf-8")  # the Windows console is cp1252 by default

    checks: list = []
    report: dict = {"checks": checks}

    def check(name: str, passed: bool, **detail) -> bool:
        checks.append({"check": name, "passed": bool(passed), **detail})
        print(("PASS " if passed else "FAIL ") + name, json.dumps(detail, default=str)[:400],
              flush=True)
        return bool(passed)

    def save(name: str, png: bytes) -> str:
        path = os.path.join(args.out, name)
        with open(path, "wb") as f:
            f.write(png)
        return path

    browser = find_browser(args.browser)
    report["browser"] = browser
    scan_id = seed_scan(args.orchestrator)
    profile = tempfile.mkdtemp(prefix="scanborn-live-check-")
    process, ws_url = launch(browser, profile, args.headed)
    try:
        page = Page(ws_url)
        for domain in ("Page.enable", "Runtime.enable"):
            page.call(domain)
        page.call("Emulation.setDeviceMetricsOverride", width=1440, height=1000,
                  deviceScaleFactor=1, mobile=False)
        page.call("Emulation.setFocusEmulationEnabled", enabled=True)
        page.call("Page.navigate", url=f"{args.dashboard}/dashboard")
        page.js("new Promise((r) => { const t = setInterval(() => { "
                "if (document.querySelectorAll('.stage').length >= 10) { clearInterval(t); r(1); }"
                " }, 100); })")
        page.js(PAGE_HELPERS)

        # Settings on this isolated server: missing consent must refuse before any remote request.
        provider_ui = page.js("""(async () => {
            const panel = [...document.querySelectorAll('details')].find(e =>
                e.querySelector('summary')?.textContent === 'Processing providers and privacy');
            panel.open = true;
            if (!await __sb.until(() => panel.querySelector('fieldset input'), 10000)) return null;
            const buttons = () => [...panel.querySelectorAll('button')];
            __sb.type(panel.querySelector('fieldset input'), 'groq, local');
            await __sb.sleep(100);
            buttons().find(b => b.textContent === 'Save provider settings').click();
            await __sb.sleep(200);
            const refusal = panel.querySelector('[role=status]')?.textContent;
            buttons().find(b => b.textContent === 'Use local providers for all stages').click();
            const saved = await __sb.until(() =>
                /Saved for subsequent/.test(panel.textContent), 10000);
            const settings = await fetch('/providers').then(r => r.json());
            panel.open = false;
            return { refusal, saved, source: settings.configuration_source,
                chains: Object.values(settings.workloads).map(w => w.chain) };
        })()""")
        check("provider UI refuses missing consent and persists local settings", provider_ui
              and "authorize" in (provider_ui["refusal"] or "").lower() and provider_ui["saved"]
              and provider_ui["source"] == "saved_settings"
              and provider_ui["chains"] == [["local"], ["local"], ["local"]], details=provider_ui)

        report["webgl"] = page.js(
            "(() => { const gl = document.createElement('canvas').getContext('webgl2'); "
            "if (!gl) return null; const info = gl.getExtension('WEBGL_debug_renderer_info'); "
            "return { renderer: info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) "
            ": gl.getParameter(gl.RENDERER), version: gl.getParameter(gl.VERSION) }; })()")
        if not check("a WebGL2 context starts", report["webgl"], **(report["webgl"] or {})):
            raise SystemExit

        # ------------------------------------------------------------ pipeline to Optimize
        page.js(f"__sb.type(__sb.stage('Capture').querySelector('input[placeholder]'), "
                f"'{scan_id}')")
        stages = [page.js(f"__sb.sleep(100).then(() => __sb.run('{name}', 30000))")
                  for name in ("Capture", "Reconstruct", "Segment")]
        page.js("__sb.choose(__sb.stage('Generate twin').querySelector('select'), 'home')")
        stages.append(page.js("__sb.sleep(100).then(() => __sb.run('Generate twin', 120000))"))
        stages.append(page.js("__sb.run('Plan', 30000)"))
        page.js(f"""(async () => {{
            const inputs = [...__sb.stage('Mission').querySelectorAll('.mission-fields input')];
            for (const [value, i] of [['{START[0]}', 0], ['{START[1]}', 1], ['{GOAL[0]}', 3],
                                      ['{GOAL[1]}', 4]]) {{
              __sb.type(inputs[i], value); await __sb.sleep(80);
              const enter = new KeyboardEvent('keydown', {{ key: 'Enter', bubbles: true }});
              inputs[i].dispatchEvent(enter);
              await __sb.sleep(80);
            }}
        }})()""")
        stages.append(page.js("__sb.run('Mission', 30000)"))
        stages.append(page.js("__sb.run('Train', 120000)"))
        stages.append(page.js("__sb.run('Optimize', 60000)"))
        report["stages"] = stages
        if not check("capture → optimize in the console", all(s["cls"] == "done" for s in stages),
                     mission=stages[5]["out"], train=stages[6]["out"]):
            raise SystemExit

        registered = page.js("""(async () => {
            const stage = __sb.stage('Deploy');
            __sb.choose(stage.querySelector('select'), 'fake_rover'); await __sb.sleep(200);
            [...stage.querySelectorAll('button')]
              .find((b) => /Register/.test(b.textContent)).click();
            await __sb.until(() => /idle/.test(__sb.robot() || ''), 15000);
            return __sb.robot();
        })()""")
        check("the fake rover registers", "fake rover" in (registered or "")
              and "idle" in registered, robot=registered)

        # --------------------------------------------------- drive 1: the 3D view, live
        page.js("__sb.tab('Twin 3D')")
        assert page.js("__sb.until(() => __sb.canvas(), 10000)"), "3D canvas did not mount"
        page.js("__sb.canvas().scrollIntoView({block: 'center'})")
        assert page.js("__sb.until(() => /reconstructed points/.test("
                       "document.querySelector('.scan-workspace__status')"
                       "?.textContent || ''), 10000)"), \
            "Reconstructed geometry did not finish loading"
        page.js("__sb.sleep(1500)")
        rect = page.js("(() => { const r = __sb.canvas().getBoundingClientRect(); "
                       "return { x: r.left + scrollX, y: r.top + scrollY, "
                       "width: r.width, height: r.height }; })()")
        unavailable = page.js("document.querySelector('.robot-viewport .viewport-loading')"
                              "?.innerText ?? null")
        check("the 3D view renders", rect and rect["width"] > 100 and not unavailable,
              canvas=rect, unavailable=unavailable)
        before = pixels(page.screenshot(rect))
        save("3d_before_drive.png", page.screenshot(rect))

        page.js("""(async () => {
            window.__canvasBeforeDeploy = __sb.canvas();
            __sb.stage('Deploy').querySelector('.body > button').click();
            await __sb.until(() => __sb.driving() && __sb.poses() >= 30, 20000);
            window.__canvasAtStart = __sb.canvas();
        })()""")
        shots = []
        for i in range(2):
            page.js("__sb.canvas().scrollIntoView({block: 'center'})")
            page.js("__sb.sleep(200)")
            rect = page.js("(() => { const r = __sb.canvas().getBoundingClientRect(); "
                           "return { x: r.left + scrollX, y: r.top + scrollY, "
                           "width: r.width, height: r.height }; })()")
            png = page.screenshot(rect)
            save(f"3d_driving_{i + 1}.png", png)
            image = pixels(png)
            new_dark = dark(image) & ~dark(before)
            shots.append({
                "poses": page.js("__sb.poses()"),
                "violet_px": int(near(image, LIVE_VIOLET).sum()),
                "rover_px": int(new_dark.sum()),
                "rover_centroid": centroid(new_dark),
                "hud": page.js("[...document.querySelectorAll('.scan-workspace__status')]"
                               ".map((c) => c.textContent).join(' || ')"),
            })
            page.js("__sb.sleep(2000)")
        same_canvas = page.js("__sb.canvas() === window.__canvasAtStart")
        kept_at_deploy = page.js("__sb.canvas() === window.__canvasBeforeDeploy")
        report["drive_1_3d"] = shots
        first, second = shots
        check("the 3D view draws the rover and its trace live", first["violet_px"] > 20
              and second["violet_px"] > first["violet_px"] and first["rover_px"] > 5
              and second["rover_px"] > 5 and first["rover_centroid"] != second["rover_centroid"]
              and "driving" in second["hud"].lower(), before_violet_px=int(
                  near(before, LIVE_VIOLET).sum()), shots=shots)
        check("live frames move the rover without rebuilding the scene", same_canvas,
              kept_since_before_deploy=kept_at_deploy)

        ended = page.js("""(async () => {
            await __sb.until(() => !__sb.driving(), 40000);
            await __sb.until(() => /RMS/.test(__sb.state('Deploy').out || ''), 10000);
            return { bar: __sb.bar(), result: __sb.state('Deploy').out };
        })()""")
        after = pixels(page.screenshot(rect))
        save("3d_after_drive.png", page.screenshot(rect))
        check("drive 1 ends done with its result", "goal reached" in (ended["result"] or "")
              and "Drive done" in (ended["bar"] or ""), **ended,
              violet_px_after=int(near(after, LIVE_VIOLET).sum()))

        # ------------------------------------- drive 2: E-STOP by a trusted Space press
        def start_drive() -> dict:
            return page.js("""(async () => {
                await __sb.until(() => !__sb.driving(), 20000);
                __sb.stage('Deploy').querySelector('.body > button').click();
                const started = await __sb.until(() => __sb.driving() && __sb.poses() >= 20, 20000);
                window.__estop = {};
                addEventListener('keydown', (e) => {
                  window.__estop.key = { t: performance.now(), trusted: e.isTrusted, key: e.key,
                                         code: e.code };
                }, { capture: true, once: true });
                addEventListener('pointerdown', (e) => {
                  window.__estop.pointer = { t: performance.now(), trusted: e.isTrusted };
                }, { capture: true, once: true });
                new MutationObserver((changes, observer) => {
                  if (/E-STOP latched/.test(__sb.bar() || '')) {
                    window.__estop.latched = performance.now(); observer.disconnect();
                  }
                }).observe(document.body, { subtree: true, childList: true, characterData: true });
                return { started, bar: __sb.bar() };
            })()""")

        def latched(trigger: str) -> dict:
            return page.js(f"""(async () => {{
                await __sb.until(() => window.__estop.latched, 5000);
                await __sb.until(() => /estop/.test(__sb.robot() || ''), 5000);
                const input = window.__estop.{trigger};
                return {{ input, latched_ms: input && window.__estop.latched
                            ? Math.round(window.__estop.latched - input.t) : null,
                          bar: __sb.bar(), robot: __sb.robot(),
                          result: __sb.state('Deploy').out }};
            }})()""")

        running = start_drive()
        context_lost = page.js("""(async () => {
            window.__loss = __sb.canvas().getContext('webgl2').getExtension('WEBGL_lose_context');
            __loss.loseContext();
            return await __sb.until(() =>
                document.querySelector('.scan-workspace__fallback'), 5000);
        })()""")
        check("renderer failure exposes fallback during a live drive", context_lost)
        page.key(" ", "Space", 32)
        by_key = latched("key")
        report["estop_key"] = {"running": running, **by_key}
        check("Space pressed as a trusted key event latches E-STOP", running["started"]
              and by_key["input"] and by_key["input"]["trusted"] and by_key["latched_ms"]
              is not None and "reason: dashboard" in (by_key["bar"] or "")
              and "estop (dashboard)" in (by_key["robot"] or ""), **report["estop_key"])
        restored = page.js("""(async () => {
            __loss.restoreContext();
            return await __sb.until(() =>
                !document.querySelector('.scan-workspace__fallback'), 5000);
        })()""")
        check("WebGL context restores after independent E-STOP", restored)

        refused = page.js("""(async () => {
            const stage = __sb.stage('Deploy');
            stage.querySelector('.body > button').click();
            await __sb.until(() => stage.querySelector('.error') || __sb.driving(), 10000);
            return { error: __sb.state('Deploy').error, driving: __sb.driving() };
        })()""")
        check("a deploy to the latched rover is refused",
              "rover_refused" in (refused["error"] or "") and not refused["driving"], **refused)
        acknowledged = page.js("""(async () => {
            const found = await __sb.until(() => [...document.querySelectorAll('button')]
                .some(b => /Acknowledge failed request/i.test(b.textContent)), 10000);
            if (!found) return false;
            [...document.querySelectorAll('button')]
                .find(b => /Acknowledge failed request/i.test(b.textContent)).click();
            return await __sb.until(() =>
                !/Previous deployment request/.test(__sb.bar() || ''), 5000);
        })()""")
        check("failed deployment is explicitly acknowledged before a new drive", acknowledged)

        def reset() -> str:
            return page.js("""(async () => {
                const button = [...document.querySelectorAll(
                  '.rover-register button, .live-bar button')]
                  .find((b) => b.textContent === 'Reset rover');
                button?.click();
                await __sb.until(() => /· idle/.test(__sb.robot() || ''), 10000);
                return __sb.robot();
            })()""")

        after_reset = reset()
        check("reset returns the rover to idle", "· idle" in (after_reset or ""), robot=after_reset)

        # --------------------------------------- drive 3: E-STOP by a trusted mouse click
        running = start_drive()
        button = page.js("(() => { const b = document.querySelector('.btn--estop'); "
                         "b.scrollIntoView({ block: 'center' }); "
                         "const r = b.getBoundingClientRect(); "
                         "return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()")
        page.click(button["x"], button["y"])
        by_click = latched("pointer")
        save("3d_after_estop.png", page.screenshot(rect))
        report["estop_click"] = {"running": running, **by_click}
        check("the E-STOP button, clicked, latches E-STOP", running["started"]
              and by_click["input"] and by_click["input"]["trusted"]
              and by_click["latched_ms"] is not None
              and "estop (dashboard)" in (by_click["robot"] or ""), **report["estop_click"])
        after_reset = reset()
        check("reset after the button E-STOP", "· idle" in (after_reset or ""), robot=after_reset)

        page.call("Emulation.setDeviceMetricsOverride", width=390, height=844,
                  deviceScaleFactor=1, mobile=False)
        page.js("__sb.canvas().scrollIntoView({block: 'center'})")
        page.js("__sb.sleep(800)")
        narrow = page.js("(() => { const r = __sb.canvas().getBoundingClientRect(); return "
                         "{width:r.width,left:r.left,right:r.right,viewport:innerWidth,"
                         "documentWidth:document.documentElement.scrollWidth, overflow:"
                         "[...document.querySelectorAll('body *')]"
                         ".filter(e=>e.getBoundingClientRect().right>391)"
                         ".slice(0,15).map(e=>({tag:e.tagName,cls:e.className,"
                         "text:e.textContent.slice(0,60)}))}; })()")
        check("shared workspace fits a narrow screen", narrow["width"] > 100
              and narrow["left"] >= 0 and narrow["right"] <= 391
              and narrow["documentWidth"] <= 391, **narrow)
        page.call("Emulation.setDeviceMetricsOverride", width=1440, height=1000,
                  deviceScaleFactor=1, mobile=False)

        page.js("__sb.tab('Mission map')")
        page.js("__sb.sleep(800)")
        full = page.call("Page.getLayoutMetrics")["cssContentSize"]
        save("console_map.png", page.screenshot({"x": 0, "y": 0, "width": 1440,
                                                 "height": min(full["height"], 4000)}))
        errors = [e for e in page.errors if e]
        report["page_errors"] = errors
        check("no page errors", not errors, errors=errors[:5])
    except SystemExit:
        pass
    finally:
        process.kill()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        shutil.rmtree(profile, ignore_errors=True)

    report["passed"] = bool(checks) and all(c["passed"] for c in checks)
    with open(os.path.join(args.out, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(json.dumps({"passed": report["passed"], "checks": len(checks),
                      "failed": [c["check"] for c in checks if not c["passed"]],
                      "webgl": report.get("webgl"), "out": args.out}, indent=2))
    sys.exit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
