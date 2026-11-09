/**
 * Library: the reusable items used to plan and record experiments — controller profiles,
 * media, buffers and manual variables. New ones can be added here or from New; used items
 * are archived rather than deleted.
 */
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type Named, type Variable } from "../api";
import { usePolling } from "../hooks";
import { Profiles } from "./Profiles";

const TABS = [
  { k: "profiles", label: "Controller profiles" },
  { k: "media", label: "Media" },
  { k: "buffers", label: "Buffers / base" },
  { k: "variables", label: "Manual variables" },
] as const;
type Tab = (typeof TABS)[number]["k"];

export function Library() {
  const [q, setQ] = useSearchParams();
  const tab = (TABS.find((t) => t.k === q.get("tab"))?.k ?? "profiles") as Tab;
  return (
    <>
      <h1>Library</h1>
      <span className="muted small">Reusable items for planning and recording experiments. Items already used by an experiment can be archived (hidden from new plans) but not deleted.</span>
      <div className="segmented lib-tabs" role="tablist" aria-label="Library sections">
        {TABS.map((t) => (
          <button key={t.k} role="tab" aria-selected={tab === t.k} className={tab === t.k ? "on" : ""} onClick={() => setQ({ tab: t.k })}>{t.label}</button>
        ))}
      </div>
      {tab === "profiles" && <Profiles embedded />}
      {tab === "media" && <NamedManager kind="media" noun="medium" placeholder="e.g. MRS, CDM + 2% glucose" />}
      {tab === "buffers" && <NamedManager kind="buffers" noun="buffer / base" placeholder="e.g. NaOH 2 M" />}
      {tab === "variables" && <Variables />}
    </>
  );
}

function useMsg() {
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const run = async (f: () => Promise<unknown>, ok?: string) => {
    setMsg(null);
    try { await f(); if (ok) setMsg({ kind: "good", text: ok }); return true; }
    catch (x) { setMsg({ kind: "alert", text: (x as Error).message }); return false; }
  };
  const banner = msg && <div className={`banner ${msg.kind} small`} role="status">{msg.text}</div>;
  return { run, banner };
}

function NamedManager({ kind, noun, placeholder }: { kind: "media" | "buffers"; noun: string; placeholder: string }) {
  const list = usePolling(() => api.namedAll(kind), 600000, [kind]);
  const { run, banner } = useMsg();
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [edit, setEdit] = useState<{ id: number; name: string; description: string } | null>(null);
  const [showArch, setShowArch] = useState(false);
  const rows = (list.data ?? []).filter((x) => showArch || !x.archived);
  const nArch = (list.data ?? []).filter((x) => x.archived).length;
  return (
    <div className="split">
      <div className="wide">
        <section className="card">
          <div className="row between">
            <h2 style={{ fontSize: 18 }}>{kind === "media" ? "Media" : "Buffers / base"}</h2>
            <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={showArch} onChange={(e) => setShowArch(e.target.checked)}
              style={{ width: 18, height: 18 }} />Show archived · {nArch}</label>
          </div>
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>Name</th><th>Description</th><th className="num">Used by</th><th /></tr></thead>
              <tbody>
                {rows.map((x: Named) => edit?.id === x.id ? (
                  <tr key={x.id}>
                    <td><input className="cell-input" style={{ width: "100%" }} value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} aria-label="Name" /></td>
                    <td><input className="cell-input" style={{ width: "100%" }} value={edit.description} onChange={(e) => setEdit({ ...edit, description: e.target.value })} aria-label="Description" /></td>
                    <td />
                    <td className="num" style={{ whiteSpace: "nowrap" }}>
                      <button className="btn text" onClick={() => setEdit(null)}>Cancel</button>
                      <button className="btn filled" onClick={async () => { if (await run(() => api.updateNamed(kind, x.id, { name: edit.name, description: edit.description }))) { setEdit(null); list.refresh(); } }}>Save</button>
                    </td>
                  </tr>
                ) : (
                  <tr key={x.id} style={x.archived ? { opacity: 0.6 } : undefined}>
                    <td style={{ fontWeight: 500 }}>{x.name}{x.archived && <span className="tag archived" style={{ marginLeft: 8 }}>Archived</span>}</td>
                    <td className="small">{x.description || <span className="muted">—</span>}</td>
                    <td className="num mono">{x.n_used} exp.</td>
                    <td className="num" style={{ whiteSpace: "nowrap" }}>
                      <button className="btn text" onClick={() => setEdit({ id: x.id, name: x.name, description: x.description })}>Edit</button>
                      <button className="btn text" onClick={async () => { await run(() => api.updateNamed(kind, x.id, { archived: !x.archived })); list.refresh(); }}>
                        {x.archived ? "Restore" : "Archive"}</button>
                      {x.n_used === 0 && <button className="btn text" style={{ color: "#b30020" }} onClick={async () => {
                        if (window.confirm(`Delete ${noun} “${x.name}”?`)) { await run(() => api.deleteNamed(kind, x.id)); list.refresh(); }
                      }}>Delete</button>}
                    </td>
                  </tr>
                ))}
                {rows.length === 0 && <tr><td colSpan={4} className="muted">None yet.</td></tr>}
              </tbody>
            </table>
          </div>
          {banner}
        </section>
      </div>
      <aside className="side">
        <section className="card">
          <h2 style={{ fontSize: 18 }}>New {noun}</h2>
          <label className="field">Name<input value={name} placeholder={placeholder} onChange={(e) => setName(e.target.value)} /></label>
          <label className="field">Description (optional)<textarea rows={2} value={desc} onChange={(e) => setDesc(e.target.value)} /></label>
          <button className="btn filled" disabled={!name.trim()} onClick={async () => {
            if (await run(() => api.createNamed(kind, name.trim(), desc), `Added “${name.trim()}”.`)) { setName(""); setDesc(""); list.refresh(); }
          }}>Add</button>
        </section>
      </aside>
    </div>
  );
}

export const ROLES: { k: string; label: string; help: string }[] = [
  { k: "biomass", label: "Biomass", help: "OD600, CFU, dry weight. Growth model fit: λ, μmax = specific growth rate, doubling time." },
  { k: "product", label: "Product", help: "AFU, riboflavin, lactate. Model fit as an accumulation curve: μmax = specific accumulation rate, not growth." },
  { k: "substrate", label: "Substrate", help: "Residual sugar. Decreasing curve: plotted and exported, no growth fit." },
  { k: "other", label: "Other", help: "Viability, offline pH… Plotted and exported only." },
];
export const roleOf = (c: string) => (ROLES.some((r) => r.k === c) ? c : "other");

function Variables() {
  const list = usePolling(api.variablesAll, 600000);
  const { run, banner } = useMsg();
  const [f, setF] = useState({ name: "", unit: "", category: "biomass" });
  const [edit, setEdit] = useState<{ id: number; name: string; unit: string; category: string } | null>(null);
  const [showArch, setShowArch] = useState(false);
  const rows = (list.data ?? []).filter((x) => showArch || !x.archived);
  const nArch = (list.data ?? []).filter((x) => x.archived).length;
  const roleSelect = (v: string, on: (v: string) => void) => (
    <select value={roleOf(v)} onChange={(e) => on(e.target.value)} style={{ height: 36 }} aria-label="Role">
      {ROLES.map((r) => <option key={r.k} value={r.k}>{r.label}</option>)}
    </select>
  );
  return (
    <div className="split">
      <div className="wide">
        <section className="card">
          <div className="row between">
            <h2 style={{ fontSize: 18 }}>Manual variables</h2>
            <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={showArch} onChange={(e) => setShowArch(e.target.checked)}
              style={{ width: 18, height: 18 }} />Show archived · {nArch}</label>
          </div>
          <span className="small muted">Measured by hand and entered on the experiment page. The role decides how a variable enters the analysis.</span>
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>Name</th><th>Unit</th><th>Role</th><th className="num">Measurements</th><th /></tr></thead>
              <tbody>
                {rows.map((x: Variable) => edit?.id === x.id ? (
                  <tr key={x.id}>
                    <td><input className="cell-input" style={{ width: "100%" }} value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} aria-label="Name" /></td>
                    <td><input className="cell-input" style={{ width: 90 }} value={edit.unit} onChange={(e) => setEdit({ ...edit, unit: e.target.value })} aria-label="Unit" /></td>
                    <td>{roleSelect(edit.category, (c) => setEdit({ ...edit, category: c }))}</td>
                    <td />
                    <td className="num" style={{ whiteSpace: "nowrap" }}>
                      <button className="btn text" onClick={() => setEdit(null)}>Cancel</button>
                      <button className="btn filled" onClick={async () => {
                        if (await run(() => api.updateVariable(x.id, { name: edit.name, unit: edit.unit, category: edit.category }))) { setEdit(null); list.refresh(); }
                      }}>Save</button>
                    </td>
                  </tr>
                ) : (
                  <tr key={x.id} style={x.archived ? { opacity: 0.6 } : undefined}>
                    <td style={{ fontWeight: 500 }}>{x.name}{x.archived && <span className="tag archived" style={{ marginLeft: 8 }}>Archived</span>}</td>
                    <td className="mono">{x.unit || "—"}</td>
                    <td>{ROLES.find((r) => r.k === roleOf(x.category))?.label}</td>
                    <td className="num mono">{x.n_measurements}</td>
                    <td className="num" style={{ whiteSpace: "nowrap" }}>
                      <button className="btn text" onClick={() => setEdit({ id: x.id, name: x.name, unit: x.unit, category: roleOf(x.category) })}>Edit</button>
                      <button className="btn text" onClick={async () => { await run(() => api.updateVariable(x.id, { archived: !x.archived })); list.refresh(); }}>
                        {x.archived ? "Restore" : "Archive"}</button>
                      {x.n_measurements === 0 && <button className="btn text" style={{ color: "#b30020" }} onClick={async () => {
                        if (window.confirm(`Delete variable “${x.name}”?`)) { await run(() => api.deleteVariable(x.id)); list.refresh(); }
                      }}>Delete</button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <span className="xs muted">Changing a unit does not convert existing measurements: each keeps the unit it was entered with.</span>
          {banner}
        </section>
      </div>
      <aside className="side">
        <section className="card">
          <h2 style={{ fontSize: 18 }}>New variable</h2>
          <label className="field">Name<input value={f.name} placeholder="e.g. Residual sugar" onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
          <label className="field">Unit<input value={f.unit} placeholder="e.g. g/L" onChange={(e) => setF({ ...f, unit: e.target.value })} /></label>
          <div className="stack" role="radiogroup" aria-label="Role" style={{ gap: 6 }}>
            <span className="small muted">Role in the analysis</span>
            {ROLES.map((r) => (
              <label key={r.k} className={`choice ${f.category === r.k ? "on" : ""}`}>
                <input type="radio" name="role" checked={f.category === r.k} onChange={() => setF({ ...f, category: r.k })} />
                <span className="stack" style={{ gap: 2 }}><b>{r.label}</b><span className="xs muted">{r.help}</span></span>
              </label>
            ))}
          </div>
          <button className="btn filled" disabled={!f.name.trim()} onClick={async () => {
            if (await run(() => api.createVariable({ name: f.name.trim(), unit: f.unit.trim(), category: f.category }), `Added “${f.name.trim()}”.`)) {
              setF({ name: "", unit: "", category: "biomass" }); list.refresh();
            }
          }}>Add</button>
        </section>
      </aside>
    </div>
  );
}
