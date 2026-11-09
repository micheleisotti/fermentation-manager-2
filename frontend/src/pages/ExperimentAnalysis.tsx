import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import * as echarts from "echarts/core";
import { CustomChart, ScatterChart } from "echarts/charts";
import { api, type AnalysisResult, type Experiment } from "../api";
import { fmt, usePolling } from "../hooks";
import { C, DataValue, EChart, Icon, Segmented, StatusChip } from "../components/ui";

echarts.use([ScatterChart, CustomChart]);

const STEPS = [{ value: "0.25", label: "15 min" }, { value: "1", label: "1 h" }, { value: "2", label: "2 h" }, { value: "4", label: "4 h" }];
const PANEL_COLORS: Record<string, string> = {
  OD600: "#1d3786", AFU: "#7a4fa0", ph: "#1d3786", base_rate: "#1f7a63", base_cumulative_ml: "#1f7a63",
  temperature: "#b3261e", stirrer: "#54565a", do2: "#00639b",
};
const OTHER_COLORS = ["#8a5300", "#006a6a", "#5c4d00", "#7d2a52"];
const DEFAULT_ON = ["OD600", "AFU", "ph", "base_rate"];

interface PanelDef { key: string; title: string; color: string; manual: boolean }

/** Mode switch shared by the fermentation and analysis views of one experiment. */
export function ModeTabs({ e, current }: { e: Experiment; current: "fermentation" | "analysis" }) {
  const nav = useNavigate();
  const tabs = [
    { k: "fermentation", label: "Fermentation", to: `/experiments/${e.id}` },
    { k: "analysis", label: "Analysis", to: `/analysis/experiments/${e.id}` },
  ] as const;
  return (
    <div className="segmented mode-tabs" role="tablist" aria-label="Mode">
      {tabs.map((t) => (
        <button key={t.k} role="tab" aria-selected={t.k === current} className={t.k === current ? "on" : ""}
          onClick={() => nav(t.to)}>
          {t.k === current && <Icon name="check" size={16} width={2.2} />}{t.label}
        </button>
      ))}
    </div>
  );
}

export function ExperimentAnalysisPage() {
  const id = Number(useParams().id);
  const exp = usePolling(() => api.experiment(id), 60000, [id]);
  const [model, setModel] = useState("gompertz");
  const [step, setStep] = useState("1");
  const running = exp.data?.status === "fermenting";
  const an = usePolling(() => api.analysisFull(id, model, Number(step)), running ? 60000 : 3600000, [id, model, step]);
  const [chosen, setChosen] = useState<string[] | null>(null);
  const [showFit, setShowFit] = useState(true);

  const panelsAll: PanelDef[] = useMemo(() => {
    if (!an.data) return [];
    let other = 0;
    const manual = Object.entries(an.data.manual).map(([k, m]) => ({
      key: k, title: m.unit ? `${k} (${m.unit})` : k, manual: true,
      color: PANEL_COLORS[k] ?? OTHER_COLORS[other++ % OTHER_COLORS.length],
    }));
    const ctrl = an.data.series_defs.map((d) => ({
      key: d.key, title: d.unit ? `${d.label} (${d.unit})` : d.label, manual: false, color: PANEL_COLORS[d.key] ?? C.slate,
    }));
    return [...manual, ...ctrl];
  }, [an.data]);
  const on = chosen ?? panelsAll.filter((p) => DEFAULT_ON.includes(p.key)).map((p) => p.key);
  const panels = panelsAll.filter((p) => on.includes(p.key));

  if (exp.error) return <div className="card">Experiment not found: {exp.error}</div>;
  if (!exp.data) return <div className="muted">Loading…</div>;
  const e = exp.data;
  const a = an.data;
  const tMax = a ? Math.max(1, a.summary.recorded_h ?? 0, ...Object.values(a.manual).flatMap((m) => m.t), ...a.series.t) : 1;

  return (
    <>
      <header className="row between">
        <div className="row" style={{ gap: 12 }}>
          <Link to={`/experiments/${e.id}`} className="btn icon text" aria-label="Back to the experiment"><Icon name="back" /></Link>
          <div className="stack" style={{ gap: 2 }}>
            <h1 style={{ fontSize: 22 }}><span className="mono">{e.code}</span> · {e.name}{e.strain && <> — <i>L. plantarum</i> {e.strain}</>}</h1>
            <span className="small muted">Bioreactor {e.reactor_id.slice(1)}
              {e.started_at && ` · ${new Date(e.started_at).toLocaleDateString()}`}
              {a?.summary.recorded_h != null && ` · recorded ${fmt(a.summary.recorded_h, 1)} h`}
              {a?.experiment.partner && <> · parallel run <b className="mono">{a.experiment.partner.parallel_run}</b> with {a.experiment.partner.code} ({a.experiment.partner.reactor_id})</>}
            </span>
          </div>
          <StatusChip status={e.status} />
        </div>
        <ModeTabs e={e} current="analysis" />
      </header>

      {running && <div className="card tight small" style={{ background: "#fff8e1" }}>
        The experiment is still recording: parameters are provisional and are recalculated every minute.</div>}
      {an.error && <div className="card tight" style={{ color: "#b30020" }}>Analysis failed: {an.error}</div>}
      {!a ? <div className="muted">Calculating…</div> : (
        <>
          <section className="card summary-grid" aria-label="Summary">
            <DataValue value={fmt(a.summary.recorded_h, 1)} unit="h" label="Recorded" color={C.ink} />
            <DataValue value={fmt(a.summary.final_od, 2)} unit="AU" label="Final OD600" color={C.blue} />
            <DataValue value={fmt(a.summary.max_afu, 0)} unit="AFU" label="Max AFU" color={C.blue} />
            <DataValue value={fmt(a.summary.total_base_ml, 1)} unit="mL" label="Total base" color={C.greenDark} />
            <DataValue value={fmt(a.summary.peak_base_rate_ml_min, 3)} unit="mL/min" label="Peak base rate" color={C.greenDark} />
            <DataValue value={a.summary.out_of_limit_events} label="Out-of-limit events" color={a.summary.out_of_limit_events ? "#b30020" : C.ink} />
          </section>

          <section className="card" aria-labelledby="tc">
            <div className="row between">
              <h2 id="tc" style={{ fontSize: 18 }}>Time course</h2>
              <div className="row" style={{ gap: 12 }}>
                <span className="small muted">Controller data every</span>
                <Segmented label="Point interval" options={STEPS} value={step} onChange={setStep} />
                <label className="row small" style={{ gap: 6 }}>
                  <input type="checkbox" checked={showFit} onChange={(x) => setShowFit(x.target.checked)} style={{ width: 18, height: 18 }} />Fitted model
                </label>
              </div>
            </div>
            <div className="row" role="group" aria-label="Variables shown" style={{ gap: 8 }}>
              {panelsAll.map((p) => (
                <button key={p.key} className={`chip ${on.includes(p.key) ? "on" : ""}`} aria-pressed={on.includes(p.key)}
                  onClick={() => setChosen(on.includes(p.key) ? on.filter((k) => k !== p.key) : [...on, p.key])}>
                  {p.title.replace(/ \(.*\)$/, "")}
                </button>
              ))}
            </div>
            {panels.length === 0 ? <span className="muted">Select at least one variable.</span> : (
              <div className="facets">
                {panels.map((p) => (
                  <div key={p.key} className="facet">
                    <div className="facet-strip">{p.title}</div>
                    <EChart className="facet-chart" ariaLabel={`${p.title} over time since start`}
                      option={facetOption(a, p, tMax, showFit)} />
                  </div>
                ))}
              </div>
            )}
            <span className="small muted">Time since start (h). Free y scales. Manual variables: points (mean ± SD where a time point has
              repeated readings), line = fitted {a.models[model]} model. Controller data: mean over each interval;
              base rate from the cumulative volume (gaps shown as breaks).</span>
          </section>

          <div className="split">
            <div className="wide">
              <KineticsCard a={a} model={model} setModel={setModel} />
            </div>
            <div className="side">
              <ExportCard e={e} on={panels.map((p) => p.key)} model={model} step={step} showFit={showFit} />
            </div>
          </div>
        </>
      )}
    </>
  );
}

function facetOption(a: AnalysisResult, p: PanelDef, tMax: number, showFit: boolean): echarts.EChartsCoreOption {
  const axis = {
    axisLine: { show: false }, axisTick: { show: true, lineStyle: { color: "#4d4d4d" } },
    axisLabel: { fontFamily: "IBM Plex Sans", fontSize: 11, color: "#4d4d4d" },
    splitLine: { show: true, lineStyle: { color: "#ffffff", width: 1.2 } },
  };
  const series: unknown[] = [];
  if (p.manual) {
    const m = a.manual[p.key];
    const pts = m.t.map((t, i) => [t, m.mean[i], m.sd[i], m.n[i]]);
    if (m.sd.some((s) => s != null)) {
      series.push({
        type: "custom", name: "SD", silent: true, data: pts.filter((d) => d[2] != null),
        renderItem: (_: unknown, api: { value: (i: number) => number; coord: (v: number[]) => number[] }) => {
          const [t, y, sd] = [api.value(0), api.value(1), api.value(2)];
          const lo = api.coord([t, y - sd]), hi = api.coord([t, y + sd]);
          const st = { stroke: p.color, lineWidth: 1 };
          return { type: "group", children: [
            { type: "line", shape: { x1: lo[0], y1: lo[1], x2: hi[0], y2: hi[1] }, style: st },
            { type: "line", shape: { x1: lo[0] - 3, y1: lo[1], x2: lo[0] + 3, y2: lo[1] }, style: st },
            { type: "line", shape: { x1: hi[0] - 3, y1: hi[1], x2: hi[0] + 3, y2: hi[1] }, style: st },
          ] };
        },
      });
    }
    series.push({ type: "scatter", name: p.key, data: pts, symbolSize: 7, itemStyle: { color: p.color } });
    const f = a.fits[p.key];
    if (showFit && f?.curve) {
      series.push({ type: "line", name: "fit", data: f.curve.t.map((t, i) => [t, f.curve!.y[i]]), showSymbol: false,
        lineStyle: { color: p.color, width: 1.4 }, silent: true });
    }
  } else {
    series.push({ type: "line", name: p.key, data: a.series.t.map((t, i) => [t, a.series[p.key]?.[i] ?? null]),
      showSymbol: a.series.t.length < 40, symbolSize: 5, connectNulls: false, step: p.key === "base_rate" ? "middle" : undefined,
      itemStyle: { color: p.color }, lineStyle: { color: p.color, width: 1.4 } });
  }
  return {
    animation: false,
    grid: { left: 48, right: 14, top: 8, bottom: 26, show: true, backgroundColor: "#ebebeb", borderWidth: 0 },
    xAxis: { type: "value", min: 0, max: Math.ceil(tMax), ...axis, axisLabel: { ...axis.axisLabel, showMaxLabel: false } },
    yAxis: { type: "value", scale: true, ...axis },
    tooltip: {
      trigger: "item", valueFormatter: undefined,
      formatter: (q: { data: (number | null)[]; seriesName: string }) => {
        const [t, y, sd, n] = q.data;
        if (y == null) return "";
        return `t = ${fmt(t, 2)} h<br/><b>${fmt(y, 3)}</b>${sd != null ? ` ± ${fmt(sd, 3)} (n = ${n})` : ""}`;
      },
    },
    series,
  };
}

function pm(v: number | undefined | null, se: number | undefined | null, d: number) {
  if (v == null) return "—";
  return se != null && Number.isFinite(se) ? `${fmt(v, d)} ± ${fmt(se, d)}` : fmt(v, d);
}

function KineticsCard({ a, model, setModel }: { a: AnalysisResult; model: string; setModel: (m: string) => void }) {
  const vars = Object.keys(a.fits);
  const rows: { p: string; u: string; v: (k: string) => string }[] = [
    { p: "Lag time λ", u: "(h)", v: (k) => pm(a.fits[k].params?.lag_h, a.fits[k].params?.lag_se, 2) },
    { p: "μmax", u: "(h⁻¹)", v: (k) => pm(a.fits[k].params?.mu_max_per_h, a.fits[k].params?.mu_max_se, 3) },
    { p: "Doubling time at μmax", u: "(h)", v: (k) => fmt(a.fits[k].params?.doubling_time_h, 2) },
    { p: "Time at μmax", u: "(h)", v: (k) => fmt(a.fits[k].params?.t_mu_max_h, 2) },
    { p: "Asymptote A", u: "(ln X/X₀)", v: (k) => pm(a.fits[k].params?.A_ln, a.fits[k].params?.A_se, 3) },
    { p: "Predicted maximum X₀·e^A", u: "", v: (k) => fmt(a.fits[k].params?.X_max, 3) + (a.manual[k]?.unit ? ` ${a.manual[k].unit}` : "") },
    { p: "Fit R²", u: "(ln scale)", v: (k) => fmt(a.fits[k].params?.r_squared, 4) },
    { p: "RMSE", u: "(ln scale)", v: (k) => fmt(a.fits[k].params?.rmse, 4) },
    { p: "Readings (time points)", u: "", v: (k) => `${a.fits[k].params?.n_points ?? "—"} (${a.fits[k].params?.n_times ?? "—"})` },
  ];
  const bd = a.base_dosing;
  const binLabel = a.bin_h === 0.25 ? "15-min bins" : `${a.bin_h} h bins`;
  const bufRows = [
    { p: "Total base", u: "(mL)", v: fmt(bd?.total_ml, 2), src: "controller, cumulative at end" },
    { p: "Peak base rate", u: "(mL min⁻¹)", v: fmt(bd?.peak_rate_ml_min, 4), src: binLabel },
    { p: "Time of peak base rate", u: "(h)", v: fmt(bd?.t_peak_h, 2), src: `${binLabel}, bin centre` },
    { p: "Time to 50 % of total base", u: "(h)", v: fmt(bd?.t_half_total_h, 2), src: "controller, cumulative" },
    { p: "Base rate in the last hour", u: "(mL min⁻¹)", v: fmt(bd?.last_hour_rate_ml_min, 4), src: "slowdown at end of run" },
    { p: "Total base per final OD600", u: "(mL AU⁻¹)", v: fmt(bd?.base_per_final_od, 2), src: "proxy of acid per biomass" },
    { p: "Doses", u: "", v: bd?.n_doses == null ? "—" : String(bd.n_doses), src: "steps of the cumulative counter" },
  ];
  const partner = a.experiment.partner;
  return (
    <section className="card" aria-labelledby="k">
      <div className="row between">
        <h2 id="k" style={{ fontSize: 18 }}>Kinetic and base-dosing parameters</h2>
        <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <span className="muted">Model</span>
          <select value={model} onChange={(x) => setModel(x.target.value)} style={{ height: 40, width: "auto" }}>
            {Object.entries(a.models).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </label>
      </div>
      {vars.length === 0 ? <span className="muted">No manual measurements: nothing to fit.</span> : (
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Parameter</th>{vars.map((k) => <th key={k} className="num">on {k}</th>)}</tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.p}><td>{r.p} <span className="muted">{r.u}</span></td>
                  {vars.map((k) => <td key={k} className="num mono">{a.fits[k].params ? r.v(k) : "—"}</td>)}</tr>
              ))}
            </tbody>
          </table>
          {vars.filter((k) => a.fits[k].params?.warnings.length).map((k) => (
            <div key={k} className="small" style={{ color: "#8a5a00", marginTop: 6 }}>
              <Icon name="warn" size={14} /> <b>{k}:</b> {a.fits[k].params!.warnings.join("; ")} — interpret with caution.</div>
          ))}
          {vars.filter((k) => a.fits[k].error).map((k) => (
            <div key={k} className="small" style={{ color: "#8a5a00", marginTop: 6 }}><b>{k}:</b> {a.fits[k].error}</div>
          ))}
          <div className="small muted" style={{ marginTop: 6 }}>Fitted on y = ln(X/X₀), X₀ = first measurement; ± = standard error of the fit.</div>
        </div>
      )}
      {bd ? (
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Base dosing</th><th className="num">Value</th><th>Source</th></tr></thead>
            <tbody>{bufRows.map((r) => (
              <tr key={r.p}><td>{r.p} <span className="muted">{r.u}</span></td><td className="num mono">{r.v}</td>
                <td className="small muted">{r.src}</td></tr>
            ))}</tbody>
          </table>
        </div>
      ) : <span className="small muted">No base-dosing data (pH channel off or no controller readings).</span>}
      {partner && (
        <button className="btn tonal" disabled title="Analysis sessions are the next screen to build" style={{ alignSelf: "flex-start" }}>
          Compare with {partner.code} (parallel run) in a new analysis session
        </button>
      )}
    </section>
  );
}

const RESAMPLE = [{ v: "", l: "raw (every reading)" }, { v: "1", l: "1 min" }, { v: "5", l: "5 min" }, { v: "15", l: "15 min" }, { v: "60", l: "1 h" }];
const BLOCKS = [
  { k: "controller", l: "Controller data (pH, T, stirrer, base, actuator outputs)" },
  { k: "manual", l: "Manual variables" },
  { k: "parameters", l: "Kinetic and base-dosing parameters" },
  { k: "metadata", l: "Metadata, profile, change log and events" },
];

function ExportCard({ e, on, model, step, showFit }: { e: Experiment; on: string[]; model: string; step: string; showFit: boolean }) {
  const [blocks, setBlocks] = useState<string[]>(BLOCKS.map((b) => b.k));
  const [resample, setResample] = useState("");
  const [format, setFormat] = useState<"xlsx" | "zip">("xlsx");
  const [fig, setFig] = useState<"png" | "svg" | "pdf">("png");
  const [width, setWidth] = useState("174");
  const [dpi, setDpi] = useState("300");
  const [theme, setTheme] = useState("grey");
  const w = Number(width);
  const widthOk = Number.isFinite(w) && w >= 40 && w <= 500;
  const dataUrl = `/api/experiments/${e.id}/export.${format}?blocks=${blocks.join(",")}&model=${model}${resample ? `&resample_min=${resample}` : ""}`;
  const figUrl = `/api/experiments/${e.id}/figure.${fig}?vars=${encodeURIComponent(on.join(","))}&model=${model}&step_h=${step}` +
    `&width_mm=${w}&dpi=${dpi}&theme=${theme}&fit=${showFit}`;
  return (
    <section className="card" aria-labelledby="ex">
      <h2 id="ex" style={{ fontSize: 18 }}>Export</h2>
      <fieldset className="stack" style={{ border: 0, padding: 0, margin: 0, gap: 4 }}>
        <legend className="small muted" style={{ paddingBottom: 6 }}>Data</legend>
        {BLOCKS.map((b) => (
          <div key={b.k}>
            <label className="row" style={{ gap: 12, minHeight: 40, flexWrap: "nowrap" }}>
              <input type="checkbox" checked={blocks.includes(b.k)} style={{ width: 20, height: 20, flex: "none" }}
                onChange={() => setBlocks(blocks.includes(b.k) ? blocks.filter((x) => x !== b.k) : [...blocks, b.k])} />{b.l}
            </label>
            {b.k === "controller" && (
              <label className="row small muted" style={{ gap: 8, paddingLeft: 32 }}>Resample
                <select value={resample} onChange={(x) => setResample(x.target.value)} style={{ height: 36, width: "auto" }}
                  disabled={!blocks.includes("controller")}>
                  {RESAMPLE.map((r) => <option key={r.v} value={r.v}>{r.l}</option>)}
                </select>
              </label>
            )}
          </div>
        ))}
      </fieldset>
      <Segmented label="Data format" value={format} onChange={setFormat}
        options={[{ value: "zip", label: "CSV (zip)" }, { value: "xlsx", label: "XLSX · one sheet per block" }]} />
      <a className={`btn filled ${blocks.length ? "" : "disabled"}`} href={blocks.length ? dataUrl : undefined} download
        aria-disabled={!blocks.length} style={{ justifyContent: "center" }}>Export data</a>
      <div style={{ height: 1, background: "#eef0f4" }} />
      <fieldset className="stack" style={{ border: 0, padding: 0, margin: 0, gap: 10 }}>
        <legend className="small muted" style={{ paddingBottom: 6 }}>Figure (the panels shown above)</legend>
        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          <label className="field" style={{ flex: "1 1 90px" }}>Format
            <select value={fig} onChange={(x) => setFig(x.target.value as "png")}>
              <option value="png">PNG</option><option value="svg">SVG</option><option value="pdf">PDF</option></select>
          </label>
          <label className="field" style={{ flex: "1 1 90px" }}>Width (mm)
            <input className="mono" value={width} onChange={(x) => setWidth(x.target.value)} inputMode="decimal"
              aria-invalid={!widthOk} />
          </label>
          <label className="field" style={{ flex: "1 1 90px" }}>Resolution
            <select value={dpi} onChange={(x) => setDpi(x.target.value)} disabled={fig !== "png"}>
              <option value="300">300 dpi</option><option value="600">600 dpi</option></select>
          </label>
        </div>
        <label className="field">Theme
          <select value={theme} onChange={(x) => setTheme(x.target.value)}>
            <option value="grey">ggplot grey (as shown)</option><option value="bw">ggplot bw (white panel)</option>
            <option value="classic">Classic (axes only)</option>
          </select>
        </label>
        <span className="small muted">Common widths: 85 mm (one column), 174 mm (two columns).</span>
      </fieldset>
      <a className="btn outlined" href={widthOk && on.length ? figUrl : undefined} download aria-disabled={!widthOk || !on.length}
        style={{ justifyContent: "center" }}>Export figure</a>
      <span className="small muted">Times in exports are hours since start; absolute timestamps (UTC) are a separate column.</span>
    </section>
  );
}

/** /analysis: completed experiments to open, and the place for analysis sessions. */
export function AnalysisHome() {
  const list = usePolling(() => api.experiments(), 60000);
  const done = (list.data ?? []).filter((x) => x.status !== "planned")
    .sort((a, b) => (b.started_at ?? "").localeCompare(a.started_at ?? ""));
  return (
    <>
      <h1>Analysis</h1>
      <section className="card" aria-labelledby="one">
        <h2 id="one" style={{ fontSize: 18 }}>Single experiment</h2>
        {done.length === 0 ? <span className="muted">No recorded experiments yet.</span> : (
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>Code</th><th>Name</th><th>Strain</th><th>Reactor</th><th>Started</th><th>Status</th></tr></thead>
              <tbody>{done.map((x) => (
                <tr key={x.id} >
                  <td className="mono"><Link to={`/analysis/experiments/${x.id}`}>{x.code}</Link></td>
                  <td>{x.name}</td><td>{x.strain || "—"}</td><td className="mono">{x.reactor_id}</td>
                  <td>{x.started_at ? new Date(x.started_at).toLocaleDateString() : "—"}</td>
                  <td><StatusChip status={x.status} /></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </section>
      <section className="card">
        <h2 style={{ fontSize: 18 }}>Analysis sessions</h2>
        <span className="muted">Comparing experiments by condition (Welch t-test, ANOVA + Tukey) is the next screen to build.</span>
      </section>
    </>
  );
}
