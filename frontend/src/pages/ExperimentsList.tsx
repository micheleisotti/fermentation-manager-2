import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type Experiment, type Status } from "../api";
import { usePolling } from "../hooks";
import { ExpName, Icon, StatusChip } from "../components/ui";

const FILTERS: { key: Status | "all"; label: string }[] = [
  { key: "all", label: "All" }, { key: "planned", label: "Planned" }, { key: "fermenting", label: "Fermenting" },
  { key: "completed", label: "Completed" }, { key: "aborted", label: "Aborted" },
];

/** Labels shown next to the status: Analysed (in N sessions) and Archived. */
export function ExpTags({ e }: { e: Experiment }) {
  return (
    <>
      {e.n_sessions > 0 && <span className="tag analysed" title={`Used in ${e.n_sessions} analysis session(s)`}>Analysed · {e.n_sessions}</span>}
      {e.archived && <span className="tag archived">Archived</span>}
    </>
  );
}

export function ExperimentTable({ list, empty = "No experiments." }: { list: Experiment[]; empty?: string }) {
  const nav = useNavigate();
  return (
    <div className="card" style={{ padding: 0 }}>
      <ul className="exp-list" aria-label="Experiments">
        {list.map((e) => (
          <li key={e.id}>
            <Link to={`/experiments/${e.id}`}>
              <span className="exp-list-main"><ExpName name={e.name} code={e.code} />
                <span className="xs muted">{e.started_at?.slice(0, 10) ?? e.planned_date ?? "—"} · {e.reactor_id}{e.strain ? ` · ${e.strain}` : ""}</span></span>
              <StatusChip status={e.status} />
            </Link>
          </li>
        ))}
        {list.length === 0 && <li className="muted" style={{ padding: 16 }}>{empty}</li>}
      </ul>
      <div className="table-wrap exp-table">
        <table className="data" style={{ minWidth: 900 }}>
          <thead><tr><th>Date</th><th>Experiment</th><th>Strain</th><th>Reactor</th><th>Parallel run</th><th>Status</th><th>By</th><th>Last modified</th></tr></thead>
          <tbody>
            {list.map((e) => (
              <tr key={e.id} className="click" onClick={() => nav(`/experiments/${e.id}`)}>
                <td className="mono">{e.started_at?.slice(0, 10) ?? e.planned_date ?? "—"}</td>
                <td><ExpName name={e.name} code={e.code} /></td>
                <td className="mono">{e.strain || "—"}</td>
                <td className="mono">{e.reactor_id}</td>
                <td className="mono muted">{e.parallel_run_code ?? "—"}</td>
                <td><span className="row" style={{ gap: 6, flexWrap: "nowrap" }}><StatusChip status={e.status} /><ExpTags e={e} /></span></td>
                <td className="small">{e.author || "—"}</td>
                <td className="mono muted small">{new Date(e.updated_at).toLocaleString()}</td>
              </tr>
            ))}
            {list.length === 0 && <tr><td colSpan={8} className="muted">{empty}</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** /experiments: every experiment, with status filters, search and archived toggle. */
export function ExperimentsList() {
  const exps = usePolling(() => api.experiments(), 30000);
  const [filter, setFilter] = useState<Status | "all">("all");
  const [q, setQ] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [onlyAnalysed, setOnlyAnalysed] = useState<"any" | "yes" | "no">("any");
  const all = (exps.data ?? []).filter((e) => showArchived || !e.archived);
  const list = all.filter((e) => (filter === "all" || e.status === filter)
    && (onlyAnalysed === "any" || (onlyAnalysed === "yes") === (e.n_sessions > 0))
    && (!q || `${e.code} ${e.name} ${e.strain} ${e.author} ${e.started_at ?? e.planned_date ?? ""}`.toLowerCase().includes(q.toLowerCase())));
  const count = (k: Status | "all") => all.filter((e) => k === "all" || e.status === k).length;
  const nArchived = (exps.data ?? []).filter((e) => e.archived).length;
  return (
    <>
      <div className="row between">
        <h1 className="display">Experiments</h1>
        <Link className="btn filled" to="/experiments/new"><Icon name="plus" size={18} />Plan experiment</Link>
      </div>
      <div className="row between">
        <div className="row" role="group" aria-label="Filter by status" style={{ gap: 8 }}>
          {FILTERS.map((f) => (
            <button key={f.key} className={`chip ${filter === f.key ? "on" : ""}`} aria-pressed={filter === f.key} onClick={() => setFilter(f.key)}>
              {filter === f.key && <Icon name="check" size={16} width={2.2} />}{f.label} · {count(f.key)}
            </button>
          ))}
        </div>
        <label className="field" style={{ minWidth: 280 }}>
          <span className="sr-only">Search experiments</span>
          <input type="search" placeholder="Search by code, name, strain, user or date" value={q} onChange={(e) => setQ(e.target.value)}
            style={{ borderRadius: 24, background: "var(--surface-container)", border: 0 }} />
        </label>
      </div>
      <div className="row small" style={{ gap: 16 }}>
        <label className="row" style={{ gap: 6 }}>Analysed
          <select value={onlyAnalysed} onChange={(e) => setOnlyAnalysed(e.target.value as "any")} style={{ height: 32 }}>
            <option value="any">any</option><option value="yes">in at least one session</option><option value="no">not yet</option>
          </select>
        </label>
        <label className="row" style={{ gap: 6 }}>
          <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} style={{ width: 18, height: 18 }} />
          Show archived · {nArchived}
        </label>
      </div>
      <ExperimentTable list={list} />
    </>
  );
}

/** One "End of experiment" button: the dialog asks Completed or Aborted, with an optional note. */
export function EndExperimentButton({ e, onDone, compact = false }: { e: Experiment; onDone: () => void; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const [outcome, setOutcome] = useState<"completed" | "aborted">("completed");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [cfg, setCfg] = useState<{ dest: string; on: boolean } | null>(null);
  const [copy, setCopy] = useState(true);
  const [result, setResult] = useState<{ path?: string; error?: string } | null>(null);
  const [loopsOn, setLoopsOn] = useState<string[] | null>(null);
  const openDialog = async () => {
    setOpen(true); setResult(null); setErr(null); setLoopsOn(null);
    // 2.3: warn when loops are still on — ending the experiment does not switch them off
    api.loopsState(e.reactor_id).then((ls) => setLoopsOn(ls.reachable ? ls.loops.filter((l) => l.active).map((l) => l.label) : null)).catch(() => undefined);
    try {
      const b = (await api.settings()).backup;
      setCfg({ dest: b.archive_destination, on: b.archive_on_end }); setCopy(!!b.archive_destination && b.archive_on_end);
    } catch { setCfg({ dest: "", on: false }); setCopy(false); }
  };
  const go = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await api.end(e.id, outcome, note, cfg?.dest ? copy : false);
      if (r.copy) setResult(r.copy); else { setOpen(false); onDone(); }
    }
    catch (x) { setErr((x as Error).message); }
    finally { setBusy(false); }
  };
  return (
    <>
      <button className="btn outlined" style={{ color: "var(--ink)", ...(compact ? { width: "100%", justifyContent: "center" } : {}) }} onClick={openDialog}>
        <Icon name="stop" size={16} />End of experiment</button>
      {open && (
        <div className="scrim" onMouseDown={(x) => { if (x.target === x.currentTarget && !busy) setOpen(false); }}>
          <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="end-title" style={{ width: "min(520px, 100vw)" }}>
            <h2 id="end-title" style={{ fontSize: 20 }}>End {e.code}?</h2>
            <span className="small muted">Recording stops now. The controller keeps running as it is: stop the loops on the controller if needed.</span>
            {loopsOn && loopsOn.length > 0 && (
              <div className="banner warn small" role="alert"><Icon name="warn" size={18} color="#8a5a00" />
                <span><b>Loops still on: {loopsOn.join(", ")}.</b> Ending the experiment does not switch them off: pumps, heating and
                  stirrer keep working. Stop them with <b>Loops</b> (or on the controller) if the run is over.</span></div>
            )}
            <div className="stack" role="radiogroup" aria-label="Outcome" style={{ gap: 8 }}>
              {([["completed", "Completed", "End of production: the run is valid and is offered in analysis sessions."],
                ["aborted", "Aborted", "The run failed (contamination, fault, test…): data are kept, but it is not offered in analysis sessions."]] as const).map(([k, t, d]) => (
                <label key={k} className={`choice ${outcome === k ? "on" : ""}`}>
                  <input type="radio" name="outcome" checked={outcome === k} onChange={() => setOutcome(k)} />
                  <span className="stack" style={{ gap: 2 }}><b>{t}</b><span className="small muted">{d}</span></span>
                </label>
              ))}
            </div>
            <label className="field">Note {outcome === "aborted" ? "(why it was aborted)" : "(optional)"}
              <textarea rows={2} value={note} onChange={(x) => setNote(x.target.value)} /></label>
            {cfg?.dest ? (
              <label className="row" style={{ gap: 10, flexWrap: "nowrap" }}>
                <input type="checkbox" checked={copy} onChange={(x) => setCopy(x.target.checked)} style={{ width: 20, height: 20, flex: "none" }} />
                <span>Save a copy of the data to <span className="mono">{cfg.dest}</span></span>
              </label>
            ) : cfg && <span className="xs muted">To save a copy (e.g. on a USB stick) at the end, set an archive folder in Settings › Data and backup.</span>}
            {err && <div className="banner alert small">{err}</div>}
            {result && (
              <div className={`banner ${result.error ? "warn" : "good"} small`} role="status">
                {result.error ? <>Experiment ended, but the copy was not saved: {result.error}</> : <>Experiment ended. Copy saved: <span className="mono">{result.path}</span></>}
              </div>
            )}
            {result ? (
              <div className="row" style={{ justifyContent: "flex-end" }}>
                <button className="btn filled" onClick={() => { setOpen(false); onDone(); }}>Close</button>
              </div>
            ) : (
            <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}>
              <button className="btn text" disabled={busy} onClick={() => setOpen(false)}>Cancel</button>
              <button className="btn filled" disabled={busy} onClick={go}>{busy ? "Ending…" : outcome === "completed" ? "End as completed" : "End as aborted"}</button>
            </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}

/** Archive / restore an experiment (hidden from Home and analysis pickers; nothing deleted). */
export function ArchiveButton({ e, onDone }: { e: Experiment; onDone: () => void }) {
  if (e.status === "fermenting" || e.status === "planned") return null;
  return (
    <button className="btn text" title={e.archived ? "Show it again in Home and analysis" : "Hide from Home and analysis pickers; nothing is deleted"}
      onClick={async () => { await api.setArchived(e.id, !e.archived); onDone(); }}>
      {e.archived ? "Restore from archive" : "Archive"}
    </button>
  );
}

/** "Start experiment" (t = 0) with the reminder of the preliminary operations on the bioreactor. */
export function StartExperimentButton({ e, onDone, compact = false }: { e: Experiment; onDone: () => void; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const probes = ["pH", ...(e.channels.includes("do2") ? ["dO2"] : [])].join(" and ");
  return (
    <>
      <button className="btn filled" style={compact ? { width: "100%", justifyContent: "center", height: 48 } : undefined}
        onClick={() => { setOpen(true); setErr(null); }}><Icon name="play" size={18} />Start experiment</button>
      {open && (
        <div className="scrim" onMouseDown={(x) => { if (x.target === x.currentTarget && !busy) setOpen(false); }}>
          <div className="dialog" role="alertdialog" aria-modal="true" aria-labelledby="pre-title" aria-describedby="pre-d" style={{ width: "min(520px, 100vw)" }}>
            <h2 id="pre-title" style={{ fontSize: 20 }}>Start {e.code}?</h2>
            <div className="banner warn" id="pre-d"><Icon name="warn" color="#8a5a00" size={20} />
              <span>Check basic operations on the bioreactor controller: <b>{probes} probe calibration</b>
                {e.channels.includes("ph") && <>, <b>dose monitoring calibration</b></>}, <b>cooling and condenser water valves</b> open.</span>
            </div>
            <span className="small muted">t = 0 is now: recording of the run starts. Readings taken while preparing are kept as negative times.</span>
            {err && <div className="banner alert small">{err}</div>}
            <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}>
              <button className="btn text" disabled={busy} onClick={() => setOpen(false)}>Cancel</button>
              <button className="btn filled" autoFocus disabled={busy} onClick={async () => {
                setBusy(true); setErr(null);
                try { await api.start(e.id); setOpen(false); onDone(); }
                catch (x) { setErr((x as Error).message); }
                finally { setBusy(false); }
              }}><Icon name="play" size={18} />{busy ? "Starting…" : "Start experiment"}</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

