/**
 * Phone layout of a fermentation (approved mockups "M3Phone" and "M3PhoneChart"):
 * reactor tabs, four key values, one chart with presets and a full-screen landscape view,
 * collapsible panels for events, a new measurement and setpoints.
 */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type Channel, type EventItem, type Experiment, type Readings } from "../api";
import { fmt, fmtDuration, usePolling } from "../hooks";
import { BASE_RATE_VAR, phAndRateOption, trendsOption, TREND_VARS, varsFor, type TrendVar } from "../components/charts";
import { DataValue, EChart, Icon, StatusChip } from "../components/ui";
import { SetpointDialog } from "../components/SetpointDialog";
import { LimitsDialog } from "../components/LimitsDialog";

const V = (k: string) => TREND_VARS.find((v) => v.key === k)!;
const PRESETS: { key: string; label: string; left: TrendVar; right: TrendVar | null; needs: Channel[] }[] = [
  { key: "ph_rate", label: "pH + base rate", left: V("ph"), right: BASE_RATE_VAR, needs: ["ph"] },
  { key: "ph_cum", label: "pH + base cumulative", left: V("ph"), right: V("base_cumulative_ml"), needs: ["ph"] },
  { key: "ph", label: "pH only", left: V("ph"), right: null, needs: ["ph"] },
  { key: "ph_t", label: "pH + temperature", left: V("ph"), right: V("temperature"), needs: ["ph", "temperature"] },
  { key: "t", label: "Temperature only", left: V("temperature"), right: null, needs: ["temperature"] },
  { key: "t_heat", label: "Temperature + heating", left: V("temperature"), right: V("heating_pct"), needs: ["temperature"] },
  { key: "stir", label: "Stirrer", left: V("stirrer"), right: V("stirrer_out_rpm"), needs: ["stirrer"] },
  { key: "do2", label: "dO2 + N2", left: V("do2"), right: V("n2_pct"), needs: ["do2"] },
  { key: "do2_air", label: "dO2 + air", left: V("do2"), right: V("air_pct"), needs: ["do2"] },
];

const dotColor = (sev: EventItem["severity"]) => (sev === "alert" ? "bad" : sev === "good" ? "ok" : "idle");
const clock = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });

function Collapsible({ open, onToggle, icon, title, sub, aside, children, label }:
  { open: boolean; onToggle: () => void; icon: ReactNode; title: ReactNode; sub?: ReactNode; aside?: ReactNode; children: ReactNode; label: string }) {
  return (
    <section className="card p-panel" aria-label={label}>
      <button className="p-panel-head" aria-expanded={open} onClick={onToggle}>
        {icon}
        <span className="stack" style={{ gap: 0, flexGrow: 1, minWidth: 0 }}>
          {sub && <span className="xs muted">{sub}</span>}
          <span className="p-panel-title">{title}</span>
        </span>
        {aside}
        <span className={`p-chev ${open ? "open" : ""}`} aria-hidden="true"><Icon name="chevron" size={22} color="#54565a" width={2} /></span>
      </button>
      {open && <div className="p-panel-body">{children}</div>}
    </section>
  );
}

export function PhoneFermentation({ e, partner }: { e: Experiment; partner?: Experiment }) {
  const nav = useNavigate();
  const running = e.status === "fermenting";
  const readings = usePolling(() => api.readings(e.id), running ? 30000 : 600000, [e.id]);
  const partnerReadings = usePolling<Readings | undefined>(() => (partner ? api.readings(partner.id) : Promise.resolve(undefined)), 60000, [partner?.id]);
  const analysis = usePolling(() => api.analysis(e.id, 0.25), running ? 60000 : 600000, [e.id]);
  const live = usePolling(api.live, running ? 10000 : 600000);
  const events = usePolling(() => api.events(e.id), 20000, [e.id]);
  const partnerEvents = usePolling(() => (partner ? api.events(partner.id) : Promise.resolve([] as EventItem[])), 30000, [partner?.id]);

  const ch = e.channels;
  const presets = PRESETS.filter((x) => x.needs.every((n) => ch.includes(n)));
  const [preset, setPreset] = useState(presets[0]?.key ?? "");
  const [full, setFull] = useState(false);
  const [evOpen, setEvOpen] = useState(false);
  const [amOpen, setAmOpen] = useState(running);
  const [spOpen, setSpOpen] = useState(false);
  const [dialog, setDialog] = useState<string | null>(null);
  const [limitsDialog, setLimitsDialog] = useState<string | null>(null);

  const r = live.data?.find((x) => x.reactor_id === e.reactor_id);
  const s = running ? r?.snapshot : null;
  const tOutOf = () => !!s && s.temperature != null && s.temperature_low_limit != null && s.temperature_high_limit != null &&
    (s.temperature < s.temperature_low_limit || s.temperature > s.temperature_high_limit);
  const phOutOf = () => !!s && s.ph != null && s.ph_low_limit != null && s.ph_high_limit != null &&
    (s.ph < s.ph_low_limit || s.ph > s.ph_high_limit);
  const lastBin = analysis.data?.rate_bins.filter((b) => b.complete).at(-1);
  const p = presets.find((x) => x.key === preset) ?? presets[0];
  const option = useMemo(() => (!p ? {} : p.key === "ph_rate"
    ? phAndRateOption(readings.data, analysis.data?.rate_bins)
    : trendsOption(readings.data, p.left, p.right, { setpoints: true, limits: true, bins: analysis.data?.rate_bins, compact: true })),
  [readings.data, analysis.data, p]);
  const values = [
    ch.includes("ph") && <DataValue key="ph" value={fmt(s?.ph)} label={`pH · set ${fmt(s?.ph_setpoint)}`} color={phOutOf() ? "#b30020" : "#1d3786"} />,
    ch.includes("temperature") && <DataValue key="t" value={fmt(s?.temperature, 1)} unit="°C" label={`T · set ${fmt(s?.temperature_setpoint, 1)}`} color={tOutOf() ? "#b30020" : undefined} />,
    ch.includes("ph") && <DataValue key="cum" value={fmt(s?.base_cumulative_ml ?? (analysis.data?.base_dosing?.total_ml as number), 1)} unit="mL" label="Base, cumulative" color="#1f7a63" />,
    ch.includes("ph") && <DataValue key="rate" value={fmt(lastBin?.rate_ml_min, 3)} unit="mL/min" label="Base rate, 15 min" color="#1f7a63" />,
    ch.includes("do2") && <DataValue key="do2" value={fmt(s?.extra?.do2, 1)} unit="%" label={`dO2 · set ${fmt(s?.extra?.do2_setpoint, 1)}`} />,
    ch.includes("stirrer") && <DataValue key="stir" value={fmt(s?.stirrer, 0)} unit="rpm" label={`Stirrer · set ${fmt(s?.stirrer_setpoint, 0)}`} />,
  ].filter(Boolean).slice(0, 4);

  const evs = events.data ?? [];
  const recentAlert = (list: EventItem[], id?: number) => list.find((x) => x.experiment_id === id && x.severity === "alert" &&
    Date.now() - new Date(x.at).getTime() < 30 * 60 * 1000);
  const alert = recentAlert(evs, e.id);
  const partnerAlert = partner ? recentAlert(partnerEvents.data ?? [], partner.id) : undefined;

  const tabs = partner ? [e, partner].sort((a, b) => a.reactor_id.localeCompare(b.reactor_id)) : [];

  useEffect(() => {   // as in the mockup: no bottom navigation on this screen (back arrow instead)
    document.body.classList.add("no-rail");
    return () => document.body.classList.remove("no-rail");
  }, []);

  return (
    <div className="phone">
      <header className="row p-top">
        <Link to="/" className="btn icon text" aria-label="Back to home"><Icon name="back" /></Link>
        <span className="p-top-title">
          {e.parallel_run_code ? <>Parallel run <span className="mono">{e.parallel_run_code}</span></> : <span className="mono">{e.code}</span>}
        </span>
        {running && <span className="row xs muted" style={{ gap: 6 }}><span className={`dot ${r?.online ? "ok" : "bad"}`} />{r?.online ? "Live" : "Offline"}</span>}
      </header>

      {tabs.length > 0 && (
        <div role="tablist" aria-label="Reactor" className="p-tabs">
          {tabs.map((x) => (
            <button key={x.id} role="tab" aria-selected={x.id === e.id} className={x.id === e.id ? "on" : ""}
              onClick={() => x.id !== e.id && nav(`/experiments/${x.id}`, { replace: true })}>
              <span className="mono">{x.reactor_id}</span> · {x.strain || x.code}
              {x.id !== e.id && partnerAlert && <span className="dot bad" aria-label="Alert" style={{ marginLeft: 4 }} />}
            </button>
          ))}
        </div>
      )}

      <div className="row between" style={{ flexWrap: "nowrap", alignItems: "center" }}>
        <span className="stack" style={{ gap: 0, minWidth: 0 }}>
          <span style={{ fontSize: 17, fontWeight: 500 }}><span className="mono">{e.code}</span> · {e.strain || e.name}</span>
          <span className="xs muted">
            {e.started_at ? <>t = <span className="mono">{fmtDuration(running ? r?.experiment?.t_h : null)}</span>{running ? " since start" : ""}</> : "Not started"}
            {running && <> · reading <span className="mono">{r?.last_reading_age_s == null ? "—" : `${Math.round(r.last_reading_age_s)} s`}</span> ago</>}
          </span>
        </span>
        <StatusChip status={e.status} />
      </div>

      {alert && <div className="banner alert" role="alert"><Icon name="bell" color="#b30020" size={18} /><span>{alert.message}</span></div>}
      {running && r && !r.online && <div className="banner alert" role="alert"><Icon name="bell" color="#b30020" size={18} /><span>Controller not responding{r.error ? `: ${r.error}` : ""}</span></div>}

      {values.length > 0 && <div className="p-values">{values.map((v, i) => <div key={i} className="card tight">{v}</div>)}</div>}

      {p && <section className="card tight" aria-label="Chart">
        <div className="row between" style={{ flexWrap: "nowrap" }}>
          <label className="p-select"><span className="sr-only">Variables shown</span>
            <select value={preset} onChange={(x) => setPreset(x.target.value)}>
              {presets.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
            </select>
          </label>
          <button className="btn icon tonal" aria-label="Open chart full screen" onClick={() => setFull(true)}><Icon name="expand" size={20} width={2} /></button>
        </div>
        <EChart option={option} className="chart p-chart" ariaLabel={`${p.label} since start`} />
        <div className="row xs muted" style={{ gap: 12 }}>
          <span className="row" style={{ gap: 4 }}><span style={{ width: 12, height: 2, background: "#1d3786" }} />{p.left.label}</span>
          {p.left.limits && <span className="row" style={{ gap: 4 }}><span style={{ width: 12, borderTop: "1px dashed #ea0029" }} />Limits</span>}
          {p.right && <span className="row" style={{ gap: 4 }}><span style={{ width: 8, height: 8, background: p.right.key === "base_rate" ? "#38b292" : "#1f7a63" }} />{p.right.label}</span>}
        </div>
      </section>}

      <Collapsible label="Recent events" open={evOpen} onToggle={() => setEvOpen(!evOpen)}
        icon={<span className={`dot ${evs[0] ? dotColor(evs[0].severity) : "idle"}`} style={{ width: 10, height: 10, flex: "0 0 auto" }} />}
        sub={`Recent events · ${evs.length}`} title={evs[0]?.message ?? "No events yet"}
        aside={evs[0] && <span className="xs muted mono">{clock(evs[0].at)}</span>}>
        {evs.slice(0, 12).map((x) => (
          <div key={x.id} className="p-row">
            <span className={`dot ${dotColor(x.severity)}`} style={{ width: 10, height: 10, marginTop: 5, flex: "0 0 auto" }} />
            <span className="stack" style={{ gap: 0, flexGrow: 1 }}>
              <span className="small">{x.message}</span>
              {x.t_h != null && <span className="xs muted">t = {x.t_h.toFixed(1)} h</span>}
            </span>
            <span className="xs muted mono">{clock(x.at)}</span>
          </div>
        ))}
      </Collapsible>

      {running && (
        <Collapsible label="Add measurement" open={amOpen} onToggle={() => setAmOpen(!amOpen)}
          icon={<Icon name="plus" size={22} color="#003366" width={2} />} title="Add measurement"
          aside={<span className="xs muted">to {e.code}</span>}>
          <PhoneMeasurement e={e} startedAt={e.started_at} />
        </Collapsible>
      )}

      {running && s && ch.some((c) => c !== "do2") && (
        <Collapsible label="Setpoints" open={spOpen} onToggle={() => setSpOpen(!spOpen)}
          icon={<Icon name="sliders" size={22} color="#003366" />} title={<>Setpoints & limits · <span className="mono">{e.reactor_id}</span></>}
          sub={<span className="mono">{[ch.includes("ph") && `pH ${fmt(s.ph_setpoint)}`, ch.includes("temperature") && `${fmt(s.temperature_setpoint, 1)} °C`,
            ch.includes("stirrer") && `${fmt(s.stirrer_setpoint, 0)} rpm`].filter(Boolean).join(" · ")}</span>}>
          {[
            { p: "ph", name: "pH", v: fmt(s.ph_setpoint), lim: `limits ${fmt(s.ph_low_limit)}–${fmt(s.ph_high_limit)}` },
            { p: "temperature", name: "Temperature", v: `${fmt(s.temperature_setpoint, 1)} °C`, lim: `limits ${fmt(s.temperature_low_limit, 1)}–${fmt(s.temperature_high_limit, 1)} °C` },
            { p: "stirrer", name: "Stirrer", v: `${fmt(s.stirrer_setpoint, 0)} rpm`, lim: "" },
          ].filter((row) => ch.includes(row.p as Channel)).map((row) => (
            <div key={row.p} className="p-row" style={{ alignItems: "center" }}>
              <span className="stack" style={{ gap: 0, flexGrow: 1 }}>{row.name}{row.lim && <span className="xs muted">{row.lim}</span>}</span>
              <span className="mono">{row.v}</span>
              <button className="btn text" onClick={() => setDialog(row.p)}>Edit</button>
            </div>
          ))}
          {[
            { p: "ph", name: "pH process limits", v: `${fmt(s.ph_low_limit)}–${fmt(s.ph_high_limit)}` },
            { p: "temperature", name: "T process limits", v: `${fmt(s.temperature_low_limit, 1)}–${fmt(s.temperature_high_limit, 1)} °C` },
          ].filter((row) => ch.includes(row.p as Channel)).map((row) => (
            <div key={`lim-${row.p}`} className="p-row" style={{ alignItems: "center" }}>
              <span className="stack" style={{ gap: 0, flexGrow: 1 }}>{row.name}<span className="xs muted">alarm only · no effect on pumps</span></span>
              <span className="mono small">{row.v}</span>
              <button className="btn text" onClick={() => setLimitsDialog(row.p)}>Edit</button>
            </div>
          ))}
        </Collapsible>
      )}

      {full && <FullscreenChart e={e} partner={partner} partnerReadings={partnerReadings.data} onClose={() => setFull(false)} />}
      {limitsDialog && s && (
        <LimitsDialog reactor={e.reactor_id} parameter={limitsDialog} snapshot={s} remote={!["localhost", "127.0.0.1"].includes(location.hostname)}
          onClose={(changed) => { setLimitsDialog(null); if (changed) live.refresh(); }} />
      )}
      {dialog && s && (
        <SetpointDialog reactor={e.reactor_id} parameter={dialog} snapshot={s} remote={!["localhost", "127.0.0.1"].includes(location.hostname)}
          onClose={(changed) => { setDialog(null); if (changed) live.refresh(); }} />
      )}
    </div>
  );
}

/** Add measurement: variable chips, big inputs, defaults to now. */
function PhoneMeasurement({ e, startedAt }: { e: Experiment; startedAt: string | null }) {
  const vars = usePolling(api.variables, 600000);
  const [variable, setVariable] = useState<number | null>(null);
  const [value, setValue] = useState("");
  const [time, setTime] = useState("");
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const list = vars.data ?? [];
  const quick = ["OD600", "AFU", "pH", "Riboflavin"];
  const shown = [...list.filter((v) => quick.includes(v.name)), ...list.filter((v) => !quick.includes(v.name))];
  const [more, setMore] = useState(false);
  const varId = variable ?? (list.find((v) => v.name === "OD600") ?? list[0])?.id;
  const unit = list.find((v) => v.id === varId)?.unit ?? "";

  async function save() {
    setMsg(null);
    const v = Number(value.replace(",", "."));
    if (!varId || value.trim() === "" || !Number.isFinite(v)) { setMsg({ kind: "alert", text: "Enter a numeric value." }); return; }
    let sampled_at: string | undefined;
    if (time) {
      const [hh, mm] = time.split(":").map(Number);
      const d = new Date(); d.setHours(hh, mm, 0, 0);
      if (d.getTime() > Date.now()) d.setDate(d.getDate() - 1);   // taken before midnight
      if (startedAt && d.getTime() < new Date(startedAt).getTime()) { setMsg({ kind: "alert", text: "That time is before Start." }); return; }
      sampled_at = d.toISOString();
    }
    try {
      await api.addMeasurement(e.id, { variable_id: varId, value: v, sampled_at, note });
      setMsg({ kind: "good", text: `Saved: ${list.find((x) => x.id === varId)?.name} ${v}${unit ? ` ${unit}` : ""}.` });
      setValue(""); setNote(""); setTime("");
    } catch (x) { setMsg({ kind: "alert", text: (x as Error).message }); }
  }

  return (
    <div className="stack" style={{ gap: 14, padding: "10px 16px 16px" }}>
      <div className="stack" style={{ gap: 6 }}>
        <span className="xs muted" id="pm-var">Variable</span>
        <div role="radiogroup" aria-labelledby="pm-var" className="row" style={{ gap: 8 }}>
          {shown.slice(0, more ? shown.length : 4).map((v) => (
            <button key={v.id} role="radio" aria-checked={v.id === varId} className={`chip ${v.id === varId ? "on" : ""}`} style={{ height: 36 }}
              onClick={() => setVariable(v.id)}>{v.id === varId && <Icon name="check" size={16} width={2.4} />}{v.name}</button>
          ))}
          {!more && shown.length > 4 && <button className="chip" style={{ height: 36 }} onClick={() => setMore(true)}>Other…</button>}
        </div>
      </div>
      <label className="field p-big">Value{unit ? ` (${unit})` : ""}
        <input inputMode="decimal" placeholder="0.000" value={value} onChange={(x) => setValue(x.target.value)} className="mono" />
      </label>
      <label className="field p-big">Sampling time
        <input type="time" value={time} onChange={(x) => setTime(x.target.value)} className="mono" />
        <span className="xs muted">Empty = now; set it if the sample was taken earlier</span>
      </label>
      <label className="field p-big">Notes
        <input placeholder="e.g. dilution 1:10" value={note} onChange={(x) => setNote(x.target.value)} />
      </label>
      {msg && <div className={`banner ${msg.kind}`} role="status">{msg.text}</div>}
      <button className="btn filled" style={{ height: 48, borderRadius: 24, justifyContent: "center" }} onClick={save}>Save to {e.code}</button>
    </div>
  );
}

const RANGES = [{ key: "1", label: "1 h", h: 1 }, { key: "6", label: "6 h", h: 6 }, { key: "all", label: "All", h: 0 }];

/**
 * Full-screen chart, meant for landscape. Asks the browser for full screen + landscape lock
 * where supported (Android); otherwise suggests rotating. 1 h / 6 h fetch only that window,
 * so the chart shows the readings at full resolution.
 */
function FullscreenChart({ e, partner, partnerReadings, onClose }:
  { e: Experiment; partner?: Experiment; partnerReadings?: Readings; onClose: () => void }) {
  const vars = varsFor(e.channels);
  const leftVars = vars.filter((v) => ["ph", "temperature", "stirrer", "do2"].includes(v.key));
  const rightVars = [...vars.filter((v) => !["ph", "temperature", "stirrer", "do2"].includes(v.key)),
    ...(e.channels.includes("ph") ? [BASE_RATE_VAR] : []), ...leftVars];
  const [left, setLeft] = useState(leftVars[0]?.key ?? "ph");
  const [right, setRight] = useState(rightVars.find((v) => v.key !== (leftVars[0]?.key))?.key ?? "none");
  const [range, setRange] = useState("all");
  const [showSp, setShowSp] = useState(true);
  const [showLim, setShowLim] = useState(true);
  const [showPartner, setShowPartner] = useState(false);
  const running = e.status === "fermenting";
  const live = usePolling(api.live, running ? 10000 : 600000);
  const tNow = live.data?.find((x) => x.reactor_id === e.reactor_id)?.experiment?.t_h;
  const rg = RANGES.find((x) => x.key === range)!;
  const since = rg.h && tNow != null ? Math.max(0, tNow - rg.h) : 0;
  const readings = usePolling(() => api.readings(e.id, since), running ? 15000 : 600000, [e.id, range, rg.h ? Math.floor(since * 4) : 0]);
  const analysis = usePolling(() => api.analysis(e.id, 0.25), running ? 60000 : 600000, [e.id]);

  useEffect(() => {   // full screen + landscape where the browser allows it
    const el = document.documentElement;
    const o = screen.orientation as ScreenOrientation & { lock?: (o: string) => Promise<void> };
    el.requestFullscreen?.().then(() => o?.lock?.("landscape")).catch(() => { /* not supported: rotate by hand */ });
    const esc = (k: KeyboardEvent) => { if (k.key === "Escape") onClose(); };
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("keydown", esc);
      try { o?.unlock?.(); } catch { /* ignore */ }
      if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined);
    };
  }, [onClose]);

  const lv = leftVars.find((v) => v.key === left) ?? leftVars[0] ?? V("ph");
  const rv = right === "none" ? null : rightVars.find((v) => v.key === right && v.key !== lv.key) ?? null;
  const option = useMemo(() => trendsOption(readings.data, lv, rv, {
    setpoints: showSp, limits: showLim, bins: analysis.data?.rate_bins, compact: true,
    partner: showPartner && partner ? partnerReadings : undefined, partnerLabel: partner ? `${lv.label} ${partner.reactor_id}` : undefined,
    xRange: rg.h && tNow != null ? { min: Math.max(0, Math.floor((tNow - rg.h) * 10) / 10), max: Math.ceil(tNow * 10) / 10 } : undefined,
  }), [readings.data, analysis.data, lv, rv, showSp, showLim, showPartner, partner, partnerReadings, rg.h, tNow]);

  return (
    <div className="p-full" role="dialog" aria-modal="true" aria-label={`${e.code} chart, full screen`}>
      <div className="p-full-bar">
        <button className="btn icon text" aria-label="Close full screen chart" onClick={onClose}><Icon name="close" size={22} width={2} /></button>
        <span className="small" style={{ fontWeight: 500, whiteSpace: "nowrap" }}><span className="mono">{e.code}</span> · {e.reactor_id}</span>
        <label className="p-select"><span style={{ width: 12, height: 3, background: "#1d3786" }} aria-hidden="true" /><span className="sr-only">Left axis</span>
          <select value={left} onChange={(x) => setLeft(x.target.value)}>{leftVars.map((v) => <option key={v.key} value={v.key}>{v.label}</option>)}</select>
        </label>
        <label className="p-select"><span style={{ width: 12, height: 3, background: "#1f7a63" }} aria-hidden="true" /><span className="sr-only">Right axis</span>
          <select value={right} onChange={(x) => setRight(x.target.value)}>
            {rightVars.filter((v) => v.key !== left).map((v) => <option key={v.key} value={v.key}>{v.label}</option>)}
            <option value="none">None</option>
          </select>
        </label>
        <div className="segmented" role="group" aria-label="Time range" style={{ marginLeft: "auto", height: 32 }}>
          {RANGES.map((x) => <button key={x.key} className={x.key === range ? "on" : ""} aria-pressed={x.key === range} onClick={() => setRange(x.key)}>{x.label}</button>)}
        </div>
      </div>
      <div className="row" role="group" aria-label="Overlays" style={{ gap: 6, padding: "0 4px" }}>
        <button className={`chip ${showSp ? "on" : ""}`} style={{ height: 28 }} aria-pressed={showSp} onClick={() => setShowSp(!showSp)}>Setpoint</button>
        <button className={`chip ${showLim ? "on" : ""}`} style={{ height: 28 }} aria-pressed={showLim} onClick={() => setShowLim(!showLim)}>Process limits</button>
        {partner && <button className={`chip ${showPartner ? "on" : ""}`} style={{ height: 28 }} aria-pressed={showPartner} onClick={() => setShowPartner(!showPartner)}>Paired {partner.reactor_id}</button>}
        <span className="xs muted p-hint">Pinch to zoom · drag to pan · tap for values</span>
      </div>
      <div className="p-rotate xs muted">Rotate the phone for a wider chart</div>
      <EChart option={option} className="p-full-chart" ariaLabel={`${lv.label}${rv ? ` and ${rv.label}` : ""} over time since start`} />
    </div>
  );
}
