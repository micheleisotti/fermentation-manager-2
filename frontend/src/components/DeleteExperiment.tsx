import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Experiment } from "../api";
import { Icon } from "./ui";

/** "Delete experiment" button + confirmation dialog that asks to type the experiment code. */
export function DeleteExperiment({ e }: { e: Experiment }) {
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const nav = useNavigate();
  if (e.status === "fermenting") return null;

  async function go() {
    setBusy(true); setErr(null);
    try { await api.deleteExperiment(e.id, code.trim()); nav("/", { replace: true }); }
    catch (x) { setErr((x as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <>
      <button className="btn text" style={{ color: "#22294d" }} onClick={() => { setOpen(true); setCode(""); setErr(null); }}>Delete experiment</button>
      {open && (
        <div className="scrim" onClick={(x) => { if (x.target === x.currentTarget) setOpen(false); }}>
          <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="del-t">
            <h2 id="del-t" style={{ fontSize: 22, fontWeight: 500, margin: 0 }}>Delete <span className="mono">{e.code}</span>?</h2>
            <div className="banner warn"><Icon name="warn" color="#8a5a00" size={20} />
              <span>This permanently deletes the experiment and all its data: controller readings, manual measurements and their
                edit history, events and the setpoint log{e.parallel_run_code ? `; parallel run ${e.parallel_run_code} is unlinked` : ""}. It cannot be undone.</span>
            </div>
            <label className="field"><span>Type <b className="mono">{e.code}</b> to confirm</span>
              <input value={code} onChange={(x) => setCode(x.target.value)} className="mono" autoFocus aria-describedby="del-t" />
            </label>
            {err && <div className="banner alert">{err}</div>}
            <div className="row" style={{ justifyContent: "flex-end", gap: 8 }}>
              <button className="btn text" onClick={() => setOpen(false)}>Cancel</button>
              <button className="btn filled" disabled={busy || code.trim() !== e.code} onClick={go}>Delete permanently</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
