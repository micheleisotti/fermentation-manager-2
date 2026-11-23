import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { api, type EventItem, type LiveReactor } from "../api";
import { usePolling } from "../hooks";
import { Icon } from "./ui";
import { UserButton, useCan } from "./UserGate";
import { ConnectionBanner } from "./ConnectionBanner";
import wordmark from "../assets/wordmark.svg";
import wordmarkLight from "../assets/wordmark-light.svg";

const NAV = [
  { to: "/", label: "Home", icon: "home", end: true },
  { to: "/experiments", label: "Experiments", icon: "flask" },
  { to: "/analysis", label: "Analysis", icon: "chart" },
  { to: "/library", label: "Library", icon: "list" },
  { to: "/settings", label: "Settings", icon: "gear" },
];

const NEW_ITEMS = [
  { label: "Experiment", hint: "Plan a new fermentation", icon: "flask", to: "/experiments/new" },
  { label: "Experiment from template", hint: "Start from a saved plan", icon: "list", to: "/experiments/new?template=pick" },
  { label: "Analysis session", hint: "Compare completed experiments", icon: "chart", to: "/analysis/new" },
  { label: "Controller profile", hint: "Named set of controller values", icon: "sliders", to: "/library?tab=profiles" },
  { label: "Medium", hint: "Add to the Library", icon: "flask", to: "/library?tab=media" },
  { label: "Buffer / base", hint: "Add to the Library (with concentration)", icon: "drop", to: "/library?tab=buffers" },
  { label: "Manual variable", hint: "e.g. residual sugar (Library)", icon: "chart", to: "/library?tab=variables" },
];

/** Logo: dark wordmark on light themes, light one on Dim and Dark (switched in CSS). */
export function Wordmark({ height = 26 }: { height?: number }) {
  return (
    <span className="wordmark" style={{ height }}>
      <img className="wm-dark" src={wordmark} alt="Fermentation Manager 2" style={{ height }} />
      <img className="wm-light" src={wordmarkLight} alt="" aria-hidden="true" style={{ height }} />
    </span>
  );
}

/** Where a reactor in the side list leads: a running experiment, otherwise the Controller page. */
export function reactorLink(r: LiveReactor) {
  return r.experiment ? `/experiments/${r.experiment.id}` : `/reactors/${r.reactor_id}`;
}
export const reactorName = (r: { reactor_id: string; name?: string }) => r.name || r.reactor_id;

// --- notification centre -------------------------------------------------------------------
const SEEN_KEY = "fm2.notificationsSeen";
const getSeen = () => { try { return localStorage.getItem(SEEN_KEY) ?? ""; } catch { return ""; } };
const setSeenStore = (v: string) => { try { localStorage.setItem(SEEN_KEY, v); } catch { /* unavailable */ } };

function useNotifications() {
  const ev = usePolling(api.recentEvents, 20000);
  const [seen, setSeen] = useState(getSeen);
  const list = ev.data ?? [];
  const unread = list.filter((e) => e.severity === "alert" && e.at > seen).length;
  const markRead = () => { const v = list[0]?.at ?? new Date().toISOString(); setSeen(v); setSeenStore(v); };
  return { list, unread, markRead, seen };
}

function NotificationPanel({ list, seen, onClose, onRead }: { list: EventItem[]; seen: string; onClose: () => void; onRead: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) onClose(); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    const t = window.setTimeout(() => document.addEventListener("mousedown", close), 0);
    document.addEventListener("keydown", esc);
    return () => { window.clearTimeout(t); document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [onClose]);
  return (
    <div className="notif-panel" ref={ref} role="dialog" aria-label="Notifications">
      <div className="row between" style={{ padding: "12px 16px 8px" }}>
        <h3>Notifications</h3>
        <button className="btn text small" onClick={onRead}>Mark all read</button>
      </div>
      {list.length === 0 ? <div className="small muted" style={{ padding: "8px 16px 16px" }}>No notifications.</div> : (
        <div className="notif-list">
          {list.slice(0, 40).map((e) => (
            <Link key={e.id} className={`notif ${e.severity} ${e.at > seen ? "unread" : ""}`}
              to={e.experiment_id ? `/experiments/${e.experiment_id}` : `/reactors/${e.reactor_id}`} onClick={onClose}>
              <Icon name={e.severity === "alert" ? "warn" : e.severity === "good" ? "check" : "info"} size={18}
                color={e.severity === "alert" ? "#b30020" : e.severity === "good" ? "#1f7a63" : "#54565a"} />
              <span className="stack" style={{ gap: 2 }}>
                <span className="small">{e.message}</span>
                <span className="xs muted">{e.reactor_id} · {new Date(e.at).toLocaleString([], { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</span>
              </span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

function BellButton({ n, onClick, label = true }: { n: number; onClick: () => void; label?: boolean }) {
  return (
    <button className="bell-btn" onClick={onClick} aria-label={`Notifications${n ? `, ${n} unread` : ""}`}>
      <span className="bell-ico"><Icon name="bell" size={20} />{n > 0 && <span className="badge">{n > 9 ? "9+" : n}</span>}</span>
      {label && <span className="small">Notifications</span>}
    </button>
  );
}

function NewMenu({ wide }: { wide: boolean }) {
  const [open, setOpen] = useState(false);
  const nav = useNavigate();
  const can = useCan();
  const menuRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!menuRef.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [open]);
  if (!can("operator")) return null;
  return (
    <div className={wide ? "new-wide" : "fab-wrap"} ref={menuRef}>
      {wide ? (
        <button className="btn filled new-btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
          <Icon name="plus" size={20} width={2} />New
        </button>
      ) : (
        <>
          <button className="fab" aria-label="New" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
            <Icon name="plus" size={24} width={2} />
          </button>
          <span className="xs" style={{ fontWeight: 600 }}>New</span>
        </>
      )}
      {open && (
        <div className="menu" role="menu" aria-label="Create new">
          {NEW_ITEMS.map((m) => (
            <button key={m.label} role="menuitem" onClick={() => { setOpen(false); nav(m.to); }}>
              <Icon name={m.icon} color="#54565a" />
              <span style={{ display: "flex", flexDirection: "column" }}>{m.label}<span className="xs muted">{m.hint}</span></span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function SideReactors({ live }: { live: LiveReactor[] | undefined }) {
  if (!live || live.length === 0) return null;
  return (
    <div className="side-reactors">
      <span className="kicker" style={{ padding: "0 12px" }}>Bioreactors</span>
      {live.map((r) => {
        const state = !r.online ? "bad" : r.experiment?.status === "fermenting" ? "ok" : r.experiment ? "prep" : "idle";
        return (
          <NavLink key={r.reactor_id} to={reactorLink(r)} className={({ isActive }) => `side-reactor ${isActive ? "active" : ""}`}
            title={!r.online ? `Controller not reachable${r.error ? `: ${r.error}` : ""}` : undefined}>
            <span className={`dot ${state}`} />
            <span className="stack" style={{ gap: 0, minWidth: 0 }}>
              <span className="small side-name">{reactorName(r)}</span>
              <span className="xs muted side-sub">{!r.online ? "Not reachable" : r.experiment
                ? `${r.experiment.code} · ${r.experiment.status === "fermenting" ? "recording" : "preparing"}` : "Idle"}</span>
            </span>
          </NavLink>
        );
      })}
    </div>
  );
}

function ServerStatus() {
  const sys = usePolling(api.systemStatus, 30000);
  const down = !!sys.error;
  const remote = sys.data?.remote.state;
  return (
    <div className="side-status xs muted">
      <span title={sys.data ? `${sys.data.server.host} · v${sys.data.server.version}` : ""}>
        <span className={`dot ${down ? "bad" : sys.data ? "ok" : ""}`} />Server</span>
      <span title={sys.data?.remote.detail ?? sys.data?.remote.name ?? ""}>
        <span className={`dot ${down ? "" : remote === "available" ? "ok" : remote === "unknown" ? "idle" : "bad"}`} />
        Remote{remote === "available" ? "" : remote === "unknown" ? " ?" : " off"}</span>
    </div>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const live = usePolling(api.live, 10000);
  const notif = useNotifications();
  const [panel, setPanel] = useState(false);
  const openPanel = () => setPanel(true);
  const closePanel = () => setPanel(false);
  const read = () => { notif.markRead(); };

  return (
    <div className="app">
      {/* wide screens: side column with logo, navigation, bioreactors, notifications, account */}
      <aside className="sidebar" aria-label="Main navigation">
        <Link to="/" className="side-logo" aria-label="Home"><Wordmark height={24} /></Link>
        <ServerStatus />
        <NewMenu wide />
        <nav className="side-nav">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => (isActive ? "active" : "")}>
              <Icon name={n.icon} size={20} />{n.label}
            </NavLink>
          ))}
        </nav>
        <SideReactors live={live.data} />
        <div className="side-foot">
          <BellButton n={notif.unread} onClick={openPanel} />
          <UserButton wide />
        </div>
      </aside>

      {/* tablets: compact rail; phones: bottom bar (CSS) */}
      <nav className="rail" aria-label="Main navigation">
        <NewMenu wide={false} />
        {NAV.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => (isActive ? "active" : "")}>
            <span className="ind"><Icon name={n.icon} /></span>{n.label}
          </NavLink>
        ))}
        <div className="rail-bell"><BellButton n={notif.unread} onClick={openPanel} label={false} /></div>
        <UserButton />
      </nav>

      <main className="main">
        <div className="topbar">
          <Link to="/" aria-label="Home"><Wordmark height={20} /></Link>
          <BellButton n={notif.unread} onClick={openPanel} label={false} />
        </div>
        <ConnectionBanner />
        {children}
      </main>
      {panel && <NotificationPanel list={notif.list} seen={notif.seen} onClose={closePanel} onRead={read} />}
    </div>
  );
}
