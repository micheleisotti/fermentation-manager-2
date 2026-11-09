import { useState } from "react";
import { api, type CommandResult, type Snapshot } from "../api";
import { Icon } from "./ui";

const META: Record<string, { label: string; unit: string; digits: number; lo: keyof Snapshot; hi: keyof Snapshot; sp: keyof Snapshot }> = {
  ph: { label: "pH", unit: "", digits: 2, lo: "ph_low_limit", hi: "ph_high_limit", sp: "ph_setpoint" },
  temperature: { label: "Temperature", unit: "°C", digits: 1, lo: "temperature_low_limit", hi: "temperature_high_limit", sp: "temperature_setpoint" },
};
const num = (s: string) => Number(s.replace(",", "."));

/** Change the process (alarm) limits of pH or temperature on the controller, with read-back. */
export function LimitsDialog({ reactor, parameter, snapshot, remote, onClose }:
  { reactor: string; parameter: string; snapshot: Snapshot; remote: boolean; onClose: (changed: boolean) => void }) {
  const m = META[parameter];
  const curLo = snapshot[m.lo] as number | null, curHi = snapshot[m.hi] as number | null, sp = snapshot[m.sp] as number | null;
  const [lo, setLo] = useState(curLo == null ? "" : curLo.toFixed(m.digits));
  const [hi, setHi] = useState(curHi == null ? "" : curHi.toFixed(m.digits));
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<CommandResult[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const vLo = num(lo), vHi = num(hi);
  const valid = lo !== "" && hi !== "" && Number.isFinite(vLo) && Number.isFinite(vHi) && vLo < vHi;
  const changed = valid && (vLo !== curLo || vHi !== curHi);
  const spOutside = valid && sp != null && (sp < vLo || sp > vHi);

  async function send() {
    setBusy(true); setError(null);
    try {
      setResults(await api.limits(reactor, parameter, vLo !== curLo ? vLo : null, vHi !== curHi ? vHi : null, remote ? "remote" : "local"));
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  const field = (label: string, v: string, set: (s: string) => void) => (
    <label className="field" style={{ flex: "1 1 0", color: "#003366" }}>{label}{m.unit ? ` (${m.unit})` : ""}
      <input inputMode="decimal" value={v} onChange={(e) => set(e.target.value)}
        style={{ fontFamily: "var(--font-mono)", fontSize: 20, fontWeight: 600, border: "2px solid #003366", height: 56 }} />
    </label>
  );

  return (
    <div className="scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(!!results); }}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="lim-title">
        <h2 id="lim-title">{m.label} process limits · <span className="mono">{reactor}</span></h2>
        {!results ? (
          <>
            <span className="small muted">Now <span className="mono">{curLo ?? "—"} – {curHi ?? "—"}{m.unit ? ` ${m.unit}` : ""}</span>
              {sp != null && <> · setpoint <span className="mono">{sp}</span></>}</span>
            <div className="row" style={{ gap: 12, flexWrap: "nowrap" }}>{field("Low", lo, setLo)}{field("High", hi, setHi)}</div>
            <div className="banner warn">
              <Icon name="warn" color="#8a5a00" size={20} />
              <span><b>{remote ? "Remote command" : "Command"} to {reactor}.</b> Process limits raise the controller's alarm and this
                software's out-of-limits alerts; they do not act on pumps or heaters (interlocks stay on the controller). Read back and logged.
                {spOutside && <> <b>The setpoint {sp} is outside {vLo}–{vHi}:</b> an alert will follow.</>}
              </span>
            </div>
            {lo !== "" && hi !== "" && !(vLo < vHi) && <div className="banner alert">Low must be below high.</div>}
            {error && <div className="banner alert" role="alert">{error}</div>}
            <div className="row" style={{ justifyContent: "flex-end", gap: 8 }}>
              <button className="btn text" onClick={() => onClose(false)}>Cancel</button>
              <button className="btn filled" disabled={busy || !changed} onClick={send}>{busy ? "Sending…" : "Send to controller"}</button>
            </div>
          </>
        ) : (
          <>
            {results.map((r) => (
              <div key={r.parameter} className={`banner ${r.status === "confirmed" ? "good" : "alert"}`} role="status">
                <Icon name={r.status === "confirmed" ? "check" : "warn"} color={r.status === "confirmed" ? "#1f6e5a" : "#b30020"} size={20} />
                <span><b>{r.parameter.includes("low") ? "Low" : "High"} limit {r.status === "confirmed" ? "confirmed" : "not confirmed"}</b> ·{" "}
                  {r.old_value ?? "—"} → read back <span className="mono">{r.readback_value ?? "—"}</span>. {r.status !== "confirmed" && r.message}</span>
              </div>
            ))}
            {results[0]?.warnings.map((w) => <div key={w} className="banner warn small">{w}</div>)}
            <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn filled" onClick={() => onClose(true)}>Close</button></div>
          </>
        )}
      </div>
    </div>
  );
}
