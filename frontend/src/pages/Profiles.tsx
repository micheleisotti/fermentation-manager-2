import { useState } from "react";
import { api } from "../api";
import { usePolling } from "../hooks";

const num = (s: string) => (s.trim() === "" ? null : Number(s.replace(",", ".")));

export function Profiles() {
  const list = usePolling(api.profiles, 60000);
  const [f, setF] = useState({ name: "", ph: "", t: "", stir: "", mode: "pid", P: "", I: "", D: "", notes: "" });
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });

  async function create() {
    const pid = f.mode === "pid" && (f.P || f.I || f.D)
      ? Object.fromEntries((["P", "I", "D"] as const).filter((k) => f[k] !== "").map((k) => [k, Number(f[k])])) : null;
    try {
      await api.createProfile({ name: f.name.trim(), ph_setpoint: num(f.ph), temperature_setpoint: num(f.t), stirrer_setpoint: num(f.stir),
        ph_control_mode: f.mode || null, ph_pid: pid, notes: f.notes });
      setF({ name: "", ph: "", t: "", stir: "", mode: "pid", P: "", I: "", D: "", notes: "" });
      setMsg({ kind: "good", text: "Profile saved." }); list.refresh();
    } catch (x) { setMsg({ kind: "alert", text: (x as Error).message }); }
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
              <table className="data" style={{ minWidth: 640 }}>
                <thead><tr><th>Name</th><th>pH</th><th>T (°C)</th><th>Stirrer (rpm)</th><th>pH control</th><th>PID</th></tr></thead>
                <tbody>
                  {(list.data ?? []).map((p) => (
                    <tr key={p.id}>
                      <td style={{ fontWeight: 500 }}>{p.name}</td>
                      <td className="mono">{p.ph_setpoint ?? "—"}</td><td className="mono">{p.temperature_setpoint ?? "—"}</td>
                      <td className="mono">{p.stirrer_setpoint ?? "—"}</td><td>{p.ph_control_mode ?? "—"}</td>
                      <td className="mono small">{p.ph_pid ? Object.entries(p.ph_pid).map(([k, v]) => `${k} ${v}`).join(" · ") : "—"}</td>
                    </tr>
                  ))}
                  {(list.data ?? []).length === 0 && <tr><td colSpan={6} className="muted">No profiles yet.</td></tr>}
                </tbody>
              </table>
            </div>
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
            {f.mode === "pid" && (
              <div className="row" style={{ flexWrap: "nowrap" }}>
                {(["P", "I", "D"] as const).map((k) => <label key={k} className="field">{k}<input inputMode="decimal" value={f[k]} onChange={set(k)} /></label>)}
              </div>
            )}
            <span className="xs muted">PID parameter names will be aligned with the ez2-Control once inspected.</span>
            <label className="field">Notes<textarea rows={3} value={f.notes} onChange={set("notes")} /></label>
            {msg && <div className={`banner ${msg.kind}`}>{msg.text}</div>}
            <button className="btn filled" disabled={!f.name.trim()} onClick={create}>Save profile</button>
          </section>
        </aside>
      </div>
    </>
  );
}
