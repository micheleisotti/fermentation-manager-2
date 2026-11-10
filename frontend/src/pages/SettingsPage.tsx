/** Settings (approved mockup "M3Settings"): bioreactors, remote access, notifications, data and backup, credits. */
import { useEffect, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { api, type AppSettings, type BackupStatus } from "../api";
import { usePolling } from "../hooks";
import { DataValue, Icon } from "../components/ui";
import { REACTOR_LABEL } from "./Home";
import { initials, useUser } from "../components/UserGate";
import { getTheme, setTheme, type Theme } from "../theme";

const SECTIONS = [
  { id: "users", label: "Users" },
  { id: "appearance", label: "Appearance" },
  { id: "bioreactors", label: "Bioreactors" },
  { id: "remote", label: "Remote access" },
  { id: "alerts", label: "Notifications" },
  { id: "backup", label: "Data and backup" },
  { id: "credits", label: "Credits and license" },
];

function Switch({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button role="switch" aria-checked={on} aria-label={label} className={`switch ${on ? "on" : ""}`} onClick={() => onChange(!on)}>
      <span />
    </button>
  );
}

function Section({ id, title, sub, children }: { id: string; title: string; sub?: ReactNode; children: ReactNode }) {
  return (
    <section id={id} className="card" aria-labelledby={`h-${id}`} style={{ padding: 24, gap: 16, scrollMarginTop: 16 }}>
      <div className="stack" style={{ gap: 2 }}>
        <h2 id={`h-${id}`} style={{ fontSize: 20, fontWeight: 500, margin: 0 }}>{title}</h2>
        {sub && <span className="small muted">{sub}</span>}
      </div>
      {children}
    </section>
  );
}

/** Edit a copy of one settings section; Save sends it, the server validates. */
function useSection<K extends keyof AppSettings>(all: AppSettings | undefined, key: K, refresh: () => void) {
  const [v, setV] = useState<AppSettings[K] | null>(null);
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const [dirty, setDirty] = useState(false);
  const [ver, setVer] = useState(0);          // bumps on save/discard so number inputs re-read their value
  useEffect(() => { if (all && !dirty) setV(structuredClone(all[key])); }, [all, key, dirty]);
  const edit = (f: (x: AppSettings[K]) => void) => { if (!v) return; const c = structuredClone(v); f(c); setV(c); setDirty(true); setMsg(null); };
  const save = async () => {
    if (!v) return;
    try { await api.putSettings(key, v); setDirty(false); setVer((x) => x + 1); setMsg({ kind: "good", text: "Saved — applied immediately." }); refresh(); }
    catch (x) { setMsg({ kind: "alert", text: (x as Error).message }); }
  };
  const footer = (
    <div className="row" style={{ gap: 12, alignItems: "center" }}>
      <button className="btn filled" disabled={!dirty} onClick={save}>Save</button>
      {dirty && <button className="btn text" onClick={() => { setDirty(false); setMsg(null); setVer((x) => x + 1); }}>Discard changes</button>}
      {msg && <span className={`banner ${msg.kind} small`} role="status" style={{ padding: "6px 12px" }}>{msg.text}</span>}
    </div>
  );
  return { v, edit, footer, ver, dirty };
}

const NUM = { style: { fontFamily: "var(--font-mono)", width: 96 }, inputMode: "decimal" as const };
const num = (s: string) => (s.trim() === "" ? NaN : Number(s.replace(",", ".")));

function TelegramChatFinder({ token, onPick, onClose }: { token: string; onPick: (id: string) => void; onClose: () => void }) {
  const [chats, setChats] = useState<{ id: string; name: string; type: string }[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const look = async () => {
    setErr(null); setChats(null);
    try { setChats((await api.telegramChats(token)).chats); } catch (x) { setErr((x as Error).message); }
  };
  useEffect(() => { void look(); }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="note" style={{ flex: "1 1 100%", flexDirection: "column", gap: 8 }}>
      <span><b>Find your chat id.</b> On your phone, open the chat with your bot and send it any message (e.g. “hi”), then press Look again.</span>
      {err && <span className="small" style={{ color: "var(--alert-text)" }}>{err}</span>}
      {chats && chats.length === 0 && <span className="small muted">No messages yet: write to the bot and look again.</span>}
      {chats && chats.length > 0 && (
        <div className="stack" style={{ gap: 6 }}>
          {chats.map((c) => (
            <button key={c.id} type="button" className="btn outlined" style={{ justifyContent: "space-between" }} onClick={() => onPick(c.id)}>
              <span>{c.name} <span className="muted small">· {c.type}</span></span><span className="mono">{c.id}</span>
            </button>
          ))}
        </div>
      )}
      <div className="row" style={{ gap: 8 }}>
        <button type="button" className="btn text" onClick={look}>Look again</button>
        <button type="button" className="btn text" onClick={onClose}>Close</button>
      </div>
    </div>
  );
}

function AppearancePicker() {
  const [t, setT] = useState<Theme>(getTheme());
  const opts: { k: Theme; label: string; help: string }[] = [
    { k: "light", label: "Light", help: "Light page and cards" },
    { k: "grey", label: "Grey", help: "Softer grey page (#DADFE1), white cards" },
    { k: "dim", label: "Dim", help: "Dark teal page, white cards and charts" },
    { k: "dark", label: "Dark", help: "Dark page and cards; charts on light panels" },
  ];
  return (
    <div className="theme-grid" role="radiogroup" aria-label="Appearance">
      {opts.map((o) => (
        <label key={o.k} className={`choice ${t === o.k ? "on" : ""}`}>
          <input type="radio" name="theme" checked={t === o.k} onChange={() => { setT(o.k); setTheme(o.k); }} />
          <span className={`theme-swatch ${o.k}`} aria-hidden="true"><span /></span>
          <span className="stack" style={{ gap: 2 }}><b>{o.label}</b><span className="xs muted">{o.help}</span></span>
        </label>
      ))}
    </div>
  );
}

function UsersSection() {
  const ctx = useUser();
  const list = usePolling(() => api.users(true), 600000);
  const [pin, setPin] = useState("");
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const [name, setName] = useState("");
  if (!ctx) return null;
  const me = ctx.user;
  const run = async (f: () => Promise<string | void>) => {
    setMsg(null);
    try { const t = await f(); if (t) setMsg({ kind: "good", text: t }); list.refresh(); }
    catch (x) { setMsg({ kind: "alert", text: (x as Error).message }); }
  };
  return (
    <Section id="users" title="Users"
      sub="Who is using the software. The signed-in user signs experiments, measurements and setpoint changes. No permission levels: a PIN only stops others from signing with your name.">
      <div className="row" style={{ gap: 12, alignItems: "center" }}>
        <span className="avatar">{initials(me.name)}</span>
        <span>Signed in as <b>{me.name}</b> on this device{me.has_pin ? " (with PIN)" : ""}.</span>
        <button className="btn outlined" onClick={ctx.switchUser}>Switch user</button>
      </div>
      {!me.guest && (
        <div className="row" style={{ gap: 8, alignItems: "flex-end" }}>
          <label className="field" style={{ width: 200 }}>{me.has_pin ? "New PIN" : "Set a PIN"} (4–8 digits)
            <input className="mono" type="password" inputMode="numeric" autoComplete="new-password" maxLength={8} value={pin}
              onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))} /></label>
          <button className="btn tonal" disabled={pin.length < 4} onClick={() => run(async () => {
            const r = await api.updateUser(me.id, { pin }); ctx.setUser(r.user, r.token); setPin("");
            return "PIN saved. Other devices signed in as you must sign in again.";
          })}>Save PIN</button>
          {me.has_pin && <button className="btn text" onClick={() => run(async () => {
            const r = await api.updateUser(me.id, { pin: "" }); ctx.setUser(r.user, r.token); return "PIN removed.";
          })}>Remove PIN</button>}
        </div>
      )}
      <div className="table-wrap">
        <table className="data">
          <thead><tr><th>User</th><th>PIN</th><th>Status</th><th /></tr></thead>
          <tbody>{(list.data ?? []).map((u) => (
            <tr key={u.id}>
              <td>{u.name}{u.id === me.id && <span className="muted"> (you)</span>}</td>
              <td>{u.guest ? "—" : u.has_pin ? "yes" : "no"}</td>
              <td>{u.archived ? "archived" : "active"}</td>
              <td className="num">{!u.guest && (!u.has_pin || u.id === me.id) && u.id !== me.id && (
                <button className="btn text" onClick={() => run(async () => { await api.updateUser(u.id, { archived: !u.archived }); })}>
                  {u.archived ? "Restore" : "Archive"}</button>)}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      <div className="row" style={{ gap: 8, flexWrap: "nowrap", maxWidth: 520 }}>
        <label className="field" style={{ flex: "1 1 auto" }}><span className="sr-only">New user</span>
          <input value={name} placeholder="Add a user (name)" onChange={(e) => setName(e.target.value)} maxLength={60} /></label>
        <button className="btn tonal" disabled={!name.trim()} onClick={() => run(async () => {
          await api.createUser(name.trim()); setName(""); return "User added: they can set their own PIN after signing in.";
        })}>Add</button>
      </div>
      <span className="xs muted">Forgotten PIN: on the lab PC (admin PowerShell) run
        <span className="mono"> .venv\Scripts\python -m fm2.users reset-pin "Name" --data-dir C:\ProgramData\FermentationManager\data</span></span>
      {msg && <div className={`banner ${msg.kind} small`} role="status">{msg.text}</div>}
    </Section>
  );
}

export function SettingsPage() {
  const all = usePolling(api.settings, 600000);
  const sys = usePolling(api.system, 15000);
  const backup = usePolling(api.backupStatus, 15000);

  const remote = useSection(all.data, "remote", all.refresh);
  const alerts = useSection(all.data, "alerts", all.refresh);
  const notif = useSection(all.data, "notifications", all.refresh);
  const bk = useSection(all.data, "backup", all.refresh);
  const [testMsg, setTestMsg] = useState<{ kind: string; text: string } | null>(null);
  const [bkMsg, setBkMsg] = useState<{ kind: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const loaded = !!all.data;
  const hash = useLocation().hash;
  useEffect(() => {   // "New → Medium/Buffer" links to #lists: scroll there once the page is rendered
    if (loaded && hash) document.getElementById(hash.slice(1))?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [loaded, hash]);

  const [finding, setFinding] = useState(false);
  if (all.error) return <div className="banner alert">Settings not available: {all.error}</div>;
  if (!all.data || !remote.v || !alerts.v || !notif.v || !bk.v) return <div className="muted">Loading…</div>;
  const r = remote.v, a = alerts.v, n = notif.v, b = bk.v;
  const local = sys.data?.client_local ?? false;
  const st: BackupStatus | undefined = backup.data;

  const CAPS: [string, string][] = [["read_values", "Read values"], ["read_limits", "Read process limits"],
    ["write_setpoints", "Write setpoints"], ["write_limits", "Write process limits"], ["write_ph_control", "Load pH control (planning)"]];
  const RULES: { k: string; t: string; d: string }[] = [
    { k: "limits", t: "Outside process limits", d: `At Start if already outside; during the run after ${a.out_of_limits_after_min} min outside. Once per excursion, re-armed after ${a.rearm_min} min back within limits` },
    { k: "setpoint_ph", t: "Setpoint reached — pH", d: `Within ±${a.ph_tolerance} for ${a.hold_min} min, once per setpoint` },
    { k: "setpoint_t", t: "Setpoint reached — temperature", d: `Within ±${a.temp_tolerance} °C for ${a.hold_min} min, once per setpoint` },
    { k: "silent", t: "Controller not responding", d: `No reading for ${a.silent_after_min} min, and when readings resume` },
    { k: "external", t: "Setpoint changed on the controller", d: "A change not made from this software" },
    { k: "finished", t: "Fermentation probably finished", d: `Base rate below ${a.finished_fraction_pct} % of its peak for ${a.finished_hold_min} min` },
  ];
  const ALERT_FIELDS: { k: keyof typeof a; label: string; unit: string }[] = [
    { k: "out_of_limits_after_min", label: "Out of limits for", unit: "min" },
    { k: "rearm_min", label: "Re-arm after back inside for", unit: "min" },
    { k: "ph_tolerance", label: "pH setpoint tolerance", unit: "±pH" },
    { k: "temp_tolerance", label: "T setpoint tolerance", unit: "±°C" },
    { k: "hold_min", label: "Within tolerance for", unit: "min" },
    { k: "silent_after_min", label: "Controller silent after", unit: "min" },
    { k: "finished_fraction_pct", label: "Finished: base rate below", unit: "% of peak" },
    { k: "finished_hold_min", label: "Finished: for", unit: "min" },
  ];

  return (
    <>
      <h1>Settings</h1>
      <div className="row" style={{ gap: 24, alignItems: "flex-start", flexWrap: "wrap" }}>
        <nav aria-label="Settings sections" className="settings-nav">
          {SECTIONS.map((x) => <a key={x.id} href={`#${x.id}`}>{x.label}</a>)}
        </nav>
        <div className="stack" style={{ flex: "999 1 640px", minWidth: 0, gap: 24 }}>

          <UsersSection />

          <Section id="appearance" title="Appearance" sub="Saved on this device only: each phone or PC keeps its own choice.">
            <AppearancePicker />
          </Section>

          <Section id="bioreactors" title="Bioreactors"
            sub={<>Read from the service configuration (address and driver change with a service restart). Sampling every <span className="mono">{sys.data?.sampling_interval_s ?? "—"} s</span>.</>}>
            <div className="grid-cards">
              {(sys.data?.reactors ?? []).map((x) => (
                <div key={x.reactor_id} className="stack" style={{ border: "1px solid var(--outline-variant)", borderRadius: 12, padding: 16, gap: 10 }}>
                  <div className="row between">
                    <span className="row" style={{ gap: 10, fontSize: 16, fontWeight: 500 }}>
                      <span className="pill mono" style={{ background: "#003366", color: "#fff", borderColor: "transparent", height: 28 }}>{x.reactor_id}</span>
                      Bioreactor {REACTOR_LABEL[x.reactor_id] ?? x.reactor_id}
                    </span>
                    <span className="row small" style={{ gap: 6 }}><span className={`dot ${x.online ? "ok" : "bad"}`} />{x.online ? "Online" : "Offline"}</span>
                  </div>
                  <span className="small">Driver <b className="mono">{x.driver}</b>{x.address && <> · address <b className="mono">{x.address}</b></>}</span>
                  <div className="stack" style={{ gap: 4 }}>
                    {CAPS.map(([k, label]) => (
                      <span key={k} className="row small" style={{ gap: 8 }}>
                        <Icon name={x.capabilities[k] ? "check" : "close"} size={16} color={x.capabilities[k] ? "#1f7a63" : "#9a9ca0"} width={2.2} />{label}
                      </span>
                    ))}
                    <span className="row small muted" style={{ gap: 8 }}><Icon name="close" size={16} color="#9a9ca0" width={2.2} />Actuators, dose counters, calibration, interlocks: controller only</span>
                  </div>
                  {x.address && <a className="btn text" style={{ alignSelf: "flex-start", paddingLeft: 0 }} href={`http://${x.address}`} target="_blank" rel="noreferrer">Open controller web UI</a>}
                </div>
              ))}
            </div>
          </Section>

          <Section id="remote" title="Remote access" sub="Through the private network (Tailscale): nothing is exposed to the public internet.">
            {!local && <div className="banner warn small" role="note"><Icon name="warn" size={18} /><span>Read only from this device: this section can be changed only on the lab PC.</span></div>}
            <fieldset className="plain" disabled={!local}>
            <div className="row between" style={{ flexWrap: "nowrap", gap: 16 }}>
              <span className="stack" style={{ gap: 0 }}>Allow setpoint and limit changes from remote devices
                <span className="xs muted">Always with confirmation, read-back from the controller and a log entry</span></span>
              <Switch on={r.allow_remote_setpoints} label="Allow remote changes" onChange={(v) => remote.edit((x) => { x.allow_remote_setpoints = v; })} />
            </div>
            <div className="table-wrap">
              <table className="data" style={{ minWidth: 460 }}>
                <caption className="small muted" style={{ textAlign: "left", paddingBottom: 6 }}>Allowed range for setpoint changes from this software (local and remote)</caption>
                <thead><tr><th>Parameter</th><th>Min</th><th>Max</th><th>Max step per change</th></tr></thead>
                <tbody>
                  {([["ph", "pH"], ["temperature", "Temperature (°C)"], ["stirrer", "Stirrer (rpm)"]] as const).map(([k, label]) => (
                    <tr key={k}>
                      <td>{label}</td>
                      {(["min", "max", "max_step"] as const).map((f) => (
                        <td key={f}><input className="cell-input" {...NUM} aria-label={`${label} ${f}`} defaultValue={r.ranges[k][f]}
                          key={`${k}-${f}-${remote.ver}`}
                          onChange={(e) => remote.edit((x) => { x.ranges[k][f] = num(e.target.value); })} /></td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <span className="xs muted">The controller's own sensor ranges always apply on top.</span>
            {local && remote.footer}
            </fieldset>
          </Section>

          <Section id="alerts" title="Notifications" sub="Every event is always stored and shown in the app; here you choose which ones are also sent to your phone.">
            <div className="stack" style={{ gap: 6 }}>
              <span className="xs muted" id="ch-label">Channel</span>
              <div className="segmented" role="radiogroup" aria-labelledby="ch-label" style={{ height: 40, maxWidth: 560 }}>
                {([["none", "Off"], ["ntfy", "ntfy"], ["telegram", "Telegram"]] as const).map(([k, label]) => (
                  <button key={k} role="radio" aria-checked={n.channel === k} className={n.channel === k ? "on" : ""} style={{ flex: "1 1 0" }}
                    onClick={() => notif.edit((x) => { x.channel = k; })}>{label}</button>
                ))}
                <button role="radio" aria-checked={false} disabled style={{ flex: "1 1 0", opacity: 0.5 }} title="Needs HTTPS on the private network: later">App push · later</button>
              </div>
            </div>
            {n.channel === "ntfy" && (
              <div className="row" style={{ gap: 12 }}>
                <label className="field" style={{ flex: "2 1 220px" }}>Server<input value={n.ntfy_server} onChange={(e) => notif.edit((x) => { x.ntfy_server = e.target.value; })} /></label>
                <label className="field" style={{ flex: "2 1 220px" }}>Topic (hard to guess)<input className="mono" value={n.ntfy_topic} placeholder="e.g. fm2-lab-7q3k9" onChange={(e) => notif.edit((x) => { x.ntfy_topic = e.target.value; })} /></label>
                <label className="field" style={{ flex: "1 1 160px" }}>Access token (optional)<input type="password" value={n.ntfy_token} onChange={(e) => notif.edit((x) => { x.ntfy_token = e.target.value; })} /></label>
                <span className="xs muted" style={{ flexBasis: "100%" }}>Install the ntfy app on the phone and subscribe to the same topic. On the public server anyone who knows the topic can read it: use a long random name.</span>
              </div>
            )}
            {n.channel === "telegram" && (
              <div className="row" style={{ gap: 12 }}>
                <label className="field" style={{ flex: "2 1 260px" }}>Token of your bot (from @BotFather)<input type="password" value={n.telegram_token} placeholder="123456789:AAH…" onChange={(e) => notif.edit((x) => { x.telegram_token = e.target.value; })} /></label>
                <label className="field" style={{ flex: "1 1 200px" }}>Your chat id (your Telegram account, not the bot)
                  <span className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
                    <input className="mono" value={n.telegram_chat_id} placeholder="e.g. 987654321" onChange={(e) => notif.edit((x) => { x.telegram_chat_id = e.target.value; })} />
                    <button type="button" className="btn tonal" style={{ whiteSpace: "nowrap" }} onClick={() => setFinding(true)}>Find it</button>
                  </span></label>
                {finding && <TelegramChatFinder token={n.telegram_token} onPick={(id) => { notif.edit((x) => { x.telegram_chat_id = id; }); setFinding(false); }}
                  onClose={() => setFinding(false)} />}
                <span className="xs muted" style={{ flexBasis: "100%" }}>1) Create a bot with @BotFather and paste its token. 2) Send your bot any message, then press “Find it” to fill in your own chat id. The token is stored on this PC and never shown again.</span>
              </div>
            )}
            <div className="stack" style={{ gap: 0 }}>
              {RULES.map((x) => (
                <div key={x.k} className="row between" style={{ flexWrap: "nowrap", gap: 16, minHeight: 64, borderBottom: "1px solid var(--divider)" }}>
                  <span className="stack" style={{ gap: 0 }}>{x.t}<span className="xs muted">{x.d}</span></span>
                  <Switch on={n.rules[x.k] ?? true} label={x.t} onChange={(v) => notif.edit((y) => { y.rules[x.k] = v; })} />
                </div>
              ))}
            </div>
            <div className="row" style={{ gap: 12, alignItems: "center" }}>
              {notif.footer}
              <button className="btn outlined" disabled={n.channel === "none"} onClick={async () => {
                setTestMsg(null);
                if (notif.dirty) { setTestMsg({ kind: "warn", text: "Save the changes first: the test uses the saved settings." }); return; }
                try { await api.testNotification(); setTestMsg({ kind: "good", text: "Test sent: check the phone." }); }
                catch (x) { setTestMsg({ kind: "alert", text: (x as Error).message }); }
              }}>Send test notification</button>
              {testMsg && <span className={`banner ${testMsg.kind} small`} style={{ padding: "6px 12px" }}>{testMsg.text}</span>}
            </div>
            <details>
              <summary className="small" style={{ cursor: "pointer", color: "#003366", fontWeight: 500 }}>Alert timing and tolerances</summary>
              <div className="row" style={{ gap: 12, marginTop: 12 }}>
                {ALERT_FIELDS.map((f) => (
                  <label key={f.k} className="field" style={{ flex: "1 1 200px" }}>{f.label} ({f.unit})
                    <input {...NUM} style={{ ...NUM.style, width: "100%" }} defaultValue={a[f.k]} key={`${f.k}-${alerts.ver}`}
                      onChange={(e) => alerts.edit((x) => { (x as Record<string, number>)[f.k] = num(e.target.value); })} />
                  </label>
                ))}
              </div>
              <div style={{ marginTop: 12 }}>{alerts.footer}</div>
            </details>
          </Section>

          <Section id="backup" title="Data and backup">
            <div className="dv-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))" }}>
              <DataValue value={st?.last_ok ? new Date(st.last_ok).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }) : "—"}
                label={st?.last_error ? "Last backup · FAILED" : st?.last_ok
                  ? `Last backup · OK · ${new Date(st.last_ok).toLocaleDateString([], { day: "numeric", month: "short" })}` : "No backup yet"}
                color={st?.last_error ? "#b30020" : "#1f7a63"} />
              <DataValue value={st?.db_bytes != null ? (st.db_bytes / 1e6).toFixed(1) : "—"} unit="MB" label="Local database" />
              <DataValue value={String(sys.data?.experiments ?? "—")} label="Experiments stored" />
              <DataValue value={String(st?.backups_present ?? 0)} label="Backups in the destination" />
            </div>
            {st?.last_error && <div className="banner alert" role="alert"><Icon name="warn" color="#b30020" size={20} /><span>{st.last_error}</span></div>}
            {!local && <div className="banner warn small" role="note"><Icon name="warn" size={18} /><span>Read only from this device: this section can be changed only on the lab PC.</span></div>}
            <fieldset className="plain" disabled={!local}>
            <div className="row" style={{ gap: 12 }}>
              <label className="field" style={{ flex: "2 1 260px" }}>Local data folder<input className="mono" value={sys.data?.data_dir ?? ""} readOnly /></label>
              <label className="field" style={{ flex: "2 1 260px" }}>Backup destination (folder on this PC or network share)
                <input className="mono" value={b.destination} placeholder="not chosen yet" onChange={(e) => bk.edit((x) => { x.destination = e.target.value; })} />
              </label>
            </div>
            <div className="stack" style={{ gap: 0 }}>
              {[
                { t: "Automatic backup", c: <Switch on={b.enabled} label="Automatic backup" onChange={(v) => bk.edit((x) => { x.enabled = v; })} /> },
                { t: "Frequency", c: <select value={b.every_h} aria-label="Backup frequency" onChange={(e) => bk.edit((x) => { x.every_h = Number(e.target.value); })}
                  style={{ height: 40, padding: "0 8px", border: "1px solid #74777f", borderRadius: 4 }}>
                  <option value={1}>Every hour</option><option value={6}>Every 6 hours</option><option value={24}>Daily</option></select> },
                { t: "Keep the newest", c: <span className="row" style={{ gap: 6 }}><input {...NUM} aria-label="Backups to keep" defaultValue={b.keep} key={`keep-${bk.ver}`}
                  onChange={(e) => bk.edit((x) => { x.keep = num(e.target.value); })} /> backups</span> },
                { t: "Also back up at each “End of production”", c: <Switch on={b.on_end_of_production} label="Back up at end of production" onChange={(v) => bk.edit((x) => { x.on_end_of_production = v; })} /> },
                { t: "Include a CSV export per experiment", c: <Switch on={b.csv_per_experiment} label="Include CSV export" onChange={(v) => bk.edit((x) => { x.csv_per_experiment = v; })} /> },
              ].map((x) => (
                <div key={x.t} className="row between" style={{ minHeight: 52, borderBottom: "1px solid var(--divider)", flexWrap: "nowrap" }}><span>{x.t}</span>{x.c}</div>
              ))}
            </div>
            <div className="stack" style={{ gap: 8, marginTop: 8 }}>
              <h3 style={{ margin: 0, fontSize: 16, fontWeight: 500 }}>Experiment copies</h3>
              <span className="small muted">A copy of one experiment (controller data, manual variables, parameters, metadata) written by this PC,
                e.g. on a USB stick. File name: date_time_code_name, e.g. <span className="mono">2026-11-10_1432_FM-031_Riboflavin pH 6.xlsx</span>.</span>
              <div className="row" style={{ gap: 12 }}>
                <label className="field" style={{ flex: "2 1 260px" }}>Archive folder
                  <input className="mono" value={b.archive_destination} placeholder="e.g. E:\FM2-archive" onChange={(e) => bk.edit((x) => { x.archive_destination = e.target.value; })} /></label>
                <label className="field" style={{ flex: "1 1 160px" }}>Format
                  <select value={b.archive_format} onChange={(e) => bk.edit((x) => { x.archive_format = e.target.value as "xlsx"; })}>
                    <option value="xlsx">XLSX (one sheet per block)</option><option value="zip">CSV (zip)</option></select></label>
              </div>
              <div className="row between" style={{ minHeight: 52, flexWrap: "nowrap" }}>
                <span>Offer a copy at “End of experiment” (ticked by default)</span>
                <Switch on={b.archive_on_end} label="Copy at end of experiment" onChange={(v) => bk.edit((x) => { x.archive_on_end = v; })} />
              </div>
            </div>
            <div className="row" style={{ gap: 12, alignItems: "center" }}>
              {bk.footer}
              <button className="btn outlined" disabled={busy || !all.data.backup.destination} onClick={async () => {
                setBusy(true); setBkMsg(null);
                try { const r2 = await api.backupRun(); setBkMsg({ kind: "good", text: `Written: ${r2.file}` }); backup.refresh(); }
                catch (x) { setBkMsg({ kind: "alert", text: (x as Error).message }); backup.refresh(); }
                finally { setBusy(false); }
              }}>{busy ? "Backing up…" : "Back up now"}</button>
              {bkMsg && <span className={`banner ${bkMsg.kind} small`} style={{ padding: "6px 12px" }}>{bkMsg.text}</span>}
            </div>
            </fieldset>
            <span className="xs muted">Backups are consistent copies made while recording continues. Do not point the destination at the live data folder, and never open the live database from a synced folder.
              Restore from a backup is done with the service stopped (procedure in the docs).</span>
          </Section>

          <Section id="credits" title="Credits and license">
            <div className="stack" style={{ gap: 0 }}>
              {[
                ["Version", <span className="mono">{sys.data?.version ?? "—"}</span>],
                ["Developed by", "Michele Isotti · DeFENS, University of Milan · DiSAFA, University of Turin"],
                ["License", <span>GNU General Public License v3.0 or later <span className="muted">(GPL-3.0-or-later) · free software, provided without warranty</span></span>],
                ["Source code", <a className="mono" href="https://github.com/micheleisotti/fermentation-manager-2" target="_blank" rel="noreferrer">github.com/micheleisotti/fermentation-manager-2</a>],
              ].map(([k, v]) => (
                <div key={String(k)} className="row between" style={{ minHeight: 52, borderBottom: "1px solid var(--divider)", gap: 16 }}>
                  <span className="muted">{k}</span><span style={{ textAlign: "right" }}>{v}</span>
                </div>
              ))}
            </div>
          </Section>
        </div>
      </div>
    </>
  );
}
