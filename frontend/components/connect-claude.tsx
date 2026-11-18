"use client";

import { useEffect, useState } from "react";
import { api, type AuthStatus, type OAuthStart } from "@/lib/api";

/**
 * Links a Claude subscription from the UI.
 *
 * Anthropic's flow is copy-code: there is no redirect back to us, so the user
 * approves in a new tab and pastes the code here. The PKCE `state` must survive
 * that tab switch — it lives in component state, mirrored to sessionStorage so
 * an accidental reload doesn't strand a half-finished connection.
 */

const STORAGE_KEY = "mimicuser.oauth.pending";

interface Pending {
  state: string;
  auth_url: string;
  expires_at: number;
}

function loadPending(): Pending | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const pending = JSON.parse(raw) as Pending;
    // The signed state self-expires server-side; drop it here too so the user
    // sees "start again" rather than a confusing 400 after a long pause.
    if (pending.expires_at < Date.now()) {
      sessionStorage.removeItem(STORAGE_KEY);
      return null;
    }
    return pending;
  } catch {
    return null;
  }
}

export function ConnectClaude({
  onChange,
  compact = false,
}: {
  onChange?: (s: AuthStatus) => void;
  compact?: boolean;
}) {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState<"start" | "submit" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [justConnected, setJustConnected] = useState(false);

  const refresh = async () => {
    try {
      const s = await api.authStatus();
      setStatus(s);
      onChange?.(s);
      return s;
    } catch (e) {
      setError((e as Error).message);
      return null;
    }
  };

  useEffect(() => {
    refresh();
    setPending(loadPending());
  }, []);

  async function begin() {
    setBusy("start");
    setError(null);
    try {
      const started: OAuthStart = await api.startOAuth("Claude subscription");
      const next: Pending = {
        state: started.state,
        auth_url: started.auth_url,
        expires_at: Date.now() + started.expires_in * 1000,
      };
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      setPending(next);
      // Popup blockers are common; the anchor below is the fallback.
      window.open(started.auth_url, "_blank", "noopener,noreferrer");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function finish() {
    if (!pending) return;
    setBusy("submit");
    setError(null);
    try {
      // Users paste with stray whitespace and newlines almost every time.
      await api.submitOAuthCode(code.trim(), pending.state);
      sessionStorage.removeItem(STORAGE_KEY);
      setPending(null);
      setCode("");
      setJustConnected(true);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  function cancel() {
    sessionStorage.removeItem(STORAGE_KEY);
    setPending(null);
    setCode("");
    setError(null);
  }

  if (!status) {
    return <span className="connection-loading">Checking connection…</span>;
  }

  // --- API-key mode: nothing to connect --------------------------------
  if (status.credential_kind === "api_key") {
    return (
      <span className="connection-loading">An Anthropic API key is active.</span>
    );
  }

  // --- Connected --------------------------------------------------------
  if (status.accounts.length > 0 && !pending) {
    const account = status.accounts[0];
    return (
      <div className="claude-connected">
        <span className="connection-account">
          Connected{!compact ? " to Claude" : ""}
          {account.email ? (
            <span> · {account.email}</span>
          ) : null}
        </span>
        {justConnected && (
          <span className="connection-just-now">just now</span>
        )}
        <button
          onClick={begin}
          disabled={busy !== null}
          className="connection-link"
        >
          Reconnect
        </button>
        {error && (
          <p className="connection-error">
            {error}
          </p>
        )}
      </div>
    );
  }

  // --- Connecting / not connected --------------------------------------
  return (
    <div className="claude-connect">
      <div>
        {!compact && <h2>Connect your Claude subscription</h2>}
        {!compact && <p>Synthetic customers run on your Claude account. Runs stay queued until one is connected.</p>}
      </div>

      {!pending ? (
        <button className="btn btn-primary" onClick={begin} disabled={busy !== null}>
          {busy === "start" ? "Opening Anthropic…" : "Connect Claude"}
        </button>
      ) : (
        <div className="oauth-pending">
          <ol>
            <li>
              Approve access in the tab that opened
              {" — "}
              <a
                href={pending.auth_url}
                target="_blank"
                rel="noopener noreferrer"
                className="connection-link"
              >
                reopen it
              </a>{" "}
              if it was blocked.
            </li>
            <li>Copy the code Anthropic shows you and paste it below.</li>
          </ol>

          <div className="oauth-code-row">
            <input
              autoFocus
              value={code}
              onChange={(e) => setCode(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && code.trim()) finish();
              }}
              placeholder="Paste the code from Anthropic"
              aria-label="Authorization code from Anthropic"
            />
            <button
              className="btn btn-primary"
              onClick={finish}
              disabled={busy !== null || !code.trim()}
            >
              {busy === "submit" ? "Connecting…" : "Finish connecting"}
            </button>
            <button
              onClick={cancel}
              className="connection-link"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {error && (
        <p className="connection-error">
          {error}
          {/expired|Invalid/i.test(error) && pending && (
            <>
              {" "}
              <button onClick={begin} className="underline underline-offset-4">
                Start again
              </button>
            </>
          )}
        </p>
      )}
    </div>
  );
}

