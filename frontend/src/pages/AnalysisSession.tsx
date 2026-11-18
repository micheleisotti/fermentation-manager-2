import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import * as echarts from "echarts/core";
import { CustomChart, ScatterChart } from "echarts/charts";
import { api, type Experiment, type SessionBody, type SessionInfo, type SessionResults } from "../api";
import { fmt, usePolling } from "../hooks";
import { EChart, ExpName, Icon, Segmented } from "../components/ui";
import { ExpTags } from "./ExperimentsList";

echarts.use([ScatterChart, CustomChart]);

export const COND_COLORS = ["#1d3786", "#c98a00", "#1f7a63", "#7a4fa0", "#b3261e", "#54565a"];
const COND_BG = ["#e3e8f3", "#fdf0d2", "#dff1ea", "#eee6f5", "#f8e1df", "#eceded"];
const COND_SYMBOLS = ["circle", "triangle", "rect", "diamond", "pin", "roundRect"];
const LETTERS = "ABCDEFGHIJ";
const STEPS = [{ value: "1", label: "1 h" }, { value: "2", label: "2 h" }, { value: "4", label: "4 h" }];

/** /analysis/new[?with=1,2&cond=x,y]: creates a session and opens it. */
export function NewSession() {
  const [q] = useSearchParams();
  const nav = useNavigate();
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    const ids = (q.get("with") ?? "").split(",").filter(Boolean).map(Number);
    const conds = (q.get("cond") ?? "").split(",");
    const members: Record<number, string> = {};
    ids.forEach((id, i) => { members[id] = conds[i] || LETTERS[i]; });
    const conditions = [...new Set(Object.values(members))];
    api.createSession({ name: q.get("name") || "New analysis session", members, conditions, fit_variable: "OD600", model: "gompertz" })
      .then((s) => nav(`/analysis/sessions/${s.id}`, { replace: true }))
      .catch((e) => setErr((e as Error).message));
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  return err ? <div className="card" style={{ color: "#b30020" }}>Could not create the session: {err}</div> : <div className="muted">Creating…</div>;
}

export function SessionPage() {
  const id = Number(useParams().id);
  const nav = useNavigate();
  const [sess, setSess] = useState<SessionInfo | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [step, setStep] = useState("2");
  const [variable, setVariable] = useState<string | undefined>(undefined);
  const [rev, setRev] = useState(0);
  const all = usePolling(() => api.experiments("completed"), 120000);
  const res = usePolling(() => api.sessionResults(id, Number(step), variable), 600000, [id, step, variable, rev]);

  useEffect(() => { api.session(id).then(setSess).catch((e) => setErr((e as Error).message)); }, [id]);

  const save = async (next: SessionBody) => {
    if (!sess) return;
    setSess({ ...sess, ...next });
    setSaving(true);
    try { setSess(await api.updateSession(id, next)); setErr(null); setRev((r) => r + 1); }
    catch (e) { setErr((e as Error).message); }
    finally { setSaving(false); }
  };

  if (!sess) return err ? <div className="card">Session not found: {err}</div> : <div className="muted">Loading…</div>;
  const body: SessionBody = { name: sess.name, members: sess.members, conditions: sess.conditions, fit_variable: sess.fit_variable, model: sess.model };
  const r = res.data;

  return (
    <>
      <header className="row between">
        <div className="row" style={{ gap: 12 }}>
          <Link to="/analysis" className="btn icon text" aria-label="Back to analysis"><Icon name="back" /></Link>
          <div className="stack" style={{ gap: 2 }}>
            <h1 style={{ fontSize: 22, fontWeight: 600 }}><InlineName value={sess.name} onSave={(name) => save({ ...body, name })} /> <span className="mono code-chip">{sess.code}</span></h1>
            <span className="small muted">Analysis session · saved {new Date(sess.updated_at).toLocaleString()} · {Object.keys(sess.members).length} experiments
              {saving && " · saving…"}</span>
          </div>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <a className="btn outlined" href={`/api/analysis-sessions/${id}/export.zip?step_h=${step}`} download>Export CSV</a>
          <a className="btn outlined" href={`/api/analysis-sessions/${id}/export.xlsx?step_h=${step}`} download>Export XLSX</a>
          <button className="btn text" style={{ color: "#b30020" }} onClick={async () => {
            if (window.confirm(`Delete analysis session ${sess.code}? The experiments are not affected.`)) {
              await api.deleteSession(id); nav("/analysis");
            }
          }}>Delete</button>
        </div>
      </header>
      {err && <div className="card tight" style={{ color: "#b30020" }}>{err}</div>}

      <Step1 sess={sess} body={body} save={save} all={all.data ?? []} flags={r?.parallel_run_flags ?? []} />

      <section className="card" aria-labelledby="st2">
        <div className="row between" style={{ alignItems: "flex-end" }}>
          <div className="stack" style={{ gap: 2 }}>
            <span className="kicker">Step 2</span>
            <h2 id="st2" style={{ fontSize: 18 }}>Growth, kinetics and base dosing</h2>
            {r && <span className="small muted">{describeDesign(r)}</span>}
          </div>
          <div className="row" style={{ gap: 12 }}>
            <span className="small muted">Fit on</span>
            <select value={sess.fit_variable} onChange={(x) => save({ ...body, fit_variable: x.target.value })} style={{ height: 36 }}
              aria-label="Variable for kinetic fit">
              {(r?.variable_options.filter((o) => o.fit) ?? [{ key: sess.fit_variable }]).map((o) => <option key={o.key}>{o.key}</option>)}
            </select>
            <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
              <span className="muted">Model</span>
              <select value={sess.model} onChange={(x) => save({ ...body, model: x.target.value })} style={{ height: 40, width: "auto" }}>
                {Object.entries(r?.models ?? { gompertz: "Modified Gompertz (Zwietering)" }).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
            </label>
          </div>
        </div>
        {!r ? <div className="muted">{res.error ?? "Calculating…"}</div> : Object.keys(sess.members).length === 0 ? (
          <span className="muted">Select experiments and give each a condition in step 1.</span>
        ) : (
          <>
            <div className="row" style={{ gap: 12 }}>
              <span className="small muted">Points every</span>
              <Segmented label="Point interval" options={STEPS} value={step} onChange={setStep} />
              <label className="row small" style={{ gap: 8 }}><span className="muted">Variable</span>
                <select value={r.variable} onChange={(x) => setVariable(x.target.value)} style={{ height: 36 }}>
                  {r.variable_options.map((o) => <option key={o.key} value={o.key}>{o.label}{o.unit ? ` (${o.unit})` : ""}</option>)}
                </select>
              </label>
              <span className="small muted">Error bars: ± SD of replicates</span>
            </div>
            <SessionChart r={r} />
            <span className="small muted">
              {r.variable_options.find((o) => o.key === r.variable)?.manual
                ? (r.variable_options.find((o) => o.key === r.variable)?.fit ? "" : "Not a growth-like variable: no model line. ") + "Mean ± SD per condition: each experiment is interpolated linearly on the grid, within the time range common to its replicates. Line = model fitted on the pooled replicates (each normalised to its first point)."
                : "Mean ± SD per condition of the per-experiment interval means; intervals missing in any replicate are left out."}
              {Object.values(r.curves).some((c) => c?.warning) && ` ${[...new Set(Object.values(r.curves).map((c) => c?.warning).filter(Boolean))].join(" ")}`}
            </span>
            <FigureExport url={(fig) => `/api/analysis-sessions/${id}/figure.${fig}?variable=${encodeURIComponent(r.variable)}&step_h=${step}`} />
            <ComparisonTable r={r} />
            <BoxPlotCard r={r} id={id} step={step} />
          </>
        )}
      </section>
    </>
  );
}

function describeDesign(r: SessionResults): string {
  const used = r.conditions.filter((c) => r.n_by_condition[c] > 0);
  const ns = used.map((c) => r.n_by_condition[c]);
  if (used.length < 2) return "Assign experiments to at least two conditions to compare them.";
  const nTxt = new Set(ns).size === 1 ? `n = ${ns[0]} each` : `n = ${ns.join(", ")}`;
  const test = used.length === 2 ? "Welch's t-test per parameter" : "one-way ANOVA + Tukey HSD per parameter";
  return `${used.length} conditions, ${nTxt} → mean ± SD per condition, ${ns.some((n) => n < 2) ? "no test (n < 2 in a condition)" : test}`;
}

function InlineName({ value, onSave }: { value: string; onSave: (v: string) => void }) {
  const [edit, setEdit] = useState(false);
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  if (!edit) return <button className="linklike" onClick={() => setEdit(true)} title="Rename">{value}</button>;
  const done = () => { setEdit(false); if (v.trim() && v !== value) onSave(v.trim()); else setV(value); };
  return <input autoFocus value={v} onChange={(x) => setV(x.target.value)} onBlur={done}
    onKeyDown={(k) => { if (k.key === "Enter") done(); if (k.key === "Escape") { setV(value); setEdit(false); } }}
    style={{ fontSize: 20, padding: "2px 6px", border: "1px solid #74777f", borderRadius: 4, width: 360 }} aria-label="Session name" />;
}

function Step1({ sess, body, save, all, flags }: { sess: SessionInfo; body: SessionBody; save: (b: SessionBody) => void; all: Experiment[];
  flags: SessionResults["parallel_run_flags"] }) {
  const [show, setShow] = useState<"selected" | "all">(Object.keys(sess.members).length ? "selected" : "all");
  const [filter, setFilter] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [newCond, setNewCond] = useState<string | null>(null);
  const selected = new Set(Object.keys(sess.members).map(Number));
  const rows = useMemo(() => all
    .filter((e) => show === "all" || selected.has(e.id))
    .filter((e) => showArchived || !e.archived || selected.has(e.id))
    .filter((e) => !filter || `${e.code} ${e.name} ${e.strain} ${e.started_at ?? ""}`.toLowerCase().includes(filter.toLowerCase()))
    .sort((a, b) => (b.started_at ?? "").localeCompare(a.started_at ?? "")),
  [all, show, filter, showArchived, sess.members]);  // eslint-disable-line react-hooks/exhaustive-deps
  const ci = (c: string) => Math.max(0, sess.conditions.indexOf(c));

  const setMember = (eid: number, cond: string | null) => {
    const members = { ...sess.members };
    if (cond === null) delete members[eid]; else members[eid] = cond;
    save({ ...body, members });
  };
  const addCondition = (name: string) => {
    if (!name.trim() || sess.conditions.includes(name.trim())) return;
    save({ ...body, conditions: [...sess.conditions, name.trim()] });
  };
  const removeCondition = (c: string) => {
    const members = Object.fromEntries(Object.entries(sess.members).filter(([, v]) => v !== c));
    save({ ...body, members, conditions: sess.conditions.filter((x) => x !== c) });
  };
  const renameCondition = (c: string) => {
    const n = window.prompt(`Rename condition ${LETTERS[ci(c)]}`, c)?.trim();
    if (!n || n === c || sess.conditions.includes(n)) return;
    const members = Object.fromEntries(Object.entries(sess.members).map(([k, v]) => [k, v === c ? n : v]));
    save({ ...body, members, conditions: sess.conditions.map((x) => (x === c ? n : x)) });
  };

  return (
    <section className="card" aria-labelledby="st1">
      <div className="row between" style={{ alignItems: "flex-end" }}>
        <div className="stack" style={{ gap: 2 }}>
          <span className="kicker">Step 1</span>
          <h2 id="st1" style={{ fontSize: 18 }}>Experiments and conditions</h2>
          <span className="small muted">Experiments with the same condition are treated as replicates. Conditions belong to this session only.</span>
        </div>
        <div className="row" style={{ gap: 8 }}>
          {sess.conditions.map((c, i) => (
            <span key={c} className="cond-chip" style={{ background: COND_BG[i % 6] }}>
              <span className="cond-letter" style={{ background: COND_COLORS[i % 6] }}>{LETTERS[i]}</span>
              <button className="linklike" onClick={() => renameCondition(c)} title="Rename condition">{c}</button>
              · <b className="mono">n = {sess.n_by_condition[c] ?? 0}</b>
              <button className="btn icon text" style={{ width: 24, height: 24 }} aria-label={`Remove condition ${c}`}
                onClick={() => { if (!sess.n_by_condition[c] || window.confirm(`Remove condition ${c}? Its experiments are unassigned.`)) removeCondition(c); }}>
                <Icon name="close" size={14} /></button>
            </span>
          ))}
          {newCond === null
            ? <button className="cond-add" onClick={() => setNewCond("")}>+ Condition</button>
            : <input autoFocus className="cond-add" placeholder="Condition name" value={newCond} onChange={(x) => setNewCond(x.target.value)}
                onBlur={() => { addCondition(newCond); setNewCond(null); }}
                onKeyDown={(k) => { if (k.key === "Enter") { addCondition(newCond); setNewCond(null); } if (k.key === "Escape") setNewCond(null); }} />}
        </div>
      </div>
      <div className="row" style={{ gap: 8 }} role="group" aria-label="Show">
        <button className={`chip ${show === "all" ? "on" : ""}`} aria-pressed={show === "all"} onClick={() => setShow("all")}>All completed · {all.length}</button>
        <button className={`chip ${show === "selected" ? "on" : ""}`} aria-pressed={show === "selected"} onClick={() => setShow("selected")}>
          {show === "selected" && <Icon name="check" size={16} width={2.4} />}Selected · {selected.size}</button>
        <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={showArchived}
          onChange={(x) => setShowArchived(x.target.checked)} style={{ width: 18, height: 18 }} />Show archived</label>
        <label className="search-pill"><span className="sr-only">Filter experiments</span>
          <input type="search" placeholder="Filter by code, strain, name, date" value={filter} onChange={(x) => setFilter(x.target.value)} /></label>
      </div>
      <div className="table-wrap">
        <table className="data" style={{ minWidth: 820 }}>
          <thead><tr><th style={{ width: 48 }}><span className="sr-only">Selected</span></th><th>Experiment</th><th>Date</th><th>Strain</th>
            <th>Reactor</th><th>Parallel run</th><th>Condition</th></tr></thead>
          <tbody>
            {rows.length === 0 && <tr><td colSpan={7} className="muted">{all.length ? "No experiment matches." : "No completed experiments yet."}</td></tr>}
            {rows.map((e) => {
              const cond = sess.members[e.id];
              const i = cond ? ci(cond) : -1;
              return (
                <tr key={e.id}>
                  <td><input type="checkbox" checked={selected.has(e.id)} aria-label={`Include ${e.code}`} style={{ width: 20, height: 20 }}
                    onChange={() => setMember(e.id, selected.has(e.id) ? null : (sess.conditions[0] ?? "A"))} /></td>
                  <td><ExpName name={e.name} code={e.code} to={`/analysis/experiments/${e.id}`} />
                    <span className="row" style={{ gap: 4, display: "inline-flex", marginLeft: 6 }}><ExpTags e={e} /></span></td>
                  <td className="mono">{e.started_at ? e.started_at.slice(0, 10) : "—"}</td>
                  <td className="mono">{e.strain || "—"}</td>
                  <td className="mono">{e.reactor_id}</td>
                  <td>{e.parallel_run_code ? <span className="run-pill">{e.parallel_run_code}</span> : <span className="muted">—</span>}</td>
                  <td>
                    <select aria-label={`Condition for ${e.code}`} value={cond ?? ""} className="cond-select"
                      style={i >= 0 ? { borderColor: COND_COLORS[i % 6], background: COND_BG[i % 6] } : undefined}
                      onChange={(x) => setMember(e.id, x.target.value || null)}>
                      <option value="">— not assigned</option>
                      {sess.conditions.map((c, j) => <option key={c} value={c}>{LETTERS[j]} · {c}</option>)}
                    </select>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {flags.length > 0 && (
        <div role="note" className="note">
          <Icon name="list" size={20} />
          <span className="stack" style={{ gap: 4 }}><b>Parallel runs in this selection</b>
            {flags.map((f) => (
              <span key={f.parallel_run}><span className="mono">{f.parallel_run}</span> · {f.experiments.map((x) => `${x.code} (${LETTERS[ci(x.condition)]})`).join(" and ")}
                , run together{f.date ? ` on ${f.date}` : ""}{f.same_condition ? " — same condition: replicates that shared inoculum and day are not fully independent." : "."}</span>
            ))}
            <span className="muted">For information only: statistics are not affected.</span>
          </span>
        </div>
      )}
    </section>
  );
}

function SessionChart({ r }: { r: SessionResults }) {
  const opt = r.variable_options.find((o) => o.key === r.variable);
  const option = useMemo(() => {
    const series: unknown[] = [];
    const conds = r.conditions.filter((c) => r.curves[c]);
    const span = Math.max(1, ...conds.flatMap((c) => r.curves[c]!.t));
    conds.forEach((c) => {
      const i = r.conditions.indexOf(c);
      const cv = r.curves[c]!;
      const color = COND_COLORS[i % 6];
      const dx = (conds.indexOf(c) - (conds.length - 1) / 2) * span * 0.006;
      const pts = cv.t.map((t, k) => [t + dx, cv.mean[k], cv.sd[k], cv.n, t]);
      const name = `${LETTERS[i]} · ${c}`;
      series.push({
        type: "custom", name, silent: true, itemStyle: { color }, data: pts.filter((d) => d[2] != null),
        renderItem: (_: unknown, api: { value: (i: number) => number; coord: (v: number[]) => number[] }) => {
          const [t, y, sd] = [api.value(0), api.value(1), api.value(2)];
          const lo = api.coord([t, y - sd]), hi = api.coord([t, y + sd]);
          const st = { stroke: color, lineWidth: 1.2 };
          return { type: "group", children: [
            { type: "line", shape: { x1: lo[0], y1: lo[1], x2: hi[0], y2: hi[1] }, style: st },
            { type: "line", shape: { x1: lo[0] - 4, y1: lo[1], x2: lo[0] + 4, y2: lo[1] }, style: st },
            { type: "line", shape: { x1: hi[0] - 4, y1: hi[1], x2: hi[0] + 4, y2: hi[1] }, style: st },
          ] };
        },
      });
      series.push({ type: opt?.manual && cv.fit ? "scatter" : "line", name, data: pts, symbol: COND_SYMBOLS[i % 6], symbolSize: 8,
        itemStyle: { color }, lineStyle: { color, width: 1.2 }, showSymbol: true });
      if (cv.fit) series.push({ type: "line", name, data: cv.fit.t.map((t, k) => [t, cv.fit!.y[k]]), showSymbol: false, silent: true,
        lineStyle: { color, width: 1.3 }, tooltip: { show: false } });
    });
    const axis = { axisLine: { show: false }, axisTick: { show: true, lineStyle: { color: "#4d4d4d" } },
      axisLabel: { fontFamily: "IBM Plex Sans", fontSize: 12, color: "#4d4d4d" },
      splitLine: { show: true, lineStyle: { color: "#ffffff", width: 1.3 } },
      nameTextStyle: { fontFamily: "IBM Plex Sans", fontSize: 13, color: "#000" }, nameLocation: "middle" };
    return {
      animation: false,
      grid: { left: 64, right: 170, top: 16, bottom: 48, show: true, backgroundColor: "#ebebeb", borderWidth: 0 },
      legend: { orient: "vertical", right: 0, top: "middle", data: conds.map((c) => `${LETTERS[r.conditions.indexOf(c)]} · ${c}`),
        textStyle: { fontSize: 13 } },
      xAxis: { type: "value", min: 0, name: "Time since start (h)", nameGap: 28, ...axis },
      yAxis: { type: "value", scale: true, name: opt ? `${opt.label}${opt.unit ? ` (${opt.unit})` : ""}` : "", nameGap: 44, ...axis },
      tooltip: { trigger: "item", formatter: (q: { seriesName: string; data: (number | null)[] }) => {
        const [, y, sd, n, t] = q.data;
        return y == null ? "" : `${q.seriesName}<br/>t = ${fmt(t, 2)} h<br/><b>${fmt(y, 3)}</b>${sd != null ? ` ± ${fmt(sd, 3)}` : ""} (n = ${n})`;
      } },
      series,
    };
  }, [r, opt]);
  return <EChart option={option} className="session-chart" ariaLabel={`Mean ${opt?.label ?? ""} over time by condition`} />;
}

/** Figure download (PNG / SVG / PDF) of one server-side figure; `url` gets format and options appended. */
function FigureExport({ url, label = "Figure", defaultWidth = "174" }: { url: (fig: string) => string; label?: string; defaultWidth?: string }) {
  const [fig, setFig] = useState("png");
  const [width, setWidth] = useState(defaultWidth);
  const [dpi, setDpi] = useState("300");
  const [theme, setTheme] = useState("grey");
  const [mono, setMono] = useState(false);
  const w = Number(width);
  const ok = Number.isFinite(w) && w >= 40 && w <= 500;
  return (
    <div className="row small" style={{ gap: 8 }}>
      <span className="muted">{label}</span>
      <select value={fig} onChange={(x) => setFig(x.target.value)} style={{ height: 36 }} aria-label="Figure format">
        <option value="png">PNG</option><option value="svg">SVG</option><option value="pdf">PDF</option></select>
      <label className="row" style={{ gap: 4 }}><input value={width} onChange={(x) => setWidth(x.target.value)} className="mono"
        style={{ height: 36, width: 64, padding: "0 8px", border: "1px solid #74777f", borderRadius: 4 }} aria-invalid={!ok} aria-label="Width in mm" />mm</label>
      <select value={dpi} onChange={(x) => setDpi(x.target.value)} style={{ height: 36 }} disabled={fig !== "png"} aria-label="Resolution">
        <option value="300">300 dpi</option><option value="600">600 dpi</option></select>
      <select value={theme} onChange={(x) => setTheme(x.target.value)} style={{ height: 36 }} aria-label="Theme">
        <option value="grey">ggplot grey</option><option value="bw">ggplot bw</option><option value="classic">Classic</option></select>
      <select value={mono ? "1" : "0"} onChange={(x) => setMono(x.target.value === "1")} style={{ height: 36 }} aria-label="Series colours"
        title="Black and white: conditions told apart by line type, marker shape (filled / open) and hatching">
        <option value="0">Colour</option><option value="1">Black &amp; white</option></select>
      <a className="btn filled" style={{ height: 36 }} aria-disabled={!ok} download
        href={ok ? `${url(fig)}&width_mm=${w}&dpi=${dpi}&theme=${theme}&mono=${mono}` : undefined}>
        Export</a>
    </div>
  );
}

/** Quartiles as numpy / matplotlib compute them (linear interpolation). */
function quantile(sorted: number[], q: number) {
  const pos = (sorted.length - 1) * q, lo = Math.floor(pos), hi = Math.ceil(pos);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}
export function boxStats(values: number[]) {
  const s = [...values].sort((a, b) => a - b);
  const q1 = quantile(s, 0.25), med = quantile(s, 0.5), q3 = quantile(s, 0.75), iqr = q3 - q1;
  const inside = s.filter((v) => v >= q1 - 1.5 * iqr && v <= q3 + 1.5 * iqr);
  return { lo: inside[0], q1, med, q3, hi: inside[inside.length - 1], n: s.length };
}

/** Box-and-whisker of one per-experiment parameter by condition; every experiment is a point on top. */
function BoxPlotCard({ r, id, step }: { r: SessionResults; id: number; step: string }) {
  // Only parameters with at least one value in the selected experiments
  const avail = r.comparisons.filter((c) => r.experiments.some((x) => x.values[c.key] != null));
  const [param, setParam] = useState(avail[0]?.key ?? "");
  const k = avail.find((x) => x.key === param) ?? avail[0];
  const used = r.conditions.filter((c) => r.n_by_condition[c] > 0);
  const option = useMemo(() => {
    if (!k) return {};
    const groups = used.map((c) => {
      const i = r.conditions.indexOf(c);
      const pts = r.experiments.filter((x) => x.condition === c && x.values[k.key] != null)
        .map((x) => ({ v: x.values[k.key] as number, code: x.code, name: x.name }));
      return { c, i, pts, st: pts.length ? boxStats(pts.map((p) => p.v)) : null };
    });
    const boxes = groups.map((g, gi) => [gi, g.st?.lo, g.st?.q1, g.st?.med, g.st?.q3, g.st?.hi, g.i]).filter((d) => d[1] != null);
    let seed = 7;
    const jitter = () => { seed = (seed * 9301 + 49297) % 233280; return (seed / 233280 - 0.5) * 0.24; };
    return {
      animation: false,
      grid: { left: 72, right: 24, top: 16, bottom: 56, show: true, backgroundColor: "#ebebeb", borderWidth: 0 },
      xAxis: { type: "value", min: -0.6, max: groups.length - 0.4, splitLine: { show: false }, axisLine: { onZero: false },
        axisTick: { customValues: groups.map((_, gi) => gi) },
        axisLabel: { fontSize: 12, color: "#4d4d4d", lineHeight: 16, customValues: groups.map((_, gi) => gi),
          formatter: (v: number) => { const g = groups[Math.round(v)]; return g ? `${LETTERS[g.i]} · ${g.c}\n(n = ${g.pts.length})` : ""; } } },
      yAxis: { type: "value", scale: true, name: `${k.label}${k.unit ? ` (${k.unit})` : ""}`, nameLocation: "middle", nameGap: 52,
        axisLine: { show: false, onZero: false },
        axisLabel: { fontSize: 12, color: "#4d4d4d" }, splitLine: { lineStyle: { color: "#ffffff", width: 1.3 } },
        nameTextStyle: { fontSize: 13, color: "#000" } },
      tooltip: { trigger: "item" },
      series: [
        { type: "custom", silent: true, data: boxes, z: 2,
          renderItem: (_: unknown, api: { value: (i: number) => number; coord: (v: number[]) => number[]; size: (v: number[]) => number[] }) => {
            const x = api.value(0), [lo, q1, med, q3, hi] = [1, 2, 3, 4, 5].map((j) => api.value(j));
            const color = COND_COLORS[api.value(6) % 6];
            const half = api.size([0.28, 0])[0];
            const p = (y: number) => api.coord([x, y]);
            const [cx] = p(med);
            const st = { stroke: color, lineWidth: 1.2 };
            return { type: "group", children: [
              { type: "rect", shape: { x: cx - half, y: p(q3)[1], width: 2 * half, height: p(q1)[1] - p(q3)[1] },
                style: { fill: color + "38", stroke: color, lineWidth: 1.2 } },
              { type: "line", shape: { x1: cx - half, y1: p(med)[1], x2: cx + half, y2: p(med)[1] }, style: { stroke: "#000", lineWidth: 1.8 } },
              { type: "line", shape: { x1: cx, y1: p(q3)[1], x2: cx, y2: p(hi)[1] }, style: st },
              { type: "line", shape: { x1: cx, y1: p(q1)[1], x2: cx, y2: p(lo)[1] }, style: st },
              { type: "line", shape: { x1: cx - half / 2, y1: p(hi)[1], x2: cx + half / 2, y2: p(hi)[1] }, style: st },
              { type: "line", shape: { x1: cx - half / 2, y1: p(lo)[1], x2: cx + half / 2, y2: p(lo)[1] }, style: st },
            ] };
          } },
        ...groups.map((g, gi) => ({
          type: "scatter", z: 3, name: `${LETTERS[g.i]} · ${g.c}`, symbol: COND_SYMBOLS[g.i % 6], symbolSize: 9,
          itemStyle: { color: COND_COLORS[g.i % 6], borderColor: "#fff", borderWidth: 1 },
          data: g.pts.map((p) => ({ value: [gi + jitter(), p.v], code: p.code, nm: p.name })),
          tooltip: { formatter: (q: { data: { value: number[]; code: string; nm: string }; seriesName: string }) =>
            `${q.data.nm} <span style="opacity:.7">${q.data.code}</span><br/>${q.seriesName}: <b>${fmt(q.data.value[1], 4)}</b>` },
        })),
      ],
    };
  }, [r, k, used.join("|")]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (!k) return <span className="small muted">Distribution by condition: no per-experiment values yet.</span>;
  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="row" style={{ gap: 12 }}>
        <h3 style={{ margin: 0 }}>Distribution by condition</h3>
        <label className="row small" style={{ gap: 8 }}><span className="muted">Parameter</span>
          <select value={k.key} onChange={(x) => setParam(x.target.value)} style={{ height: 36 }}>
            {[...new Set(avail.map((c) => c.source))].map((src) => (
              <optgroup key={src} label={src}>
                {avail.filter((c) => c.source === src).map((c) => <option key={c.key} value={c.key}>{c.label}{c.unit ? ` (${c.unit})` : ""}</option>)}
              </optgroup>
            ))}
          </select>
        </label>
      </div>
      <EChart option={option} className="box-chart" ariaLabel={`Box plot of ${k.label} by condition`} />
      <span className="small muted">Box: median and quartiles; whiskers: most extreme values within 1.5 × IQR. Every experiment is a point.
        With 2–3 experiments per condition the box only summarises the points: read them, not the box.</span>
      <FigureExport label="Box plot" defaultWidth="85"
        url={(fig) => `/api/analysis-sessions/${id}/boxplot.${fig}?param=${encodeURIComponent(k.key)}&variable=${encodeURIComponent(r.variable)}&step_h=${step}`} />
    </div>
  );
}

function pval(p: number | null) {
  if (p == null) return "—";
  return p < 0.001 ? "< 0.001" : p.toFixed(3);
}

function ComparisonTable({ r }: { r: SessionResults }) {
  const [perExp, setPerExp] = useState(false);
  const used = r.conditions.filter((c) => r.n_by_condition[c] > 0);
  const two = used.length === 2;
  const digits = (k: string) => (k.includes("rate") ? 4 : k === "mu_max_per_h" ? 3 : 2);
  return (
    <>
      <div className="table-wrap">
        <table className="data" style={{ minWidth: 820 }}>
          <thead><tr><th>Parameter</th>
            {used.map((c) => <th key={c} className="num">{LETTERS[r.conditions.indexOf(c)]} · {c}</th>)}
            {two && <th className="num">Difference {LETTERS[r.conditions.indexOf(used[0])]} − {LETTERS[r.conditions.indexOf(used[1])]} [95 % CI]</th>}
            <th className="num">{two ? "p (Welch)" : used.length > 2 ? "p (ANOVA)" : "p"}</th></tr></thead>
          <tbody>
            {r.comparisons.map((k) => (
              <tr key={k.key}>
                <td>{k.label} <span className="muted">{k.unit && `(${k.unit})`}</span><span className="xs muted" style={{ display: "block" }}>{k.source}</span></td>
                {used.map((c) => {
                  const s = k.conditions[c];
                  return <td key={c} className="num mono">{s?.mean == null ? "—" : `${fmt(s.mean, digits(k.key))}${s.sd != null ? ` ± ${fmt(s.sd, digits(k.key))}` : ""}`}
                    {s && s.n !== r.n_by_condition[c] && <span className="xs muted" style={{ display: "block" }}>n = {s.n}</span>}</td>;
                })}
                {two && <td className="num mono">{k.difference == null ? "—" : `${fmt(k.difference, digits(k.key))}${k.ci95 && k.ci95[0] != null ? ` [${fmt(k.ci95[0], digits(k.key))}, ${fmt(k.ci95[1], digits(k.key))}]` : ""}`}</td>}
                <td className="num mono" style={{ fontWeight: k.p_value != null && k.p_value < 0.05 ? 600 : 400 }}>{pval(k.p_value)}
                  {k.posthoc.length > 0 && <details className="xs" style={{ textAlign: "left" }}><summary>Tukey</summary>
                    {k.posthoc.map((p) => <div key={p.a + p.b}>{LETTERS[r.conditions.indexOf(p.a)]}−{LETTERS[r.conditions.indexOf(p.b)]}: p<sub>adj</sub> {pval(p.p_adj)}</div>)}
                  </details>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row between">
        <span className="small muted">Values are computed from the selected experiments. With few replicates per condition the test has low power:
          read p together with the difference and its confidence interval. SD and tests need at least 2 experiments per condition.</span>
        <button className="btn text" onClick={() => setPerExp(!perExp)}>{perExp ? "Hide" : "Show"} per-experiment values</button>
      </div>
      {perExp && (
        <div className="table-wrap">
          <table className="data" style={{ minWidth: 900 }}>
            <thead><tr><th>Experiment</th><th>Condition</th>{r.comparisons.map((k) => <th key={k.key} className="num" title={k.label}>{k.label}</th>)}</tr></thead>
            <tbody>{r.experiments.map((x) => (
              <tr key={x.id}>
                <td><ExpName name={x.name} code={x.code} to={`/analysis/experiments/${x.id}`} />
                  {(x.fit_error || x.fit_warnings.length > 0) && <span className="xs" style={{ display: "block", color: "#8a5a00" }}>
                    <Icon name="warn" size={12} /> {x.fit_error ?? x.fit_warnings.join("; ")}</span>}</td>
                <td>{LETTERS[r.conditions.indexOf(x.condition)]} · {x.condition}</td>
                {r.comparisons.map((k) => <td key={k.key} className="num mono">{fmt(x.values[k.key], digits(k.key))}</td>)}
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </>
  );
}
