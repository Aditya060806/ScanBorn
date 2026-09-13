import { useEffect, useRef } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

gsap.registerPlugin(ScrollTrigger);

/**
 * Scroll reveal for a section's own content. Elements marked `data-reveal` rise into
 * place as the section enters; the stagger is what makes a list read as a sequence
 * rather than a block that blinks on.
 *
 * Returns a ref to put on the container. No-ops under reduced motion — gsap.matchMedia
 * handles the teardown, so nothing is left half-faded if the setting changes.
 */
export function useReveal(optionsOrDeps = {}, maybeDeps = []) {
  const isOptions = typeof optionsOrDeps === "object" && !Array.isArray(optionsOrDeps);
  const options = isOptions ? optionsOrDeps : {};
  const deps = isOptions ? maybeDeps : (Array.isArray(optionsOrDeps) ? optionsOrDeps : []);

  const {
    stagger = 0.07,
    y = 26,
    duration = 0.7,
    start = "top 78%",
  } = options;

  const ref = useRef(null);

  useEffect(() => {
    const scope = ref.current;
    if (!scope) return;

    const targets = scope.querySelectorAll("[data-reveal]");
    if (!targets.length) return;

    const mm = gsap.matchMedia();
    mm.add("(prefers-reduced-motion: no-preference)", () => {
      gsap.from(targets, {
        opacity: 0,
        y,
        duration,
        ease: "power2.out",
        stagger,
        scrollTrigger: { trigger: scope, start },
      });
    });

    return () => mm.revert();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return ref;
}
