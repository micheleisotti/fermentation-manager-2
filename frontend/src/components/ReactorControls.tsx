import { useState } from "react";
import { api, type Channel, type CountersState, type LiveReactor, type LoopsState, type Snapshot, type SwitchLoop } from "../api";
import { fmt } from "../hooks";
import { Icon, THEMED } from "./ui";
import { ControlEditDialog } from "./ControlEditDialog";
import { sidesText } from "../loopText";

const isRemote = () => !["localhost", "127.0.0.1"].includes(location.hostname);

/**
 * Live values and commands of one controller: one row per channel with the name, the value and the
 * setpoint/limits below, and a single Edit (target and limits) on the right. Used on the fermentation
 * page and on the reactor monitoring page (also with no experiment: the controller does not need one).
 * `channels` limits the rows to the experiment's channels; without it, every channel the controller reports.
 */
export function ControllerPanel({ reactor, s, channels, phMode, onChanged }:
  { reactor: string; s: Snapshot; channels?: Channel[]; phMode?: string | null; onChanged?: () => void }) {
  const [edit, setEdit] = useState<string | null>(null);
  const x = s.extra ?? {};
  const has = (c: Channel, reported: boolean) => (channels ? channels.includes(c) : reported);
  const out = (v: number | null | undefined, lo: number | null | undefined, hi: number | null | undefined) =>
    v != null && lo != null && hi != null && (v < lo || v > hi);
  const rows: { p: string; name: string; v: string; u: string; info: string[]; c?: string; edit?: boolean; note?: string }[] = [];
  if (has("ph", s.ph != null)) {
    rows.push({ p: "ph", name: "pH", v: fmt(s.ph), u: "", c: out(s.ph, s.ph_low_limit, s.ph_high_limit) ? "#b30020" : "#1d3786", edit: true,
      info: [`target ${fmt(s.ph_setpoint)}`, `limits ${fmt(s.ph_low_limit)}–${fmt(s.ph_high_limit)}`, `control ${phMode ?? "—"}`] });
    rows.push({ p: "base", name: "Base buffer", v: fmt(s.base_cumulative_ml, 2), u: "mL", c: "#1f7a63", info: ["dose counter", `pump ${fmt(s.base_pump_pct, 0)} %`] });
    if (x.acid_cumulative_ml != null)
      rows.push({ p: "acid", name: "Acid buffer (Addition 2)", v: fmt(x.acid_cumulative_ml, 2), u: "mL", c: "#1d3786",
        info: ["dose counter", ...(x.acid_pump_pct != null ? [`pump ${fmt(x.acid_pump_pct, 0)} %`] : [])] });
  }
  if (has("temperature", s.temperature != null))
    rows.push({ p: "temperature", name: "Temperature", v: fmt(s.temperature, 1), u: "°C", edit: true,
      c: out(s.temperature, s.temperature_low_limit, s.temperature_high_limit) ? "#b30020" : undefined,
      info: [`target ${fmt(s.temperature_setpoint, 1)}`, `limits ${fmt(s.temperature_low_limit, 1)}–${fmt(s.temperature_high_limit, 1)}`,
        `heating ${fmt(x.heating_pct, 0)} %`, `cooling ${fmt(x.cooling_pct, 0)} %`] });
  if (has("stirrer", s.stirrer != null))
    rows.push({ p: "stirrer", name: "Stirrer", v: fmt(s.stirrer, 0), u: "rpm", edit: true, info: [`target ${fmt(s.stirrer_setpoint, 0)}`] });
  if (has("do2", x.do2 != null))
    rows.push({ p: "do2", name: "dO2", v: fmt(x.do2, 1), u: "%", note: "set on the controller",
      info: [`target ${fmt(x.do2_setpoint, 1)}`, `N2 ${fmt(x.n2_pct, 0)} %`, `air ${fmt(x.air_pct, 0)} %`] });
  if (has("level", x.level_contact != null))
    rows.push({ p: "level", name: "Level / foam probe", v: x.level_contact == null ? "—" : x.level_contact ? "Contact" : "No contact", u: "",
      note: "set on the controller", info: x.antifoam_cumulative_ml != null ? [`antifoam counter ${fmt(x.antifoam_cumulative_ml, 2)} mL`] : [] });
  if (has("redox", x.redox_mv != null))
    rows.push({ p: "redox", name: "Redox", v: fmt(x.redox_mv, 0), u: "mV", note: "read only", info: [] });
  return (
    <>
      {rows.map((row) => (
        <div key={row.p} className="ctrl-row">
          <div className="ctrl-main">
            <span className="ctrl-name">{row.name}</span>
            <span className="ctrl-value mono" style={{ color: row.c && THEMED[row.c] ? THEMED[row.c] : row.c }}>{row.v}{row.u && <span className="u">{row.u}</span>}</span>
            {row.info.length > 0 && <span className="ctrl-info">{row.info.map((t, k) => <span key={k}>{t}</span>)}</span>}
          </div>
          <div className="ctrl-act">
            {row.edit ? <button className="btn tonal" onClick={() => setEdit(row.p)} aria-label={`Edit ${row.name} target${row.p !== "stirrer" ? " and limits" : ""}`}>Edit</button>
              : row.note && <span className="xs muted">{row.note}</span>}
          </div>
        </div>
      ))}
      {rows.length === 0 && <span className="small muted" style={{ padding: 8 }}>No channel reported.</span>}
      <span className="xs muted" style={{ padding: "4px 8px" }}>
        Dose calibration, interlocks and actuator assignments stay on the controller.
      </span>
      {edit && <ControlEditDialog reactor={reactor} parameter={edit} snapshot={s} remote={isRemote()}
        onClose={(changed) => { setEdit(null); if (changed) onChanged?.(); }} />}
    </>
  );
}

function Dialog({ title, onClose, busy, children, width = 560 }:
  { title: string; onClose: () => void; busy: boolean; children: React.ReactNode; width?: number }) {
  return (
    <div className="scrim side" onMouseDown={(x) => { if (x.target === x.currentTarget && !busy) onClose(); }}>
      <div className="dialog" role="dialog" aria-modal="true" aria-label={title} data-width={width}>
        <div className="row between" style={{ flexWrap: "nowrap" }}><h2 style={{ fontSize: 20 }}>{title}</h2>
          <button className="btn icon text" aria-label="Close" disabled={busy} onClick={onClose}><Icon name="close" /></button></div>
        {children}
      </div>
    </div>
  );
}

/**
 * One "Loops" button: the loops of the experiment's channels (or all, on the monitoring page) with
 * their state and Start / Stop next to each. Only loops enabled in Settings › Loop control can be switched.
 */
export function LoopsButton({ reactor, channels, compact = false, dots }: { reactor: string; channels?: Channel[]; compact?: boolean; dots?: boolean[] }) {
  const [open, setOpen] = useState(false);
  const [st, setSt] = useState<LoopsState | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const load = async () => { try { setSt(await api.loopsState(reactor)); } catch (x) { setMsg({ kind: "alert", text: (x as Error).message }); } };
  const show = async () => { setOpen(true); setSt(null); setMsg(null); await load(); };
  const loops = (st?.loops ?? []).filter((l) => !channels || channels.includes(l.key as Channel) || l.active);
  const switchOne = async (l: SwitchLoop, on: boolean) => {
    if (on && l.also_switches.length && !window.confirm(`Starting ${l.label} also starts the ${l.also_switches.join(", ")} loop on the controller (cascade). Continue?`)) return;
    setBusy(l.key); setMsg(null);
    try {
      const r = (await api.switchLoops(reactor, [l.key], on)).results[0];
      setMsg(r.status === "confirmed" ? { kind: "good", text: `${l.label} ${on ? "started" : "stopped"} and confirmed by the controller.` }
        : { kind: "alert", text: `${l.label}: not confirmed (${r.message || r.status}).` });
      await load();
    } catch (x) { setMsg({ kind: "alert", text: (x as Error).message }); }
    finally { setBusy(null); }
  };
  return (
    <>
      <button className="btn outlined" style={compact ? { flex: "1 1 0", justifyContent: "center" } : undefined} onClick={show}>Loops{dots && dots.length > 0 && <span className="loop-dots" aria-label={`${dots.filter(Boolean).length} of ${dots.length} loops on`}>
        {dots.map((on, i) => <i key={i} className={on ? "on" : ""} />)}</span>}</button>
      {open && (
        <Dialog title={`Control loops on ${reactor}`} busy={!!busy} onClose={() => setOpen(false)}>
          {!st && !msg && <span className="muted small">Reading the controller…</span>}
          {st && !st.reachable && <div className="banner alert small">{reactor} not reachable: {st.message}</div>}
          {st?.reachable && (
            <div className="stack" style={{ gap: 0 }}>
              {loops.map((l) => {
                const startWhy = !l.allowed ? "not enabled in Settings › Loop control" : l.start_refused;
                return (
                  <div key={l.key} className="row between" style={{ padding: "10px 0", borderBottom: "1px solid var(--divider)", flexWrap: "nowrap", alignItems: "flex-start" }}>
                    <span className="stack" style={{ gap: 2, minWidth: 0 }}>
                      <span><b>{l.label}</b> <span className={`loop-badge ${l.active ? "on" : ""}`} style={{ marginLeft: 6 }}>loop {l.active ? "on" : "off"}</span></span>
                      <span className="small muted">{sidesText(l.actuators)}
                        {l.also_switches.length > 0 && ` · switches the ${l.also_switches.join(", ")} loop too`}</span>
                      {(!l.allowed || (!l.active && startWhy)) && <span className="xs muted">{l.allowed ? `Cannot start: ${startWhy}` : startWhy}</span>}
                    </span>
                    <span className="row" style={{ gap: 6, flexWrap: "nowrap", flex: "0 0 auto" }}>
                      {busy === l.key && <span className="xs muted">…</span>}
                      <button role="switch" aria-checked={!!l.active} aria-label={`${l.label} loop ${l.active ? "on" : "off"}`}
                        className={`switch green ${l.active ? "on" : ""}`} title={l.active ? (l.allowed ? "Stop the loop" : "Not enabled in Settings › Loop control") : startWhy ?? "Start the loop"}
                        disabled={!!busy || (l.active ? !l.allowed : !!startWhy)} onClick={() => switchOne(l, !l.active)}><span /></button>
                    </span>
                  </div>
                );
              })}
              {loops.length === 0 && <span className="muted small">No loop for these channels.</span>}
            </div>
          )}
          {msg && <div className={`banner ${msg.kind} small`}>{msg.text}</div>}
          <span className="xs muted">Only the control loops (actuators) are affected: sensors and probes keep being read.
            The controller is asked to switch, then read back; each change is logged as an event.</span>
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button className="btn text" disabled={!!busy} onClick={() => setOpen(false)}>Close</button>
          </div>
        </Dialog>
      )}
    </>
  );
}

/**
 * "Reset counters": choose which dose counters to reset to 0 on the controller (like its "Reset Dose
 * Monitor"). During an experiment the cumulative curve restarts from 0 there and the reset is an event;
 * the totals in the analysis keep the whole volume dosed since Start.
 */
export function ResetCountersButton({ reactor, recording, compact = false, onDone }:
  { reactor: string; recording: boolean; compact?: boolean; onDone?: () => void }) {
  const [open, setOpen] = useState(false);
  const [st, setSt] = useState<CountersState | null>(null);
  const [sel, setSel] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const show = async () => {
    setOpen(true); setSt(null); setSel([]); setMsg(null);
    try { setSt(await api.counters(reactor)); } catch (x) { setMsg({ kind: "alert", text: (x as Error).message }); }
  };
  const list = (st?.counters ?? []).filter((c) => c.value != null);
  return (
    <>
      <button className="btn outlined" style={compact ? { flex: "1 1 0", justifyContent: "center" } : undefined} onClick={show}>Reset counters</button>
      {open && (
        <Dialog title={`Reset dose counters on ${reactor}`} busy={busy} onClose={() => setOpen(false)} width={480}>
          {!st && !msg && <span className="muted small">Reading the controller…</span>}
          {st && !st.reachable && <div className="banner alert small">{reactor}: no reading from the controller yet.</div>}
          {st?.reachable && (
            <div className="stack" style={{ gap: 8 }}>
              {list.map((c) => (
                <label key={c.key} className="choice">
                  <input type="checkbox" disabled={busy} checked={sel.includes(c.key)}
                    onChange={() => setSel(sel.includes(c.key) ? sel.filter((k) => k !== c.key) : [...sel, c.key])} />
                  <span><b>{c.label}</b> <span className="mono muted">{fmt(c.value, 2)} mL</span></span>
                </label>
              ))}
              {list.length === 0 && <span className="muted small">The controller reports no dose counter.</span>}
            </div>
          )}
          {recording && <div className="banner warn small"><Icon name="warn" color="#8a5a00" size={18} />
            <span>An experiment is recording: the cumulative curve restarts from 0 at the reset. The totals in the analysis keep the volume dosed since Start.</span></div>}
          {msg && <div className={`banner ${msg.kind} small`}>{msg.text}</div>}
          <span className="xs muted">Same as "Reset Dose Monitor" on the controller; logged as an event. Calibration is not touched.</span>
          <div className="row" style={{ justifyContent: "flex-end", gap: 8 }}>
            <button className="btn text" disabled={busy} onClick={() => setOpen(false)}>Close</button>
            <button className="btn filled" disabled={busy || sel.length === 0} onClick={async () => {
              setBusy(true); setMsg(null);
              try {
                const r = await api.resetCounters(reactor, sel);
                const bad = r.results.filter((x) => x.status !== "confirmed");
                setMsg(bad.length ? { kind: "alert", text: `Not confirmed: ${bad.map((b) => `${b.counter} (${b.message || b.status})`).join(", ")}` }
                  : { kind: "good", text: "Reset and confirmed by the controller." });
                setSel([]); setSt(await api.counters(reactor)); onDone?.();
              } catch (x) { setMsg({ kind: "alert", text: (x as Error).message }); }
              finally { setBusy(false); }
            }}>{busy ? "Resetting…" : `Reset ${sel.length || ""} counter${sel.length === 1 ? "" : "s"}`}</button>
          </div>
        </Dialog>
      )}
    </>
  );
}

/** Small helper for pages that only know the live reactor list. */
export const liveOf = (list: LiveReactor[] | undefined, reactor: string) => list?.find((x) => x.reactor_id === reactor);
