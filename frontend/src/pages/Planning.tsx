import { Fragment, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Experiment, type ExperimentDraft, type Named, type PlanCheckRow } from "../api";
import { usePolling } from "../hooks";
import { Icon, StatusChip } from "../components/ui";

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
const show = (v: unknown) => v === null || v === undefined ? "—" : Array.isArray(v) ? v.join("–")
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
                    <span className={`status ${r.read_only ? "planned" : r.match ? "completed" : "aborted"}`} style={r.match && !r.read_only ? { background: "#d3efe7", color: "#22294d" } : undefined}>
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

export function Planning({ existing, onChange }: { existing?: Experiment; onChange?: () => void }) {
  const nav = useNavigate();
  const [d, setD] = useState<ExperimentDraft>(() => existing ? {
    name: existing.name, reactor_id: existing.reactor_id, planned_date: existing.planned_date, strain: existing.strain,
    medium_id: existing.medium_id, buffer_id: existing.buffer_id, working_volume_ml: existing.working_volume_ml,
    profile_id: existing.profile_id, notes: existing.notes,
  } : { name: "", reactor_id: "R1", planned_date: today(), strain: "", notes: "" });
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
      { ok: !!c?.reachable, text: c?.reachable ? `Controller ${d.reactor_id} reachable` : `Controller ${d.reactor_id} not reachable` },
      { ok: c?.reactor_free !== false, text: c?.reactor_free === false ? `${d.reactor_id} is recording another experiment` : `${d.reactor_id} free` },
      { ok: mismatches.length === 0, warn: true, text: mismatches.length ? `${mismatches.length} setting(s) differ from the controller` : "Controller matches the plan" },
    ];
  }, [check.data, existing, dirty, d.reactor_id, mismatches.length]);
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
        <button className="btn filled" onClick={save} disabled={!!existing && !dirty}>{existing ? "Save plan" : "Create"}</button>
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
            <div className="row between">
              <div className="stack" style={{ gap: 2 }}>
                <h2 id="g3" style={{ fontSize: 18 }}>Controller profile</h2>
                <span className="small muted">Plan compared with what controller {d.reactor_id} reports now</span>
              </div>
              <label className="field" style={{ minWidth: 260 }}>Profile
                <select value={d.profile_id ?? ""} onChange={(e) => set({ profile_id: e.target.value === "" ? null : Number(e.target.value) })}>
                  <option value="">Custom (no profile)</option>
                  {(profiles.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </label>
            </div>
            {profile && (
              <span className="small muted">Profile: pH {profile.ph_setpoint ?? "—"} · T {profile.temperature_setpoint ?? "—"} °C · stirrer {profile.stirrer_setpoint ?? "—"} rpm
                · pH control {profile.ph_control_mode ?? "—"}{profile.ph_pid && ` (${show(profile.ph_pid)})`}</span>
            )}
            {!existing && <span className="small muted">Save the plan to compare it with the controller.</span>}
            {existing && dirty && <span className="small muted">Save the plan to refresh the comparison.</span>}
            {check.data && !check.data.reachable && <div className="banner alert">Controller not reachable: {check.data.message}</div>}
            {check.data?.reachable && <PlanCheckTable rows={check.data.rows} />}
            {existing && profile && mismatches.length > 0 && (
              <div className="banner warn"><Icon name="warn" color="#8a5a00" size={20} />
                <span><b>{pidDiffers ? "pH control differs." : "Setpoints differ."}</b> {pidDiffers && "The controller may have been left in another mode by a previous user. "}
                  Loading the profile writes setpoints, pH control mode and PID to the controller, then reads them back.</span>
              </div>
            )}
            <div className="row">
              <button className="btn filled" disabled={!existing || dirty || !profile} onClick={async () => {
                if (!existing) return;
                try {
                  const r = await api.loadProfile(existing.id);
                  const bad = r.results.filter((x) => x.status !== "confirmed");
                  setMsg(bad.length ? { kind: "alert", text: `Not confirmed: ${bad.map((b) => `${b.parameter} (${b.message ?? b.status})`).join(", ")}` }
                    : { kind: "good", text: "Profile loaded and confirmed by the controller." });
                  check.refresh();
                } catch (x) { setMsg({ kind: "alert", text: (x as Error).message }); }
              }}>Load profile to controller</button>
              <span className="xs muted">Process limits are read only; actuator settings stay on the controller. PID can be loaded only here, before Start.</span>
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
              <span className="pill" style={{ alignSelf: "flex-start", background: "#e4e5e8", border: 0 }}>
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
            <h2 id="rd" style={{ fontSize: 18 }}>Ready to start?</h2>
            {ready.map((r) => (
              <span key={r.text} className="row small" style={{ gap: 10, flexWrap: "nowrap" }}>
                <Icon name={r.ok ? "check" : "warn"} size={18} color={r.ok ? "#1f6e5a" : r.warn ? "#8a5a00" : "#b30020"} width={2.2} />{r.text}
              </span>
            ))}
            <button className="btn filled" style={{ height: 48, justifyContent: "center" }} disabled={!existing || blocking}
              onClick={async () => {
                if (!existing) return;
                if (mismatches.length && !window.confirm("Some settings differ from the controller. Start anyway?")) return;
                try { await api.start(existing.id); onChange?.(); } catch (x) { setMsg({ kind: "alert", text: (x as Error).message }); }
              }}><Icon name="play" size={18} />Start recording</button>
            <span className="xs muted">t = 0 is the moment you press Start.</span>
          </section>
        </aside>
      </div>
    </>
  );
}
