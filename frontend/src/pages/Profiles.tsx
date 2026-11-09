import { useState } from "react";
import { api, type Profile } from "../api";
import { usePolling } from "../hooks";

const num = (s: string) => (s.trim() === "" ? null : Number(s.replace(",", ".")));

/** pH loop parameters as named on the ez2-Control ("Controller setup" window). */
const PARAMS = {
  P: { label: "P", unit: "" },
  I: { label: "I", unit: "" },
  D: { label: "D", unit: "" },
  deadband: { label: "Dead band", unit: "pH" },
  cycle_time: { label: "Cycle time", unit: "s" },
  settling_time: { label: "Settling time", unit: "s" },
} as const;
type ParamKey = keyof typeof PARAMS;
/** Loaded for each mode; in Adaptive, P/I/D are starting values the controller then adapts. */
const MODE_PARAMS: Record<string, ParamKey[]> = {
  pid: ["P", "I", "D", "deadband", "cycle_time"],
  adaptive: ["P", "I", "D", "deadband", "cycle_time", "settling_time"],
};

const EMPTY = { name: "", ph: "", t: "", stir: "", mode: "pid", notes: "",
  P: "", I: "", D: "", deadband: "", cycle_time: "", settling_time: "" };

function showParams(p: Profile) {
  if (!p.ph_pid) return "—";
  const keys = MODE_PARAMS[p.ph_control_mode ?? ""] ?? (Object.keys(p.ph_pid) as ParamKey[]);
  const items = keys.filter((k) => p.ph_pid![k] != null);
  if (!items.length) return "—";
  return (
    <span className="stack" style={{ gap: 2 }}>
      {items.map((k) => (
        <span key={k} style={{ whiteSpace: "nowrap" }}>
          <span className="muted">{PARAMS[k]?.label ?? k}</span> {p.ph_pid![k]}{PARAMS[k]?.unit === "s" ? " s" : ""}
        </span>
      ))}
    </span>
  );
}

export function Profiles() {
  const list = usePolling(api.profiles, 60000);
  const [f, setF] = useState(EMPTY);
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const [confirmId, setConfirmId] = useState<number | null>(null);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const modeParams = MODE_PARAMS[f.mode] ?? [];

  async function create() {
    const entries = modeParams.filter((k) => f[k].trim() !== "").map((k) => [k, num(f[k])] as const);
    if (entries.some(([, v]) => v === null || Number.isNaN(v))) {
      setMsg({ kind: "alert", text: "Loop parameters must be numbers." });
      return;
    }
    try {
      await api.createProfile({ name: f.name.trim(), ph_setpoint: num(f.ph), temperature_setpoint: num(f.t), stirrer_setpoint: num(f.stir),
        ph_control_mode: f.mode || null, ph_pid: entries.length ? Object.fromEntries(entries) as Record<string, number> : null, notes: f.notes });
      setF(EMPTY);
      setMsg({ kind: "good", text: "Profile saved." }); list.refresh();
    } catch (x) { setMsg({ kind: "alert", text: (x as Error).message }); }
  }

  async function remove(p: Profile) {
    try {
      const r = await api.deleteProfile(p.id);
      setMsg({ kind: "good", text: `Profile “${p.name}” deleted.` +
        (r.unlinked_experiments.length ? ` Experiments ${r.unlinked_experiments.join(", ")} keep their own copy of the plan.` : "") });
      list.refresh();
    } catch (x) { setMsg({ kind: "alert", text: (x as Error).message }); }
    setConfirmId(null);
  }

  return (
    <>
      <h1>Controller profiles</h1>
      <span className="muted small">A profile is a named set of setpoints and pH control settings, loaded onto a controller from the planning screen, before Start.</span>
      <div className="split">
        <div className="wide">
          <section className="card">
            <h2 style={{ fontSize: 18 }}>Saved profiles</h2>
            <div className="table-wrap">
              <table className="data" style={{ minWidth: 700 }}>
                <thead><tr><th>Name</th><th>pH</th><th>T (°C)</th><th>Stirrer (rpm)</th><th>pH control</th><th>Loop parameters</th><th aria-label="Actions" /></tr></thead>
                <tbody>
                  {(list.data ?? []).map((p) => (
                    <tr key={p.id}>
                      <td style={{ fontWeight: 500 }}>{p.name}</td>
                      <td className="mono">{p.ph_setpoint ?? "—"}</td><td className="mono">{p.temperature_setpoint ?? "—"}</td>
                      <td className="mono">{p.stirrer_setpoint ?? "—"}</td><td>{p.ph_control_mode ?? "—"}</td>
                      <td className="mono small">{showParams(p)}</td>
                      <td style={{ whiteSpace: "nowrap", textAlign: "right", width: 1 }}>
                        {confirmId === p.id ? (
                          <>
                            <button className="btn text" onClick={() => setConfirmId(null)}>Cancel</button>
                            <button className="btn filled" onClick={() => remove(p)} aria-label={`Confirm deleting profile ${p.name}`}>Delete</button>
                          </>
                        ) : (
                          <button className="btn text" onClick={() => { setConfirmId(p.id); setMsg(null); }} aria-label={`Delete profile ${p.name}`}>Delete</button>
                        )}
                      </td>
                    </tr>
                  ))}
                  {(list.data ?? []).length === 0 && <tr><td colSpan={7} className="muted">No profiles yet.</td></tr>}
                </tbody>
              </table>
            </div>
            {msg && <div className={`banner ${msg.kind}`} role="status">{msg.text}</div>}
          </section>
        </div>
        <aside className="side">
          <section className="card">
            <h2 style={{ fontSize: 18 }}>New profile</h2>
            <label className="field">Name<input value={f.name} onChange={set("name")} placeholder="e.g. plantarum standard 1" /></label>
            <div className="row" style={{ flexWrap: "nowrap" }}>
              <label className="field">pH setpoint<input inputMode="decimal" value={f.ph} onChange={set("ph")} /></label>
              <label className="field">T (°C)<input inputMode="decimal" value={f.t} onChange={set("t")} /></label>
              <label className="field">Stirrer (rpm)<input inputMode="decimal" value={f.stir} onChange={set("stir")} /></label>
            </div>
            <label className="field">pH control mode
              <select value={f.mode} onChange={set("mode")}><option value="pid">PID</option><option value="adaptive">Adaptive</option><option value="">Not set</option></select>
            </label>
            {modeParams.length > 0 && (
              <div className="row" style={{ flexWrap: "wrap" }}>
                {modeParams.map((k) => (
                  <label key={k} className="field" style={{ flex: "1 1 30%", minWidth: 90 }}>
                    {PARAMS[k].label}{PARAMS[k].unit === "s" ? " (s)" : ""}
                    <input inputMode="decimal" value={f[k]} onChange={set(k)} />
                  </label>
                ))}
              </div>
            )}
            <span className="xs muted">
              {f.mode === "adaptive"
                ? "In Adaptive, P, I and D are starting values: the controller adapts them after Start. "
                : ""}{f.mode ? "Empty fields are left as they are on the controller. Values are checked against the controller's own limits when loaded." : ""}
            </span>
            <label className="field">Notes<textarea rows={3} value={f.notes} onChange={set("notes")} /></label>
            <button className="btn filled" disabled={!f.name.trim()} onClick={create}>Save profile</button>
          </section>
        </aside>
      </div>
    </>
  );
}
