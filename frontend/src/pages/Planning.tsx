import { Fragment, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api, type Experiment, type ExperimentDraft, type Named, type PlanCheckRow, type PlanParams, type Profile, DEFAULT_CHANNELS, type Channel } from "../api";
import { usePolling } from "../hooks";
import { Icon, StatusChip } from "../components/ui";
import { DeleteExperiment } from "../components/DeleteExperiment";

const today = () => new Date().toISOString().slice(0, 10);

function NamedSelect({ kind, label, value, onChange }: { kind: "media" | "buffers"; label: string; value: number | null; onChange: (id: number | null) => void }) {
  const list = usePolling<Named[]>(() => (kind === "media" ? api.media() : api.buffers()), 600000);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="field">
      <label className="field">{label}
        <select value={adding ? "__new" : value ?? ""} onChange={(e) => {
          if (e.target.value === "__new") { setAdding(true); return; }
          setAdding(false); onChange(e.target.value === "" ? null : Number(e.target.value));
        }}>
          <option value="">— not set —</option>
          {(list.data ?? []).map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          <option value="__new">+ New {kind === "media" ? "medium" : "buffer"}…</option>
        </select>
      </label>
      {adding && (
        <div className="row" style={{ gap: 8, marginTop: 6 }}>
          <input autoFocus placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} style={{ flex: 1 }} />
          <button className="btn tonal" disabled={!name.trim()} onClick={async () => {
            try { const n = await api.createNamed(kind, name.trim()); await list.refresh(); onChange(n.id); setAdding(false); setName(""); setErr(null); }
            catch (x) { setErr((x as Error).message); }
          }}>Add</button>
        </div>
      )}
      {err && <span style={{ color: "#b30020" }}>{err}</span>}
      <span className="xs">Used before, most recent first. Used items can be archived, never deleted.</span>
    </div>
  );
}

const LABELS: Record<string, string> = {
  ph_setpoint: "pH setpoint", temperature_setpoint: "Temperature setpoint (°C)", stirrer_setpoint: "Stirrer setpoint (rpm)",
  ph_control_mode: "pH control mode", ph_pid: "pH PID parameters", ph_limits: "pH process limits", temperature_limits: "T process limits (°C)",
};
const GROUP: Record<string, string> = {
  ph_setpoint: "pH controller", ph_control_mode: "pH controller", ph_pid: "pH controller", ph_limits: "pH controller",
  temperature_setpoint: "Temperature controller", temperature_limits: "Temperature controller", stirrer_setpoint: "Stirrer",
};
const show = (v: unknown) => v === null || v === undefined ? "—" : Array.isArray(v) ? v.map((x) => x ?? "·").join("–")
  : typeof v === "object" ? Object.entries(v as Record<string, number>).map(([k, x]) => `${k} ${x}`).join(" / ") : String(v);

function PlanCheckTable({ rows }: { rows: PlanCheckRow[] }) {
  const groups = ["pH controller", "Temperature controller", "Stirrer"];
  return (
    <div className="table-wrap">
      <table className="data" style={{ minWidth: 560 }}>
        <thead><tr><th>Parameter</th><th style={{ textAlign: "right" }}>Plan</th><th style={{ textAlign: "right" }}>Controller</th><th>Check</th></tr></thead>
        <tbody>
          {groups.map((g) => (
            <Fragment key={g}>
              <tr><td colSpan={4} className="kicker" style={{ paddingTop: 16 }}>{g}</td></tr>
              {rows.filter((r) => GROUP[r.parameter] === g).map((r) => (
                <tr key={r.parameter}>
                  <td style={{ paddingLeft: 24 }}>{LABELS[r.parameter] ?? r.parameter}</td>
                  <td className="mono" style={{ textAlign: "right" }}>{r.read_only ? "—" : show(r.plan)}</td>
                  <td className="mono" style={{ textAlign: "right" }}>{show(r.controller)}</td>
                  <td>
                    <span className={`status ${r.read_only ? "planned" : r.match ? "completed" : "aborted"}`} style={r.match && !r.read_only ? { background: "#d3efe7", color: "var(--ink)" } : undefined}>
                      {r.read_only ? "From controller" : r.match ? "Match" : "Differs"}
                    </span>
                  </td>
                </tr>
              ))}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const CHANNEL_INFO: { key: Channel; label: string; what: string }[] = [
  { key: "temperature", label: "Temperature", what: "T sensor · heating / cooling" },
  { key: "ph", label: "pH", what: "pH sensor · base pump · cumulative and mL/min" },
  { key: "stirrer", label: "Stirrer", what: "speed sensor · motor" },
  { key: "do2", label: "dO2", what: "dO2 probe · N2 and air valves" },
];

export function Planning({ existing, onChange }: { existing?: Experiment; onChange?: () => void }) {
  const nav = useNavigate();
  const preselected = new URLSearchParams(useLocation().search).get("reactor");
  const [d, setD] = useState<ExperimentDraft>(() => existing ? {
    name: existing.name, reactor_id: existing.reactor_id, planned_date: existing.planned_date, strain: existing.strain,
    medium_id: existing.medium_id, buffer_id: existing.buffer_id, working_volume_ml: existing.working_volume_ml,
    profile_id: existing.profile_id, notes: existing.notes, channels: existing.channels, plan_params: existing.plan_params,
  } : { name: "", reactor_id: preselected === "R2" ? "R2" : "R1", planned_date: today(), strain: "", notes: "", channels: [...DEFAULT_CHANNELS] });
  const [do2Notice, setDo2Notice] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [msg, setMsg] = useState<{ kind: "good" | "alert" | "warn"; text: string } | null>(null);
  const set = (patch: Partial<ExperimentDraft>) => { setD({ ...d, ...patch }); setDirty(true); };

  const profiles = usePolling(api.profiles, 600000);
  const all = usePolling(() => api.experiments(), 60000);
  const check = usePolling(() => (existing ? api.planCheck(existing.id) : Promise.resolve(undefined)), 15000, [existing?.id]);

  const [parallel, setParallel] = useState(!!existing?.parallel_run_code);
  const [partnerId, setPartnerId] = useState<number | "">("");
  const otherReactor = d.reactor_id === "R1" ? "R2" : "R1";
  const candidates = (all.data ?? []).filter((x) => x.status === "planned" && x.reactor_id === otherReactor && x.id !== existing?.id && !x.parallel_run_code);
  useEffect(() => { if (partnerId === "" && candidates[0]) setPartnerId(candidates[0].id); }, [candidates, partnerId]);

  const profile = profiles.data?.find((p) => p.id === d.profile_id);
  const modified = !!profile && !!d.plan_params && !sameParams(d.plan_params, fromProfile(profile));
  const mismatches = (check.data?.rows ?? []).filter((r) => !r.match && !r.read_only);
  const pidDiffers = mismatches.some((r) => r.parameter === "ph_control_mode" || r.parameter === "ph_pid");

  async function save(): Promise<Experiment | null> {
    setMsg(null);
    if (!d.name.trim()) { setMsg({ kind: "alert", text: "Experiment name is required." }); return null; }
    try {
      const e = existing ? await api.patchExperiment(existing.id, d) : await api.createExperiment(d);
      setDirty(false);
      if (!existing) nav(`/experiments/${e.id}`, { replace: true });
      onChange?.();
      setMsg({ kind: "good", text: "Plan saved." });
      return e;
    } catch (x) { setMsg({ kind: "alert", text: (x as Error).message }); return null; }
  }

  async function linkPartner(createNew: boolean) {
    const e = existing ?? (await save());
    if (!e) return;
    try {
      let pid = partnerId as number;
      if (createNew) {
        const p = await api.createExperiment({ ...d, name: `${d.name} — partner`, reactor_id: otherReactor, strain: "" });
        pid = p.id;
      }
      await api.linkParallel(e.id, pid);
      onChange?.(); all.refresh();
      setMsg({ kind: "good", text: createNew ? "Partner experiment created on " + otherReactor + " and linked: open it to complete its plan." : "Parallel run linked." });
    } catch (x) { setMsg({ kind: "alert", text: (x as Error).message }); }
  }

  const ready = useMemo(() => {
    const c = check.data;
    return [
      { ok: !!existing && !dirty, text: existing ? (dirty ? "Unsaved changes" : "Plan saved") : "Plan not saved yet" },
      !existing
        ? { ok: false, warn: true, text: `Controller ${d.reactor_id}: checked after the plan is saved` }
        : !c
          ? { ok: false, warn: true, text: check.error ? `Controller ${d.reactor_id}: check failed (${check.error})` : `Checking controller ${d.reactor_id}…` }
          : { ok: !!c.reachable, warn: true, text: c.reachable ? `Controller ${d.reactor_id} reachable` : `Controller ${d.reactor_id} off or not reachable: you can still go to fermentation` },
      { ok: c?.reactor_free !== false, text: c?.reactor_free === false ? `${d.reactor_id} is in use by another experiment` : `${d.reactor_id} free` },
      c?.reachable
        ? { ok: mismatches.length === 0, warn: true, text: mismatches.length ? `${mismatches.length} setting(s) differ from the controller` : "Controller matches the plan" }
        : { ok: false, warn: true, text: hasValues(d.plan_params) ? "Profile chosen: load it when the controller is on" : "Plan vs controller: compared when the controller is on" },
    ];
  }, [check.data, check.error, existing, dirty, d.reactor_id, mismatches.length]);
  const blocking = ready.some((r) => !r.ok && !r.warn);

  return (
    <>
      <header className="row between">
        <div className="row" style={{ gap: 12 }}>
          <div className="stack" style={{ gap: 2 }}>
            <h1 style={{ fontSize: 22 }}>{existing ? <><span className="mono">{existing.code}</span> · {existing.name}</> : "New experiment"}</h1>
            <span className="small muted">{existing ? "Planning · not started" : "Fill in the plan, then save"}</span>
          </div>
          <StatusChip status="planned" />
        </div>
        <div className="row" style={{ gap: 8 }}>
          {existing && <DeleteExperiment e={existing} />}
          <button className="btn filled" onClick={save} disabled={!!existing && !dirty}>{existing ? "Save plan" : "Create"}</button>
        </div>
      </header>
      {msg && <div className={`banner ${msg.kind}`} role="status">{msg.text}</div>}

      <div className="split">
        <div className="wide">
          <section className="card" aria-labelledby="g1">
            <h2 id="g1" style={{ fontSize: 18 }}>General</h2>
            <label className="field">Experiment name<input value={d.name} onChange={(e) => set({ name: e.target.value })} /></label>
            <div className="row" style={{ alignItems: "flex-start" }}>
              <label className="field" style={{ flex: "1 1 180px" }}>Planned date
                <input type="date" value={d.planned_date ?? ""} onChange={(e) => set({ planned_date: e.target.value })} />
                <span className="xs">Filled automatically, editable</span>
              </label>
              <label className="field" style={{ flex: "1 1 180px" }}>Strain
                <input value={d.strain ?? ""} onChange={(e) => set({ strain: e.target.value })} style={{ fontFamily: "var(--font-mono)" }} list="strains" />
                <datalist id="strains">{[...new Set((all.data ?? []).map((x) => x.strain).filter(Boolean))].map((s) => <option key={s} value={s} />)}</datalist>
              </label>
              <div className="field" style={{ flex: "1 1 180px" }}>Bioreactor
                <div className="segmented" style={{ height: 48, borderRadius: 24 }} role="radiogroup" aria-label="Bioreactor">
                  {["R1", "R2"].map((r) => (
                    <button key={r} role="radio" aria-checked={d.reactor_id === r} className={d.reactor_id === r ? "on" : ""}
                      style={{ flex: 1, fontFamily: "var(--font-mono)", fontSize: 15 }} onClick={() => set({ reactor_id: r })}>{r}</button>
                  ))}
                </div>
              </div>
            </div>
          </section>

          <section className="card" aria-labelledby="gch">
            <div className="stack" style={{ gap: 2 }}>
              <h2 id="gch" style={{ fontSize: 18 }}>Channels</h2>
              <span className="small muted">What this experiment uses. Channels switched off are hidden and raise no alerts; the profile does not write them.
                Everything is still recorded, so a channel can be switched on later.</span>
            </div>
            <div className="ch-grid" role="group" aria-label="Channels in use">
              {CHANNEL_INFO.map((c) => {
                const on = (d.channels ?? DEFAULT_CHANNELS).includes(c.key);
                return (
                  <label key={c.key} className={`ch ${on ? "on" : ""}`}>
                    <input type="checkbox" checked={on} onChange={() => {
                      const cur = d.channels ?? DEFAULT_CHANNELS;
                      const next = on ? cur.filter((x) => x !== c.key) : [...cur, c.key];
                      set({ channels: next });
                      if (!on && c.key === "do2") setDo2Notice(true);
                    }} />
                    <span className="stack" style={{ gap: 2 }}>
                      <span style={{ fontWeight: 600 }}>{c.label}</span>
                      <span className="xs muted">{c.what}</span>
                    </span>
                  </label>
                );
              })}
            </div>
            {(d.channels ?? DEFAULT_CHANNELS).length === 0 && <div className="banner warn">No channel selected: only manual measurements will be shown.</div>}
          </section>
          {do2Notice && (
            <div className="scrim" onMouseDown={(x) => { if (x.target === x.currentTarget) setDo2Notice(false); }}>
              <div className="dialog" role="alertdialog" aria-modal="true" aria-labelledby="do2-t" aria-describedby="do2-d">
                <h2 id="do2-t" style={{ fontSize: 22, fontWeight: 500, margin: 0 }}>dO2: air is not configured yet</h2>
                <div className="banner warn" id="do2-d"><Icon name="warn" color="#8a5a00" size={20} />
                  <span>The dO2 probe and the N2 / air valves are recorded and shown, but the <b>air supply is not configured</b> on the
                    controllers yet: the dO2 loop cannot aerate until it is set up on the controller.</span>
                </div>
                <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn filled" autoFocus onClick={() => setDo2Notice(false)}>OK</button></div>
              </div>
            </div>
          )}
          <section className="card" aria-labelledby="g2">
            <h2 id="g2" style={{ fontSize: 18 }}>Medium and buffer</h2>
            <div className="row" style={{ alignItems: "flex-start" }}>
              <div style={{ flex: "1 1 220px" }}><NamedSelect kind="media" label="Medium" value={d.medium_id ?? null} onChange={(id) => set({ medium_id: id })} /></div>
              <div style={{ flex: "1 1 220px" }}><NamedSelect kind="buffers" label="Buffer / base" value={d.buffer_id ?? null} onChange={(id) => set({ buffer_id: id })} /></div>
              <label className="field" style={{ flex: "0 1 160px" }}>Working volume (mL)
                <input inputMode="decimal" value={d.working_volume_ml ?? ""} onChange={(e) => set({ working_volume_ml: e.target.value === "" ? null : Number(e.target.value) })} style={{ fontFamily: "var(--font-mono)" }} />
              </label>
            </div>
          </section>

          <section className="card" aria-labelledby="g3">
            <div className="stack" style={{ gap: 2 }}>
              <h2 id="g3" style={{ fontSize: 18 }}>Controller profile</h2>
              <span className="small muted">Part of the plan: choose it now, even with the bioreactor off, and adjust any value for this experiment. It is written to the controller later, before Start.</span>
            </div>
            <div className="row" style={{ gap: 12, alignItems: "flex-end" }}>
              <label className="field" style={{ flex: "1 1 260px", maxWidth: 420 }}>Profile
                <select value={d.profile_id ?? ""} onChange={(e) => {
                  const id = e.target.value === "" ? null : Number(e.target.value);
                  const p = profiles.data?.find((x) => x.id === id);
                  set({ profile_id: id, plan_params: p ? fromProfile(p) : d.plan_params ?? null });
                }}>
                  <option value="">Custom (no profile)</option>
                  {(profiles.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </label>
              <PlanSource d={d} profile={profile} />
            </div>
            <PlanParamsTable value={d.plan_params ?? null} channels={d.channels ?? DEFAULT_CHANNELS} onChange={(v) => set({ plan_params: v })} />
            <div className="row" style={{ gap: 8 }}>
              {(modified || (!profile && hasValues(d.plan_params))) && (
                <button className="btn tonal" onClick={async () => {
                  const name = window.prompt("Name of the new profile", profile ? `${profile.name} (modified)` : "")?.trim();
                  if (!name || !d.plan_params) return;
                  try {
                    const np = await api.createProfile({ name, notes: "", ...d.plan_params } as never);
                    profiles.refresh(); set({ profile_id: np.id });
                    setMsg({ kind: "good", text: `Profile “${name}” saved in the Library.` });
                  } catch (x) { setMsg({ kind: "alert", text: (x as Error).message }); }
                }}>Save as new profile</button>
              )}
              {modified && profile && <button className="btn text" onClick={() => set({ plan_params: fromProfile(profile) })}>Reset to “{profile.name}”</button>}
            </div>
            {(profiles.data ?? []).length === 0 && <span className="xs muted">No profiles yet: fill in the values here and save them as a profile, or create one in Library › Controller profiles.</span>}
          </section>

          <section className="card" aria-labelledby="g3b">
            <div className="stack" style={{ gap: 2 }}>
              <h2 id="g3b" style={{ fontSize: 18 }}>Controller {d.reactor_id}: check and load</h2>
              <span className="small muted">When the bioreactor is on: the plan is compared with what the controller reports now, and the profile can be loaded.</span>
            </div>
            {!existing && <span className="small muted">Create the experiment first; the check starts as soon as the plan is saved and the controller answers.</span>}
            {existing && dirty && <span className="small muted">Save the plan to refresh the comparison.</span>}
            {existing && check.data && !check.data.reachable && (
              <div className="banner warn small"><Icon name="warn" color="#8a5a00" size={18} />
                <span>Controller {d.reactor_id} is off or not reachable ({check.data.message}). That is fine while planning: switch it on before Start to compare and load the profile.</span></div>
            )}
            {check.data?.reachable && <PlanCheckTable rows={check.data.rows} />}
            {existing && hasValues(d.plan_params) && mismatches.length > 0 && (
              <div className="banner warn"><Icon name="warn" color="#8a5a00" size={20} />
                <span><b>{pidDiffers ? "pH control differs." : "Setpoints differ."}</b> {pidDiffers && "The controller may have been left in another mode by a previous user. "}
                  Loading the profile writes setpoints, process limits, pH control mode and loop parameters to the controller, then reads them back.</span>
              </div>
            )}
            <div className="row">
              <button className="btn filled" disabled={!existing || dirty || !hasValues(d.plan_params) || !check.data?.reachable} onClick={async () => {
                if (!existing) return;
                try {
                  const r = await api.loadProfile(existing.id);
                  const bad = r.results.filter((x) => x.status !== "confirmed");
                  setMsg(bad.length ? { kind: "alert", text: `Not confirmed: ${bad.map((b) => `${b.parameter} (${b.message ?? b.status})`).join(", ")}` }
                    : { kind: "good", text: "Plan loaded and confirmed by the controller." });
                  check.refresh();
                } catch (x) { setMsg({ kind: "alert", text: (x as Error).message }); }
              }}>Load to controller</button>
              <span className="xs muted">Limits left empty in the profile stay as they are on the controller; actuators, interlocks and calibration stay on the controller. pH control mode and loop parameters can be loaded only here, before Start.</span>
            </div>
          </section>

          <section className="card" aria-labelledby="g4">
            <h2 id="g4" style={{ fontSize: 18 }}>Notes</h2>
            <label className="field"><span className="sr-only">Notes</span>
              <textarea rows={4} placeholder="Pre-culture, inoculum, anything worth remembering…" value={d.notes ?? ""} onChange={(e) => set({ notes: e.target.value })} />
            </label>
          </section>
        </div>

        <aside className="side">
          <section className="card" aria-labelledby="pr">
            <div className="row between">
              <h2 id="pr" style={{ fontSize: 18 }}>Parallel run</h2>
              <button role="switch" aria-checked={parallel} aria-label="Run in parallel" onClick={() => setParallel(!parallel)} disabled={!!existing?.parallel_run_code}
                style={{ width: 52, height: 32, borderRadius: 16, border: 0, padding: "0 4px", display: "flex", alignItems: "center",
                  justifyContent: parallel ? "flex-end" : "flex-start", background: parallel ? "#003366" : "#c4c7cc", cursor: "pointer" }}>
                <span style={{ width: 24, height: 24, borderRadius: 12, background: "#fff" }} />
              </button>
            </div>
            <span className="small muted">Logistics only: live monitoring side by side and R1 | R2 switching. It does not define conditions or replicates for analysis.</span>
            {existing?.parallel_run_code ? (
              <span className="pill" style={{ alignSelf: "flex-start", background: "var(--neutral-chip)", border: 0 }}>
                <b className="mono">{existing.parallel_run_code}</b>&nbsp;with {existing.parallel_partner_codes.join(", ")}
              </span>
            ) : parallel && (
              <div className="stack" style={{ gap: 10 }}>
                <label className="field">Planned experiment on {otherReactor}
                  <select value={partnerId} onChange={(e) => setPartnerId(Number(e.target.value))} disabled={!candidates.length}>
                    {candidates.length ? candidates.map((c) => <option key={c.id} value={c.id}>{c.code} · {c.strain || c.name} · {c.planned_date}</option>)
                      : <option>No planned experiment on {otherReactor}</option>}
                  </select>
                </label>
                <div className="row" style={{ gap: 8 }}>
                  <button className="btn tonal" disabled={!candidates.length} onClick={() => linkPartner(false)}>Link</button>
                  <button className="btn outlined" onClick={() => linkPartner(true)}>Create partner on {otherReactor}</button>
                </div>
              </div>
            )}
          </section>

          <section className="card" aria-labelledby="rd">
            <h2 id="rd" style={{ fontSize: 18 }}>Ready for fermentation?</h2>
            {ready.map((r) => (
              <span key={r.text} className="row small" style={{ gap: 10, flexWrap: "nowrap" }}>
                <Icon name={r.ok ? "check" : "warn"} size={18} color={r.ok ? "#1f6e5a" : r.warn ? "#8a5a00" : "#b30020"} width={2.2} />{r.text}
              </span>
            ))}
            <button className="btn filled" style={{ height: 48, justifyContent: "center" }} disabled={!existing || blocking}
              onClick={async () => {
                if (!existing) return;
                if (dirty && !(await save())) return;
                try { await api.prepare(existing.id); onChange?.(); } catch (x) { setMsg({ kind: "alert", text: (x as Error).message }); }
              }}><Icon name="arrow" size={18} />Go to fermentation</button>
            <span className="xs muted">Opens the live page: monitoring and alerts start, the run is not recorded yet. There you load the profile when the bioreactor is ready and press <b>Start experiment</b> (t = 0) after inoculation.</span>
          </section>
        </aside>
      </div>
    </>
  );
}


// --- plan parameters (profile values, editable per experiment) --------------------------------

const PID_PARAMS: { k: string; label: string; unit?: string }[] = [
  { k: "P", label: "P" }, { k: "I", label: "I" }, { k: "D", label: "D" },
  { k: "deadband", label: "Dead band", unit: "pH" }, { k: "cycle_time", label: "Cycle time", unit: "s" },
  { k: "settling_time", label: "Settling time", unit: "s" },
];
const MODE_KEYS: Record<string, string[]> = {
  pid: ["P", "I", "D", "deadband", "cycle_time"], adaptive: ["P", "I", "D", "deadband", "cycle_time", "settling_time"],
};

export function fromProfile(p: Profile): PlanParams {
  return {
    ph_setpoint: p.ph_setpoint, temperature_setpoint: p.temperature_setpoint, stirrer_setpoint: p.stirrer_setpoint,
    ph_control_mode: p.ph_control_mode, ph_pid: p.ph_pid ? { ...p.ph_pid } : null,
    ph_low_limit: p.ph_low_limit ?? null, ph_high_limit: p.ph_high_limit ?? null,
    temperature_low_limit: p.temperature_low_limit ?? null, temperature_high_limit: p.temperature_high_limit ?? null,
  };
}

function eq(a: unknown, b: unknown): boolean {
  if ((a === null || a === undefined || a === "") && (b === null || b === undefined || b === "")) return true;
  if (typeof a === "object" || typeof b === "object") {
    const x = (a ?? {}) as Record<string, unknown>, y = (b ?? {}) as Record<string, unknown>;
    return [...new Set([...Object.keys(x), ...Object.keys(y)])].every((k) => eq(x[k], y[k]));
  }
  return typeof a === "number" && typeof b === "number" ? Math.abs(a - b) < 1e-9 : a === b;
}
export const sameParams = (a: PlanParams, b: PlanParams) => eq(a, b);
export const hasValues = (p: PlanParams | null | undefined) =>
  !!p && Object.entries(p).some(([k, v]) => k === "ph_pid" ? Object.values(v ?? {}).some((x) => x != null) : v != null && v !== "");

function PlanSource({ d, profile }: { d: ExperimentDraft; profile?: Profile }) {
  if (!profile) return hasValues(d.plan_params)
    ? <span className="tag archived" style={{ height: 28 }}>Custom · not saved as a profile</span> : null;
  const modified = !!d.plan_params && !sameParams(d.plan_params, fromProfile(profile));
  return modified
    ? <span className="tag" style={{ height: 28, background: "var(--warn-container)" }}>Custom · based on “{profile.name}”, not saved</span>
    : <span className="tag analysed" style={{ height: 28 }}>As in profile “{profile.name}”</span>;
}

const EMPTY_PLAN: PlanParams = { ph_setpoint: null, temperature_setpoint: null, stirrer_setpoint: null, ph_control_mode: null, ph_pid: null,
  ph_low_limit: null, ph_high_limit: null, temperature_low_limit: null, temperature_high_limit: null };

/** Schematic, editable table of the plan's controller values (no controller column: that is the check step). */
function PlanParamsTable({ value, channels, onChange }: { value: PlanParams | null; channels: Channel[]; onChange: (v: PlanParams) => void }) {
  const v = value ?? EMPTY_PLAN;
  const n = (s: string) => (s.trim() === "" ? null : Number(s.replace(",", ".")));
  const cell = (key: keyof PlanParams, label: string) => (
    <input className="cell-input mono" inputMode="decimal" aria-label={label} style={{ width: 90, textAlign: "right" }}
      value={(v[key] as number | null) ?? ""} onChange={(e) => onChange({ ...v, [key]: n(e.target.value) })} />
  );
  const pidCell = (k: string, label: string) => (
    <input className="cell-input mono" inputMode="decimal" aria-label={label} style={{ width: 90, textAlign: "right" }}
      value={v.ph_pid?.[k] ?? ""} onChange={(e) => onChange({ ...v, ph_pid: { ...(v.ph_pid ?? {}), [k]: n(e.target.value) } })} />
  );
  const keys = MODE_KEYS[v.ph_control_mode ?? ""] ?? [];
  return (
    <div className="table-wrap">
      <table className="data plan-table">
        <thead><tr><th>Parameter</th><th className="num">Setpoint</th><th className="num">Low limit</th><th className="num">High limit</th></tr></thead>
        <tbody>
          {channels.includes("ph") && <tr><td>pH</td><td className="num">{cell("ph_setpoint", "pH setpoint")}</td>
            <td className="num">{cell("ph_low_limit", "pH low limit")}</td><td className="num">{cell("ph_high_limit", "pH high limit")}</td></tr>}
          {channels.includes("temperature") && <tr><td>Temperature <span className="muted">(°C)</span></td><td className="num">{cell("temperature_setpoint", "Temperature setpoint")}</td>
            <td className="num">{cell("temperature_low_limit", "Temperature low limit")}</td><td className="num">{cell("temperature_high_limit", "Temperature high limit")}</td></tr>}
          {channels.includes("stirrer") && <tr><td>Stirrer <span className="muted">(rpm)</span></td><td className="num">{cell("stirrer_setpoint", "Stirrer setpoint")}</td>
            <td className="num muted">—</td><td className="num muted">—</td></tr>}
          {channels.includes("ph") && (
            <>
              <tr><td colSpan={4} className="kicker" style={{ paddingTop: 14 }}>pH control</td></tr>
              <tr><td>Mode</td><td colSpan={3}>
                <select value={v.ph_control_mode ?? ""} onChange={(e) => onChange({ ...v, ph_control_mode: e.target.value || null })} style={{ height: 36 }} aria-label="pH control mode">
                  <option value="">Leave as on the controller</option><option value="pid">PID</option><option value="adaptive">Adaptive</option>
                </select></td></tr>
              {keys.length > 0 && (
                <tr><td colSpan={4}>
                  <div className="row" style={{ gap: 10 }}>
                    {PID_PARAMS.filter((p) => keys.includes(p.k)).map((p) => (
                      <label key={p.k} className="field" style={{ flex: "0 0 auto" }}>{p.label}{p.unit ? ` (${p.unit})` : ""}{pidCell(p.k, p.label)}</label>
                    ))}
                  </div>
                </td></tr>
              )}
            </>
          )}
        </tbody>
      </table>
      <span className="xs muted">Empty = leave the controller's value. Limits are alarm limits only. {v.ph_control_mode === "adaptive" && "In Adaptive, P, I and D are starting values."}</span>
    </div>
  );
}

/** Live page, preparing phase: plan vs controller, and loading the plan once the bioreactor is ready. */
export function ControllerCheckCard({ e }: { e: Experiment }) {
  const check = usePolling(() => api.planCheck(e.id), 15000, [e.id]);
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const mism = (check.data?.rows ?? []).filter((r) => !r.match && !r.read_only).length;
  return (
    <section className="card" aria-labelledby="cc">
      <div className="row between">
        <div className="stack" style={{ gap: 2 }}>
          <h2 id="cc" style={{ fontSize: 18 }}>Plan vs controller {e.reactor_id}</h2>
          <span className="small muted">Load the plan when the bioreactor is ready; values are read back after writing.</span>
        </div>
        <button className="btn filled" disabled={busy || !check.data?.reachable || !hasValues(e.plan_params)} onClick={async () => {
          setBusy(true); setMsg(null);
          try {
            const r = await api.loadProfile(e.id);
            const bad = r.results.filter((x) => x.status !== "confirmed");
            setMsg(bad.length ? { kind: "alert", text: `Not confirmed: ${bad.map((b) => `${b.parameter} (${b.message ?? b.status})`).join(", ")}` }
              : { kind: "good", text: "Plan loaded and confirmed by the controller." });
            check.refresh();
          } catch (x) { setMsg({ kind: "alert", text: (x as Error).message }); }
          finally { setBusy(false); }
        }}>{busy ? "Loading…" : "Load to controller"}</button>
      </div>
      {check.data && !check.data.reachable && <div className="banner warn small"><Icon name="warn" color="#8a5a00" size={18} />
        <span>Controller {e.reactor_id} is off or not reachable ({check.data.message}).</span></div>}
      {check.data?.reachable && <PlanCheckTable rows={check.data.rows} />}
      {check.data?.reachable && mism > 0 && <span className="small" style={{ color: "var(--data-orange-text)" }}>{mism} setting(s) differ from the plan.</span>}
      {!hasValues(e.plan_params) && <span className="small muted">No profile or controller values in the plan: setpoints stay as set on the controller.</span>}
      {msg && <div className={`banner ${msg.kind} small`} role="status">{msg.text}</div>}
    </section>
  );
}
