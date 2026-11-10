// Thin typed client for the fm2 backend (/api).

export type Status = "planned" | "preparing" | "fermenting" | "completed" | "aborted";

export interface Experiment {
  id: number; code: string; name: string; planned_date: string | null; strain: string;
  medium_id: number | null; buffer_id: number | null; working_volume_ml: number | null;
  reactor_id: string; status: Status; profile_id: number | null;
  parallel_run_code: string | null; parallel_partner_codes: string[]; channels: Channel[];
  notes: string; started_at: string | null; ended_at: string | null; updated_at: string;
  archived: boolean; end_note: string | null; author: string; n_sessions: number; created_at: string;
  plan_params: PlanParams | null; profile_modified: boolean; prepared_at: string | null; plan_snapshot: Record<string, unknown> | null;
}

/** Controller parameters of a plan (copied from a profile, editable). */
export interface PlanParams {
  ph_setpoint: number | null; temperature_setpoint: number | null; stirrer_setpoint: number | null;
  ph_control_mode: string | null; ph_pid: Record<string, number | null> | null;
  ph_low_limit: number | null; ph_high_limit: number | null; temperature_low_limit: number | null; temperature_high_limit: number | null;
  temperature_control_mode?: string | null; temperature_pid?: Record<string, number | null> | null;
  loop_assignments?: LoopAssignments | null;
}

/** Per loop: actuators (and cascade loops) a profile was made for, as read from a controller. */
export type LoopAssignments = Record<string, { id: number; name: string; side: number; kind: string }[]>;
export interface LoopActuator { id: number; name: string; side: number; kind: string; pump: boolean; calibrated: boolean | null;
  dose_monitoring: boolean | null; unit: string; side_label?: string }
export interface LoopInfo { key: string; label: string; sensor: number; name: string; available: number; installed: boolean;
  active: boolean | null; control_type: string | null; value: string | null; setpoint: number | null; has_actuator: boolean | null;
  actuators: LoopActuator[]; drives: string | null; probe: string | null; warnings: string[] }
export interface LoopConfigView { reachable: boolean; message?: string; loops: LoopInfo[]; unassigned_pumps: LoopActuator[];
  assignments: LoopAssignments }

export type Channel = "temperature" | "ph" | "stirrer" | "do2" | "level" | "redox";
export const DEFAULT_CHANNELS: Channel[] = ["temperature", "ph", "stirrer"];

export interface Snapshot {
  ph: number | null; ph_setpoint: number | null; temperature: number | null; temperature_setpoint: number | null;
  stirrer: number | null; stirrer_setpoint: number | null; base_cumulative_ml: number | null; base_pump_pct: number | null;
  ph_low_limit: number | null; ph_high_limit: number | null;
  temperature_low_limit: number | null; temperature_high_limit: number | null;
  extra?: Partial<Record<"do2" | "do2_setpoint" | "do2_low_limit" | "do2_high_limit" | "heating_pct" | "cooling_pct" |
    "stirrer_out_rpm" | "n2_pct" | "air_pct" | "acid_cumulative_ml" | "acid_pump_pct" | "level_contact" |
    "antifoam_cumulative_ml" | "antifoam_pct" | "redox_mv", number>>;
}

export interface LiveReactor {
  reactor_id: string; online: boolean; last_reading_age_s: number | null; error: string | null;
  snapshot: Snapshot | null; ph_control: { mode: string | null; pid: Record<string, number> | null } | null;
  temperature_control?: { mode: string | null; pid: Record<string, number> | null } | null;
  experiment: { id: number; code: string; strain: string; status: Status; t_h: number; parallel_run: string | null; channels: Channel[] } | null;
}

export interface Named { id: number; name: string; description: string; archived: boolean; n_used: number }
export interface Profile {
  id: number; name: string; ph_setpoint: number | null; temperature_setpoint: number | null; stirrer_setpoint: number | null;
  ph_control_mode: string | null; ph_pid: Record<string, number> | null; notes: string;
  ph_low_limit: number | null; ph_high_limit: number | null;
  temperature_low_limit: number | null; temperature_high_limit: number | null;
  temperature_control_mode: string | null; temperature_pid: Record<string, number> | null; loop_assignments: LoopAssignments | null;
}
export interface PlanCheckRow { parameter: string; plan: unknown; controller: unknown; match: boolean; read_only?: boolean }
export interface PlanCheck { reachable: boolean; message?: string; reactor_free?: boolean; rows: PlanCheckRow[];
  assignment_differences?: Record<string, string> }
export type ExperimentDraft = { name: string; reactor_id: string; planned_date?: string | null; strain?: string;
  medium_id?: number | null; buffer_id?: number | null; working_volume_ml?: number | null; profile_id?: number | null; notes?: string;
  channels?: Channel[]; plan_params?: PlanParams | null };

export interface Readings { columns: string[]; rows: (number | null)[][] }
export interface Measurement { id: number; variable_id: number; variable_name: string; sampled_at: string; t_h: number; value: number; unit: string; note: string; author: string }
export interface Variable { id: number; name: string; unit: string; category: string; archived: boolean; n_measurements: number }
export interface EventItem { id: number; experiment_id: number | null; reactor_id: string; kind: string; severity: "alert" | "info" | "good"; message: string; t_h: number | null; at: string }
export interface RateBin { t_start_h: number; t_end_h: number; rate_ml_min: number | null; complete: boolean }
export interface CommandResult { parameter: string; old_value: number | null; requested_value: number; readback_value: number | null; status: string; message: string; warnings: string[] }

export interface FitParams {
  model: string; lag_h: number; lag_se: number; mu_max_per_h: number; mu_max_se: number; t_mu_max_h: number;
  A_ln: number; A_se: number; X0: number; X_max: number; doubling_time_h: number; r_squared: number; rmse: number; n_points: number; n_times: number; warnings: string[];
}
export interface BaseDosing {
  total_ml: number | null; peak_rate_ml_min: number | null; t_peak_h: number | null; t_half_total_h: number | null;
  last_hour_rate_ml_min: number | null; n_doses: number | null; base_per_final_od: number | null;
}
export interface AnalysisResult {
  experiment: { id: number; code: string; reactor_id: string; channels: Channel[];
    partner: { id: number; code: string; reactor_id: string; status: Status; parallel_run: string } | null };
  summary: { recorded_h: number | null; final_od: number | null; max_afu: number | null; total_base_ml: number | null;
    peak_base_rate_ml_min: number | null; out_of_limit_events: number };
  models: Record<string, string>;
  manual: Record<string, { unit: string; category: string; t: number[]; mean: number[]; sd: (number | null)[]; n: number[] }>;
  fits: Record<string, { params?: FitParams; curve?: { t: number[]; y: number[] }; error?: string; skipped?: boolean }>;
  series_defs: { key: string; label: string; unit: string }[];
  series: { t: number[] } & Record<string, (number | null)[]>;
  base_dosing: BaseDosing | null; rate_bins: RateBin[]; step_h: number; bin_h: number;
}

export interface SessionBody { name: string; members: Record<number, string>; conditions: string[]; fit_variable: string; model: string }
export interface SessionInfo extends SessionBody {
  id: number; code: string; author: string; created_at: string; updated_at: string; n_by_condition: Record<string, number>;
}
export interface CondSummary { n: number; mean: number | null; sd: number | null }
export interface SessionComparison {
  key: string; label: string; unit: string; source: string; test: string; statistic: number | null; p_value: number | null;
  difference: number | null; ci95: [number, number] | null; warning: string | null;
  posthoc: { a: string; b: string; difference: number; p_adj: number; ci95: [number, number] }[];
  conditions: Record<string, CondSummary>;
}
export interface SessionCurve { t: number[]; mean: number[]; sd: (number | null)[]; n: number; warning: string | null; fit?: { t: number[]; y: number[] } | null }
export interface SessionResults extends SessionInfo {
  models: Record<string, string>; comparisons: SessionComparison[]; variable: string; step_h: number;
  variable_options: { key: string; label: string; unit: string; manual: boolean; category: string | null; fit: boolean }[];
  curves: Record<string, SessionCurve | null>;
  parallel_run_flags: { parallel_run: string; date: string | null; same_condition: boolean; experiments: { code: string; condition: string }[] }[];
  experiments: { id: number; code: string; name: string; strain: string; reactor_id: string; date: string | null; parallel_run: string | null;
    condition: string; values: Record<string, number | null>; fit_error: string | null; fit_warnings: string[] }[];
}

export interface UserInfo { id: number; name: string; has_pin: boolean; archived: boolean; guest: boolean }
const TOKEN_KEY = "fm2.token";
export function getToken(): string {
  try { return localStorage.getItem(TOKEN_KEY) ?? ""; } catch { return ""; }
}
export function setToken(t: string | null): void {
  try { if (t) localStorage.setItem(TOKEN_KEY, t); else localStorage.removeItem(TOKEN_KEY); } catch { /* storage unavailable */ }
}

// Connection state with the lab PC, for the "connection lost" banner
type ConnListener = (online: boolean, lastOk: number) => void;
const connListeners = new Set<ConnListener>();
let connOnline = true;
let connLastOk = Date.now();
export function onConnection(fn: ConnListener): () => void {
  connListeners.add(fn);
  fn(connOnline, connLastOk);
  return () => { connListeners.delete(fn); };
}
function setConn(ok: boolean) {
  if (ok) connLastOk = Date.now();
  if (ok !== connOnline || ok) {
    const changed = ok !== connOnline;
    connOnline = ok;
    if (changed) connListeners.forEach((f) => f(connOnline, connLastOk));
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const tok = getToken();
  let r: Response;
  try {
    r = await fetch(`/api${path}`, { ...init, headers: { "Content-Type": "application/json", ...(tok ? { "X-FM2-Token": tok } : {}) } });
  } catch {
    setConn(false);
    throw new Error("No connection to the lab PC");
  }
  // 502/503/504 without our JSON body = the proxy could not reach the server (e.g. service restarting)
  const proxyDown = r.status >= 502 && r.status <= 504 && !(r.headers.get("content-type") ?? "").includes("json");
  setConn(!proxyDown);
  if (proxyDown) throw new Error("The lab PC is not answering");
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail ?? detail; } catch { /* not JSON */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return r.json() as Promise<T>;
}

export const api = {
  live: () => req<LiveReactor[]>("/reactors/live"),
  experiments: (status?: string) => req<Experiment[]>(`/experiments${status ? `?status=${status}` : ""}`),
  experiment: (id: number) => req<Experiment>(`/experiments/${id}`),
  createExperiment: (body: ExperimentDraft) =>
    req<Experiment>("/experiments", { method: "POST", body: JSON.stringify(body) }),
  patchExperiment: (id: number, body: Partial<ExperimentDraft>) =>
    req<Experiment>(`/experiments/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  linkParallel: (id: number, partnerId: number) =>
    req<Experiment>(`/experiments/${id}/parallel`, { method: "POST", body: JSON.stringify({ partner_id: partnerId }) }),
  loopsState: (reactor: string) => req<LoopsState>(`/reactors/${reactor}/loops`),
  switchLoops: (reactor: string, loops: string[], on: boolean) => req<{ results: { loop: string; requested: boolean; readback: boolean | null; status: string; message: string }[] }>(
    `/reactors/${reactor}/loops`, { method: "POST", body: JSON.stringify({ loops, on }) }),
  loopConfig: (reactor: string, fresh = false) => req<LoopConfigView>(`/reactors/${reactor}/loop-config${fresh ? "?fresh=true" : ""}`),
  planCheck: (id: number) => req<PlanCheck>(`/experiments/${id}/plan-check`),
  loadProfile: (id: number) => req<{ results: { parameter: string; status: string; message?: string }[] }>(`/experiments/${id}/load-profile`, { method: "POST" }),
  media: () => req<Named[]>("/media"),
  buffers: () => req<Named[]>("/buffers"),
  createNamed: (kind: "media" | "buffers", name: string, description = "") =>
    req<Named>(`/${kind}`, { method: "POST", body: JSON.stringify({ name, description }) }),
  profiles: () => req<Profile[]>("/profiles"),
  createProfile: (body: Omit<Profile, "id">) => req<Profile>("/profiles", { method: "POST", body: JSON.stringify(body) }),
  deleteExperiment: (id: number, code: string) =>
    req<{ deleted: string }>(`/experiments/${id}?confirm=${encodeURIComponent(code)}`, { method: "DELETE" }),
  deleteProfile: (id: number) => req<{ deleted: number; unlinked_experiments: string[] }>(`/profiles/${id}`, { method: "DELETE" }),
  prepare: (id: number) => req<Experiment>(`/experiments/${id}/prepare`, { method: "POST" }),
  unprepare: (id: number) => req<Experiment>(`/experiments/${id}/unprepare`, { method: "POST" }),
  start: (id: number, checklist?: string[]) =>
    req<Experiment>(`/experiments/${id}/start`, { method: "POST", body: JSON.stringify({ checklist: checklist ?? null }) }),
  end: (id: number, outcome: "completed" | "aborted" = "completed", note = "", save_copy?: boolean) =>
    req<Experiment & { copy?: { path?: string; bytes?: number; error?: string } | null }>(`/experiments/${id}/end`,
      { method: "POST", body: JSON.stringify({ outcome, note, save_copy }) }),
  archiveCopy: (id: number, body: { format?: string; blocks?: string[]; resample_min?: number | null; model?: string }) =>
    req<{ path: string; bytes: number }>(`/experiments/${id}/archive-copy`, { method: "POST", body: JSON.stringify(body) }),
  setArchived: (id: number, archived: boolean) =>
    req<Experiment>(`/experiments/${id}`, { method: "PATCH", body: JSON.stringify({ archived }) }),
  namedAll: (kind: "media" | "buffers") => req<Named[]>(`/${kind}?include_archived=true`),
  updateNamed: (kind: "media" | "buffers", id: number, body: { name?: string; description?: string; archived?: boolean }) =>
    req<Named>(`/${kind}/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteNamed: (kind: "media" | "buffers", id: number) => req<{ deleted: number }>(`/${kind}/${id}`, { method: "DELETE" }),
  variablesAll: () => req<Variable[]>("/variables?include_archived=true"),
  createVariable: (body: { name: string; unit: string; category: string }) =>
    req<Variable>("/variables", { method: "POST", body: JSON.stringify(body) }),
  updateVariable: (id: number, body: { name?: string; unit?: string; category?: string; archived?: boolean }) =>
    req<Variable>(`/variables/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteVariable: (id: number) => req<{ deleted: number }>(`/variables/${id}`, { method: "DELETE" }),
  readings: (id: number, sinceH = 0) => req<Readings>(`/experiments/${id}/readings?since_h=${sinceH}`),
  measurements: (id: number) => req<Measurement[]>(`/experiments/${id}/measurements`),
  addMeasurement: (id: number, body: { variable_id: number; value: number; sampled_at?: string; unit?: string; note?: string }) =>
    req<Measurement>(`/experiments/${id}/measurements`, { method: "POST", body: JSON.stringify(body) }),
  variables: () => req<Variable[]>("/variables"),
  events: (id: number) => req<EventItem[]>(`/experiments/${id}/events`),
  recentEvents: () => req<EventItem[]>("/events/recent"),
  users: (archived = false) => req<UserInfo[]>(`/users${archived ? "?include_archived=true" : ""}`),
  me: () => req<{ user: UserInfo | null }>("/auth/me"),
  login: (user_id: number, pin?: string) => req<{ user: UserInfo; token: string }>("/auth/login", { method: "POST", body: JSON.stringify({ user_id, pin }) }),
  createUser: (name: string, pin?: string) => req<{ user: UserInfo; token: string }>("/users", { method: "POST", body: JSON.stringify({ name, pin: pin || null }) }),
  updateUser: (id: number, body: { name?: string; pin?: string; archived?: boolean }) =>
    req<{ user: UserInfo; token: string | null }>(`/users/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  telegramChats: (token: string) => req<{ chats: { id: string; name: string; type: string }[] }>(
    "/settings/notifications/telegram-chats", { method: "POST", body: JSON.stringify({ token }) }),
  sessions: () => req<SessionInfo[]>("/analysis-sessions"),
  session: (id: number) => req<SessionInfo>(`/analysis-sessions/${id}`),
  createSession: (body: SessionBody) => req<SessionInfo>("/analysis-sessions", { method: "POST", body: JSON.stringify(body) }),
  updateSession: (id: number, body: SessionBody) =>
    req<SessionInfo>(`/analysis-sessions/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteSession: (id: number) => req<{ deleted: string }>(`/analysis-sessions/${id}`, { method: "DELETE" }),
  sessionResults: (id: number, stepH: number, variable?: string) =>
    req<SessionResults>(`/analysis-sessions/${id}/results?step_h=${stepH}${variable ? `&variable=${encodeURIComponent(variable)}` : ""}`),
  analysisFull: (id: number, model = "gompertz", stepH = 1) =>
    req<AnalysisResult>(`/experiments/${id}/analysis?model=${model}&step_h=${stepH}`),
  analysis: (id: number, binH = 0.25) => req<{ rate_bins: RateBin[]; base_dosing: Record<string, number | null> | null }>(`/experiments/${id}/analysis?bin_h=${binH}`),
  setpoint: (reactor: string, parameter: string, value: number, origin = "local") =>
    req<CommandResult>(`/reactors/${reactor}/setpoints`, { method: "POST", body: JSON.stringify({ parameter, value, origin }) }),
  limits: (reactor: string, parameter: string, low: number | null, high: number | null, origin = "local") =>
    req<CommandResult[]>(`/reactors/${reactor}/limits`, { method: "POST", body: JSON.stringify({ parameter, low, high, origin }) }),
  settings: () => req<AppSettings>("/settings"),
  putSettings: <K extends keyof AppSettings>(section: K, value: AppSettings[K]) =>
    req<AppSettings[K]>(`/settings/${section}`, { method: "PUT", body: JSON.stringify(value) }),
  testNotification: () => req<{ sent: boolean; channel: string }>("/settings/notifications/test", { method: "POST" }),
  backupStatus: () => req<BackupStatus>("/backup/status"),
  backupRun: () => req<BackupStatus & { file: string }>("/backup/run", { method: "POST" }),
  system: () => req<SystemInfo>("/system"),
  systemStatus: () => req<SystemStatus>("/system/status"),
  setpointLog: (id: number) => req<{ at: string; parameter: string; old: number | null; new: number; readback: number | null; status: string; origin: string }[]>(`/experiments/${id}/setpoint-log`),
};

/** Columnar readings → series helper. */
export function col(r: Readings | undefined, name: string): [number, number | null][] {
  if (!r) return [];
  const it = r.columns.indexOf("t_h"), ic = r.columns.indexOf(name);
  return r.rows.map((row) => [row[it] as number, row[ic]]);
}

export interface Range { min: number; max: number; max_step: number }
export interface AppSettings {
  remote: { allow_remote_setpoints: boolean; ranges: Record<"ph" | "temperature" | "stirrer", Range> };
  alerts: { out_of_limits_after_min: number; rearm_min: number; ph_tolerance: number; temp_tolerance: number;
    hold_min: number; silent_after_min: number; finished_fraction_pct: number; finished_hold_min: number };
  notifications: { channel: "none" | "ntfy" | "telegram"; ntfy_server: string; ntfy_topic: string; ntfy_token: string;
    telegram_token: string; telegram_chat_id: string; rules: Record<string, boolean> };
  backup: { enabled: boolean; destination: string; every_h: number; keep: number; on_end_of_production: boolean; csv_per_experiment: boolean;
    archive_destination: string; archive_format: "xlsx" | "zip"; archive_on_end: boolean };
  loops: { allowed: Record<string, string[]>; allow_remote: boolean };
}
export interface SwitchLoop extends LoopInfo { allowed: boolean; start_refused: string | null; also_switches: string[] }
export interface LoopsState { reachable: boolean; message?: string; allowed: string[]; loops: SwitchLoop[] }
export interface BackupStatus { enabled: boolean; destination: string; last_ok: string | null; last_file: string | null;
  last_error: string | null; last_attempt: string | null; db_bytes: number | null; backups_present: number; newest_backup: string | null }
export interface SystemInfo { version: string; client_local: boolean; data_dir: string; sampling_interval_s: number; experiments: number;
  reactors: { reactor_id: string; driver: string; address: string; online: boolean; capabilities: Record<string, boolean> }[] }
export interface SystemStatus {
  server: { host: string; started_at: string; uptime_s: number; version: string };
  remote: { state: "available" | "not_available" | "unknown"; name?: string; ips?: string[]; devices_online?: number; backend_state?: string; detail?: string };
}
