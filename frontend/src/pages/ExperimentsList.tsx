import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type Experiment, type Status } from "../api";
import { usePolling } from "../hooks";
import { Icon, StatusChip } from "../components/ui";

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
      <div className="table-wrap">
        <table className="data" style={{ minWidth: 900 }}>
          <thead><tr><th>Date</th><th>Experiment</th><th>Strain</th><th>Reactor</th><th>Parallel run</th><th>Status</th><th>By</th><th>Last modified</th></tr></thead>
          <tbody>
            {list.map((e) => (
              <tr key={e.id} className="click" onClick={() => nav(`/experiments/${e.id}`)}>
                <td className="mono">{e.started_at?.slice(0, 10) ?? e.planned_date ?? "—"}</td>
                <td><span className="mono" style={{ fontWeight: 600 }}>{e.code}</span> · {e.name}</td>
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
        <h1>Experiments</h1>
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
            style={{ borderRadius: 24, background: "#eef0f4", border: 0 }} />
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
  const go = async () => {
    setBusy(true); setErr(null);
    try { await api.end(e.id, outcome, note); setOpen(false); onDone(); }
    catch (x) { setErr((x as Error).message); }
    finally { setBusy(false); }
  };
  return (
    <>
      <button className="btn outlined" style={{ color: "#22294d", ...(compact ? { width: "100%", justifyContent: "center" } : {}) }} onClick={() => setOpen(true)}>
        <Icon name="stop" size={16} />End of experiment</button>
      {open && (
        <div className="scrim" onMouseDown={(x) => { if (x.target === x.currentTarget && !busy) setOpen(false); }}>
          <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="end-title" style={{ width: "min(520px, 100vw)" }}>
            <h2 id="end-title" style={{ fontSize: 20 }}>End {e.code}?</h2>
            <span className="small muted">Recording stops now. The controller keeps running as it is: stop the loops on the controller if needed.</span>
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
            {err && <div className="banner alert small">{err}</div>}
            <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}>
              <button className="btn text" disabled={busy} onClick={() => setOpen(false)}>Cancel</button>
              <button className="btn filled" disabled={busy} onClick={go}>{busy ? "Ending…" : outcome === "completed" ? "End as completed" : "End as aborted"}</button>
            </div>
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
