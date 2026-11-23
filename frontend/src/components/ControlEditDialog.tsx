import { useState } from "react";
import { api, type CommandResult, type Snapshot } from "../api";
import { Icon } from "./ui";

const META: Record<string, { label: string; unit: string; digits: number; sp: keyof Snapshot; lo?: keyof Snapshot; hi?: keyof Snapshot }> = {
  ph: { label: "pH", unit: "", digits: 2, sp: "ph_setpoint", lo: "ph_low_limit", hi: "ph_high_limit" },
  temperature: { label: "Temperature", unit: "°C", digits: 1, sp: "temperature_setpoint", lo: "temperature_low_limit", hi: "temperature_high_limit" },
  stirrer: { label: "Stirrer", unit: "rpm", digits: 0, sp: "stirrer_setpoint" },
};
const num = (s: string) => Number(s.replace(",", "."));
const str = (v: number | null, d: number) => (v == null ? "" : v.toFixed(d));

/**
 * One Edit for a controller channel: target (setpoint) and, for pH and temperature, the process limits.
 * Only what changed is sent; each value is read back from the controller and logged.
 */
export function ControlEditDialog({ reactor, parameter, snapshot, remote, onClose }:
  { reactor: string; parameter: string; snapshot: Snapshot; remote: boolean; onClose: (changed: boolean) => void }) {
  const m = META[parameter];
  const curSp = snapshot[m.sp] as number | null;
  const curLo = m.lo ? (snapshot[m.lo] as number | null) : null, curHi = m.hi ? (snapshot[m.hi] as number | null) : null;
  const [sp, setSp] = useState(str(curSp, m.digits));
  const [lo, setLo] = useState(str(curLo, m.digits));
  const [hi, setHi] = useState(str(curHi, m.digits));
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<CommandResult[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const vSp = num(sp), vLo = num(lo), vHi = num(hi);
  const spOk = sp !== "" && Number.isFinite(vSp);
  const limOk = !m.lo || (lo !== "" && hi !== "" && Number.isFinite(vLo) && Number.isFinite(vHi) && vLo < vHi);
  const spChanged = spOk && (curSp == null || Math.abs(vSp - curSp) > 1e-9);
  const loChanged = !!m.lo && limOk && vLo !== curLo, hiChanged = !!m.hi && limOk && vHi !== curHi;
  const nothing = !spChanged && !loChanged && !hiChanged;
  const outside = m.lo && spOk && limOk && (vSp < vLo || vSp > vHi);

  async function send() {
    setBusy(true); setError(null);
    const out: CommandResult[] = [];
    try {
      // limits first: a new target inside the new limits raises no alert in between
      if (loChanged || hiChanged)
        out.push(...await api.limits(reactor, parameter, loChanged ? vLo : null, hiChanged ? vHi : null, remote ? "remote" : "local"));
      if (spChanged) out.push(await api.setpoint(reactor, parameter, vSp, remote ? "remote" : "local"));
      setResults(out);
    } catch (e) { setError((e as Error).message); if (out.length) setResults(out); }
    finally { setBusy(false); }
  }

  const field = (label: string, v: string, set: (s: string) => void, big = false) => (
    <label className="field" style={{ flex: "1 1 0", color: "#003366" }}>{label}{m.unit ? ` (${m.unit})` : ""}
      <input inputMode="decimal" value={v} onChange={(e) => set(e.target.value)} autoFocus={big}
        style={{ fontFamily: "var(--font-mono)", fontSize: big ? 22 : 18, fontWeight: 600, border: "2px solid #003366", height: 56 }} />
    </label>
  );
  const what = (p: string) => (p.endsWith("low_limit") ? "Low limit" : p.endsWith("high_limit") ? "High limit" : "Target");

  return (
    <div className="scrim side" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(!!results); }}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="ce-title">
        <h2 id="ce-title">{m.label} · <span className="mono">{reactor}</span></h2>
        {!results ? (
          <>
            <span className="small muted">Now: target <span className="mono">{curSp ?? "—"}</span>
              {m.lo && <> · limits <span className="mono">{curLo ?? "—"} – {curHi ?? "—"}</span></>}{m.unit ? ` ${m.unit}` : ""}</span>
            <div className="row" style={{ gap: 12, flexWrap: "nowrap" }}>{field("Target", sp, setSp, true)}</div>
            {m.lo && <div className="row" style={{ gap: 12, flexWrap: "nowrap" }}>{field("Low limit", lo, setLo)}{field("High limit", hi, setHi)}</div>}
            <div className="banner warn">
              <Icon name="warn" color="#8a5a00" size={20} />
              <span><b>{remote ? "Remote command" : "Command"} to {reactor}.</b> Only the values you change are sent; each is read back and logged.
                {m.lo && " Process limits raise alarms and alerts only: they do not act on pumps or heaters."}
                {outside && <> <b>The target {vSp} is outside the limits {vLo}–{vHi}:</b> an alert will follow.</>}</span>
            </div>
            {m.lo && lo !== "" && hi !== "" && !(vLo < vHi) && <div className="banner alert">Low limit must be below the high limit.</div>}
            {error && <div className="banner alert" role="alert">{error}</div>}
            <div className="row" style={{ justifyContent: "flex-end", gap: 8 }}>
              <button className="btn text" disabled={busy} onClick={() => onClose(false)}>Cancel</button>
              <button className="btn filled" disabled={busy || nothing || !spOk || !limOk} onClick={send}>{busy ? "Sending…" : "Send to controller"}</button>
            </div>
          </>
        ) : (
          <>
            {results.map((r) => (
              <div key={r.parameter} className={`banner ${r.status === "confirmed" ? "good" : "alert"}`} role="status">
                <Icon name={r.status === "confirmed" ? "check" : "warn"} color={r.status === "confirmed" ? "#1f6e5a" : "#b30020"} size={20} />
                <span><b>{what(r.parameter)} {r.status === "confirmed" ? "confirmed" : "not confirmed"}</b> · {r.old_value ?? "—"} → read back{" "}
                  <span className="mono">{r.readback_value ?? "—"}</span>. {r.status !== "confirmed" && r.message}</span>
              </div>
            ))}
            {[...new Set(results.flatMap((r) => r.warnings))].map((w) => <div key={w} className="banner warn small">{w}</div>)}
            {error && <div className="banner alert" role="alert">{error}</div>}
            <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn filled" onClick={() => onClose(true)}>Close</button></div>
          </>
        )}
      </div>
    </div>
  );
}
