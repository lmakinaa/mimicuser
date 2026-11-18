"use client";

import { useEffect, useState } from "react";
import { api, type AuthStatus, type ProviderStatus } from "@/lib/api";
import { ConnectClaude } from "@/components/connect-claude";

export function Connections() {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [provider, setProvider] = useState<ProviderStatus | null>(null);
  const [openAIKey, setOpenAIKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const refreshProvider = () => api.providerStatus().then(setProvider).catch(() => undefined);
    api.authStatus().then(setStatus).catch(() => setStatus(null));
    refreshProvider();
    window.addEventListener("mimicuser:provider-changed", refreshProvider);
    return () => window.removeEventListener("mimicuser:provider-changed", refreshProvider);
  }, []);

  const usesApiKey = status?.credential_kind === "api_key";
  const subscriptionConnected = Boolean(status?.accounts.length) && !usesApiKey;

  async function saveOpenAIKey() {
    setBusy(true);
    setError(null);
    try {
      setProvider(await api.saveOpenAIKey(openAIKey));
      setOpenAIKey("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function removeOpenAIKey() {
    setBusy(true);
    setError(null);
    try {
      await api.deleteOpenAIKey();
      setProvider((current) => current ? { ...current, openai_configured: false } : current);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function switchProvider(nextProvider: "anthropic" | "openai") {
    setBusy(true);
    setError(null);
    try {
      setProvider(await api.switchProvider(nextProvider));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="connections-panel" aria-labelledby="connections-heading">
      <div className="connections-intro">
        <span className="eyebrow">Model providers</span>
        <h2 id="connections-heading">Connections</h2>
        <p>Choose how Mimic accesses the models that power your customer research.</p>
      </div>

      <div className="provider-stack">
        <article className={`provider-card ${subscriptionConnected ? "is-connected" : ""}`}>
          <div className="provider-icon provider-icon-anthropic" aria-hidden="true">A</div>
          <div className="provider-copy">
            <div className="provider-title-row">
              <h3>Claude subscription</h3>
              <ProviderStatus connected={subscriptionConnected} label={subscriptionConnected ? "Connected" : "Recommended"} />
            </div>
            <p>Connect your Anthropic account with OAuth. Runs use your Claude subscription, with no API key to manage.</p>
            <div className="provider-action" style={{ display: "grid", gap: "var(--s-3)" }}>
              <ConnectClaude onChange={(nextStatus) => { setStatus(nextStatus); api.providerStatus().then(setProvider).catch(() => undefined); }} compact />
              {provider?.active_provider === "anthropic" ? <ProviderStatus connected label="Active for new runs" /> : <button className="btn" type="button" disabled={busy || !provider?.anthropic_configured} onClick={() => switchProvider("anthropic")}>Use Claude for new runs</button>}
            </div>
          </div>
        </article>

        <article className={`provider-card ${usesApiKey ? "is-connected" : ""}`}>
          <div className="provider-icon provider-icon-key" aria-hidden="true">⌘</div>
          <div className="provider-copy">
            <div className="provider-title-row">
              <h3>Anthropic API key</h3>
              <ProviderStatus connected={usesApiKey} label={usesApiKey ? "Configured" : "Optional"} />
            </div>
            <p>Useful for team or service accounts. Configure <code>ANTHROPIC_API_KEY</code> in the server environment, then restart the API and worker.</p>
          </div>
        </article>

        <article className={`provider-card ${provider?.openai_configured ? "is-connected" : ""}`}>
          <div className="provider-icon provider-icon-openai" aria-hidden="true">◎</div>
          <div className="provider-copy">
            <div className="provider-title-row">
              <h3>OpenAI API key</h3>
              <ProviderStatus connected={Boolean(provider?.openai_configured)} label={provider?.openai_configured ? "Configured" : "Not configured"} />
            </div>
            <p>Use an OpenAI API key for browser research, persona synthesis, and structured evaluation.</p>
            <div className="provider-action" style={{ display: "grid", gap: "var(--s-3)" }}>
              <input
                type="password"
                value={openAIKey}
                onChange={(event) => setOpenAIKey(event.target.value)}
                placeholder={provider?.openai_configured ? "Replace saved key" : "sk-..."}
                autoComplete="off"
                aria-label="OpenAI API key"
              />
              <div style={{ display: "flex", gap: "var(--s-3)" }}>
                <button className="btn" type="button" disabled={busy || openAIKey.trim().length < 10} onClick={saveOpenAIKey}>
                  {busy ? "Saving..." : "Save key"}
                </button>
                {provider?.openai_configured ? <button className="btn-quiet" type="button" disabled={busy || provider.active_provider === "openai"} onClick={removeOpenAIKey}>Remove</button> : null}
              </div>
              {provider?.active_provider === "openai" ? <ProviderStatus connected label="Active for new runs" /> : <button className="btn" type="button" disabled={busy || !provider?.openai_configured} onClick={() => switchProvider("openai")}>Use OpenAI for new runs</button>}
              {error ? <span style={{ color: "var(--sev-critical)", fontSize: "var(--t-small)" }}>{error}</span> : null}
            </div>
          </div>
        </article>
      </div>

      <p className="connection-security-note"><span aria-hidden="true">⌑</span> Connection tokens are encrypted at rest. Provider credentials are never shown after they are saved.</p>
    </section>
  );
}

function ProviderStatus({ connected, label }: { connected: boolean; label: string }) {
  return <span className={`provider-status${connected ? " is-connected" : ""}`}>{connected && <span aria-hidden="true">●</span>}{label}</span>;
}
