// Thin typed client for the fm2 backend (/api).

export type Status = "planned" | "fermenting" | "completed" | "aborted";

export interface Experiment {
  id: number; code: string; name: string; planned_date: string | null; strain: string;
  medium_id: number | null; buffer_id: number | null; working_volume_ml: number | null;
  reactor_id: string; status: Status; profile_id: number | null;
  parallel_run_code: string | null; parallel_partner_codes: string[]; channels: Channel[];
  notes: string; started_at: string | null; ended_at: string | null; updated_at: string;
}

export type Channel = "temperature" | "ph" | "stirrer" | "do2";
export const DEFAULT_CHANNELS: Channel[] = ["temperature", "ph", "stirrer"];

export interface Snapshot {
  ph: number | null; ph_setpoint: number | null; temperature: number | null; temperature_setpoint: number | null;
  stirrer: number | null; stirrer_setpoint: number | null; base_cumulative_ml: number | null; base_pump_pct: number | null;
  ph_low_limit: number | null; ph_high_limit: number | null;
  temperature_low_limit: number | null; temperature_high_limit: number | null;
  extra?: Partial<Record<"do2" | "do2_setpoint" | "do2_low_limit" | "do2_high_limit" | "heating_pct" | "cooling_pct" |
    "stirrer_out_rpm" | "n2_pct" | "air_pct", number>>;
}

export interface LiveReactor {
  reactor_id: string; online: boolean; last_reading_age_s: number | null; error: string | null;
  snapshot: Snapshot | null; ph_control: { mode: string | null; pid: Record<string, number> | null } | null;
  experiment: { id: number; code: string; strain: string; t_h: number; parallel_run: string | null; channels: Channel[] } | null;
}

export interface Named { id: number; name: string; description: string; archived: boolean }
export interface Profile {
  id: number; name: string; ph_setpoint: number | null; temperature_setpoint: number | null; stirrer_setpoint: number | null;
  ph_control_mode: string | null; ph_pid: Record<string, number> | null; notes: string;
  ph_low_limit: number | null; ph_high_limit: number | null;
  temperature_low_limit: number | null; temperature_high_limit: number | null;
}
export interface PlanCheckRow { parameter: string; plan: unknown; controller: unknown; match: boolean; read_only?: boolean }
export interface PlanCheck { reachable: boolean; message?: string; reactor_free?: boolean; rows: PlanCheckRow[] }
export type ExperimentDraft = { name: string; reactor_id: string; planned_date?: string | null; strain?: string;
  medium_id?: number | null; buffer_id?: number | null; working_volume_ml?: number | null; profile_id?: number | null; notes?: string;
  channels?: Channel[] };

export interface Readings { columns: string[]; rows: (number | null)[][] }
export interface Measurement { id: number; variable_id: number; variable_name: string; sampled_at: string; t_h: number; value: number; unit: string; note: string; author: string }
export interface Variable { id: number; name: string; unit: string; category: string }
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
  fits: Record<string, { params?: FitParams; curve?: { t: number[]; y: number[] }; error?: string }>;
  series_defs: { key: string; label: string; unit: string }[];
  series: { t: number[] } & Record<string, (number | null)[]>;
  base_dosing: BaseDosing | null; rate_bins: RateBin[]; step_h: number; bin_h: number;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`/api${path}`, { headers: { "Content-Type": "application/json" }, ...init });
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
  planCheck: (id: number) => req<PlanCheck>(`/experiments/${id}/plan-check`),
  loadProfile: (id: number) => req<{ results: { parameter: string; status: string; message?: string }[] }>(`/experiments/${id}/load-profile`, { method: "POST" }),
  media: () => req<Named[]>("/media"),
  buffers: () => req<Named[]>("/buffers"),
  createNamed: (kind: "media" | "buffers", name: string) => req<Named>(`/${kind}`, { method: "POST", body: JSON.stringify({ name }) }),
  profiles: () => req<Profile[]>("/profiles"),
  createProfile: (body: Omit<Profile, "id">) => req<Profile>("/profiles", { method: "POST", body: JSON.stringify(body) }),
  deleteExperiment: (id: number, code: string) =>
    req<{ deleted: string }>(`/experiments/${id}?confirm=${encodeURIComponent(code)}`, { method: "DELETE" }),
  deleteProfile: (id: number) => req<{ deleted: number; unlinked_experiments: string[] }>(`/profiles/${id}`, { method: "DELETE" }),
  start: (id: number) => req<Experiment>(`/experiments/${id}/start`, { method: "POST" }),
  end: (id: number) => req<Experiment>(`/experiments/${id}/end`, { method: "POST" }),
  readings: (id: number, sinceH = 0) => req<Readings>(`/experiments/${id}/readings?since_h=${sinceH}`),
  measurements: (id: number) => req<Measurement[]>(`/experiments/${id}/measurements`),
  addMeasurement: (id: number, body: { variable_id: number; value: number; sampled_at?: string; unit?: string; note?: string }) =>
    req<Measurement>(`/experiments/${id}/measurements`, { method: "POST", body: JSON.stringify(body) }),
  variables: () => req<Variable[]>("/variables"),
  events: (id: number) => req<EventItem[]>(`/experiments/${id}/events`),
  recentEvents: () => req<EventItem[]>("/events/recent"),
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
  backup: { enabled: boolean; destination: string; every_h: number; keep: number; on_end_of_production: boolean; csv_per_experiment: boolean };
}
export interface BackupStatus { enabled: boolean; destination: string; last_ok: string | null; last_file: string | null;
  last_error: string | null; last_attempt: string | null; db_bytes: number | null; backups_present: number; newest_backup: string | null }
export interface SystemInfo { version: string; data_dir: string; sampling_interval_s: number; experiments: number;
  reactors: { reactor_id: string; driver: string; address: string; online: boolean; capabilities: Record<string, boolean> }[] }
export interface SystemStatus {
  server: { host: string; started_at: string; uptime_s: number; version: string };
  remote: { state: "available" | "not_available" | "unknown"; name?: string; ips?: string[]; devices_online?: number; backend_state?: string; detail?: string };
}
