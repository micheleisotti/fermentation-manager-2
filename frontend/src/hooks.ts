import { useCallback, useEffect, useRef, useState } from "react";

/** Poll an async loader every `ms` (paused while the tab is hidden). */
export function usePolling<T>(load: () => Promise<T>, ms: number, deps: unknown[] = []) {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const loadRef = useRef(load);
  loadRef.current = load;

  const refresh = useCallback(async () => {
    try {
      setData(await loadRef.current());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    let timer: number | undefined;
    const tick = () => {
      if (!document.hidden) void refresh();
      timer = window.setTimeout(tick, ms);
    };
    tick();
    // phone screen on again / app back in front: refresh now instead of waiting for the next tick
    const now = () => { void refresh(); };
    window.addEventListener("fm2-refresh", now);
    return () => { window.clearTimeout(timer); window.removeEventListener("fm2-refresh", now); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, refresh };
}

/** True while the CSS media query matches (e.g. "(max-width: 760px)"). */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => typeof window !== "undefined" && window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setMatches(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return matches;
}

/** Phones in portrait (narrow) or landscape (short touch screen). */
export const PHONE_QUERY = "(max-width: 760px), (max-height: 500px) and (pointer: coarse)";

export function fmt(v: number | null | undefined, digits = 2): string {
  return v === null || v === undefined || Number.isNaN(v) ? "—" : v.toFixed(digits);
}

export function fmtDuration(h: number | null | undefined): string {
  if (h === null || h === undefined) return "—";
  const hh = Math.floor(h), mm = Math.floor((h - hh) * 60);
  return `${hh} h ${String(mm).padStart(2, "0")} min`;
}

/** Age of the last reading: "15 s", "8 min", "3 h" ("—" when unknown). */
export function fmtAge(s: number | null | undefined): string {
  if (s === null || s === undefined) return "—";
  if (s < 120) return `${Math.round(s)} s`;
  if (s < 7200) return `${Math.round(s / 60)} min`;
  return `${Math.round(s / 3600)} h`;
}
