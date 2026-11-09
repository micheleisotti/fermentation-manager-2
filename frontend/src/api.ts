// Thin typed client for the fm2 backend (/api).

export type Status = "planned" | "fermenting" | "completed" | "aborted";

export interface Experiment {
  id: number; code: string; name: string; planned_date: string | null; strain: string;
  medium_id: number | null; buffer_id: number | null; working_volume_ml: number | null;
  reactor_id: string; status: Status; profile_id: number | null;
  parallel_run_code: string | null; parallel_partner_codes: string[];
  notes: string; started_at: string | null; ended_at: string | null; updated_at: string;
}

export interface Snapshot {
  ph: number | null; ph_setpoint: number | null; temperature: number | null; temperature_setpoint: number | null;
  stirrer: number | null; stirrer_setpoint: number | null; base_cumulative_ml: number | null; base_pump_pct: number | null;
  ph_low_limit: number | null; ph_high_limit: number | null;
  temperature_low_limit: number | null; temperature_high_limit: number | null;
}

export interface LiveReactor {
  reactor_id: string; online: boolean; last_reading_age_s: number | null; error: string | null;
  snapshot: Snapshot | null; ph_control: { mode: string | null; pid: Record<string, number> | null } | null;
  experiment: { id: number; code: string; strain: string; t_h: number; parallel_run: string | null } | null;
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
  medium_id?: number | null; buffer_id?: number | null; working_volume_ml?: number | null; profile_id?: number | null; notes?: string };

export interface Readings { columns: string[]; rows: (number | null)[][] }
export interface Measurement { id: number; variable_id: number; variable_name: string; sampled_at: string; t_h: number; value: number; unit: string; note: string; author: string }
export interface Variable { id: number; name: string; unit: string; category: string }
export interface EventItem { id: number; experiment_id: number | null; reactor_id: string; kind: string; severity: "alert" | "info" | "good"; message: string; t_h: number | null; at: string }
export interface RateBin { t_start_h: number; t_end_h: number; rate_ml_min: number | null; complete: boolean }
export interface CommandResult { parameter: string; old_value: number | null; requested_value: number; readback_value: number | null; status: string; message: string; warnings: string[] }

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
  analysis: (id: number, binH = 0.25) => req<{ rate_bins: RateBin[]; base_dosing: Record<string, number | null> | null }>(`/experiments/${id}/analysis?bin_h=${binH}`),
  setpoint: (reactor: string, parameter: string, value: number, origin = "local") =>
    req<CommandResult>(`/reactors/${reactor}/setpoints`, { method: "POST", body: JSON.stringify({ parameter, value, origin }) }),
  limits: (reactor: string, parameter: string, low: number | null, high: number | null, origin = "local") =>
    req<CommandResult[]>(`/reactors/${reactor}/limits`, { method: "POST", body: JSON.stringify({ parameter, low, high, origin }) }),
  setpointLog: (id: number) => req<{ at: string; parameter: string; old: number | null; new: number; readback: number | null; status: string; origin: string }[]>(`/experiments/${id}/setpoint-log`),
};

/** Columnar readings → series helper. */
export function col(r: Readings | undefined, name: string): [number, number | null][] {
  if (!r) return [];
  const it = r.columns.indexOf("t_h"), ic = r.columns.indexOf(name);
  return r.rows.map((row) => [row[it] as number, row[ic]]);
}
