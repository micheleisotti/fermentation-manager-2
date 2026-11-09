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
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, refresh };
}

export function fmt(v: number | null | undefined, digits = 2): string {
  return v === null || v === undefined || Number.isNaN(v) ? "—" : v.toFixed(digits);
}

export function fmtDuration(h: number | null | undefined): string {
  if (h === null || h === undefined) return "—";
  const hh = Math.floor(h), mm = Math.floor((h - hh) * 60);
  return `${hh} h ${String(mm).padStart(2, "0")} min`;
}
