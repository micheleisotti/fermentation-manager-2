/**
 * Loops as read from the controller (2.3, desktop prototype "Controller"): one card per loop with value,
 * target and limits (edit), its actuators with output %, then sensors and outputs in no loop.
 */
import { useState } from "react";
import type { LoopConfigView, LoopInfo, Snapshot } from "../api";
import { fmt } from "../hooks";
import { ControlEditDialog } from "./ControlEditDialog";

const EDITABLE: Record<string, string> = { ph: "ph", temperature: "temperature", stirrer: "stirrer" };

/** Output of an actuator from the last reading, by name (the controller's names on R1/R2). */
function outputOf(name: string, s: Snapshot | null): { v: number | null; unit: string } {
  const x = (s?.extra ?? {}) as Record<string, number | undefined>;
  const n = name.toLowerCase();
  if (n.includes("alkali") || n.includes("base")) return { v: s?.base_pump_pct ?? null, unit: "%" };
  if (n.includes("addition 2") || n.includes("acid")) return { v: x.acid_pump_pct ?? null, unit: "%" };
  if (n.includes("heat")) return { v: x.heating_pct ?? null, unit: "%" };
  if (n.includes("cool")) return { v: x.cooling_pct ?? null, unit: "%" };
  if (n.includes("antifoam")) return { v: x.antifoam_pct ?? null, unit: "%" };
  if (n.includes("n2")) return { v: x.n2_pct ?? null, unit: "%" };
  if (n.includes("air")) return { v: x.air_pct ?? null, unit: "%" };
  if (n.includes("stirrer")) return { v: x.stirrer_out_rpm ?? null, unit: "rpm" };
  return { v: null, unit: "%" };
}

function limitsOf(key: string, s: Snapshot | null): [number | null | undefined, number | null | undefined, number | null | undefined, number] {
  const x = (s?.extra ?? {}) as Record<string, number | undefined>;
  if (key === "ph") return [s?.ph_setpoint, s?.ph_low_limit, s?.ph_high_limit, 2];
  if (key === "temperature") return [s?.temperature_setpoint, s?.temperature_low_limit, s?.temperature_high_limit, 1];
  if (key === "stirrer") return [s?.stirrer_setpoint, null, null, 0];
  if (key === "do2") return [x.do2_setpoint, x.do2_low_limit, x.do2_high_limit, 1];
  return [null, null, null, 1];
}

function Bar({ name, v, unit }: { name: string; v: number | null; unit: string }) {
  const pct = unit === "%" && v != null ? Math.max(0, Math.min(100, v)) : null;
  return (
    <div className="act-row">
      <span className="row between small" style={{ flexWrap: "nowrap" }}><span>{name}</span><span className="mono">{v == null ? "—" : `${fmt(v, 0)} ${unit}`}</span></span>
      {pct != null && <span className="act-bar"><i style={{ width: `${pct}%` }} /></span>}
    </div>
  );
}

function LoopCard({ l, s, onEdit }: { l: LoopInfo; s: Snapshot | null; onEdit?: (p: string) => void }) {
  const [sp, lo, hi, d] = limitsOf(l.key, s);
  const level = l.key === "level";
  const unit = { temperature: "°C", stirrer: "rpm", do2: "%", redox: "mV" }[l.key] ?? "";
  return (
    <div className="loop-card">
      <div className="row between" style={{ flexWrap: "nowrap" }}>
        <span className="kicker">{l.label}</span>
        <span className={`loop-badge ${l.active ? "on" : ""}`}>{l.has_actuator === false || l.actuators.length === 0 ? "sensor" : `loop ${l.active ? "on" : "off"}`}</span>
      </div>
      <div className="row between" style={{ alignItems: "flex-end", flexWrap: "nowrap" }}>
        <span className="lc-val mono">{l.value ?? "—"}{!level && unit && <small>{unit}</small>}</span>
        {!level && (
          <span className="stack xs muted" style={{ gap: 0, alignItems: "flex-end", textAlign: "right" }}>
            {sp != null && <span>set {fmt(sp, d)}{unit ? ` ${unit}` : ""}</span>}
            {(lo != null || hi != null) && <span>limits {fmt(lo, d)} – {fmt(hi, d)}</span>}
            {EDITABLE[l.key] && onEdit && <button className="link-btn" onClick={() => onEdit(EDITABLE[l.key])}>edit</button>}
          </span>
        )}
      </div>
      {l.actuators.map((a) => { const o = outputOf(a.name, s); return <Bar key={a.id} name={`${a.name}${a.side_label ? ` · ${a.side_label}` : ""}`} v={o.v} unit={o.unit} />; })}
      {l.drives && <span className="xs muted">Cascade to {l.drives}{l.active ? "" : " (inactive while the loop is off)"}</span>}
      {l.control_type && l.key !== "stirrer" && l.actuators.length > 0 && <span className="xs muted">control {l.control_type}</span>}
      {l.warnings.map((w) => <span key={w} className="xs" style={{ color: "var(--data-orange-text)" }}>⚠ {w}</span>)}
    </div>
  );
}

export function LoopCards({ reactor, cfg, s, onChanged, columns = true }:
  { reactor: string; cfg: LoopConfigView | undefined; s: Snapshot | null; onChanged?: () => void; columns?: boolean }) {
  const [edit, setEdit] = useState<string | null>(null);
  if (!cfg) return <span className="small muted">Reading the controller configuration…</span>;
  if (!cfg.reachable) return <span className="small muted">{cfg.message ?? "Controller not reachable."}</span>;
  const loops = cfg.loops.filter((l) => l.installed);
  const free = cfg.unassigned_pumps;
  return (
    <>
      <div className={columns ? "loop-grid" : "stack"} style={columns ? undefined : { gap: 10 }}>
        {loops.map((l) => <LoopCard key={l.key} l={l} s={s} onEdit={s ? setEdit : undefined} />)}
      </div>
      {free.length > 0 && <>
        <span className="kicker" style={{ marginTop: 6 }}>Outputs not assigned to a loop</span>
        <div className={columns ? "free-grid" : "stack"} style={columns ? undefined : { gap: 8 }}>
          {free.map((a) => { const o = outputOf(a.name, s); return <div key={a.id} className="loop-card slim"><Bar name={a.name} v={o.v} unit={o.unit} /></div>; })}
        </div>
      </>}
      <span className="xs muted">As read from the controller: each loop with its sensor and actuators (output now), then the outputs in no loop.
        Assignments are changed on the controller's display (from FM2 in 2.3.x).</span>
      {edit && s && <ControlEditDialog reactor={reactor} parameter={edit} snapshot={s} remote={!window.location.hostname.match(/^(localhost|127\.)/)}
        onClose={(changed) => { setEdit(null); if (changed) onChanged?.(); }} />}
    </>
  );
}
