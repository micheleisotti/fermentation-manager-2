/**
 * Phone layout (2.3.1, mobile prototype): status dots, value pill, reactor preview sheet, setpoint sheet
 * with steppers, and the full-width tiles that open the Loops / Dose counters / Manual pumps sheets.
 * Sheets open from the bottom on phones (the same panels open from the right on wide screens).
 */
import { useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Channel, type LiveReactor, type Snapshot } from "../api";
import { fmt, fmtAge, fmtDuration, usePolling } from "../hooks";
import { phAndRateOption } from "./charts";
import { EChart, Icon } from "./ui";
import { LoopsButton, ResetCountersButton } from "./ReactorControls";
import { ManualPumpsButton, SidePanel } from "./SidePanels";

const outOf = (v: number | null | undefined, lo: number | null | undefined, hi: number | null | undefined) =>
  v != null && lo != null && hi != null && (v < lo || v > hi);

/** Horizontal pill split in sections: one per enabled channel, plus base mL when pH is a channel. */
export function ValuePill({ s, ch, onTap }: { s: Snapshot | null | undefined; ch: Channel[] | null; onTap?: (channel: string) => void }) {
  const x = (s?.extra ?? {}) as Record<string, number | undefined>;
  const on = (c: Channel, reported: boolean) => (ch ? ch.includes(c) : reported);
  const cells: { k: string; ch?: string; label: string; v: string; bad?: boolean }[] = [];
  if (on("ph", s?.ph != null)) cells.push({ k: "ph", ch: "ph", label: "pH", v: fmt(s?.ph), bad: outOf(s?.ph, s?.ph_low_limit, s?.ph_high_limit) });
  if (on("temperature", s?.temperature != null)) cells.push({ k: "t", ch: "temperature", label: "T °C", v: fmt(s?.temperature, 1), bad: outOf(s?.temperature, s?.temperature_low_limit, s?.temperature_high_limit) });
  if (on("stirrer", s?.stirrer != null)) cells.push({ k: "st", ch: "stirrer", label: "Stirrer rpm", v: fmt(s?.stirrer, 0) });
  if (on("do2", x.do2 != null)) cells.push({ k: "do2", ch: "do2", label: "dO2 %", v: fmt(x.do2, 1) });
  if (on("ph", s?.ph != null)) cells.push({ k: "base", label: "Base mL", v: fmt(s?.base_cumulative_ml, 1) });
  if (on("redox", x.redox_mv != null)) cells.push({ k: "rdx", label: "Redox mV", v: fmt(x.redox_mv, 0) });
  if (on("level", false)) cells.push({ k: "lvl", label: "Level", v: x.level_contact == null ? "—" : x.level_contact ? "contact" : "no cont." });
  return (
    <div className="vstrip" style={{ gridTemplateColumns: `repeat(${Math.max(1, cells.length)}, minmax(0, 1fr))` }}>
      {cells.map((c) => {
        const body = <><span className="xs muted">{c.label}</span><b className="mono" style={c.bad ? { color: "var(--alert-text)" } : undefined}>{c.v}</b></>;
        return onTap && c.ch ? <button key={c.k} className="vcell" onClick={(e) => { e.stopPropagation(); onTap(c.ch!); }}>{body}</button>
          : <div key={c.k}>{body}</div>;
      })}
    </div>
  );
}

const LOOP_NAMES: Record<string, string> = { ph: "pH", temperature: "T", stirrer: "Stirrer", do2: "dO2", level: "Level" };
export function loopList(s: Snapshot | null | undefined, ch?: Channel[] | null) {
  const x = (s?.extra ?? {}) as Record<string, number | undefined>;
  return Object.keys(LOOP_NAMES).filter((k) => x[`loop_${k}`] != null && (!ch || ch.includes(k as Channel)))
    .map((k) => ({ k, name: LOOP_NAMES[k], on: x[`loop_${k}`] === 1 }));
}

/** Full-width tile: title, short status on the right, opens a sheet. */
function Tile({ title, sub, aside, onClick }: { title: ReactNode; sub?: ReactNode; aside?: ReactNode; onClick: () => void }) {
  return (
    <button className="m-tile" onClick={onClick}>
      <span className="m-tile-t"><b>{title}</b>{sub && <span className="xs muted">{sub}</span>}</span>
      <span className="row" style={{ gap: 6, flexWrap: "nowrap" }}>{aside}<span className="m-chev">›</span></span>
    </button>
  );
}

export function LoopsTile({ reactor, s, ch }: { reactor: string; s: Snapshot | null | undefined; ch?: Channel[] | null }) {
  const loops = loopList(s, ch);
  return (
    <LoopsButton reactor={reactor} channels={ch ?? undefined} trigger={(open) => (
      <Tile title="Loops" sub={`${loops.filter((l) => l.on).length} on`} onClick={open}
        aside={<span className="loop-names">{loops.map((l) => <i key={l.k} className={l.on ? "on" : ""}>{l.name}</i>)}</span>} />
    )} />
  );
}

export function CountersTile({ reactor, s, recording, onDone }: { reactor: string; s: Snapshot | null | undefined; recording: boolean; onDone?: () => void }) {
  const x = (s?.extra ?? {}) as Record<string, number | undefined>;
  const parts = [s?.base_cumulative_ml != null && `Alkali ${fmt(s.base_cumulative_ml, 1)}`, x.acid_cumulative_ml != null && `Add. 2 ${fmt(x.acid_cumulative_ml, 1)}`,
    x.antifoam_cumulative_ml != null && `Antifoam ${fmt(x.antifoam_cumulative_ml, 1)}`].filter(Boolean).join(" · ");
  return (
    <ResetCountersButton reactor={reactor} recording={recording} onDone={onDone} trigger={(open) => (
      <Tile title="Dose counters" sub={parts ? `${parts} mL` : ""} onClick={open} />
    )} />
  );
}

export function PumpsTile({ reactor, name }: { reactor: string; name: string }) {
  return (
    <ManualPumpsButton reactor={reactor} name={name} trigger={(open) => (
      <Tile title="Manual dose" sub="hold to run" onClick={open} />
    )} />
  );
}

// --- setpoint sheet ---------------------------------------------------------------------------

const SP: Record<string, { label: string; unit: string; d: number; step: number; sp: (s: Snapshot) => number | null | undefined;
  lo: (s: Snapshot) => number | null | undefined; hi: (s: Snapshot) => number | null | undefined; now: (s: Snapshot) => number | null | undefined }> = {
  ph: { label: "pH", unit: "", d: 2, step: 0.05, sp: (s) => s.ph_setpoint, lo: (s) => s.ph_low_limit, hi: (s) => s.ph_high_limit, now: (s) => s.ph },
  temperature: { label: "T", unit: "°C", d: 1, step: 0.5, sp: (s) => s.temperature_setpoint, lo: (s) => s.temperature_low_limit, hi: (s) => s.temperature_high_limit, now: (s) => s.temperature },
  stirrer: { label: "Stirrer", unit: "rpm", d: 0, step: 10, sp: (s) => s.stirrer_setpoint, lo: (s) => s.extra?.stirrer_low_limit, hi: (s) => s.extra?.stirrer_high_limit, now: (s) => s.stirrer },
  do2: { label: "dO2", unit: "%", d: 1, step: 1, sp: (s) => s.extra?.do2_setpoint, lo: (s) => s.extra?.do2_low_limit, hi: (s) => s.extra?.do2_high_limit, now: (s) => s.extra?.do2 },
};

export function SetpointSheet({ reactor, name, s, channels, initial, onClose }:
  { reactor: string; name: string; s: Snapshot; channels: Channel[]; initial: string; onClose: (changed: boolean) => void }) {
  const avail = Object.keys(SP).filter((k) => channels.includes(k as Channel));
  const [k, setK] = useState(avail.includes(initial) ? initial : avail[0]);
  const m = SP[k];
  const start = () => [m.sp(s), m.hi(s), m.lo(s)].map((v) => (v == null ? null : Number(v)));
  const [vals, setVals] = useState<(number | null)[]>(start);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const [changed, setChanged] = useState(false);
  const pick = (x: string) => { setK(x); const mm = SP[x]; setVals([mm.sp(s), mm.hi(s), mm.lo(s)].map((v) => (v == null ? null : Number(v)))); setMsg(null); };
  const step = (i: number, dir: number) => setVals(vals.map((v, j) => (j === i && v != null ? Number((v + dir * m.step).toFixed(m.d)) : v)));
  const loop = (s.extra as Record<string, number | undefined> | undefined)?.[`loop_${k}`];
  const remote = !["localhost", "127.0.0.1"].includes(location.hostname) ? "remote" : "local";
  const apply = async () => {
    const [sp0, hi0, lo0] = start();
    const [sp, hi, lo] = vals;
    setBusy(true); setMsg(null);
    try {
      const out: string[] = [];
      if (sp != null && sp !== sp0) {
        const r = await api.setpoint(reactor, k, sp, remote);
        out.push(`setpoint ${r.status === "confirmed" ? "confirmed" : `NOT confirmed (${r.message || r.status})`}`);
      }
      if ((hi != null && hi !== hi0) || (lo != null && lo !== lo0)) {
        const rs = await api.limits(reactor, k, lo !== lo0 ? lo : null, hi !== hi0 ? hi : null, remote);
        out.push(`limits ${rs.every((x) => x.status === "confirmed") ? "confirmed" : "NOT confirmed"}`);
      }
      if (!out.length) { setMsg({ kind: "warn", text: "Nothing changed." }); return; }
      setChanged(true);
      setMsg({ kind: out.some((x) => x.includes("NOT")) ? "alert" : "good", text: `${m.label}: ${out.join(", ")} by the controller.` });
    } catch (x) { setMsg({ kind: "alert", text: (x as Error).message }); }
    finally { setBusy(false); }
  };
  const row = (label: string, i: number) => (
    <div className="sp-field">
      <span>{label}</span>
      <span className="stepper">
        <button aria-label={`${label} down`} disabled={vals[i] == null || busy} onClick={() => step(i, -1)}>−</button>
        <b className="mono">{vals[i] == null ? "—" : vals[i]!.toFixed(m.d)}</b>
        <button aria-label={`${label} up`} disabled={vals[i] == null || busy} onClick={() => step(i, 1)}>+</button>
      </span>
    </div>
  );
  return (
    <SidePanel title={`${m.label} · ${name}`} busy={busy} onClose={() => onClose(changed)}>
      <div className="row" style={{ gap: 6 }}>
        {avail.map((x) => <button key={x} className={`chip ${x === k ? "on" : ""}`} onClick={() => pick(x)}>{SP[x].label}</button>)}
      </div>
      <span className="xs muted">Now <b className="mono" style={{ color: "var(--ink)" }}>{fmt(m.now(s), m.d)} {m.unit}</b>{loop != null ? ` · loop ${loop ? "on" : "off"}` : ""}</span>
      {row(`Setpoint${m.unit ? ` (${m.unit})` : ""}`, 0)}
      {row("Limit high", 1)}
      {row("Limit low", 2)}
      {msg && <div className={`banner ${msg.kind} small`}>{msg.text}</div>}
      <div className="banner info small"><span>FM2 writes the values and reads them back from the controller; the change goes to Events with your name.
        Limits are alarms only.</span></div>
      <div className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
        <button className="btn outlined" style={{ flex: 1, justifyContent: "center" }} onClick={() => onClose(changed)} disabled={busy}>Close</button>
        <button className="btn filled" style={{ flex: 1, justifyContent: "center" }} onClick={apply} disabled={busy}>{busy ? "Writing…" : "Apply"}</button>
      </div>
    </SidePanel>
  );
}

// --- reactor preview sheet (Home, phones) ------------------------------------------------------

export function ReactorPreview({ r, onClose }: { r: LiveReactor; onClose: () => void }) {
  const nav = useNavigate();
  const exp = r.experiment;
  const ch = (exp?.channels ?? null) as Channel[] | null;
  const readings = usePolling(() => (exp ? api.readings(exp.id) : Promise.resolve(undefined)), 30000, [exp?.id]);
  const analysis = usePolling(() => (exp ? api.analysis(exp.id, 0.25) : Promise.resolve(undefined)), 60000, [exp?.id]);
  const option = useMemo(() => phAndRateOption(readings.data, analysis.data?.rate_bins, undefined, analysis.data?.estimated_end?.t_end_h),
    [readings.data, analysis.data]);
  const [sp, setSp] = useState<string | null>(null);
  const s = r.snapshot;
  const name = r.name || r.reactor_id;
  const loops = loopList(s, ch);
  const spChannels = (ch ?? (["ph", "temperature", "stirrer", "do2"] as Channel[])).filter((c) => c in SP);
  return (
    <SidePanel title={exp ? exp.name : name}
      sub={<>{name}{exp ? <> · <span className="mono">{exp.code}</span>{exp.status === "fermenting" ? ` · ${fmtDuration(exp.t_h)}` : " · preparing"}</> : " · no experiment"}</>}
      onClose={onClose}>
      {r.online ? <ValuePill s={s} ch={ch} onTap={setSp} /> : <div className="banner alert small">Controller not reachable{r.error ? `: ${r.error}` : ""}</div>}
      <span className="xs muted">{exp ? "Channels planned for this experiment" : "Live values, no experiment"} · {fmtAge(r.last_reading_age_s)} ago</span>
      {exp && ch?.includes("ph") && <EChart option={option} className="chart mini" ariaLabel={`${name}: pH and base rate since start`} />}
      {loops.length > 0 && <LoopsTile reactor={r.reactor_id} s={s} ch={ch} />}
      <div className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
        <button className="btn outlined" style={{ flex: 1, justifyContent: "center" }} disabled={!r.online || !s} onClick={() => setSp(spChannels[0] ?? "ph")}>Setpoints</button>
        <button className="btn filled" style={{ flex: 1, justifyContent: "center" }}
          onClick={() => nav(exp ? `/experiments/${exp.id}` : `/reactors/${r.reactor_id}`)}>{exp ? "Open" : "Controller"}</button>
      </div>
      {sp && s && <SetpointSheet reactor={r.reactor_id} name={name} s={s} channels={spChannels} initial={sp} onClose={() => setSp(null)} />}
    </SidePanel>
  );
}

// --- status dots (phone header) ------------------------------------------------------------------

export function StatusDots({ live }: { live: LiveReactor[] | undefined }) {
  const sys = usePolling(api.systemStatus, 30000);
  const [open, setOpen] = useState(false);
  const down = !!sys.error;
  const remote = sys.data?.remote.state;
  const items = [
    { k: "pc", label: down ? "Lab PC not reachable" : "Lab PC online", st: down ? "bad" : sys.data ? "ok" : "idle" },
    { k: "remote", label: remote === "available" ? "Remote access on" : remote === "unknown" ? "Remote access: unknown" : "Remote access off",
      st: down ? "idle" : remote === "available" ? "ok" : remote === "unknown" ? "idle" : "bad" },
    ...(live ?? []).map((r) => ({ k: r.reactor_id, label: `${r.name || r.reactor_id}: ${!r.online ? "controller not reachable" : r.experiment
      ? `${r.experiment.code} ${r.experiment.status === "fermenting" ? "recording" : "preparing"}` : "idle"}`,
      st: !r.online ? "bad" : r.experiment?.status === "fermenting" ? "ok" : r.experiment ? "prep" : "idle" })),
  ];
  return (
    <>
      <button className="status-dots" aria-label="Status: Lab PC, remote access, bioreactors" onClick={() => setOpen(true)}>
        {items.map((x) => <span key={x.k} className={`dot ${x.st}`} />)}
      </button>
      {open && (
        <SidePanel title="Status" onClose={() => setOpen(false)}>
          {items.map((x) => (
            <div key={x.k} className="row" style={{ gap: 10, padding: "8px 0", borderBottom: "1px solid var(--divider)", flexWrap: "nowrap" }}>
              <span className={`dot ${x.st}`} /><span>{x.label}</span>
            </div>
          ))}
          {sys.data && <span className="xs muted">Server {sys.data.server.host} · v{sys.data.server.version}</span>}
        </SidePanel>
      )}
    </>
  );
}

export { Icon };
