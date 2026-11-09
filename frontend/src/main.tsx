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
import { UserGate } from "./components/UserGate";
import { Home } from "./pages/Home";
import { ExperimentPage } from "./pages/ExperimentPage";
import { Planning } from "./pages/Planning";
import { Library } from "./pages/Library";
import { ExperimentsList } from "./pages/ExperimentsList";
import { SettingsPage } from "./pages/SettingsPage";
import { AnalysisHome, ExperimentAnalysisPage } from "./pages/ExperimentAnalysis";
import { NewSession, SessionPage } from "./pages/AnalysisSession";

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
      <UserGate>
      <Shell>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/experiments" element={<ExperimentsList />} />
          <Route path="/experiments/new" element={<Planning />} />
          <Route path="/experiments/:id" element={<ExperimentPage />} />
          <Route path="/analysis" element={<AnalysisHome />} />
          <Route path="/analysis/experiments/:id" element={<ExperimentAnalysisPage />} />
          <Route path="/analysis/new" element={<NewSession />} />
          <Route path="/analysis/sessions/:id" element={<SessionPage />} />
          <Route path="/analysis/*" element={<Navigate to="/analysis" replace />} />
          <Route path="/library" element={<Library />} />
          <Route path="/profiles/*" element={<Navigate to="/library?tab=profiles" replace />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Shell>
      </UserGate>
    </BrowserRouter>
  </StrictMode>,
);

// Service worker: needed to install the app (only over HTTPS or on localhost). Caches nothing.
if ("serviceWorker" in navigator && (window.isSecureContext)) {
  navigator.serviceWorker.register("/sw.js").catch(() => undefined);
}
