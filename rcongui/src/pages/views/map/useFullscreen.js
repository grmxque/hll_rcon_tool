import { useCallback, useEffect, useState } from "react";

/**
 * Full-screen state of the live map. The page draws the overlay itself (fixed, covering the viewport),
 * so it also works where the Fullscreen API does not (iPhone Safari). Where the API exists, the whole
 * document goes full screen to hide the browser bars: fullscreening only the map element would hide the
 * MUI tooltips, rendered in a portal under `body`.
 */
export function useFullscreen() {
  const [active, setActive] = useState(false);

  const toggle = useCallback(() => {
    if (active) {
      setActive(false);
      return;
    }
    setActive(true);
    // A refusal (no user gesture, API disabled) leaves the overlay alone, which is still usable.
    document.documentElement.requestFullscreen?.()?.catch(() => {});
  }, [active]);

  useEffect(() => {
    if (!active) return undefined;
    // Esc or the back gesture leaves native full screen without going through `toggle`.
    const onFullscreenChange = () => {
      if (!document.fullscreenElement) setActive(false);
    };
    const onKeyDown = (event) => {
      if (event.key === "Escape") setActive(false);
    };
    document.addEventListener("fullscreenchange", onFullscreenChange);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("fullscreenchange", onFullscreenChange);
      document.removeEventListener("keydown", onKeyDown);
      // Leaving the mode, or the page, ends native full screen too.
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    };
  }, [active]);

  return { active, toggle };
}
