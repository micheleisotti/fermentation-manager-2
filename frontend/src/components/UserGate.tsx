/**
 * Who is using the app: a sign-in screen (user list, optional PIN) before anything else.
 * The chosen user is remembered on this browser/device until "Switch user".
 * Identity only — it signs experiments, measurements and commands; there are no permission levels.
 */
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, setToken, type UserInfo } from "../api";
import { Icon } from "./ui";

interface Ctx { user: UserInfo; switchUser: () => void; setUser: (u: UserInfo, token?: string | null) => void }
const UserCtx = createContext<Ctx | null>(null);
export const useUser = () => useContext(UserCtx);

export function UserGate({ children }: { children: ReactNode }) {
  const [user, setUserState] = useState<UserInfo | null | undefined>(undefined);
  useEffect(() => { api.me().then((r) => setUserState(r.user)).catch(() => setUserState(null)); }, []);
  const setUser = (u: UserInfo, token?: string | null) => { if (token) setToken(token); setUserState(u); };
  const switchUser = () => { setToken(null); setUserState(null); };
  if (user === undefined) return <div className="muted" style={{ padding: 24 }}>Loading…</div>;
  if (user === null) return <SignIn onDone={(u, t) => setUser(u, t)} />;
  return <UserCtx.Provider value={{ user, switchUser, setUser }}>{children}</UserCtx.Provider>;
}

export function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("") || "?";
}

function SignIn({ onDone }: { onDone: (u: UserInfo, token: string) => void }) {
  const [users, setUsers] = useState<UserInfo[] | null>(null);
  const [pick, setPick] = useState<UserInfo | null>(null);
  const [pin, setPin] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [newPin, setNewPin] = useState("");
  useEffect(() => { api.users().then(setUsers).catch((e) => setErr((e as Error).message)); }, []);

  const go = async (u: UserInfo, p?: string) => {
    setErr(null);
    try { const r = await api.login(u.id, p); onDone(r.user, r.token); }
    catch (e) { setErr((e as Error).message); setPin(""); }
  };
  const create = async () => {
    setErr(null);
    try { const r = await api.createUser(name.trim(), newPin || undefined); onDone(r.user, r.token); }
    catch (e) { setErr((e as Error).message); }
  };

  return (
    <div className="signin">
      <div className="card signin-card">
        <div className="stack" style={{ gap: 4, alignItems: "center", textAlign: "center" }}>
          <h1 style={{ fontSize: 24 }}>Fermentation Manager</h1>
          <span className="muted">{pick ? `Enter the PIN for ${pick.name}` : adding ? "New user" : "Who is using the software?"}</span>
        </div>

        {pick ? (
          <form className="stack" style={{ gap: 12, alignItems: "center" }} onSubmit={(e) => { e.preventDefault(); void go(pick, pin); }}>
            <span className="avatar big">{initials(pick.name)}</span>
            <input className="pin-input mono" type="password" inputMode="numeric" autoComplete="off" autoFocus maxLength={8}
              value={pin} onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))} aria-label="PIN" />
            <div className="row" style={{ gap: 8, justifyContent: "center" }}>
              <button type="button" className="btn text" onClick={() => { setPick(null); setPin(""); setErr(null); }}>Back</button>
              <button className="btn filled" disabled={pin.length < 4}>Sign in</button>
            </div>
          </form>
        ) : adding ? (
          <form className="stack" style={{ gap: 12 }} onSubmit={(e) => { e.preventDefault(); void create(); }}>
            <label className="field">Name<input autoFocus value={name} onChange={(e) => setName(e.target.value)} maxLength={60} /></label>
            <label className="field">PIN (optional, 4–8 digits)
              <input className="mono" type="password" inputMode="numeric" autoComplete="new-password" maxLength={8}
                value={newPin} onChange={(e) => setNewPin(e.target.value.replace(/\D/g, ""))} /></label>
            <span className="xs muted">With a PIN, nobody else can sign in with your name. Without, anyone can pick it.</span>
            <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}>
              <button type="button" className="btn text" onClick={() => { setAdding(false); setErr(null); }}>Back</button>
              <button className="btn filled" disabled={!name.trim() || (newPin.length > 0 && newPin.length < 4)}>Create and sign in</button>
            </div>
          </form>
        ) : (
          <>
            <div className="user-grid">
              {(users ?? []).map((u) => (
                <button key={u.id} className={`user-tile ${u.guest ? "guest" : ""}`} onClick={() => (u.has_pin ? setPick(u) : void go(u))}>
                  <span className="avatar">{u.guest ? <Icon name="home" size={18} /> : initials(u.name)}</span>
                  <span className="user-name">{u.name}</span>
                  {u.has_pin && <span className="xs muted">PIN</span>}
                </button>
              ))}
            </div>
            <button className="btn outlined" style={{ alignSelf: "center" }} onClick={() => setAdding(true)}><Icon name="plus" size={18} />New user</button>
          </>
        )}
        {err && <div className="banner alert small" role="alert">{err}</div>}
      </div>
    </div>
  );
}

/** Small user button for the navigation rail: shows who is signed in, opens "Switch user". */
export function UserButton() {
  const ctx = useUser();
  const [open, setOpen] = useState(false);
  if (!ctx) return null;
  return (
    <div className="user-btn-wrap">
      <button className="user-btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)} title={`Signed in as ${ctx.user.name}`}>
        <span className="avatar">{ctx.user.guest ? "G" : initials(ctx.user.name)}</span>
        <span className="xs user-btn-name">{ctx.user.name}</span>
      </button>
      {open && (
        <div className="menu user-menu" role="menu">
          <div className="small muted" style={{ padding: "8px 16px" }}>Signed in as <b>{ctx.user.name}</b></div>
          <button role="menuitem" onClick={() => { setOpen(false); ctx.switchUser(); }}><Icon name="back" color="#54565a" />Switch user</button>
        </div>
      )}
    </div>
  );
}
