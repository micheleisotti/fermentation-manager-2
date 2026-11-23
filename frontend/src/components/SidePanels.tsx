/**
 * Panels that open from the right (2.3): Controller, Manual pumps, and the shared shell used by Loops,
 * Reset counters and the setpoint/limit editor. On phones they open from the bottom (CSS).
 */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, type Channel, type Snapshot } from "../api";
import { usePolling } from "../hooks";
import { LoopCards } from "./LoopCards";
import { Icon } from "./ui";

export function SidePanel({ title, sub, onClose, busy = false, children }:
  { title: ReactNode; sub?: ReactNode; onClose: () => void; busy?: boolean; children: ReactNode }) {
  return (
    <div className="scrim side" onMouseDown={(x) => { if (x.target === x.currentTarget && !busy) onClose(); }}>
      <div className="dialog" role="dialog" aria-modal="true">
        <div className="row between" style={{ flexWrap: "nowrap", alignItems: "flex-start" }}>
          <div className="stack" style={{ gap: 2 }}>
            <h2 style={{ fontSize: 20 }}>{title}</h2>
            {sub && <span className="small muted">{sub}</span>}
          </div>
          <button className="btn icon text" aria-label="Close" disabled={busy} onClick={onClose}><Icon name="close" /></button>
        </div>
        {children}
      </div>
    </div>
  );
}

/** Loop on/off dots for the Loops button, from the last reading (loop_* values). */
export function loopDots(s: Snapshot | null | undefined, channels?: Channel[]): boolean[] {
  const x = (s?.extra ?? {}) as Record<string, number | undefined>;
  const keys = (["ph", "temperature", "stirrer", "do2", "level"] as const).filter((k) => !channels || channels.includes(k));
  return keys.filter((k) => x[`loop_${k}`] != null).map((k) => x[`loop_${k}`] === 1);
}

export function ControllerButton({ reactor, name, s, channels, phMode, onChanged, compact = false }:
  { reactor: string; name: string; s: Snapshot | null | undefined; channels?: Channel[]; phMode?: string | null; onChanged?: () => void; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  void channels; void phMode;
  return (
    <>
      <button className="btn outlined" style={compact ? { flex: "1 1 0", justifyContent: "center" } : undefined} onClick={() => setOpen(true)}>Controller</button>
      {open && <ControllerDrawer reactor={reactor} name={name} s={s ?? null} onChanged={onChanged} onClose={() => setOpen(false)} />}
    </>
  );
}

function ControllerDrawer({ reactor, name, s, onChanged, onClose }: { reactor: string; name: string; s: Snapshot | null; onChanged?: () => void; onClose: () => void }) {
  const cfg = usePolling(() => api.loopConfig(reactor), 30000, [reactor]);
  return (
    <SidePanel title={`Controller · ${name}`} onClose={onClose}>
      <LoopCards reactor={reactor} cfg={cfg.data} s={s} onChanged={onChanged} columns={false} />
    </SidePanel>
  );
}

/**
 * Manual pumps: hold to run (prime, 2.46). While the button is held the app tells the lab PC every 0.5 s;
 * FM2 stops the pump when the button is released, when that signal stops for 1.5 s, after 60 s, and when
 * it restarts. The controller itself never stops a manual pump (verified 23 Nov 2026).
 */
export function ManualPumpsButton({ reactor, name, compact = false, trigger }:
  { reactor: string; name: string; compact?: boolean; trigger?: (open: () => void) => ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      {trigger ? trigger(() => setOpen(true)) : <button className="btn outlined" style={compact ? { flex: "1 1 0", justifyContent: "center" } : undefined} onClick={() => setOpen(true)}>Manual pumps</button>}
      {open && <PumpsPanel reactor={reactor} name={name} onClose={() => setOpen(false)} />}
    </>
  );
}

function PumpsPanel({ reactor, name, onClose }: { reactor: string; name: string; onClose: () => void }) {
  const list = usePolling(() => api.pumps(reactor), 1000, [reactor]);
  const [held, setHeld] = useState<{ id: number; t0: number; ml0: number | null } | null>(null);
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const [now, setNow] = useState(Date.now());
  const timer = useRef<number | null>(null);
  const pressed = useRef<number | null>(null);      // pump whose button is down now
  const stop = async (why = "") => {
    const id = pressed.current ?? held?.id ?? null;
    pressed.current = null;
    if (timer.current) { window.clearInterval(timer.current); timer.current = null; }
    setHeld(null);
    if (id == null) return;
    try { await api.releasePump(reactor, id); if (why) setMsg({ kind: "warn", text: why }); }
    catch (x) { setMsg({ kind: "alert", text: (x as Error).message }); }
    list.refresh();
  };
  const start = async (id: number, ml0: number | null) => {
    setMsg(null);
    pressed.current = id;
    try {
      await api.holdPump(reactor, id, true);
      if (pressed.current !== id) { await api.releasePump(reactor, id); list.refresh(); return; }   // released before the answer
      setHeld({ id, t0: Date.now(), ml0 });
      timer.current = window.setInterval(async () => {
        setNow(Date.now());
        try { await api.holdPump(reactor, id, false); }
        catch (x) { if (timer.current) window.clearInterval(timer.current); timer.current = null; setHeld(null); setMsg({ kind: "warn", text: (x as Error).message }); }
      }, 500);
    } catch (x) { setMsg({ kind: "alert", text: (x as Error).message }); }
  };
  // stop when the page loses focus or is hidden, and when the panel closes
  useEffect(() => {
    const off = () => { void stop("Stopped: the page lost focus."); };
    window.addEventListener("blur", off);
    document.addEventListener("visibilitychange", off);
    return () => { window.removeEventListener("blur", off); document.removeEventListener("visibilitychange", off); };
  });
  useEffect(() => () => { if (timer.current) window.clearInterval(timer.current); }, []);
  const pumps = list.data?.pumps ?? [];
  return (
    <SidePanel title={`Manual pumps · ${name}`} onClose={() => { void stop(); onClose(); }}>
      <span className="kicker">Hold the button to run the pump</span>
      {list.data && !list.data.reachable && <div className="banner alert small">{list.data.message}</div>}
      {pumps.map((p) => {
        const mine = held?.id === p.id;
        const ml = mine && held?.ml0 != null && p.counter_ml != null ? Math.max(0, p.counter_ml - held.ml0) : null;
        const sec = mine ? (now - held!.t0) / 1000 : null;
        return (
          <div key={p.id} className="row between" style={{ padding: "10px 0", borderBottom: "1px solid var(--divider)", flexWrap: "nowrap", gap: 12 }}>
            <span className="stack" style={{ gap: 2, minWidth: 0 }}>
              <span>{p.name}</span>
              <span className="xs muted">{mine ? `running · ${sec!.toFixed(1)} s${ml != null ? ` · ${ml.toFixed(2)} mL` : ""}`
                : p.blocked ? `disabled: ${p.blocked}` : p.calibrated === false ? "not calibrated: volume not counted" : p.counter_ml != null ? `counter ${p.counter_ml.toFixed(2)} mL` : ""}</span>
            </span>
            <button className={`btn ${mine ? "filled hold-on" : "tonal"}`} disabled={!!p.blocked || (!!held && !mine)}
              style={{ touchAction: "none", userSelect: "none", minWidth: 128 }}
              onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); void start(p.id, p.counter_ml); }}
              onPointerUp={() => void stop()} onPointerCancel={() => void stop("Stopped.")} onContextMenu={(e) => e.preventDefault()}>
              {mine ? "Running…" : "Hold to run"}</button>
          </div>
        );
      })}
      {msg && <div className={`banner ${msg.kind} small`}>{msg.text}</div>}
      <div className="banner warn small"><Icon name="warn" size={18} color="#8a5a00" />
        <span>The pump stops when you release the button, after 60 s, if this page loses focus or the connection drops (1.5 s), and when FM2
          restarts. Disabled while the pump's loop is on or the pump is interlocked. If the lab PC stops while a pump runs, stop it on the
          controller's display. Each run is logged with its volume (calibrated pumps). Gas valves are not available here.</span></div>
    </SidePanel>
  );
}
