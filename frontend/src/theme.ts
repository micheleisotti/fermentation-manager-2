/** Appearance per device (stored in this browser): light, grey, dim (dark teal page, white cards) or dark. */
export type Theme = "light" | "grey" | "dim" | "dark";
const KEY = "fm2.theme";
const BAR: Record<Theme, string> = { light: "#f6f7f9", grey: "#dadfe1", dim: "#2b4350", dark: "#12191e" };

export function getTheme(): Theme {
  try { const t = localStorage.getItem(KEY); return t === "grey" || t === "dim" || t === "dark" ? t : "light"; } catch { return "light"; }
}

export function applyTheme(t: Theme = getTheme()): void {
  document.documentElement.dataset.theme = t;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", BAR[t]);
}

export function setTheme(t: Theme): void {
  try { localStorage.setItem(KEY, t); } catch { /* storage unavailable: applies to this visit only */ }
  applyTheme(t);
}
