import { useState } from "react";
import { api, type CommandResult, type Snapshot } from "../api";
import { Icon } from "./ui";

const META: Record<string, { label: string; unit: string; digits: number; lo?: keyof Snapshot; hi?: keyof Snapshot; sp: keyof Snapshot }> = {
  ph: { label: "pH", unit: "", digits: 2, lo: "ph_low_limit", hi: "ph_high_limit", sp: "ph_setpoint" },
  temperature: { label: "Temperature", unit: "°C", digits: 1, lo: "temperature_low_limit", hi: "temperature_high_limit", sp: "temperature_setpoint" },
  stirrer: { label: "Stirrer", unit: "rpm", digits: 0, sp: "stirrer_setpoint" },
};

export function SetpointDialog({ reactor, parameter, snapshot, remote, onClose }:
  { reactor: string; parameter: string; snapshot: Snapshot; remote: boolean; onClose: (changed: boolean) => void }) {
  const m = META[parameter];
  const current = snapshot[m.sp] as number | null;
  const [value, setValue] = useState(current === null ? "" : String(current));
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<CommandResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const v = Number(value.replace(",", "."));
  const lo = m.lo ? (snapshot[m.lo] as number | null) : null;
  const hi = m.hi ? (snapshot[m.hi] as number | null) : null;
  const outside = Number.isFinite(v) && ((lo !== null && v < lo) || (hi !== null && v > hi));

  async function send() {
    setBusy(true); setError(null);
    try { setResult(await api.setpoint(reactor, parameter, v, remote ? "remote" : "local")); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <div className="scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(!!result); }}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="sp-title">
        <h2 id="sp-title">{m.label} setpoint · <span className="mono">{reactor}</span></h2>
        {!result ? (
          <>
            <div className="row" style={{ gap: 16, alignItems: "flex-end" }}>
              <div className="dv"><span className="v muted">{current === null ? "—" : current.toFixed(m.digits)}<span className="u">{m.unit}</span></span><span className="l">Current</span></div>
              <span className="muted" style={{ fontSize: 22 }} aria-hidden="true">→</span>
              <label className="field" style={{ width: 150, color: "#003366" }}>New{m.unit ? ` (${m.unit})` : ""}
                <input autoFocus inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)}
                  style={{ fontFamily: "var(--font-mono)", fontSize: 22, fontWeight: 600, border: "2px solid #003366", height: 56 }} />
              </label>
            </div>
            <div className="banner warn">
              <Icon name="warn" color="#8a5a00" size={20} />
              <span><b>{remote ? "Remote command" : "Command"} to {reactor}.</b> The value is read back from the controller and logged.
                {outside && <> <b>{v} is outside the process limits set on the controller ({lo}–{hi}):</b> an alert will follow unless you change the limits there.</>}
              </span>
            </div>
            {error && <div className="banner alert" role="alert">{error}</div>}
            <div className="row" style={{ justifyContent: "flex-end", gap: 8 }}>
              <button className="btn text" onClick={() => onClose(false)}>Cancel</button>
              <button className="btn filled" disabled={busy || !Number.isFinite(v) || value === ""} onClick={send}>{busy ? "Sending…" : "Send to controller"}</button>
            </div>
          </>
        ) : (
          <>
            <div className={`banner ${result.status === "confirmed" ? "good" : "alert"}`} role="status">
              <Icon name={result.status === "confirmed" ? "check" : "warn"} color={result.status === "confirmed" ? "#1f6e5a" : "#b30020"} size={20} />
              <span><b>{result.status === "confirmed" ? "Confirmed by the controller" : "Not confirmed"}</b> · {m.label} {result.old_value ?? "—"} → read back{" "}
                <span className="mono">{result.readback_value ?? "—"}</span>. {result.status !== "confirmed" && result.message}</span>
            </div>
            {result.warnings.map((w) => <div key={w} className="banner warn small">{w}</div>)}
            <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn filled" onClick={() => onClose(true)}>Close</button></div>
          </>
        )}
      </div>
    </div>
  );
}
