/** Appearance per device (stored in this browser). Every theme has the dotted page; they differ in the page tint. */
export type Theme = "light" | "cream" | "sky" | "dim" | "dark";
export const THEMES: Theme[] = ["light", "cream", "sky", "dim", "dark"];
const KEY = "fm2.theme";
const BAR: Record<Theme, string> = { light: "#f6f7f9", cream: "#fbf6ea", sky: "#ebf3f5", dim: "#2b4350", dark: "#12191e" };

export function getTheme(): Theme {
  try {
    let t = localStorage.getItem(KEY);
    if (t === "grey") { t = "sky"; localStorage.setItem(KEY, t); }   // 2.2.1: Grey became Sky
    return (THEMES as string[]).includes(t ?? "") ? (t as Theme) : "light";
  } catch { return "light"; }
}

export function applyTheme(t: Theme = getTheme()): void {
  document.documentElement.dataset.theme = t;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", BAR[t]);
}

export function setTheme(t: Theme): void {
  try { localStorage.setItem(KEY, t); } catch { /* storage unavailable: applies to this visit only */ }
  applyTheme(t);
}
