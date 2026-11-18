"use client";

/**
 * Settings.
 *
 * Only what can genuinely take effect without a restart is editable. Everything
 * else is shown but locked, because a control that saves and then quietly does
 * nothing is worse than no control — the API refuses those keys outright.
 */

import { useEffect, useState } from "react";
import { api, settings as settingsApi, type SettingsPayload } from "@/lib/api";
import { Connections } from "@/components/connections";

const MODEL_KEYS = [
  "CUSTOMER_MODEL",
  "EVALUATOR_MODEL",
  "AGGREGATOR_MODEL",
  "RESOLVER_MODEL",
  "DEFAULT_MODEL",
];

/** Suggestions, not a whitelist — the field stays free text so a new model
 *  works the day it ships without a frontend release. */
const MODELS_BY_PROVIDER = {
  anthropic: [
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-haiku-4-5",
    "claude-opus-4-8",
    "claude-sonnet-4-6",
  ],
  openai: [
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4.1",
    "gpt-4.1-mini",
    "o3",
    "o4-mini",
  ],
} as const;

const GROUPS: Array<{ title: string; keys: string[] }> = [
  { title: "Provider & models", keys: ["LLM_PROVIDER", ...MODEL_KEYS] },
  { title: "Effort & concurrency", keys: ["MODEL_EFFORT", "STUDY_CONCURRENCY"] },
  {
    title: "Default budgets",
    keys: ["DEFAULT_MAX_TOOL_CALLS", "DEFAULT_MAX_WALL_SECONDS", "DEFAULT_MAX_COST_USD"],
  },
  { title: "Browser", keys: ["BROWSER_HEADLESS", "VIEWPORT_WIDTH", "VIEWPORT_HEIGHT"] },
];

export default function SettingsPage() {
  const [data, setData] = useState<SettingsPayload | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const provider = draft.LLM_PROVIDER === "openai" ? "openai" : "anthropic";
  const providerModels = MODELS_BY_PROVIDER[provider];

  function changeProvider(nextProvider: "anthropic" | "openai") {
    const nextModels = MODELS_BY_PROVIDER[nextProvider];
    setDraft({
      ...draft,
      LLM_PROVIDER: nextProvider,
      CUSTOMER_MODEL: nextModels[1],
      EVALUATOR_MODEL: nextModels[0],
      AGGREGATOR_MODEL: nextModels[0],
      RESOLVER_MODEL: nextModels[2] ?? nextModels[1],
      DEFAULT_MODEL: nextModels[1],
    });
  }

  const load = (payload: SettingsPayload) => {
    setData(payload);
    setDraft(
      Object.fromEntries(Object.entries(payload.editable).map(([k, v]) => [k, String(v)])),
    );
  };

  useEffect(() => {
    settingsApi.get().then(load).catch((e) => setError((e as Error).message));
  }, []);

  const dirty = data
    ? Object.keys(draft).filter((k) => draft[k] !== String(data.editable[k]))
    : [];

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const patch = Object.fromEntries(dirty.map((k) => [k, draft[k]]));
      const updated = await settingsApi.update(patch);
      load(updated);
      window.dispatchEvent(new Event("mimicuser:provider-changed"));
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function reset(key: string) {
    setBusy(true);
    try {
      load(await settingsApi.reset(key));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (error && !data)
    return (
      <p className="measure" style={{ color: "var(--sev-critical)" }}>
        {error}
      </p>
    );
  if (!data)
    return (
      <p className="eyebrow" style={{ paddingBlock: "var(--s-16)" }}>
        Loading…
      </p>
    );

  return (
    <div className="settings-page">
      <header className="settings-hero">
        <span className="eyebrow">Workspace settings</span>
        <h1>Make Mimic yours.</h1>
        <p>Manage model connections, research defaults, and the environment behind every customer session.</p>
      </header>

      <Connections />

      {GROUPS.map((group) => (
        <div key={group.title}>
          <hr className="rule" />
          <section className="spread" style={{ paddingBlock: "var(--s-8)" }}>
            <div className="rail">
              <span className="eyebrow">{group.title}</span>
            </div>
            <div style={{ display: "grid", gap: "var(--s-6)", maxWidth: "34rem" }}>
              {group.keys.map((key) => {
                const isModel = MODEL_KEYS.includes(key);
                const overridden = data.overridden.includes(key);
                return (
                  <div key={key}>
                    <div
                      style={{
                        display: "flex",
                        alignItems: "baseline",
                        gap: "var(--s-3)",
                      }}
                    >
                      <label htmlFor={key}>{key.replace(/_/g, " ").toLowerCase()}</label>
                      {overridden ? (
                        <button
                          type="button"
                          className="btn-quiet"
                          style={{ marginLeft: "auto", fontSize: "var(--t-micro)" }}
                          disabled={busy}
                          onClick={() => reset(key)}
                          title="Drop the override and use the environment value"
                        >
                          reset
                        </button>
                      ) : null}
                    </div>
                    {key === "LLM_PROVIDER" ? (
                      <select
                        id={key}
                        value={draft[key] ?? "anthropic"}
                        onChange={(e) => changeProvider(e.target.value as "anthropic" | "openai")}
                      >
                        <option value="anthropic">Anthropic</option>
                        <option value="openai">OpenAI</option>
                      </select>
                    ) : isModel ? (
                      <select
                        id={key}
                        value={providerModels.some((model) => model === draft[key]) ? draft[key] : providerModels[0]}
                        onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
                      >
                        {providerModels.map((model) => <option key={model} value={model}>{model}</option>)}
                      </select>
                    ) : (
                      <input
                        id={key}
                        value={draft[key] ?? ""}
                        onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
                      />
                    )}
                    <p
                      style={{
                        fontSize: "var(--t-micro)",
                        color: "var(--ink-muted)",
                        marginTop: "var(--s-1)",
                      }}
                    >
                      {data.descriptions[key]}
                    </p>
                  </div>
                );
              })}
            </div>
          </section>
        </div>
      ))}

      <hr className="rule-strong" />
      <div
        style={{
          display: "flex",
          gap: "var(--s-6)",
          alignItems: "center",
          paddingBlock: "var(--s-6)",
          position: "sticky",
          bottom: 0,
          background: "var(--surface)",
        }}
      >
        <button className="btn" disabled={busy || dirty.length === 0} onClick={save}>
          {busy ? "Saving…" : dirty.length ? `Save ${dirty.length} change${dirty.length === 1 ? "" : "s"}` : "Saved"}
        </button>
        {saved ? (
          <span className="sev-tag sev-positive">applied to the next run</span>
        ) : null}
        {error ? (
          <span style={{ color: "var(--sev-critical)", fontSize: "var(--t-small)" }}>
            {error}
          </span>
        ) : null}
      </div>

      <section className="spread" style={{ paddingBlock: "var(--s-8)" }}>
        <div className="rail">
          <span className="eyebrow">Environment</span>
          <span className="rail-quiet">Set outside the app. Shown, not editable.</span>
        </div>
        <div>
          <dl style={{ margin: 0, display: "grid", gap: "var(--s-2)" }}>
            {Object.entries(data.read_only).map(([k, v]) => (
              <div
                key={k}
                style={{ display: "flex", gap: "var(--s-4)", fontSize: "var(--t-small)" }}
              >
                <dt className="eyebrow" style={{ minWidth: "11rem" }}>
                  {k}
                </dt>
                <dd
                  style={{
                    margin: 0,
                    fontFamily: "var(--font-mono)",
                    fontSize: "var(--t-micro)",
                    color: "var(--ink-secondary)",
                    overflowWrap: "anywhere",
                  }}
                >
                  {v}
                </dd>
              </div>
            ))}
          </dl>
          <ul
            className="measure"
            style={{
              marginTop: "var(--s-6)",
              paddingLeft: "1.1rem",
              fontSize: "var(--t-small)",
              color: "var(--ink-muted)",
            }}
          >
            {data.restart_required.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </div>
      </section>
    </div>
  );
}
