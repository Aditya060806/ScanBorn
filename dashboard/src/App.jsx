import React, { useEffect, useState, lazy, Suspense } from "react";

const Console = lazy(() => import("./Console.jsx"));
const Landing = lazy(() => import("./Landing.jsx"));
import { Link, usePath } from "./router.jsx";

/**
 * The sheet both routes are printed on: `/` is the overview, `/dashboard` is the console.
 * Masthead, registration marks, and the footer stamp are shared, which is what keeps the
 * two routes reading as one document rather than two sites.
 */
export default function App() {
  const path = usePath();
  const onConsole = path.startsWith("/dashboard");
  const [entered, setEntered] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setEntered(true), 40);
    return () => clearTimeout(timer);
  }, []);

  return (
    <div className="sheet">
      <span className="reg reg--tl" aria-hidden="true" />
      <span className="reg reg--tr" aria-hidden="true" />
      <span className="reg reg--bl" aria-hidden="true" />
      <span className="reg reg--br" aria-hidden="true" />

      <header className={`masthead ${entered ? "masthead--entered" : ""}`}>
        <Link className="masthead__mark" to="/">
          ScanBorn
        </Link>

        <nav className="masthead__nav">
          <Link className={onConsole ? "" : "is-here"} to="/">
            Overview
          </Link>
          <Link className={onConsole ? "is-here" : ""} to="/dashboard">
            Console
          </Link>
        </nav>
      </header>

      <main><Suspense fallback={<p role="status">Loading workspace…</p>}>{onConsole ? <Console /> : <Landing />}</Suspense></main>

      <footer className="stamp">
        <span>ScanBorn</span>
        <span>Edge-first Physical AI operating layer</span>
      </footer>
    </div>
  );
}
