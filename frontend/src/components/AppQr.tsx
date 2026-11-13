import { useEffect, useState } from "react";
import QRCode from "qrcode";
import { api } from "../api";
import { usePolling } from "../hooks";

/**
 * QR code to open FM2 on a phone through the private network (Tailscale). The address is the one saved in
 * Settings › Remote access, or one built from this PC's Tailscale name/address: https when "tailscale serve"
 * is set up (installable app), http on port 8000 otherwise.
 */
export function AppQr({ saved, canSave, onSave }: { saved?: string; canSave: boolean; onSave: (url: string) => void }) {
  const st = usePolling(api.systemStatus, 60000);
  const rem = st.data?.remote;
  const port = location.port || "8000";
  const options = [
    ...(saved ? [saved] : []),
    ...(rem?.name ? [`https://${rem.name}`, `http://${rem.name}:${port}`] : []),
    ...(rem?.ips ?? []).filter((ip) => !ip.includes(":")).map((ip) => `http://${ip}:${port}`),
    // the address this page is open at, when it is not the PC itself
    ...(!["localhost", "127.0.0.1", "[::1]"].includes(location.hostname) ? [location.origin] : []),
  ].filter((u, i, a) => a.indexOf(u) === i);
  const [url, setUrl] = useState("");
  const [svg, setSvg] = useState("");
  const chosen = url || options[0] || "";
  useEffect(() => {
    if (!chosen) { setSvg(""); return; }
    QRCode.toString(chosen, { type: "svg", margin: 1, errorCorrectionLevel: "M", color: { dark: "#22294d", light: "#ffffff" } })
      .then(setSvg).catch(() => setSvg(""));
  }, [chosen]);
  return (
    <div className="row" style={{ gap: 20, alignItems: "flex-start", flexWrap: "wrap" }}>
      {svg && <div style={{ width: 168, height: 168, background: "#fff", borderRadius: 12, padding: 6, flex: "0 0 auto" }}
        aria-label={`QR code for ${chosen}`} role="img" dangerouslySetInnerHTML={{ __html: svg }} />}
      <div className="stack" style={{ gap: 8, flex: "1 1 240px", minWidth: 0 }}>
        <span className="small">Scan with the phone camera to open Fermentation Manager. The phone must be connected to the same
          Tailscale network (Tailscale app on, signed in).</span>
        {options.length > 0 ? (
          <label className="field">Address
            <select value={chosen} onChange={(e) => setUrl(e.target.value)}>
              {options.map((o) => <option key={o} value={o}>{o}{o === saved ? " (saved)" : ""}</option>)}
            </select>
          </label>
        ) : <span className="small muted">{rem?.detail ?? "Tailscale address not known on this PC yet."}</span>}
        <label className="field">Or type it
          <input className="mono" placeholder="https://…" value={url} onChange={(e) => setUrl(e.target.value.trim())} />
        </label>
        <span className="xs muted">https://… works only after "tailscale serve" (enable-https.ps1); otherwise use the http address with port {port}.</span>
        {canSave && chosen && chosen !== saved && <button className="btn tonal" style={{ alignSelf: "flex-start" }} onClick={() => onSave(chosen)}>Set as default (then Save)</button>}
      </div>
    </div>
  );
}
