import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/ibm-plex-sans/400-italic.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/600.css";
import "./theme.css";
import { Shell } from "./components/Shell";
import { Home } from "./pages/Home";
import { ExperimentPage } from "./pages/ExperimentPage";
import { api } from "./api";

function ComingNext({ title, text }: { title: string; text: string }) {
  return (
    <div className="card" style={{ maxWidth: 640 }}>
      <h2>{title}</h2>
      <span className="muted">{text}</span>
    </div>
  );
}

function NewExperiment() {
  const nav = useNavigate();
  const [name, setName] = useState("");
  const [strain, setStrain] = useState("");
  const [reactor, setReactor] = useState("R1");
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="card" style={{ maxWidth: 640 }}>
      <h2>New experiment</h2>
      <span className="small muted">Quick form for testing; the full planning screen (profile, media, buffer, parallel run) comes next.</span>
      <label className="field">Experiment name<input value={name} onChange={(e) => setName(e.target.value)} /></label>
      <label className="field">Strain<input value={strain} onChange={(e) => setStrain(e.target.value)} style={{ fontFamily: "var(--font-mono)" }} /></label>
      <label className="field">Bioreactor
        <select value={reactor} onChange={(e) => setReactor(e.target.value)}><option>R1</option><option>R2</option></select>
      </label>
      {err && <div className="banner alert">{err}</div>}
      <div className="row"><button className="btn filled" disabled={!name.trim()} onClick={async () => {
        try { const e = await api.createExperiment({ name, strain, reactor_id: reactor }); nav(`/experiments/${e.id}`); }
        catch (x) { setErr((x as Error).message); }
      }}>Create (Planned)</button></div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Shell>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/experiments" element={<Navigate to="/" replace />} />
          <Route path="/experiments/new" element={<NewExperiment />} />
          <Route path="/experiments/:id" element={<ExperimentPage />} />
          <Route path="/analysis/*" element={<ComingNext title="Analysis" text="Experiment analysis & export and analysis sessions are the next screens to build." />} />
          <Route path="/profiles/*" element={<ComingNext title="Controller profiles" text="Coming with the planning screen." />} />
          <Route path="/settings" element={<ComingNext title="Settings" text="Coming after planning and analysis." />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Shell>
    </BrowserRouter>
  </StrictMode>,
);
