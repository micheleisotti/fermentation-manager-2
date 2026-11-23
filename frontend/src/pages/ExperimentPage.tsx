import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type Channel, type Experiment, type Readings, type Snapshot, type TimelineItem } from "../api";
import { fmt, fmtDuration, PHONE_QUERY, useMediaQuery, usePolling, fmtAge } from "../hooks";
import { baseOption, MANUAL_PREFIX, TREND_VARS, trendsOption, varsFor, type ManualPoints, type TrendVar } from "../components/charts";
import { DataValue, EChart, Icon, Segmented, StatusChip } from "../components/ui";
import { DeleteExperiment } from "../components/DeleteExperiment";
import { LoopsButton, ResetCountersButton } from "../components/ReactorControls";
import { ControllerButton, loopDots, ManualPumpsButton } from "../components/SidePanels";
import { ControlEditDialog } from "../components/ControlEditDialog";
import { Planning } from "./Planning";
import { PhoneFermentation } from "./PhoneExperiment";
import { EndExperimentButton, ExpTags, StartExperimentButton } from "./ExperimentsList";

export function ExperimentPage() {
  const id = Number(useParams().id);
  const exp = usePolling(() => api.experiment(id), 15000, [id]);
  const all = usePolling(() => api.experiments(), 60000);
  const phone = useMediaQuery(PHONE_QUERY);
  if (exp.error) return <div className="card">Experiment not found: {exp.error}</div>;
  if (!exp.data) return <div className="muted">Loading…</div>;
  const e = exp.data;
  const partner = (all.data ?? []).find((x) => e.parallel_partner_codes.includes(x.code));
  if (e.status === "planned") return <Planning key={e.id} existing={e} onChange={exp.refresh} />;
  return phone
    ? <PhoneFermentation key={e.id} e={e} partner={partner} onChange={exp.refresh} />
    : <FermentationView e={e} partner={partner} onChange={exp.refresh} />;
}

/** ⋯ menu of the experiment: archive, back to planning, duplicate as a new plan, export, delete. */
function MoreMenu({ e, onChange }: { e: Experiment; onChange: () => void }) {
  const [open, setOpen] = useState(false);
  const [del, setDel] = useState(false);
  const nav = useNavigate();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (x: MouseEvent) => { if (!ref.current?.contains(x.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  const duplicate = async () => {
    setOpen(false);
    try {
      const c = await api.createExperiment({ name: `${e.name} (copy)`, reactor_id: e.reactor_id, strain: e.strain, medium_id: e.medium_id,
        buffer_id: e.buffer_id, working_volume_ml: e.working_volume_ml, profile_id: e.profile_id, notes: e.notes, channels: e.channels,
        plan_params: e.plan_params, buffer_volume_ml: e.buffer_volume_ml ?? null });
      nav(`/experiments/${c.id}`);
    } catch (x) { window.alert((x as Error).message); }
  };
  return (
    <div className="more-wrap" ref={ref}>
      <button className="btn icon outlined more-btn" aria-label="More actions" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span aria-hidden="true" style={{ fontSize: 20, lineHeight: 0, marginTop: -8 }}>⋯</span></button>
      {open && (
        <div className="menu more-menu" role="menu">
          {e.status !== "fermenting" && <button role="menuitem" onClick={async () => { setOpen(false); await api.setArchived(e.id, !e.archived); onChange(); }}>
            {e.archived ? "Restore from archive" : "Archive"}</button>}
          {e.status === "preparing" && <button role="menuitem" onClick={async () => {
            setOpen(false);
            if (window.confirm(`Back to planning? ${e.code} leaves the live page (nothing has been recorded yet).`)) { await api.unprepare(e.id); onChange(); }
          }}>Back to planning</button>}
          <button role="menuitem" onClick={duplicate}>Duplicate as new plan</button>
          <button role="menuitem" onClick={() => { setOpen(false); nav(`/analysis/experiments/${e.id}`); }}>Export data (Analysis)</button>
          {e.status !== "fermenting" && <button role="menuitem" style={{ color: "var(--alert-text)" }} onClick={() => { setOpen(false); setDel(true); }}>Delete experiment</button>}
        </div>
      )}
      {del && <DeleteExperiment e={e} startOpen onClosed={() => setDel(false)} />}
    </div>
  );
}

function Header({ e, partner, name, onChange }: { e: Experiment; partner?: Experiment; name: string; onChange: () => void }) {
  const nav = useNavigate();
  return (
    <header className="exp-head">
      <Link to="/" className="btn icon outlined back-btn" aria-label="Back to home"><Icon name="back" /></Link>
      <div className="stack" style={{ gap: 0, minWidth: 0, flex: "1 1 auto" }}>
        <span className="xs muted exp-kicker">{name}{e.parallel_run_code ? ` · Parallel run ${e.parallel_run_code}` : ""} · <span className="mono">{e.code}</span>
          {e.profile_name && <> · profile “{e.profile_name}”</>}
          {e.profile_changed && <span className="profile-changed" title="The profile was edited after this experiment's plan was saved; the experiment keeps the values it was saved with."> · changed after the plan was saved</span>}</span>
        <h1 className="exp-h1">{e.name}{e.strain && <span> · {e.strain}</span>} <ExpTags e={e} /></h1>
      </div>
      {e.parallel_run_code && partner && (
        <div className="segmented pair-switch" role="tablist" aria-label="Parallel run">
          {[e, partner].sort((a, b) => a.reactor_id.localeCompare(b.reactor_id)).map((x) => (
            <button key={x.id} role="tab" aria-selected={x.id === e.id} className={x.id === e.id ? "on" : ""} onClick={() => nav(`/experiments/${x.id}`)}>
              {x.reactor_id} · {x.strain || x.code}</button>
          ))}
        </div>
      )}
      <div className="segmented mode-tabs" role="tablist" aria-label="Stage">
        <button role="tab" aria-selected={false} disabled title="The plan is fixed once the experiment is on the live page">Planning</button>
        <button role="tab" aria-selected className="on">Fermentation</button>
        <button role="tab" aria-selected={false} onClick={() => nav(`/analysis/experiments/${e.id}`)}>Analysis</button>
      </div>
      <MoreMenu e={e} onChange={onChange} />
    </header>
  );
}

type Tab = "trends" | "buffering" | "gas" | "level" | "sampling";

function FermentationView({ e, partner, onChange }: { e: Experiment; partner?: Experiment; onChange: () => void }) {
  const preparing = e.status === "preparing";
  const running = e.status === "fermenting" || preparing;   // live: polling and current values
  const readings = usePolling(() => api.readings(e.id), running ? 30000 : 600000, [e.id, e.status]);
  const partnerReadings = usePolling<Readings | undefined>(() => (partner ? api.readings(partner.id) : Promise.resolve(undefined)), 60000, [partner?.id]);
  const [binH, setBinH] = useState<"0.25" | "1">("0.25");
  const analysis = usePolling(() => api.analysis(e.id, Number(binH)), running ? 60000 : 600000, [e.id, binH]);
  const live = usePolling(api.live, running ? 10000 : 600000);
  const timeline = usePolling(() => api.timeline(e.id), 20000, [e.id]);
  const meas = usePolling(() => api.measurements(e.id), 60000, [e.id]);
  const [tab, setTab] = useState<Tab>("trends");
  const [editCh, setEditCh] = useState<string | null>(null);

  const ch = e.channels;
  const manual = useMemo(() => {
    const out: ManualPoints = {};
    for (const m of [...(meas.data ?? [])].sort((a, b) => a.t_h - b.t_h)) {
      (out[m.variable_name] ??= { unit: m.unit, t: [], y: [] });
      out[m.variable_name].t.push(m.t_h); out[m.variable_name].y.push(m.value);
    }
    return out;
  }, [meas.data]);
  const vars: TrendVar[] = [...varsFor(ch),
    ...Object.entries(manual).map(([n, m]) => ({ key: `${MANUAL_PREFIX}${n}`, label: `${n} (manual)`, unit: m.unit, digits: 2 }))];
  const main = vars.filter((v) => ["ph", "temperature", "stirrer", "do2"].includes(v.key));
  const saved = loadTrendPrefs();
  const [left, setLeftRaw] = useState(saved.left ?? main[0]?.key ?? vars[0]?.key ?? "ph");
  const [right, setRightRaw] = useState(saved.right ?? main[1]?.key ?? "none");
  const [showSp, setShowSpRaw] = useState(saved.setpoints ?? true);
  const [showLim, setShowLimRaw] = useState(saved.limits ?? true);
  const setLeft = (k: string) => { setLeftRaw(k); saveTrendPrefs({ left: k }); };
  const setRight = (k: string) => { setRightRaw(k); saveTrendPrefs({ right: k }); };
  const setShowSp = (b: boolean) => { setShowSpRaw(b); saveTrendPrefs({ setpoints: b }); };
  const setShowLim = (b: boolean) => { setShowLimRaw(b); saveTrendPrefs({ limits: b }); };
  const [showPartner, setShowPartner] = useState(true);
  const [showPartnerBase, setShowPartnerBase] = useState(false);
  const [showDetected, setShowDetected] = useState(true);
  const detected = (timeline.data ?? []).filter((x) => x.category === "detected" && x.t_h != null);

  const lv = vars.find((v) => v.key === left) ?? main[0] ?? vars[0];
  const rv = vars.find((v) => v.key === right && v.key !== lv?.key) ?? null;
  const endH = analysis.data?.estimated_end?.t_end_h ?? null;
  const trend = useMemo(() => !lv ? {} : trendsOption(readings.data, lv, rv, {
    setpoints: showSp, limits: showLim, endH, manual,
    partner: showPartner && partner && !lv.key.startsWith(MANUAL_PREFIX) ? partnerReadings.data : undefined,
    partnerLabel: partner ? `${lv.label} ${partner.reactor_id} (paired)` : undefined,
  }), [readings.data, partnerReadings.data, lv, rv, showSp, showLim, showPartner, partner, endH, manual]);
  const withEvents = (opt: Record<string, unknown>): Record<string, unknown> => {
    if (!showDetected || detected.length === 0) return opt;
    const t = opt as { series?: object[] };
    return { ...opt, series: [...(t.series ?? []), { type: "line", name: "Detected events", data: [], silent: true,
      markLine: { symbol: "none", lineStyle: { type: "dashed", color: "#455e6f" }, label: { formatter: "{b}", fontSize: 10, color: "#455e6f" },
        data: detected.map((d) => ({ xAxis: d.t_h, name: d.title })) } }] };
  };
  const trendWithEvents = useMemo(() => withEvents(trend), [trend, showDetected, timeline.data]);
  const base = useMemo(() => baseOption(readings.data, analysis.data?.rate_bins, showPartnerBase ? partnerReadings.data : undefined,
    endH, analysis.data?.acid_rate_bins, partner?.reactor_id),
    [readings.data, analysis.data, partnerReadings.data, showPartnerBase, endH, partner]);
  const v = (k: string) => TREND_VARS.find((x) => x.key === k)!;
  const gasOpt = useMemo(() => withEvents(trendsOption(readings.data, v("do2"), v("air_pct"), { setpoints: true, limits: true, endH })), [readings.data, endH, showDetected, timeline.data]);
  const levelOpt = useMemo(() => withEvents(trendsOption(readings.data, v("antifoam_cumulative_ml"), v("level_contact"), { setpoints: false, limits: false, endH })), [readings.data, endH, showDetected, timeline.data]);

  const r = live.data?.find((x) => x.reactor_id === e.reactor_id);
  const name = r?.name || e.reactor_id;
  const s = running ? r?.snapshot : null;
  const bd = analysis.data?.base_dosing;
  const lastBin = analysis.data?.rate_bins.filter((b) => b.complete).at(-1);
  const refresh = () => { live.refresh(); timeline.refresh(); };
  const tabs: { k: Tab; label: string }[] = [{ k: "trends", label: "Trends" },
    ...(ch.includes("ph") ? [{ k: "buffering" as Tab, label: "Buffering" }] : []),
    ...(ch.includes("do2") ? [{ k: "gas" as Tab, label: "Gas" }] : []),
    ...(ch.includes("level") ? [{ k: "level" as Tab, label: "Level" }] : []),
    { k: "sampling", label: "Sampling" }];
  const chip = (on: boolean, label: string, f: () => void, title?: string) =>
    <button className={`chip ${on ? "on" : ""}`} aria-pressed={on} onClick={f} title={title}>{on && <Icon name="check" size={14} width={2.4} />}{label}</button>;

  return (
    <>
      <div className="sticky-head exp-sticky">
        <Header e={e} partner={partner} name={name} onChange={onChange} />
        {e.end_note && <div className={`banner ${e.status === "aborted" ? "alert" : "warn"} small`} role="note">
          <b>{e.status === "aborted" ? "Aborted" : "Ended"}{e.ended_at ? ` ${new Date(e.ended_at).toLocaleString()}` : ""}:</b>&nbsp;{e.end_note}</div>}
        <div className="action-bar">
          {running && !preparing && <span className="run-chip ok"><span className="dot ok" />Running · <span className="mono">{fmtDuration(r?.experiment?.t_h)}</span></span>}
          {preparing && <span className="run-chip prep"><span className="dot prep" />Not recording · t = —</span>}
          {!running && <span className="run-chip"><span className="dot idle" />{e.status === "aborted" ? "Aborted" : "Completed"}</span>}
          {running && r && <span className="xs muted" title="Time since the last reading from the controller">{fmtAge(r.last_reading_age_s)} ago{!r.online && <b style={{ color: "var(--alert-text)" }}> · controller not responding</b>}</span>}
          <span className="ab-sep" />
          <ControllerButton reactor={e.reactor_id} name={name} s={r?.snapshot} channels={ch} phMode={r?.ph_control?.mode} onChanged={refresh} />
          <LoopsButton reactor={e.reactor_id} channels={ch} dots={loopDots(r?.snapshot, ch)} />
          <ResetCountersButton reactor={e.reactor_id} recording={e.status === "fermenting"} onDone={() => { readings.refresh(); timeline.refresh(); }} />
          <ManualPumpsButton reactor={e.reactor_id} name={name} />
          <span style={{ flex: 1 }} />
          {preparing && <StartExperimentButton e={e} onDone={onChange} />}
          {running && !preparing && <EndExperimentButton e={e} onDone={onChange} danger />}
          {!running && <Link className="btn tonal" to={`/reactors/${e.reactor_id}`}>Open controller</Link>}
        </div>
      </div>

      <div className="exp-grid">
        <div className="exp-main">
          <div className="row" role="tablist" aria-label="Sections" style={{ gap: 8 }}>
            {tabs.map((t) => <button key={t.k} role="tab" aria-selected={tab === t.k} className={`chip tab-chip ${tab === t.k ? "on" : ""}`} onClick={() => setTab(t.k)}>{t.label}</button>)}
          </div>

          {tab === "trends" && <section className="card" aria-label="Trends">
            <div className="row" style={{ gap: 8 }}>
              <select aria-label="Left axis" value={left} onChange={(x) => setLeft(x.target.value)} style={{ height: 36, width: "auto" }}>
                {vars.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
              </select>
              <span className="xs muted">and</span>
              <select aria-label="Right axis" value={right} onChange={(x) => setRight(x.target.value)} style={{ height: 36, width: "auto" }}>
                {vars.filter((x) => x.key !== left).map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
                <option value="none">None</option>
              </select>
              {chip(showSp, "Setpoint", () => setShowSp(!showSp))}
              {chip(showLim, "Limits", () => setShowLim(!showLim))}
              {partner && chip(showPartner, `Paired ${partner.reactor_id}`, () => setShowPartner(!showPartner))}
              {chip(showDetected, `Detected events${detected.length ? ` (${detected.length})` : ""}`, () => setShowDetected(!showDetected),
                detected.length ? "" : "No detected events yet (phase detection comes after calibration)")}
            </div>
            <EChart option={trendWithEvents} className="chart tall" ariaLabel="Trends over time since start" />
          </section>}

          {tab === "buffering" && <section className="card" aria-label="Buffer dosing">
            <div className="row between">
              <span className="small muted">Base buffer (and acid, Addition 2): cumulative volume from the controller; rate per bin</span>
              <div className="row" style={{ gap: 8 }}>
                <Segmented label="Averaging window" value={binH} onChange={setBinH} options={[{ value: "0.25", label: "15 min" }, { value: "1", label: "1 h" }]} />
                {partner && chip(showPartnerBase, `Overlay ${partner.reactor_id}`, () => setShowPartnerBase(!showPartnerBase))}
              </div>
            </div>
            <div className="dv-grid">
              <DataValue value={fmt(bd?.total_ml as number, 1)} unit="mL" label="Base, total since Start" color="#1f7a63" />
              {bd?.total_mmol != null && <DataValue value={fmt(bd.total_mmol as number, 1)} unit="mmol" label={`Base, total · ${fmt(e.buffer_molarity, 2)} M`} color="#1f7a63" />}
              {e.buffer_volume_ml != null && bd?.total_ml != null && (() => {
                const leftMl = (e.buffer_volume_ml ?? 0) - (bd.total_ml as number);
                const low = leftMl < 0.1 * (e.buffer_volume_ml ?? 0);
                return <DataValue value={fmt(Math.max(0, leftMl), 0)} unit="mL" label={`Bottle left of ${fmt(e.buffer_volume_ml, 0)} mL${low ? " · below 10 %" : ""}`} color={low ? "#b30020" : undefined} />;
              })()}
              <DataValue value={fmt(lastBin?.rate_ml_min, 3)} unit="mL/min" label={`Last complete ${binH === "1" ? "hour" : "15 min"}`} color="#1f7a63" />
              <DataValue value={fmt(bd?.peak_rate_ml_min as number, 3)} unit="mL/min" label={`Peak · t = ${fmt(bd?.t_peak_h as number, 1)} h`} />
              <DataValue value={fmt(s?.base_pump_pct, 0)} unit="%" label="Pump activation · now" />
              {endH != null && <DataValue value={fmt(endH, 1)} unit="h" label="Estimated end of fermentation" />}
            </div>
            <EChart option={base} className="chart" ariaLabel="Base rate bins and cumulative base over time" />
          </section>}

          {tab === "gas" && <section className="card" aria-label="Gas">
            <span className="small muted">dO2 with its setpoint and limits; air valve activation on the right (N2 in the Trends selector).</span>
            <EChart option={gasOpt} className="chart tall" ariaLabel="dO2 and air valve over time" />
          </section>}

          {tab === "level" && <section className="card" aria-label="Level">
            <span className="small muted">Antifoam dosed (cumulative) and level probe contact (1 = contact).</span>
            <EChart option={levelOpt} className="chart tall" ariaLabel="Antifoam and level contact over time" />
          </section>}

          {tab === "sampling" && <ManualVariables e={e} />}
        </div>

        <aside className="exp-side">
          <h2 className="side-h">Channels</h2>
          <ChannelCards s={s ?? r?.snapshot ?? null} channels={ch} live={running} onEdit={(p) => setEditCh(p)} />
          <LatestEvents items={timeline.data ?? []} compact />
        </aside>
      </div>
      {editCh && r?.snapshot && <ControlEditDialog reactor={e.reactor_id} parameter={editCh} snapshot={r.snapshot} remote={!window.location.hostname.match(/^(localhost|127\.)/)}
        onClose={(changed) => { setEditCh(null); if (changed) refresh(); }} />}
    </>
  );
}

/** Right column: one card per channel of the experiment, value and target; click to edit target and limits. */
function ChannelCards({ s, channels, live, onEdit }: { s: Snapshot | null; channels: Channel[]; live: boolean; onEdit: (p: string) => void }) {
  const x = (s?.extra ?? {}) as Record<string, number | undefined>;
  const loop = (k: string) => x[`loop_${k}`];
  const out = (val: number | null | undefined, lo: number | null | undefined, hi: number | null | undefined) =>
    val != null && lo != null && hi != null && (val < lo || val > hi);
  const cards: { k: string; name: string; v: string; u: string; sub: string; edit?: string; bad?: boolean; loop?: number }[] = [];
  if (channels.includes("ph")) cards.push({ k: "ph", name: "pH", v: fmt(s?.ph), u: "", edit: "ph", loop: loop("ph"),
    sub: `set ${fmt(s?.ph_setpoint)} · ${fmt(s?.ph_low_limit)}–${fmt(s?.ph_high_limit)}`, bad: out(s?.ph, s?.ph_low_limit, s?.ph_high_limit) });
  if (channels.includes("temperature")) cards.push({ k: "t", name: "Temperature", v: fmt(s?.temperature, 1), u: "°C", edit: "temperature", loop: loop("temperature"),
    sub: `set ${fmt(s?.temperature_setpoint, 1)} · ${fmt(s?.temperature_low_limit, 1)}–${fmt(s?.temperature_high_limit, 1)}`,
    bad: out(s?.temperature, s?.temperature_low_limit, s?.temperature_high_limit) });
  if (channels.includes("stirrer")) cards.push({ k: "st", name: "Stirrer", v: fmt(s?.stirrer, 0), u: "rpm", edit: "stirrer", loop: loop("stirrer"),
    sub: `set ${fmt(s?.stirrer_setpoint, 0)}` });
  if (channels.includes("do2")) cards.push({ k: "do2", name: "dO2", v: fmt(x.do2, 1), u: "%", loop: loop("do2"),
    sub: `set ${fmt(x.do2_setpoint, 1)} · ${fmt(x.do2_low_limit, 0)}–${fmt(x.do2_high_limit, 0)} · on the controller` });
  if (channels.includes("level")) cards.push({ k: "lvl", name: "Level", v: x.level_contact == null ? "—" : x.level_contact ? "contact" : "no contact", u: "", loop: loop("level"),
    sub: x.antifoam_cumulative_ml != null ? `antifoam ${fmt(x.antifoam_cumulative_ml, 1)} mL` : "probe" });
  if (channels.includes("redox")) cards.push({ k: "rdx", name: "Redox", v: fmt(x.redox_mv, 0), u: "mV", sub: "reading" });
  return (
    <div className="stack" style={{ gap: 8 }}>
      {cards.map((c) => {
        const body = (<>
          <span className="stack" style={{ gap: 2, minWidth: 0 }}>
            <span className="ch-name">{c.name}{c.edit && <Icon name="sliders" size={13} />}</span>
            <span className="xs muted">{c.sub}</span>
          </span>
          <span className="stack" style={{ gap: 2, alignItems: "flex-end" }}>
            <span className="ch-val mono" style={c.bad ? { color: "var(--alert-text)" } : undefined}>{live ? c.v : "—"}{c.u && <small>{c.u}</small>}</span>
            {c.loop != null && <span className={`loop-badge ${c.loop ? "on" : ""}`}>loop {c.loop ? "on" : "off"}</span>}
          </span>
        </>);
        return c.edit && live
          ? <button key={c.k} className="ch-card" onClick={() => onEdit(c.edit!)} aria-label={`Edit ${c.name} target and limits`}>{body}</button>
          : <div key={c.k} className="ch-card">{body}</div>;
      })}
    </div>
  );
}

const EVENTS_SHOWN = 10;
const EVENT_FILTERS: { key: string; label: string; cats: TimelineItem["category"][] }[] = [
  { key: "all", label: "All", cats: [] },
  { key: "alerts", label: "Alerts", cats: ["alert"] },
  { key: "changes", label: "Changes", cats: ["change", "loops"] },
  { key: "sampling", label: "Sampling", cats: ["sampling"] },
  { key: "detected", label: "Detected", cats: ["detected"] },
];
const CAT_DOT: Record<string, string> = { alert: "bad", detected: "prep", change: "ok", loops: "ok", sampling: "idle", info: "idle" };

/** One list (2.3): alerts, detected events, setpoint/limit changes, loops and counters, samplings. Newest first. */
export function LatestEvents({ items, compact = false }: { items: TimelineItem[]; compact?: boolean }) {
  const [filter, setFilter] = useState("all");
  const [all, setAll] = useState(false);
  const SHOWN = compact ? 4 : EVENTS_SHOWN;
  const f = EVENT_FILTERS.find((x) => x.key === filter)!;
  const list = f.cats.length ? items.filter((x) => f.cats.includes(x.category)) : items;
  return (
    <section className="card tight" aria-labelledby="ev">
      <div className="row between"><h3 id="ev" className={compact ? "kicker" : undefined}>Latest events{compact ? ` · ${items.length}` : ""}</h3>
        {list.length > SHOWN && <button className="btn text" onClick={() => setAll(!all)} aria-expanded={all}>
          {all ? "Show fewer" : `Show all (${list.length})`}</button>}</div>
      {(!compact || all) && <div className="row" style={{ gap: 6 }}>
        {EVENT_FILTERS.map((x) => <button key={x.key} className={`chip ${filter === x.key ? "on" : ""}`} aria-pressed={filter === x.key}
          onClick={() => setFilter(x.key)}>{x.label}</button>)}
      </div>}
      {list.slice(0, all ? undefined : SHOWN).map((x, i) => (
        <div key={i} className="row" style={{ gap: 10, alignItems: "flex-start", flexWrap: "nowrap" }}>
          <span className={`dot ${x.severity === "alert" ? "bad" : CAT_DOT[x.category] ?? "idle"}`} style={{ marginTop: 6, flex: "0 0 auto" }} />
          <span className="stack" style={{ gap: 1, flexGrow: 1, minWidth: 0 }}>
            <span className="small">{x.title}</span>
            <span className="xs muted">{x.category === "change" ? "change" : x.category}{x.detail && x.category !== "alert" ? ` · ${x.detail}` : ""}{x.author ? ` · ${x.author}` : ""}</span>
          </span>
          <span className="xs muted mono" title={new Date(x.at).toLocaleString()}>{x.t_h == null
            ? new Date(x.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : `${x.t_h.toFixed(1)} h`}</span>
        </div>
      ))}
      {list.length === 0 && <span className="small muted">Nothing yet.</span>}
    </section>
  );
}

/** Trend axes and toggles: the last choice is remembered in this browser (all experiments). */
type TrendPrefs = { left?: string; right?: string; setpoints?: boolean; limits?: boolean };
const TREND_KEY = "fm2.trendPrefs";
function loadTrendPrefs(): TrendPrefs {
  try { return JSON.parse(localStorage.getItem(TREND_KEY) ?? "{}") as TrendPrefs; } catch { return {}; }
}
function saveTrendPrefs(p: TrendPrefs) {
  try { localStorage.setItem(TREND_KEY, JSON.stringify({ ...loadTrendPrefs(), ...p })); } catch { /* storage unavailable */ }
}

function ManualVariables({ e }: { e: Experiment }) {
  const vars = usePolling(api.variables, 600000);
  const meas = usePolling(() => api.measurements(e.id), 30000, [e.id]);
  const [variable, setVariable] = useState<number | "">("");
  const [value, setValue] = useState("");
  const [time, setTime] = useState("");
  const [tH, setTH] = useState("");
  const [note, setNote] = useState("");
  const [vol, setVol] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const live = e.status === "fermenting";
  const started = e.started_at != null;
  const varId = variable === "" ? (vars.data?.find((v) => v.name === "OD600") ?? vars.data?.[0])?.id : variable;
  const unit = vars.data?.find((v) => v.id === varId)?.unit ?? "";

  async function add() {
    setErr(null);
    const v = Number(value.replace(",", "."));
    if (!varId || !Number.isFinite(v) || value === "") { setErr("Enter a numeric value"); return; }
    let sampled_at: string | undefined;
    let t_h: number | undefined;
    if (tH.trim() !== "") {
      t_h = Number(tH.replace(",", "."));
      if (!Number.isFinite(t_h) || t_h < 0) { setErr("t (h) must be a number ≥ 0"); return; }
    } else if (live && time) {
      const [hh, mm] = time.split(":").map(Number);
      const d = new Date(); d.setHours(hh, mm, 0, 0);
      if (d.getTime() > Date.now()) d.setDate(d.getDate() - 1); // sample taken before midnight
      sampled_at = d.toISOString();
    } else if (!live && time) {
      sampled_at = new Date(time).toISOString();       // date and time of sampling
    } else if (!live) {
      setErr("Give when the sample was taken: t (h) since start, or date and time"); return;
    }
    let volume_taken_ml: number | null = null;
    if (vol.trim() !== "") {
      volume_taken_ml = Number(vol.replace(",", "."));
      if (!Number.isFinite(volume_taken_ml) || volume_taken_ml < 0) { setErr("Volume taken must be a number ≥ 0 (mL)"); return; }
    }
    try {
      await api.addMeasurement(e.id, { variable_id: varId, value: v, sampled_at, t_h, note, volume_taken_ml });
      setValue(""); setNote(""); setTime(""); setTH(""); setVol(""); meas.refresh();
    } catch (x) { setErr((x as Error).message); }
  }

  return (
    <section className="card" aria-labelledby="man">
      <h2 id="man" style={{ fontSize: 18 }}>Sampling</h2>
      {!started && <span className="small muted">Manual variables can be added once the experiment has started (time is counted from Start).</span>}
      {started && !live && <span className="small muted">
        The run has ended: values measured later (e.g. on frozen aliquots) are entered at the time the sample was taken.</span>}
      {started && (
        <div className="row" style={{ alignItems: "flex-end" }}>
          {live
            ? <label className="field" style={{ flex: "1 1 140px" }}>Sampling time (empty = now)
                <input type="time" value={time} disabled={tH.trim() !== ""} onChange={(x) => setTime(x.target.value)} />
              </label>
            : <label className="field" style={{ flex: "1 1 200px" }}>Sampling date and time
                <input type="datetime-local" value={time} disabled={tH.trim() !== ""} onChange={(x) => setTime(x.target.value)} />
              </label>}
          <label className="field" style={{ flex: "0 1 110px" }}>or t (h)
            <input inputMode="decimal" value={tH} onChange={(x) => setTH(x.target.value)} placeholder="e.g. 24" style={{ fontFamily: "var(--font-mono)" }} />
          </label>
          <label className="field" style={{ flex: "1 1 160px" }}>Variable
            <select value={varId ?? ""} onChange={(x) => setVariable(Number(x.target.value))}>
              {(vars.data ?? []).map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
            </select>
          </label>
          <label className="field" style={{ flex: "0 1 120px" }}>Value{unit ? ` (${unit})` : ""}
            <input inputMode="decimal" value={value} onChange={(x) => setValue(x.target.value)} placeholder="0.000" style={{ fontFamily: "var(--font-mono)" }} />
          </label>
          <label className="field" style={{ flex: "0 1 120px" }}>Volume taken (mL)
            <input inputMode="decimal" value={vol} onChange={(x) => setVol(x.target.value)} placeholder="optional" style={{ fontFamily: "var(--font-mono)" }} />
          </label>
          <label className="field" style={{ flex: "3 1 220px" }}>Notes
            <input value={note} onChange={(x) => setNote(x.target.value)} placeholder="e.g. dilution 1:10" />
          </label>
          <button className="btn filled" style={{ height: 48, borderRadius: 16 }} onClick={add}>Add</button>
        </div>
      )}
      {err && <div className="banner alert">{err}</div>}
      <div className="table-wrap">
        <table className="data" style={{ minWidth: 640 }}>
          <thead><tr><th>t (h)</th><th>Time</th><th>Variable</th><th style={{ textAlign: "right" }}>Value</th><th>Unit</th><th style={{ textAlign: "right" }}>Taken (mL)</th><th>Notes</th></tr></thead>
          <tbody>
            {(meas.data ?? []).map((m) => (
              <tr key={m.id}>
                <td className="mono">{m.t_h.toFixed(2)}</td>
                <td className="mono muted">{new Date(m.sampled_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</td>
                <td>{m.variable_name}</td>
                <td className="mono" style={{ textAlign: "right" }}>{m.value}</td>
                <td className="mono muted">{m.unit}</td>
                <td className="mono muted" style={{ textAlign: "right" }}>{m.volume_taken_ml ?? ""}</td>
                <td className="muted">{m.note}</td>
              </tr>
            ))}
            {(meas.data ?? []).length === 0 && <tr><td colSpan={7} className="muted">No manual entries yet.</td></tr>}
          </tbody>
        </table>
      </div>
      <span className="xs muted">Manual entries are editable and every edit is logged; controller data is read-only.</span>
    </section>
  );
}
