/**
 * Panels that open from the right (2.3): Controller, Manual pumps, and the shared shell used by Loops,
 * Reset counters and the setpoint/limit editor. On phones they open from the bottom (CSS).
 */
import { useState, type ReactNode } from "react";
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

/** Manual pumps: hold to run. The command is verified on the controller (23 Nov) and comes in 2.3.x. */
export function ManualPumpsButton({ name, compact = false }: { name: string; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button className="btn outlined" style={compact ? { flex: "1 1 0", justifyContent: "center" } : undefined} onClick={() => setOpen(true)}>Manual pumps</button>
      {open && (
        <SidePanel title={`Manual pumps · ${name}`} onClose={() => setOpen(false)}>
          <span className="kicker">Hold the button to run the pump</span>
          {["Alkali", "Addition 2", "Antifoam"].map((p) => (
            <div key={p} className="row between" style={{ padding: "8px 0", borderBottom: "1px solid var(--divider)", flexWrap: "nowrap" }}>
              <span>{p}</span><button className="btn tonal" disabled>Hold to run</button>
            </div>
          ))}
          <div className="banner warn small"><Icon name="warn" size={18} color="#8a5a00" />
            <span>Coming in 2.3.x. The controller does not stop a manual pump by itself, so FM2 will stop it when you release the
              button, after 60 s, when the signal from this device stops for 1.5 s, and when FM2 restarts; disabled while the
              pump's loop is on or the pump is interlocked. Until then use the controller's display.</span></div>
        </SidePanel>
      )}
    </>
  );
}
