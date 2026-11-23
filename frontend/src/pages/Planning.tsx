import { sidesText } from "../loopText";
import { Fragment, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api, type Experiment, type ExperimentDraft, type LiveReactor, type LoopConfigView, type Named, type PlanCheckRow, type PlanParams, type Profile, type Template, DEFAULT_CHANNELS, type Channel, type ChannelParams } from "../api";
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
      {kind === "buffers" && value != null && (() => {
        const b = list.data?.find((x) => x.id === value);
        return b ? <span className="xs">{b.molarity != null ? <>Concentration <b className="mono">{b.molarity} M</b>{(b.equivalents ?? 1) !== 1 ? ` × ${b.equivalents} eq` : ""}: totals also in mmol.</>
          : <>No concentration in the Library: totals in mL only (set it in Library › Buffers).</>}</span> : null;
      })()}
      <span className="xs">Used before, most recent first. Used items can be archived, never deleted.</span>
    </div>
  );
}

const LABELS: Record<string, string> = {
  ph_setpoint: "pH setpoint", temperature_setpoint: "Temperature setpoint (°C)", stirrer_setpoint: "Stirrer setpoint (rpm)",
  ph_control_mode: "pH control mode", ph_pid: "pH PID parameters", ph_limits: "pH process limits", temperature_limits: "T process limits (°C)",
  temperature_control_mode: "Temperature control mode", temperature_pid: "Temperature loop parameters",
};
const GROUP: Record<string, string> = {
  ph_setpoint: "pH controller", ph_control_mode: "pH controller", ph_pid: "pH controller", ph_limits: "pH controller",
  temperature_setpoint: "Temperature controller", temperature_limits: "Temperature controller", stirrer_setpoint: "Stirrer",
  temperature_control_mode: "Temperature controller", temperature_pid: "Temperature controller",
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
  { key: "do2", label: "dO2", what: "dO2 probe · loop actuators as set on the controller" },
  { key: "level", label: "Level / antifoam", what: "level or foam probe · antifoam pump" },
  { key: "redox", label: "Redox", what: "redox probe (mV), read only" },
];

/**
 * Loop configuration as set on the controller (read only): a reminder, one line per loop of the
 * channels in use. Assignments are changed on the controller's display, never from here.
 */
function LoopConfigBox({ cfg, channels, reactor }: { cfg?: LoopConfigView; channels: Channel[]; reactor: string }) {
  const [open, setOpen] = useState(false);
  if (!cfg) return null;
  if (!cfg.reachable) return <span className="xs muted">Loop configuration of {reactor}: read when the controller is on.</span>;
  const shown = cfg.loops.filter((l) => channels.includes(l.key as Channel));
  const nWarn = shown.reduce((n, l) => n + l.warnings.length, 0);
  return (
    <div className="stack" style={{ gap: 6, border: "1px solid var(--outline-variant, #c4c7cc)", borderRadius: 12, padding: "8px 12px" }}>
      <button className="row between btn text" style={{ height: 30, padding: 0, justifyContent: "space-between" }} onClick={() => setOpen(!open)}
        aria-expanded={open}>
        <span className="small"><b>Loops on the {reactor} controller</b> · as set on the display, read only
          {nWarn > 0 && <span style={{ color: "#8a5a00" }}> · {nWarn} note{nWarn > 1 ? "s" : ""}</span>}</span>
        <span className="xs">{open ? "Hide" : "Details"}</span>
      </button>
      {shown.map((l) => (
        <div key={l.key} className="small" style={{ lineHeight: 1.4 }}>
          <b>{l.label}</b>
          <span className="muted"> · {!l.installed ? "not installed" : `${l.active ? "loop ON" : "loop off"}${l.control_type ? ` · ${l.control_type}` : ""}`}</span>
          {l.installed && <span className="muted"> · {sidesText(l.actuators)}</span>}
          {open && l.probe && <span className="muted"> · {l.probe}</span>}
          {open && l.value != null && <span className="muted"> · now {l.value}{l.setpoint != null && l.installed && !["level", "redox"].includes(l.key) ? `, SP ${l.setpoint}` : ""}</span>}
          {open ? l.warnings.map((w) => <div key={w} className="xs" style={{ color: "#8a5a00", paddingLeft: 12 }}>⚠ {w}</div>)
            : l.installed && l.warnings.length > 0 && <span className="xs" style={{ color: "#8a5a00" }}> · ⚠ {l.warnings.join(" · ")}</span>}
        </div>
      ))}
      {open && cfg.unassigned_pumps.length > 0 && (
        <span className="xs muted">Pumps in no loop: {cfg.unassigned_pumps.map((a) => a.name).join(", ")}</span>
      )}
      {open && <span className="xs muted">Actuators, cascades, limits and calibration are changed on the controller's display;
        a profile records the actuators it was tuned for and does not load a loop's control settings if they differ.</span>}
    </div>
  );
}

export function Planning({ existing, onChange }: { existing?: Experiment; onChange?: () => void }) {
  const nav = useNavigate();
  const query = new URLSearchParams(useLocation().search);
  const preselected = query.get("reactor");
  const reactors = usePolling(api.bioreactors, 60000);
  const templates = usePolling(api.templates, 600000);
  const [pickTemplate, setPickTemplate] = useState(!existing && query.get("template") === "pick");
  const [d, setD] = useState<ExperimentDraft>(() => existing ? {
    name: existing.name, reactor_id: existing.reactor_id, planned_date: existing.planned_date, strain: existing.strain,
    medium_id: existing.medium_id, buffer_id: existing.buffer_id, working_volume_ml: existing.working_volume_ml,
    profile_id: existing.profile_id, notes: existing.notes, channels: existing.channels, plan_params: existing.plan_params,
    buffer_volume_ml: existing.buffer_volume_ml ?? null,
  } : { name: "", reactor_id: preselected ?? "R1", planned_date: today(), strain: "", notes: "", channels: [...DEFAULT_CHANNELS] });
  const [notice, setNotice] = useState<Channel | null>(null);
  const [dirty, setDirty] = useState(false);
  const [msg, setMsg] = useState<{ kind: "good" | "alert" | "warn"; text: string } | null>(null);
  const set = (patch: Partial<ExperimentDraft>) => { setD({ ...d, ...patch }); setDirty(true); };

  const profiles = usePolling(api.profiles, 600000);
  const all = usePolling(() => api.experiments(), 60000);
  const check = usePolling(() => (existing ? api.planCheck(existing.id) : Promise.resolve(undefined)), 15000, [existing?.id]);
  const loopCfg = usePolling(() => api.loopConfig(d.reactor_id), 60000, [d.reactor_id]);

  const [parallel, setParallel] = useState(!!existing?.parallel_run_code);
  const [partnerId, setPartnerId] = useState<number | "">("");
  const reactorIds = (reactors.data ?? []).filter((x) => x.enabled).map((x) => x.id);
  const otherReactor = reactorIds.find((x) => x !== d.reactor_id) ?? (d.reactor_id === "R1" ? "R2" : "R1");
  const applyTemplate = (t: Template) => {
    const x = t.data;
    setD({ ...d, strain: x.strain ?? d.strain, medium_id: x.medium_id ?? null, buffer_id: x.buffer_id ?? null,
      buffer_volume_ml: x.buffer_volume_ml ?? null, working_volume_ml: x.working_volume_ml ?? null, profile_id: x.profile_id ?? null,
      plan_params: x.plan_params ?? null, channels: x.channels ?? d.channels, notes: x.notes ?? d.notes,
      name: d.name || t.name });
    setDirty(true); setPickTemplate(false);
    setMsg({ kind: "good", text: `Filled from template “${t.name}”: check name, date and bioreactor, then save.` });
  };
  const saveTemplate = async () => {
    if (!existing) return;
    const name = window.prompt("Template name (the whole plan: medium, buffer, channels, profile and values):", existing.name);
    if (!name?.trim()) return;
    try { await api.createTemplate({ name: name.trim(), from_experiment_id: existing.id }); templates.refresh();
      setMsg({ kind: "good", text: `Saved as template “${name.trim()}” (Library › Templates).` }); }
    catch (x) { setMsg({ kind: "alert", text: (x as Error).message }); }
  };
  const candidates = (all.data ?? []).filter((x) => x.status === "planned" && x.reactor_id === otherReactor && x.id !== existing?.id && !x.parallel_run_code);
  useEffect(() => { if (partnerId === "" && candidates[0]) setPartnerId(candidates[0].id); }, [candidates, partnerId]);

  const profile = profiles.data?.find((p) => p.id === d.profile_id);
  const live = usePolling(api.live, 10000);
  const liveR = live.data?.find((x) => x.reactor_id === d.reactor_id);
  const modified = !!profile && !!d.plan_params && !sameParams(d.plan_params, fromProfile(profile));
  const mismatches = (check.data?.rows ?? []).filter((r) => !r.match && !r.read_only);
  const pidDiffers = mismatches.some((r) => /_control_mode$|_pid$/.test(r.parameter));

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
            <h1 style={{ fontSize: 22, fontWeight: 600 }}>{existing ? <>{existing.name} <span className="mono code-chip">{existing.code}</span></> : "New experiment"}</h1>
            <span className="small muted">{existing ? "Planning · not started" : "Fill in the plan, then save"}</span>
          </div>
          <StatusChip status="planned" />
        </div>
        <div className="row" style={{ gap: 8 }}>
          {!existing && (templates.data ?? []).length > 0 && <button className="btn outlined" onClick={() => setPickTemplate(true)}><Icon name="list" size={18} />From template</button>}
          {existing && <button className="btn text" onClick={saveTemplate} disabled={dirty} title={dirty ? "Save the plan first" : ""}>Save as template</button>}
          {existing && <DeleteExperiment e={existing} />}
          <button className="btn filled" onClick={save} disabled={!!existing && !dirty}>{existing ? "Save plan" : "Create"}</button>
        </div>
      </header>
      {msg && <div className={`banner ${msg.kind}`} role="status">{msg.text}</div>}
      {pickTemplate && (
        <div className="scrim" onMouseDown={(x) => { if (x.target === x.currentTarget) setPickTemplate(false); }}>
          <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="tpl-t" style={{ width: "min(560px, 100vw)" }}>
            <h2 id="tpl-t" style={{ fontSize: 20 }}>New experiment from a template</h2>
            {(templates.data ?? []).length === 0 && <span className="small muted">No templates yet: save a plan as template from its planning page.</span>}
            <div className="stack" style={{ gap: 8 }}>
              {(templates.data ?? []).map((t) => (
                <button key={t.id} className="choice" style={{ textAlign: "left", cursor: "pointer" }} onClick={() => applyTemplate(t)}>
                  <span className="stack" style={{ gap: 2 }}>
                    <b>{t.name}</b>
                    <span className="xs muted">{[t.data.strain, t.medium_name, t.buffer_name && `${t.buffer_name}${t.data.buffer_volume_ml ? ` ${t.data.buffer_volume_ml} mL` : ""}`,
                      t.profile_name && `profile “${t.profile_name}”`, (t.data.channels ?? []).join(" · ")].filter(Boolean).join(" · ")}</span>
                    {t.description && <span className="xs">{t.description}</span>}
                  </span>
                </button>
              ))}
            </div>
            <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn text" onClick={() => setPickTemplate(false)}>Start empty</button></div>
          </div>
        </div>
      )}

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
                  {(reactors.data ?? [{ id: "R1", name: "R1", enabled: true }, { id: "R2", name: "R2", enabled: true }])
                    .filter((x) => x.enabled || x.id === d.reactor_id).map((x) => (
                    <button key={x.id} role="radio" aria-checked={d.reactor_id === x.id} className={d.reactor_id === x.id ? "on" : ""}
                      style={{ flex: 1, fontSize: 14 }} title={x.id} onClick={() => set({ reactor_id: x.id })}>{x.name}</button>
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
                      const lc = loopCfg.data?.loops.find((l) => l.key === c.key);
                      if (!on && (c.key === "do2" || (lc && lc.warnings.length > 0))) setNotice(c.key);
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
          {notice && (() => {
            const lc = loopCfg.data?.loops.find((l) => l.key === notice);
            const label = CHANNEL_INFO.find((c) => c.key === notice)?.label ?? notice;
            return (
              <div className="scrim" onMouseDown={(x) => { if (x.target === x.currentTarget) setNotice(null); }}>
                <div className="dialog" role="alertdialog" aria-modal="true" aria-labelledby="ch-t" aria-describedby="ch-d">
                  <h2 id="ch-t" style={{ fontSize: 22, fontWeight: 500, margin: 0 }}>{label}: check the controller</h2>
                  <div className="banner warn" id="ch-d"><Icon name="warn" color="#8a5a00" size={20} />
                    <span className="stack" style={{ gap: 4 }}>
                      {lc ? <>
                        <span>On {d.reactor_id}, as set on the controller:</span>
                        {lc.warnings.length ? lc.warnings.map((w) => <span key={w}>• {w}</span>) : <span>no problems found.</span>}
                      </> : <span>The configuration of {d.reactor_id} is read when the controller is on.</span>}
                      <span>The channel is recorded and shown anyway; the loop is set up on the controller's display.</span>
                    </span>
                  </div>
                  <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn filled" autoFocus onClick={() => setNotice(null)}>OK</button></div>
                </div>
              </div>
            );
          })()}
          <section className="card" aria-labelledby="g2">
            <h2 id="g2" style={{ fontSize: 18 }}>Medium and buffer</h2>
            <div className="row" style={{ alignItems: "flex-start" }}>
              <div style={{ flex: "1 1 220px" }}><NamedSelect kind="media" label="Medium" value={d.medium_id ?? null} onChange={(id) => set({ medium_id: id })} /></div>
              <div style={{ flex: "1 1 220px" }}><NamedSelect kind="buffers" label="Buffer / base" value={d.buffer_id ?? null} onChange={(id) => set({ buffer_id: id })} /></div>
              <label className="field" style={{ flex: "0 1 170px" }}>Buffer available (mL)
                <input inputMode="decimal" value={d.buffer_volume_ml ?? ""} placeholder="e.g. 250"
                  onChange={(e) => set({ buffer_volume_ml: e.target.value === "" ? null : Number(e.target.value.replace(",", ".")) })} style={{ fontFamily: "var(--font-mono)" }} />
                <span className="xs">Volume in the bottle at Start: alert below 10 %. Frozen at Start.</span>
              </label>
              <label className="field" style={{ flex: "0 1 160px" }}>Working volume (mL)
                <input inputMode="decimal" value={d.working_volume_ml ?? ""} onChange={(e) => set({ working_volume_ml: e.target.value === "" ? null : Number(e.target.value) })} style={{ fontFamily: "var(--font-mono)" }} />
              </label>
            </div>
          </section>

          <section className="card" aria-labelledby="g3">
            <div className="row between" style={{ alignItems: "flex-end" }}>
              <div className="stack" style={{ gap: 2 }}>
                <h2 id="g3" style={{ fontSize: 18 }}>Controller {d.reactor_id}: plan, check and load</h2>
                <span className="small muted">Plan values come from the profile and can be edited here; when the bioreactor is on they are compared with what the controller reports and can be loaded.</span>
              </div>
              <label className="field" style={{ minWidth: 260 }}>Profile
                <select value={d.profile_id ?? ""} onChange={(e) => {
                  const id = e.target.value === "" ? null : Number(e.target.value);
                  const p = profiles.data?.find((x) => x.id === id);
                  set({ profile_id: id, plan_params: p ? fromProfile(p) : d.plan_params ?? null });
                }}>
                  <option value="">Custom (no profile)</option>
                  {(profiles.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </label>
            </div>
            <div className="row" style={{ gap: 8 }}><PlanSource d={d} profile={profile}
              changed={!!existing?.profile_changed && existing.profile_id === d.profile_id && !dirty} /></div>
            <PlanControllerTable value={d.plan_params ?? null} channels={d.channels ?? DEFAULT_CHANNELS} reactor={liveR}
              onChange={(v) => set({ plan_params: v })} />
            {existing && Object.entries(check.data?.assignment_differences ?? {}).map(([loop, m]) => (
              <div key={loop} className="banner warn"><Icon name="warn" color="#8a5a00" size={20} />
                <span><b>{loop === "ph" ? "pH" : "Temperature"} loop: actuators differ.</b> {m.charAt(0).toUpperCase() + m.slice(1)}. Its control mode and loop parameters will not be loaded
                  (setpoint and limits are).</span>
              </div>
            ))}
            {existing && <LoopConfigBox cfg={loopCfg.data} channels={d.channels ?? DEFAULT_CHANNELS} reactor={d.reactor_id} />}
            {existing && hasValues(d.plan_params) && mismatches.length > 0 && (
              <div className="banner warn"><Icon name="warn" color="#8a5a00" size={20} />
                <span><b>{pidDiffers ? "Loop control differs." : "Setpoints differ."}</b> {pidDiffers && "The controller may have been left in another mode by a previous user. "}
                  Loading writes setpoints, process limits, control modes and loop parameters (pH, temperature) to the controller, then reads them back.</span>
              </div>
            )}
            <div className="row" style={{ gap: 8 }}>
              <button className="btn filled" disabled={!existing || dirty || !hasValues(d.plan_params) || !check.data?.reachable} onClick={async () => {
                if (!existing) return;
                try {
                  const r = await api.loadProfile(existing.id);
                  const bad = r.results.filter((x) => x.status !== "confirmed" && x.status !== "skipped");
                  const skipped = r.results.filter((x) => x.status === "skipped");
                  setMsg(bad.length ? { kind: "alert", text: `Not confirmed: ${bad.map((b) => `${b.parameter} (${b.message ?? b.status})`).join(", ")}` }
                    : skipped.length ? { kind: "warn", text: `Loaded and confirmed, except: ${skipped.map((b) => `${b.parameter.replace("_control", "")} — ${b.message}`).join("; ")}` }
                    : { kind: "good", text: "Plan loaded and confirmed by the controller." });
                  check.refresh(); live.refresh();
                } catch (x) { setMsg({ kind: "alert", text: (x as Error).message }); }
              }}>Load profile to controller</button>
              {modified && profile && <button className="btn text" onClick={() => set({ plan_params: fromProfile(profile) })}>
                {existing?.profile_changed && !dirty ? `Use the new values of “${profile.name}”` : `Reset to “${profile.name}”`}</button>}
            </div>
            <span className="xs muted">{!existing ? "Create the experiment to load the plan. " : dirty ? "Save the plan to load it. " : ""}Limits left empty stay as they are on the controller; actuators, interlocks and calibration stay on the controller. Control mode and loop parameters (pH, temperature) can be loaded only before Start.</span>
          </section>

          <section className="card" aria-labelledby="g5">
            <div className="row between">
              <div className="stack" style={{ gap: 2 }}>
                <h2 id="g5" style={{ fontSize: 18 }}>Rules · run by FM2</h2>
                <span className="small muted">Logic run by this software during the experiment on actuators that are in no loop of the controller
                  (e.g. antifoam pump, gas valves, Addition 2 as a feed). Coming in 2.4, after the manual commands are verified.</span>
              </div>
              <button className="btn outlined" disabled title="Available in 2.4"><Icon name="plus" size={18} />New rule</button>
            </div>
            {loopCfg.data?.reachable && (
              <span className="xs muted">Free actuators on {d.reactor_id}: {loopCfg.data.unassigned_pumps.length
                ? loopCfg.data.unassigned_pumps.map((a) => a.name).join(", ") : "none"}.</span>
            )}
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
    temperature_control_mode: p.temperature_control_mode ?? null, temperature_pid: p.temperature_pid ? { ...p.temperature_pid } : null,
    loop_assignments: p.loop_assignments ?? null,
    channel_params: p.channel_params ?? null,
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
  !!p && Object.entries(p).some(([k, v]) => k === "loop_assignments" ? false
    : k === "ph_pid" || k === "temperature_pid" ? Object.values(v ?? {}).some((x) => x != null) : v != null && v !== "");

function PlanSource({ d, profile, changed }: { d: ExperimentDraft; profile?: Profile; changed?: boolean }) {
  if (!profile) return hasValues(d.plan_params)
    ? <span className="tag archived" style={{ height: 28 }}>Custom · not saved as a profile</span> : null;
  if (changed) return (
    <span className="tag" style={{ height: "auto", minHeight: 28, background: "var(--warn-container)", whiteSpace: "normal" }}
      title="The plan keeps the values it was saved with. Use the button below to take the profile's new values.">
      From profile “{profile.name}” · the profile was changed after this plan was saved</span>);
  const modified = !!d.plan_params && !sameParams(d.plan_params, fromProfile(profile));
  return modified
    ? <span className="tag" style={{ height: 28, background: "var(--warn-container)" }}>Custom · based on “{profile.name}”, not saved</span>
    : <span className="tag analysed" style={{ height: 28 }}>As in profile “{profile.name}”</span>;
}

const EMPTY_PLAN: PlanParams = { ph_setpoint: null, temperature_setpoint: null, stirrer_setpoint: null, ph_control_mode: null, ph_pid: null,
  ph_low_limit: null, ph_high_limit: null, temperature_low_limit: null, temperature_high_limit: null,
  temperature_control_mode: null, temperature_pid: null, loop_assignments: null, channel_params: null };

type Cell = number | string | null | undefined;
interface PlanRow { id: string; group: string; label: string; plan: Cell | [Cell, Cell]; ctrl: Cell | [Cell, Cell]; kind: "num" | "pair" | "mode";
  set: (v: Cell | [Cell, Cell]) => PlanParams }

const fmtCell = (v: Cell | [Cell, Cell]) => Array.isArray(v) ? (v[0] == null && v[1] == null ? "—" : `${v[0] ?? "·"}–${v[1] ?? "·"}`)
  : v === null || v === undefined || v === "" ? "—" : String(v);
const same = (a: Cell, b: Cell) => typeof a === "number" && typeof b === "number" ? Math.abs(a - b) < 1e-6 : a === b;

/**
 * The plan's controller values (editable, from the profile) next to what the controller reports now.
 * With the controller off the Controller column stays empty and Check says so.
 */
function PlanControllerTable({ value, channels, reactor, onChange }: {
  value: PlanParams | null; channels: Channel[]; reactor?: LiveReactor; onChange: (v: PlanParams) => void }) {
  const v = value ?? EMPTY_PLAN;
  const snap = reactor?.online ? reactor.snapshot : null;
  const pc = reactor?.online ? reactor.ph_control : null;
  const tc = reactor?.online ? reactor.temperature_control : null;
  const [edit, setEdit] = useState<{ id: string; a: string; b: string } | null>(null);
  const rows: PlanRow[] = [];
  if (channels.includes("ph")) {
    rows.push({ id: "ph_sp", group: "pH controller", label: "pH setpoint", kind: "num", plan: v.ph_setpoint, ctrl: snap?.ph_setpoint,
      set: (x) => ({ ...v, ph_setpoint: x as number | null }) });
    rows.push({ id: "ph_mode", group: "pH controller", label: "pH control mode", kind: "mode", plan: v.ph_control_mode, ctrl: pc?.mode,
      set: (x) => ({ ...v, ph_control_mode: (x as string) || null }) });
    for (const p of PID_PARAMS.filter((q) => (MODE_KEYS[v.ph_control_mode ?? ""] ?? []).includes(q.k)
      && (q.k !== "deadband" || pc?.deadband_enabled !== false))) {
      rows.push({ id: `pid_${p.k}`, group: "pH controller", label: `pH loop · ${p.label}${p.unit ? ` (${p.unit})` : ""}`, kind: "num",
        plan: v.ph_pid?.[p.k] ?? null, ctrl: pc?.pid?.[p.k], set: (x) => ({ ...v, ph_pid: { ...(v.ph_pid ?? {}), [p.k]: x as number | null } }) });
    }
    rows.push({ id: "ph_lim", group: "pH controller", label: "pH process limits", kind: "pair", plan: [v.ph_low_limit, v.ph_high_limit],
      ctrl: snap ? [snap.ph_low_limit, snap.ph_high_limit] : undefined,
      set: (x) => { const [lo, hi] = x as [Cell, Cell]; return { ...v, ph_low_limit: lo as number | null, ph_high_limit: hi as number | null }; } });
  }
  if (channels.includes("temperature")) {
    rows.push({ id: "t_sp", group: "Temperature controller", label: "Temperature setpoint (°C)", kind: "num", plan: v.temperature_setpoint,
      ctrl: snap?.temperature_setpoint, set: (x) => ({ ...v, temperature_setpoint: x as number | null }) });
    rows.push({ id: "t_mode", group: "Temperature controller", label: "Temperature control mode", kind: "mode", plan: v.temperature_control_mode ?? null,
      ctrl: tc?.mode, set: (x) => ({ ...v, temperature_control_mode: (x as string) || null }) });
    // dead band only where the controller uses it (temperature: disabled on R1/R2)
    const tDeadband = tc?.deadband_enabled === true;
    for (const p of PID_PARAMS.filter((q) => (MODE_KEYS[v.temperature_control_mode ?? ""] ?? []).includes(q.k)
      && (q.k !== "deadband" || tDeadband))) {
      rows.push({ id: `tpid_${p.k}`, group: "Temperature controller", label: `T loop · ${p.label}${p.k === "deadband" ? " (°C)" : p.unit === "s" ? " (s)" : ""}`, kind: "num",
        plan: v.temperature_pid?.[p.k] ?? null, ctrl: tc?.pid?.[p.k],
        set: (x) => ({ ...v, temperature_pid: { ...(v.temperature_pid ?? {}), [p.k]: x as number | null } }) });
    }
    rows.push({ id: "t_lim", group: "Temperature controller", label: "T process limits (°C)", kind: "pair",
      plan: [v.temperature_low_limit, v.temperature_high_limit], ctrl: snap ? [snap.temperature_low_limit, snap.temperature_high_limit] : undefined,
      set: (x) => { const [lo, hi] = x as [Cell, Cell]; return { ...v, temperature_low_limit: lo as number | null, temperature_high_limit: hi as number | null }; } });
  }
  // Other channels (2.3): saved with the plan; loaded on the controller once verified (2.3.x)
  const cp = (ch: Channel) => v.channel_params?.[ch] ?? { setpoint: null, low: null, high: null };
  const setCp = (ch: Channel, patch: Partial<{ setpoint: number | null; low: number | null; high: number | null }>): PlanParams => {
    const next: ChannelParams = { ...(v.channel_params ?? {}), [ch]: { ...cp(ch), ...patch } };
    return { ...v, channel_params: next };
  };
  if (channels.includes("stirrer")) {
    rows.push({ id: "st_sp", group: "Stirrer", label: "Stirrer setpoint (rpm)", kind: "num", plan: v.stirrer_setpoint, ctrl: snap?.stirrer_setpoint,
      set: (x) => ({ ...v, stirrer_setpoint: x as number | null }) });
    rows.push({ id: "st_lim", group: "Stirrer", label: "Stirrer alarm limits (rpm) · saved, not loaded yet", kind: "pair",
      plan: [cp("stirrer").low, cp("stirrer").high], ctrl: undefined,
      set: (x) => { const [lo, hi] = x as [Cell, Cell]; return setCp("stirrer", { low: lo as number | null, high: hi as number | null }); } });
  }
  if (channels.includes("do2")) {
    const ex = snap?.extra ?? {};
    rows.push({ id: "do2_sp", group: "dO2", label: "dO2 setpoint (%) · saved, not loaded yet", kind: "num", plan: cp("do2").setpoint, ctrl: ex.do2_setpoint,
      set: (x) => setCp("do2", { setpoint: x as number | null }) });
    rows.push({ id: "do2_lim", group: "dO2", label: "dO2 alarm limits (%) · saved, not loaded yet", kind: "pair",
      plan: [cp("do2").low, cp("do2").high], ctrl: snap ? [ex.do2_low_limit ?? null, ex.do2_high_limit ?? null] : undefined,
      set: (x) => { const [lo, hi] = x as [Cell, Cell]; return setCp("do2", { low: lo as number | null, high: hi as number | null }); } });
  }
  const n = (t: string): number | null => (t.trim() === "" ? null : Number(t.replace(",", ".")));
  const empty = (x: Cell | [Cell, Cell]) => Array.isArray(x) ? x[0] == null && x[1] == null : x === null || x === undefined || x === "";
  const check = (r: PlanRow) => {
    if (!snap) return <span className="status preparing" style={{ borderStyle: "solid" }}>Controller off</span>;
    if (empty(r.plan)) return <span className="status planned">From controller</span>;
    const ok = Array.isArray(r.plan)
      ? r.plan.every((x, i) => x == null || same(x, (r.ctrl as [Cell, Cell])?.[i])) : same(r.plan, r.ctrl as Cell);
    return <span className={`status ${ok ? "completed" : "aborted"}`} style={ok ? { background: "#d3efe7", color: "var(--ink)" } : undefined}>{ok ? "Match" : "Differs"}</span>;
  };
  const save = (r: PlanRow) => {
    if (!edit) return;
    if (r.kind === "pair") {
      const lo = n(edit.a), hi = n(edit.b);
      if (lo !== null && hi !== null && lo >= hi) { window.alert("The low limit must be lower than the high limit."); return; }
      onChange(r.set([lo, hi]));
    } else onChange(r.set(r.kind === "mode" ? edit.a : n(edit.a)));
    setEdit(null);
  };
  const groups = [...new Set(rows.map((r) => r.group))];
  return (
    <div className="table-wrap">
      <table className="data" style={{ minWidth: 640 }}>
        <thead><tr><th>Parameter</th><th style={{ textAlign: "right" }}>Plan</th><th /><th style={{ textAlign: "right" }}>Controller</th><th>Check</th></tr></thead>
        <tbody>
          {groups.map((g) => (
            <Fragment key={g}>
              <tr><td colSpan={5} className="kicker" style={{ paddingTop: 16 }}>{g}</td></tr>
              {rows.filter((r) => r.group === g).map((r) => (
                <tr key={r.id}>
                  <td style={{ paddingLeft: 24 }}>{r.label}</td>
                  {edit?.id === r.id ? (
                    <td className="num" colSpan={2}>
                      <span className="row" style={{ gap: 6, justifyContent: "flex-end", flexWrap: "nowrap" }}>
                        {r.kind === "mode" ? (
                          <select autoFocus value={edit.a} onChange={(e) => setEdit({ ...edit, a: e.target.value })} style={{ height: 34 }} aria-label={r.label}>
                            <option value="">Leave as on the controller</option><option value="pid">pid</option><option value="adaptive">adaptive</option>
                          </select>
                        ) : (
                          <>
                            <input autoFocus className="cell-input mono" inputMode="decimal" style={{ width: 80, textAlign: "right" }} aria-label={r.kind === "pair" ? `${r.label} low` : r.label}
                              value={edit.a} onChange={(e) => setEdit({ ...edit, a: e.target.value })} onKeyDown={(k) => { if (k.key === "Enter") save(r); if (k.key === "Escape") setEdit(null); }} />
                            {r.kind === "pair" && <>–<input className="cell-input mono" inputMode="decimal" style={{ width: 80, textAlign: "right" }} aria-label={`${r.label} high`}
                              value={edit.b} onChange={(e) => setEdit({ ...edit, b: e.target.value })} onKeyDown={(k) => { if (k.key === "Enter") save(r); if (k.key === "Escape") setEdit(null); }} /></>}
                          </>
                        )}
                        <button className="btn filled" style={{ height: 34, padding: "0 14px" }} onClick={() => save(r)}>OK</button>
                        <button className="btn text" style={{ height: 34 }} onClick={() => setEdit(null)}>Cancel</button>
                      </span>
                    </td>
                  ) : (
                    <>
                      <td className="mono" style={{ textAlign: "right" }}>{fmtCell(r.plan)}</td>
                      <td style={{ width: 1 }}><button className="btn text" style={{ height: 32, padding: "0 10px" }} aria-label={`Edit ${r.label}`} onClick={() => {
                        const p = r.plan;
                        setEdit(Array.isArray(p) ? { id: r.id, a: p[0] == null ? "" : String(p[0]), b: p[1] == null ? "" : String(p[1]) }
                          : { id: r.id, a: p == null ? "" : String(p), b: "" });
                      }}>Edit</button></td>
                    </>
                  )}
                  <td className="mono" style={{ textAlign: "right" }}>{snap ? fmtCell(r.ctrl as Cell) : ""}</td>
                  <td>{check(r)}</td>
                </tr>
              ))}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}
