import { useState } from "react";
import { api, type Channel, type CountersState, type LiveReactor, type LoopsState, type Snapshot, type SwitchLoop } from "../api";
import { fmt } from "../hooks";
import { DataValue, Icon } from "./ui";
import { SetpointDialog } from "./SetpointDialog";
import { LimitsDialog } from "./LimitsDialog";

const isRemote = () => !["localhost", "127.0.0.1"].includes(location.hostname);

/**
 * Live values and commands of one controller: setpoints, process limits. Used on the fermentation
 * page and on the reactor monitoring page (also with no experiment: the controller does not need one).
 * `channels` limits the rows to the experiment's channels; without it, every channel the controller reports.
 */
export function ControllerPanel({ reactor, s, channels, phMode, onChanged }:
  { reactor: string; s: Snapshot; channels?: Channel[]; phMode?: string | null; onChanged?: () => void }) {
  const [dialog, setDialog] = useState<string | null>(null);
  const [limits, setLimits] = useState<string | null>(null);
  const x = s.extra ?? {};
  const has = (c: Channel, reported: boolean) => (channels ? channels.includes(c) : reported);
  const rows: { p: string; v: string; u: string; l: string; c?: string; edit?: boolean; lim?: boolean; note?: string }[] = [];
  if (has("ph", s.ph != null)) {
    rows.push({ p: "ph", v: fmt(s.ph), u: "", l: `pH · set ${fmt(s.ph_setpoint)} · limits ${fmt(s.ph_low_limit)}–${fmt(s.ph_high_limit)}`, c: "#1d3786", edit: true, lim: true });
    rows.push({ p: "base", v: fmt(s.base_cumulative_ml, 2), u: "mL", l: `Base buffer, counter · pump ${fmt(s.base_pump_pct, 0)} %`, c: "#1f7a63" });
    if (x.acid_cumulative_ml != null)
      rows.push({ p: "acid", v: fmt(x.acid_cumulative_ml, 2), u: "mL", l: `Addition 2 (acid), counter${x.acid_pump_pct != null ? ` · pump ${fmt(x.acid_pump_pct, 0)} %` : ""}` });
  }
  if (has("temperature", s.temperature != null))
    rows.push({ p: "temperature", v: fmt(s.temperature, 1), u: "°C", edit: true, lim: true,
      l: `T · set ${fmt(s.temperature_setpoint, 1)} · limits ${fmt(s.temperature_low_limit, 1)}–${fmt(s.temperature_high_limit, 1)} · heating ${fmt(x.heating_pct, 0)} % · cooling ${fmt(x.cooling_pct, 0)} %` });
  if (has("stirrer", s.stirrer != null))
    rows.push({ p: "stirrer", v: fmt(s.stirrer, 0), u: "rpm", l: `Stirrer · set ${fmt(s.stirrer_setpoint, 0)}`, edit: true });
  if (has("do2", x.do2 != null))
    rows.push({ p: "do2", v: fmt(x.do2, 1), u: "%", l: `dO2 · set ${fmt(x.do2_setpoint, 1)} · N2 ${fmt(x.n2_pct, 0)} % · air ${fmt(x.air_pct, 0)} %`, note: "set on the controller" });
  if (has("level", x.level_contact != null))
    rows.push({ p: "level", v: x.level_contact == null ? "—" : x.level_contact ? "Contact" : "No contact", u: "",
      l: `Level / foam probe${x.antifoam_cumulative_ml != null ? ` · antifoam counter ${fmt(x.antifoam_cumulative_ml, 2)}` : ""}`, note: "set on the controller" });
  if (has("redox", x.redox_mv != null))
    rows.push({ p: "redox", v: fmt(x.redox_mv, 0), u: "mV", l: "Redox", note: "read only" });
  return (
    <>
      {rows.map((row) => (
        <div key={row.p} className="row between" style={{ padding: "8px", borderBottom: "1px solid var(--divider)" }}>
          <DataValue value={row.v} unit={row.u} label={row.l} color={row.c} />
          <span className="row" style={{ gap: 4, flexWrap: "nowrap" }}>
            {row.lim && <button className="btn text" onClick={() => setLimits(row.p)} aria-label={`Edit ${row.p} process limits`}>Limits</button>}
            {row.edit && <button className="btn tonal" onClick={() => setDialog(row.p)}>Edit</button>}
            {row.note && <span className="xs muted">{row.note}</span>}
          </span>
        </div>
      ))}
      {rows.length === 0 && <span className="small muted" style={{ padding: 8 }}>No channel reported.</span>}
      <span className="xs muted" style={{ padding: "4px 8px" }}>
        pH control: {phMode ?? "—"}. Dose calibration, interlocks and actuator assignments stay on the controller.
      </span>
      {limits && <LimitsDialog reactor={reactor} parameter={limits} snapshot={s} remote={isRemote()}
        onClose={(changed) => { setLimits(null); if (changed) onChanged?.(); }} />}
      {dialog && <SetpointDialog reactor={reactor} parameter={dialog} snapshot={s} remote={isRemote()}
        onClose={(changed) => { setDialog(null); if (changed) onChanged?.(); }} />}
    </>
  );
}

function Dialog({ title, onClose, busy, children, width = 560 }:
  { title: string; onClose: () => void; busy: boolean; children: React.ReactNode; width?: number }) {
  return (
    <div className="scrim" onMouseDown={(x) => { if (x.target === x.currentTarget && !busy) onClose(); }}>
      <div className="dialog" role="dialog" aria-modal="true" aria-label={title} style={{ width: `min(${width}px, 100vw)` }}>
        <h2 style={{ fontSize: 20 }}>{title}</h2>
        {children}
      </div>
    </div>
  );
}

/**
 * One "Loops" button: the loops of the experiment's channels (or all, on the monitoring page) with
 * their state and Start / Stop next to each. Only loops enabled in Settings › Loop control can be switched.
 */
export function LoopsButton({ reactor, channels, compact = false }: { reactor: string; channels?: Channel[]; compact?: boolean }) {
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
      <button className="btn outlined" style={compact ? { flex: "1 1 0", justifyContent: "center" } : undefined} onClick={show}>Loops</button>
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
                      <span><b>{l.label}</b> <span className={`status ${l.active ? "fermenting" : "planned"}`} style={{ marginLeft: 6 }}>{l.active ? "On" : "Off"}</span></span>
                      <span className="small muted">Actuators: {l.actuators.length ? l.actuators.map((a) => `${a.name}${a.kind === "sensor" ? " (cascade)" : ""}`).join(", ") : "none"}
                        {l.also_switches.length > 0 && ` · switches the ${l.also_switches.join(", ")} loop too`}</span>
                      {(!l.allowed || (!l.active && startWhy)) && <span className="xs muted">{l.allowed ? `Cannot start: ${startWhy}` : startWhy}</span>}
                    </span>
                    <span className="row" style={{ gap: 6, flexWrap: "nowrap", flex: "0 0 auto" }}>
                      <button className="btn tonal" disabled={!!busy || l.active || !!startWhy} onClick={() => switchOne(l, true)}>{busy === l.key && !l.active ? "…" : "Start"}</button>
                      <button className="btn outlined" disabled={!!busy || !l.active || !l.allowed} onClick={() => switchOne(l, false)}>{busy === l.key && l.active ? "…" : "Stop"}</button>
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
