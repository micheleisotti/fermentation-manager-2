import { useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { Icon } from "./ui";

const NAV = [
  { to: "/", label: "Home", icon: "home", end: true },
  { to: "/experiments", label: "Experiments", icon: "flask" },
  { to: "/analysis", label: "Analysis", icon: "chart" },
  { to: "/profiles", label: "Profiles", icon: "list" },
  { to: "/settings", label: "Settings", icon: "gear" },
];

const NEW_ITEMS = [
  { label: "Experiment", hint: "Plan a new fermentation", icon: "flask", to: "/experiments/new" },
  { label: "Analysis session", hint: "Compare completed experiments", icon: "chart", to: "/analysis/new" },
  { label: "Controller profile", hint: "Named set of setpoints", icon: "sliders", to: "/profiles/new" },
  { label: "Medium", hint: "Add to the medium list", icon: "flask", to: "/settings#lists" },
  { label: "Buffer / base", hint: "Add to the buffer list", icon: "drop", to: "/settings#lists" },
];

export function Shell({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const nav = useNavigate();
  const menuRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!menuRef.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [open]);

  return (
    <div className="app">
      <nav className="rail" aria-label="Main navigation">
        <div className="fab-wrap" ref={menuRef}>
          <button className="fab" aria-label="New" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
            <Icon name="plus" size={24} width={2} />
          </button>
          <span className="xs" style={{ fontWeight: 600 }}>New</span>
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
        {NAV.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => (isActive ? "active" : "")}>
            <span className="ind"><Icon name={n.icon} /></span>{n.label}
          </NavLink>
        ))}
      </nav>
      <main className="main">{children}</main>
    </div>
  );
}
