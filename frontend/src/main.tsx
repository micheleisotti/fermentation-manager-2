import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
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
import { Planning } from "./pages/Planning";
import { Profiles } from "./pages/Profiles";

function ComingNext({ title, text }: { title: string; text: string }) {
  return (
    <div className="card" style={{ maxWidth: 640 }}>
      <h2>{title}</h2>
      <span className="muted">{text}</span>
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
          <Route path="/experiments/new" element={<Planning />} />
          <Route path="/experiments/:id" element={<ExperimentPage />} />
          <Route path="/analysis/*" element={<ComingNext title="Analysis" text="Experiment analysis & export and analysis sessions are the next screens to build." />} />
          <Route path="/profiles/*" element={<Profiles />} />
          <Route path="/settings" element={<ComingNext title="Settings" text="Coming after planning and analysis." />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Shell>
    </BrowserRouter>
  </StrictMode>,
);
