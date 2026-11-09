import { useEffect, useState } from "react";
import { onConnection } from "../api";
import { Icon } from "./ui";

/** Shown when the app cannot reach the lab PC: values on screen are not live any more. */
export function ConnectionBanner() {
  const [st, setSt] = useState({ online: true, lastOk: Date.now() });
  const [, tick] = useState(0);
  useEffect(() => onConnection((online, lastOk) => setSt({ online, lastOk })), []);
  useEffect(() => {
    if (st.online) return;
    const t = window.setInterval(() => tick((x) => x + 1), 15000);
    return () => window.clearInterval(t);
  }, [st.online]);
  useEffect(() => {   // back from the background (phone screen on again): refresh right away
    const f = () => { if (document.visibilityState === "visible") window.dispatchEvent(new Event("fm2-refresh")); };
    document.addEventListener("visibilitychange", f);
    return () => document.removeEventListener("visibilitychange", f);
  }, []);
  if (st.online) return null;
  const min = Math.max(0, Math.round((Date.now() - st.lastOk) / 60000));
  return (
    <div className="banner alert conn-banner" role="alert">
      <Icon name="warn" color="#b30020" size={20} />
      <span><b>No connection to the lab PC.</b> Values shown are from {min < 1 ? "less than a minute" : `${min} min`} ago; retrying automatically.
        Check Tailscale on this device and that the lab PC is on. Recording on the PC is not affected.</span>
    </div>
  );
}
