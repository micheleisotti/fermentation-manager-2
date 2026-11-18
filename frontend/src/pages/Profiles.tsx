import { sidesText } from "../loopText";
import { useState } from "react";
import { api, type LoopAssignments, type Profile } from "../api";
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
/** The temperature loop has no dead band on R1/R2 (disabled on the controller: not shown there either). */
const T_MODE_PARAMS: Record<string, ParamKey[]> = Object.fromEntries(
  Object.entries(MODE_PARAMS).map(([m, ks]) => [m, ks.filter((k) => k !== "deadband")]));

const EMPTY = { name: "", ph: "", t: "", stir: "", mode: "pid", notes: "", phLo: "", phHi: "", tLo: "", tHi: "",
  P: "", I: "", D: "", deadband: "", cycle_time: "", settling_time: "",
  tmode: "", tP: "", tI: "", tD: "", tdeadband: "", tcycle_time: "", tsettling_time: "" };
type Form = typeof EMPTY;

function showParams(mode: string | null, pid: Record<string, number> | null, unitDb: string, table = MODE_PARAMS) {
  if (!mode && !pid) return null;
  const keys = table[mode ?? ""] ?? (Object.keys(pid ?? {}) as ParamKey[]);
  const items = keys.filter((k) => pid?.[k] != null);
  return (
    <span className="small">
      <b>{mode ?? "—"}</b>{items.map((k) => (
        <span key={k} style={{ whiteSpace: "nowrap" }}> · <span className="muted">{PARAMS[k]?.label ?? k}</span> {pid![k]}{PARAMS[k]?.unit === "s" ? " s" : k === "deadband" ? ` ${unitDb}` : ""}</span>
      ))}
    </span>
  );
}

/** "Alkali (+), Addition 2 (−)" — + acts below the setpoint, − above. */
/** "below target: Alkali · above target: Addition 2" */
export function showAssignments(items: { name: string; side: number; kind?: string }[] | undefined) {
  return sidesText(items?.map((a) => ({ kind: "actuator", ...a })));
}

const str = (v: number | null | undefined) => (v == null ? "" : String(v));
/** The form filled with a saved profile (Edit). */
function formOf(p: Profile): Form {
  const pid = (pre: "" | "t", d: Record<string, number> | null) =>
    Object.fromEntries(Object.keys(PARAMS).map((k) => [`${pre}${k}`, str(d?.[k])]));
  return { ...EMPTY, name: p.name, ph: str(p.ph_setpoint), t: str(p.temperature_setpoint), stir: str(p.stirrer_setpoint),
    mode: p.ph_control_mode ?? "", notes: p.notes ?? "", phLo: str(p.ph_low_limit), phHi: str(p.ph_high_limit),
    tLo: str(p.temperature_low_limit), tHi: str(p.temperature_high_limit), tmode: p.temperature_control_mode ?? "",
    ...pid("", p.ph_pid), ...pid("t", p.temperature_pid) };
}

function loopEntries(f: Form, prefix: "" | "t", mode: string) {
  const keys = (prefix === "t" ? T_MODE_PARAMS : MODE_PARAMS)[mode] ?? [];
  return keys.map((k) => [k, f[`${prefix}${k}` as keyof Form] as string] as const).filter(([, v]) => v.trim() !== "")
    .map(([k, v]) => [k, num(v)] as const);
}

function showLimits(p: Profile) {
  const r = (lo: number | null, hi: number | null) => (lo == null && hi == null ? null : `${lo ?? "·"}–${hi ?? "·"}`);
  const ph = r(p.ph_low_limit, p.ph_high_limit), t = r(p.temperature_low_limit, p.temperature_high_limit);
  if (!ph && !t) return "—";
  return <span className="stack" style={{ gap: 2 }}>{ph && <span><span className="muted">pH</span> {ph}</span>}{t && <span><span className="muted">T</span> {t} °C</span>}</span>;
}

export function Profiles({ embedded = false }: { embedded?: boolean }) {
  const list = usePolling(api.profiles, 60000);
  const [f, setF] = useState(EMPTY);
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const [confirmId, setConfirmId] = useState<number | null>(null);
  const [assign, setAssign] = useState<{ from: string; a: LoopAssignments } | null>(null);
  const [editing, setEditing] = useState<Profile | null>(null);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const modeParams = MODE_PARAMS[f.mode] ?? [];
  const tModeParams = T_MODE_PARAMS[f.tmode] ?? [];

  async function record(reactor: string) {
    try {
      const c = await api.loopConfig(reactor, true);
      if (!c.reachable) { setMsg({ kind: "alert", text: `${reactor}: controller not reachable (${c.message ?? ""})` }); return; }
      setAssign({ from: reactor, a: c.assignments });
    } catch (x) { setMsg({ kind: "alert", text: (x as Error).message }); }
  }

  function startEdit(p: Profile) {
    setEditing(p); setF(formOf(p)); setMsg(null);
    setAssign(p.loop_assignments ? { from: "the saved profile", a: p.loop_assignments } : null);
    document.getElementById("profile-form")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  function cancelEdit() { setEditing(null); setF(EMPTY); setAssign(null); }

  async function create() {
    const entries = loopEntries(f, "", f.mode), tEntries = loopEntries(f, "t", f.tmode);
    if ([...entries, ...tEntries].some(([, v]) => v === null || Number.isNaN(v))) {
      setMsg({ kind: "alert", text: "Loop parameters must be numbers." });
      return;
    }
    try {
      const lim = [num(f.phLo), num(f.phHi), num(f.tLo), num(f.tHi)];
      if (lim.some((v) => v !== null && Number.isNaN(v))) { setMsg({ kind: "alert", text: "Limits must be numbers." }); return; }
      if ((lim[0] !== null && lim[1] !== null && lim[0] >= lim[1]) || (lim[2] !== null && lim[3] !== null && lim[2] >= lim[3])) {
        setMsg({ kind: "alert", text: "Each low limit must be below its high limit." }); return;
      }
      const body = { name: f.name.trim(), ph_setpoint: num(f.ph), temperature_setpoint: num(f.t), stirrer_setpoint: num(f.stir),
        ph_low_limit: lim[0], ph_high_limit: lim[1], temperature_low_limit: lim[2], temperature_high_limit: lim[3],
        ph_control_mode: f.mode || null, ph_pid: entries.length ? Object.fromEntries(entries) as Record<string, number> : null,
        temperature_control_mode: f.tmode || null,
        temperature_pid: tEntries.length ? Object.fromEntries(tEntries) as Record<string, number> : null,
        loop_assignments: assign?.a ?? null, notes: f.notes };
      if (editing) {
        const r = await api.updateProfile(editing.id, body);
        const n = r.n_experiments ?? 0;
        setMsg({ kind: "good", text: `Profile “${r.name}” updated.` + (n && r.updated_at !== editing.updated_at
          ? ` ${n} experiment${n > 1 ? "s" : ""} using it keep${n > 1 ? "" : "s"} its previous values and show that the profile changed.` : "") });
      } else {
        await api.createProfile(body);
        setMsg({ kind: "good", text: "Profile saved." });
      }
      setF(EMPTY); setAssign(null); setEditing(null); list.refresh();
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
      {!embedded && <h1 className="display">Controller profiles</h1>}
      <span className="muted small">A profile is a named set of setpoints, process limits and pH / temperature control settings, loaded onto a controller from the planning screen, before Start.
        It can record which actuators each loop had when it was made: if the controller has different ones, that loop's control settings are not loaded.</span>
      <div className="split">
        <div className="wide">
          <section className="card">
            <h2 style={{ fontSize: 18 }}>Saved profiles</h2>
            <div className="table-wrap">
              <table className="data" style={{ minWidth: 760 }}>
                <thead><tr><th>Name</th><th>pH</th><th>T (°C)</th><th>Stirrer (rpm)</th><th>Limits</th><th>Loop control</th><th aria-label="Actions" /></tr></thead>
                <tbody>
                  {(list.data ?? []).map((p) => (
                    <tr key={p.id}>
                      <td><span style={{ fontWeight: 600 }}>{p.name}</span>
                        {!!p.n_experiments && <span className="xs muted" style={{ display: "block" }}>used by {p.n_experiments} experiment{p.n_experiments > 1 ? "s" : ""}</span>}</td>
                      <td className="mono">{p.ph_setpoint ?? "—"}</td><td className="mono">{p.temperature_setpoint ?? "—"}</td>
                      <td className="mono">{p.stirrer_setpoint ?? "—"}</td>
                      <td className="mono small" style={{ whiteSpace: "nowrap" }}>{showLimits(p)}</td>
                      <td><div className="stack" style={{ gap: 4 }}>
                        {!p.ph_control_mode && !p.temperature_control_mode && <span className="muted">—</span>}
                        {p.ph_control_mode && <span><span className="kicker">pH</span> {showParams(p.ph_control_mode, p.ph_pid, "pH")}
                          {p.loop_assignments?.ph && <span className="xs muted"><br />for {showAssignments(p.loop_assignments.ph)}</span>}</span>}
                        {p.temperature_control_mode && <span><span className="kicker">T</span> {showParams(p.temperature_control_mode, p.temperature_pid, "°C", T_MODE_PARAMS)}
                          {p.loop_assignments?.temperature && <span className="xs muted"><br />for {showAssignments(p.loop_assignments.temperature)}</span>}</span>}
                      </div></td>
                      <td style={{ whiteSpace: "nowrap", textAlign: "right", width: 1 }}>
                        {confirmId === p.id ? (
                          <>
                            <button className="btn text" onClick={() => setConfirmId(null)}>Cancel</button>
                            <button className="btn filled" onClick={() => remove(p)} aria-label={`Confirm deleting profile ${p.name}`}>Delete</button>
                          </>
                        ) : (
                          <>
                            <button className="btn text" onClick={() => startEdit(p)} aria-label={`Edit profile ${p.name}`}>Edit</button>
                            <button className="btn text" onClick={() => { setConfirmId(p.id); setMsg(null); }} aria-label={`Delete profile ${p.name}`}>Delete</button>
                          </>
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
          <section className="card" id="profile-form" style={{ scrollMarginTop: 16 }}>
            <h2 style={{ fontSize: 18 }}>{editing ? `Edit profile “${editing.name}”` : "New profile"}</h2>
            {editing && !!editing.n_experiments && (
              <div className="banner warn small" role="note">Used by {editing.n_experiments} experiment{editing.n_experiments > 1 ? "s" : ""}.
                They keep the values they were saved with; if you change any value they show that the profile changed after they were saved.
                Renaming only changes the name everywhere.</div>)}
            <label className="field">Name<input value={f.name} onChange={set("name")} placeholder="e.g. plantarum standard 1" /></label>
            <div className="row" style={{ flexWrap: "nowrap" }}>
              <label className="field">pH setpoint<input inputMode="decimal" value={f.ph} onChange={set("ph")} /></label>
              <label className="field">T (°C)<input inputMode="decimal" value={f.t} onChange={set("t")} /></label>
              <label className="field">Stirrer (rpm)<input inputMode="decimal" value={f.stir} onChange={set("stir")} /></label>
            </div>
            <span className="xs muted">Process limits (alarm only; empty = leave the controller's value)</span>
            <div className="row" style={{ flexWrap: "nowrap" }}>
              <label className="field">pH low<input inputMode="decimal" value={f.phLo} onChange={set("phLo")} /></label>
              <label className="field">pH high<input inputMode="decimal" value={f.phHi} onChange={set("phHi")} /></label>
              <label className="field">T low (°C)<input inputMode="decimal" value={f.tLo} onChange={set("tLo")} /></label>
              <label className="field">T high (°C)<input inputMode="decimal" value={f.tHi} onChange={set("tHi")} /></label>
            </div>
            {([["", "pH", f.mode, modeParams, "pH"], ["t", "Temperature", f.tmode, tModeParams, "°C"]] as const).map(([pre, label, mode, keys, dbUnit]) => (
              <div key={label} className="stack" style={{ gap: 8 }}>
                <label className="field">{label} control mode
                  <select value={mode} onChange={set(pre ? "tmode" : "mode")}><option value="pid">PID</option><option value="adaptive">Adaptive</option><option value="">Not set (leave as on the controller)</option></select>
                </label>
                {keys.length > 0 && (
                  <div className="row" style={{ flexWrap: "wrap" }}>
                    {keys.map((k) => (
                      <label key={k} className="field" style={{ flex: "1 1 30%", minWidth: 90 }}>
                        {PARAMS[k].label}{PARAMS[k].unit === "s" ? " (s)" : k === "deadband" ? ` (${dbUnit})` : ""}
                        <input inputMode="decimal" value={f[`${pre}${k}` as keyof Form]} onChange={set(`${pre}${k}` as keyof Form)} />
                      </label>
                    ))}
                  </div>
                )}
              </div>
            ))}
            <span className="xs muted">
              {f.mode === "adaptive" || f.tmode === "adaptive"
                ? "In Adaptive, P, I and D are starting values: the controller adapts them after Start. "
                : ""}{f.mode || f.tmode ? "Empty fields are left as they are on the controller. Values are checked against the controller's own limits when loaded." : ""}
            </span>
            <div className="stack" style={{ gap: 6 }}>
              <span className="small" style={{ fontWeight: 500 }}>Actuators the loops are tuned for</span>
              {assign ? (
                <span className="small">From {assign.from}: <b>pH</b> {showAssignments(assign.a.ph)}; <b>T</b> {showAssignments(assign.a.temperature)}
                  <button className="btn text" style={{ height: 28 }} onClick={() => setAssign(null)}>Clear</button></span>
              ) : <span className="xs muted">Not recorded: the control settings are loaded whatever the controller's actuators.</span>}
              <div className="row" style={{ gap: 8 }}>
                {["R1", "R2"].map((r) => <button key={r} className="btn outlined" style={{ height: 34 }} onClick={() => record(r)}>Record from {r}</button>)}
              </div>
              <span className="xs muted">Reads which actuators act on the pH and temperature loops (+ below setpoint, − above). Nothing is written.</span>
            </div>
            <label className="field">Notes<textarea rows={3} value={f.notes} onChange={set("notes")} /></label>
            <div className="row" style={{ gap: 8 }}>
              <button className="btn filled" disabled={!f.name.trim()} onClick={create}>{editing ? "Save changes" : "Save profile"}</button>
              {editing && <button className="btn text" onClick={cancelEdit}>Cancel</button>}
            </div>
          </section>
        </aside>
      </div>
    </>
  );
}
