export type RunStatus =
  | "queued" | "running" | "evaluating" | "completed" | "failed" | "cancelled";

export type ClaimType = "observed_behavior" | "interpretation" | "hypothesis";
export type Severity = "critical" | "high" | "medium" | "low" | "positive";

export interface Evidence {
  event_seq: number;
  url: string | null;
  quote: string | null;
  screenshot_key: string | null;
}

export interface Finding {
  dimension: string;
  claim_type: ClaimType;
  observation: string;
  evidence: Evidence[];
  impact: string;
  severity: Severity;
  confidence: "high" | "medium" | "low";
  recommendation: string | null;
}

export interface RunReport {
  summary: string;
  first_impression: string;
  what_they_thought_it_was: string;
  task_completed: boolean | null;
  outcome: string;
  would_continue: boolean;
  purchase_intent: string;
  retention_intent: string;
  abandonment_reason: string | null;
  customer_satisfaction: number;
  quotes: string[];
  findings: Finding[];
}

export interface RunSummary {
  id: string;
  project_id: string;
  status: RunStatus;
  mode: "autonomous" | "directed";
  product_url: string;
  audience: string;
  task: string | null;
  persona_label: string;
  persona_id: string;
  persona_source: string;
  match_confidence: number;
  engine: string;
  model: string;
  outcome: string | null;
  would_continue: boolean | null;
  stop_reason: string | null;
  duration_ms: number | null;
  cost_usd: number | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface RunDetail extends RunSummary {
  customer: Record<string, any>;
  budget: Record<string, any>;
  prompt_versions: Record<string, any>;
  event_count: number;
  report: RunReport | null;
}

export interface SessionEvent {
  seq: number;
  ts: string;
  type: string;
  url: string | null;
  action: string | null;
  target: string | null;
  input: Record<string, any> | null;
  result: Record<string, any> | null;
  observation: string | null;
  screenshot_url: string | null;
  error: string | null;
  meta: Record<string, any>;
}

export interface Persona {
  id: string;
  label: string;
  summary: string;
  aliases: string[];
  traits: Record<string, string>;
}

export interface OAuthAccount {
  id: string;
  label: string;
  email: string | null;
  provider: string;
  token_expires_at: string | null;
  created_at: string;
}

export interface AuthStatus {
  configured: boolean;
  credential_kind: "oauth" | "api_key";
  accounts: OAuthAccount[];
}

export interface ProviderStatus {
  active_provider: "anthropic" | "openai";
  anthropic_configured: boolean;
  openai_configured: boolean;
}

export interface OAuthStart {
  auth_url: string;
  state: string;
  expires_in: number;
  instructions: string;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.text();
    let detail = body;
    try {
      detail = JSON.parse(body).detail ?? body;
    } catch {
      /* plain-text error body */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  listRuns: () => req<RunSummary[]>("/v1/runs"),
  getRun: (id: string) => req<RunDetail>(`/v1/runs/${id}`),
  getEvents: (id: string) =>
    req<{ events: SessionEvent[] }>(`/v1/runs/${id}/events?limit=1000`),
  listPersonas: () => req<{ personas: Persona[] }>("/v1/personas"),

  authStatus: () => req<AuthStatus>("/v1/auth/status"),
  providerStatus: () => req<ProviderStatus>("/v1/auth/providers"),
  switchProvider: (provider: "anthropic" | "openai") =>
    req<ProviderStatus>(`/v1/auth/providers/${provider}`, { method: "PUT" }),
  saveOpenAIKey: (apiKey: string) =>
    req<ProviderStatus>("/v1/auth/openai/key", {
      method: "PUT",
      body: JSON.stringify({ api_key: apiKey }),
    }),
  deleteOpenAIKey: () =>
    req<void>("/v1/auth/openai/key", { method: "DELETE" }),
  startOAuth: (label: string) =>
    req<OAuthStart>("/v1/auth/anthropic/start", {
      method: "POST",
      body: JSON.stringify({ label }),
    }),
  submitOAuthCode: (code: string, state: string) =>
    req<OAuthAccount>("/v1/auth/anthropic/submit-code", {
      method: "POST",
      body: JSON.stringify({ code, state }),
    }),
  createRun: (body: {
    product_url: string;
    audience: string;
    task?: string | null;
    persona_id?: string | null;
    budget?: { max_tool_calls: number; max_wall_seconds: number; max_cost_usd: number };
  }) => req<RunDetail>("/v1/runs", { method: "POST", body: JSON.stringify(body) }),
  cancelRun: (id: string) =>
    req<RunDetail>(`/v1/runs/${id}/cancel`, { method: "POST" }),
  reevaluateRun: (id: string) =>
    req<RunDetail>(`/v1/runs/${id}/evaluate`, { method: "POST" }),
};

export const ACTIVE_STATUSES: RunStatus[] = ["queued", "running", "evaluating"];

export function formatDuration(ms: number | null): string {
  if (!ms) return "—";
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

// -- studies ----------------------------------------------------------------

export interface SegmentAgreement { segment: string; hit: number; total: number }

export interface ConsensusFinding {
  statement: string;
  dimension: string;
  severity: Severity;
  claim_type: ClaimType;
  impact: string;
  recommendation: string | null;
  agreement_count: number;
  run_count: number;
  segments: SegmentAgreement[];
  severity_spread: Severity[];
  source_finding_ids: string[];
  run_ids: string[];
  quotes: string[];
  likely_environmental: boolean;
}

export interface SegmentSummary {
  segment: string;
  persona_label: string;
  runs: number;
  completed: number;
  would_continue: number;
  outcomes: Record<string, number>;
  mean_satisfaction: number | null;
}

export interface StudyReport {
  headline: string;
  run_count: number;
  analysed_count: number;
  would_continue_rate: number;
  purchase_intent: Record<string, number>;
  retention_intent: Record<string, number>;
  segments: SegmentSummary[];
  consensus_findings: ConsensusFinding[];
  environment_warnings: ConsensusFinding[];
}

export interface StudySummary {
  id: string;
  project_id: string;
  name: string;
  status: "queued" | "running" | "aggregating" | "completed" | "failed" | "cancelled";
  product_url: string;
  task: string | null;
  segments: Array<Record<string, any>>;
  progress: Record<string, number>;
  headline: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface StudyDetail extends StudySummary {
  models: Record<string, any>;
  budget: Record<string, any>;
  prompt_versions: Record<string, any>;
  error: string | null;
  report: StudyReport | null;
  runs: Array<{
    id: string; status: string; segment: string; persona_label: string;
    model: string; outcome: string | null; would_continue: boolean | null;
    duration_ms: number | null; stop_reason: string | null;
  }>;
}

export const ACTIVE_STUDY_STATUSES = ["queued", "running", "aggregating"];

export const studies = {
  list: () => req<StudySummary[]>("/v1/studies"),
  get: (id: string) => req<StudyDetail>(`/v1/studies/${id}`),
  create: (body: {
    product_url: string;
    // A segment is either a free-text audience (a persona gets synthesised) or
    // an explicit persona_id (synthesis skipped entirely).
    segments: Array<{ audience: string; count: number; persona_id?: string | null }>;
    name?: string | null;
    task?: string | null;
    project_id?: string | null;
    budget?: { max_tool_calls: number; max_wall_seconds: number; max_cost_usd: number };
  }) => req<StudyDetail>("/v1/studies", { method: "POST", body: JSON.stringify(body) }),
  reaggregate: (id: string) =>
    req<StudyDetail>(`/v1/studies/${id}/aggregate`, { method: "POST" }),
  cancel: (id: string) =>
    req<StudyDetail>(`/v1/studies/${id}/cancel`, { method: "POST" }),
};


// -- personas ---------------------------------------------------------------

export interface PersonaEntry {
  persona: Persona;
  /** "builtin" | "override" | "custom" */
  origin: string;
  editable: boolean;
  deletable: boolean;
}

export interface PersonaWrite {
  id?: string | null;
  label?: string | null;
  summary?: string | null;
  background?: string | null;
  motivation?: string | null;
  aliases?: string[];
  goals?: string[];
  traits?: Record<string, string>;
}

export const personas = {
  list: () => req<{ version: number; personas: Persona[]; entries: PersonaEntry[] }>("/v1/personas"),
  get: (id: string) => req<PersonaEntry>(`/v1/personas/${id}`),
  create: (body: PersonaWrite) =>
    req<PersonaEntry>("/v1/personas", { method: "POST", body: JSON.stringify(body) }),
  update: (id: string, body: PersonaWrite) =>
    req<PersonaEntry>(`/v1/personas/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  remove: (id: string) => req<void>(`/v1/personas/${id}`, { method: "DELETE" }),
};

// -- settings ---------------------------------------------------------------

export interface SettingsPayload {
  editable: Record<string, string | number | boolean>;
  descriptions: Record<string, string>;
  /** Keys pinned in the database rather than taken from the environment. */
  overridden: string[];
  read_only: Record<string, string>;
  restart_required: string[];
}

export const settings = {
  get: () => req<SettingsPayload>("/v1/settings"),
  update: (values: Record<string, unknown>) =>
    req<SettingsPayload>("/v1/settings", { method: "PATCH", body: JSON.stringify({ values }) }),
  reset: (key: string) => req<SettingsPayload>(`/v1/settings/${key}`, { method: "DELETE" }),
};

// -- projects ---------------------------------------------------------------

export interface Project {
  id: string;
  name: string;
  product_url: string;
  product_description: string | null;
  default_audience: string | null;
  created_at: string;
  run_count: number;
  study_count: number;
}

export const projects = {
  list: () => req<Project[]>("/v1/projects"),
  get: (id: string) => req<Project>(`/v1/projects/${id}`),
  update: (id: string, body: Partial<Project>) =>
    req<Project>(`/v1/projects/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
};
