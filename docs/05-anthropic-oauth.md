# Anthropic OAuth — Subscription Auth for the Agent

The brief requires that the platform can drive Claude through a **Claude
subscription** rather than a metered API key. This is the design, adapted from
`refs/vl-app-main-services-agents_service/.../app/api/v2/endpoints/oauth_flow.py`
with the multi-tenant scoping removed.

---

## Flow — PKCE, copy-code

There is no public redirect URI to register and no callback endpoint to expose.
Anthropic renders the authorization code on a page; the operator pastes it back.

```
POST /v1/auth/anthropic/start
   ├─ generate code_verifier (43+ chars) + S256 challenge
   ├─ build a signed state payload (below)
   └─ 200 { auth_url, state, expires_in }

  operator opens auth_url → approves → copies the code from
  https://platform.claude.com/oauth/code/callback

POST /v1/auth/anthropic/submit-code  { code, state }
   ├─ verify + decode state           (signature, expiry)
   ├─ POST https://api.anthropic.com/v1/oauth/token
   │     grant_type=authorization_code, code, state,
   │     redirect_uri, client_id, code_verifier
   ├─ GET  https://api.anthropic.com/api/oauth/profile   (account uuid + email)
   └─ upsert oauth_accounts (tokens encrypted)
```

Scopes requested: `org:create_api_key user:profile user:inference`.

---

## Stateless signed state

The state parameter carries its own payload instead of being a lookup key:

```
state = base64url(json_payload) + "." + base64url(HMAC_SHA256(key, body))

payload = { code_verifier, label, exp, jti }
```

Signed with the app's `ENCRYPTION_KEY`. Same security properties as JWT-HS256 minus
the unused header segment.

**Why not a server-side store:** the alternative is Redis with `GETDEL` for one-shot
use. That replay defence is redundant — Anthropic's `/v1/oauth/token` already rejects
a redeemed `code` on the second attempt, so replaying the state buys an attacker
nothing. Trading it away removes a dependency from the auth path entirely: no Redis
client, no lifespan hook, no "is Redis up?" failure mode on OAuth start.

All decode failures — bad split, bad signature, expired, malformed JSON — collapse to
the same `400 Invalid OAuth state`, so a probe cannot distinguish tampered from
expired.

---

## Token storage

`oauth_accounts`:

| Column | Notes |
|---|---|
| `id`, `provider`, `provider_account_id`, `email`, `label` | |
| `access_token_encrypted` | Fernet, `bytea` |
| `refresh_token_encrypted` | Fernet, `bytea` |
| `token_expires_at` | `timestamptz` |
| `created_at`, `updated_at` | |

Encryption and decryption happen **only** at the repository boundary
(`OAuthAccountRepo.get_access_token(row)`); plaintext never touches an ORM attribute
that could be serialised into a log or a response model.

---

## Refresh — the part that is easy to get wrong

Anthropic **rotates the refresh token on every use and invalidates the previous one.**
A naive implementation under concurrency (several runs starting at once behind one
expired access token) has every caller send the same refresh token; one wins and the
rest fail with `invalid_grant`, permanently breaking the account.

The fix, carried over from the reference:

```python
async with session.begin():
    row = await OAuthAccountRepo.get_for_update(session, account_id)   # SELECT … FOR UPDATE

    # A concurrent holder may already have rotated while we waited on the lock.
    if row.token_expires_at - now > FRESHNESS_MARGIN:      # 60s
        return OAuthAccountRepo.get_access_token(row)      # reuse; no rotation

    new = await exchange_refresh_token(...)
    await OAuthAccountRepo.update_tokens(session, row.id, **new)
    return new.access_token
```

The row lock serialises the herd; the freshness re-check collapses it into exactly one
rotation. `invalid_grant` from Anthropic means the account must be **re-linked**, not
retried — it is surfaced as a distinct, actionable error rather than a generic 502.

Refresh is attempted proactively when the token is within `REFRESH_MARGIN` (5 min) of
expiry at run start, so a long run does not expire mid-session.

---

## Reaching the agent

The Claude Agent SDK spawns the Claude Code CLI as a subprocess. The credential is
passed through the subprocess environment, resolved per run in
`engines/claude_agent/credentials.py`:

| Credential kind | Environment |
|---|---|
| `oauth` | `CLAUDE_CODE_OAUTH_TOKEN=<access_token>` |
| `api_key` | `ANTHROPIC_API_KEY=<key>` |

Only one is ever set — both together is a rejected configuration, not a precedence
question. `CLAUDE_CONFIG_DIR` is pointed at a per-run directory so the CLI's own state
never leaks between runs or into the operator's `~/.claude`.

Non-agentic calls (the evaluator, persona synthesis) go through the `anthropic` SDK
instead, which takes the same OAuth token as
`Anthropic(auth_token=…, default_headers={"anthropic-beta": "oauth-2025-04-20"})` —
OAuth tokens ride `Authorization: Bearer`, never `x-api-key`.

---

## Configuration

| Setting | Default |
|---|---|
| `CLAUDE_OAUTH_CLIENT_ID` | `9d1c250a-e61b-44d9-88ed-5944d1962f5e` |
| `OAUTH_CALLBACK_URL` | `https://platform.claude.com/oauth/code/callback` |
| `OAUTH_STATE_TTL_SECONDS` | `600` |
| `ENCRYPTION_KEY` | **required**; Fernet key, also the state-signing key |

`ENCRYPTION_KEY` is validated at startup — an unset key fails the boot rather than
silently signing state with an empty secret.

---

## Operational notes

- Rate limits and availability follow the **subscription**, not an API tier. A
  fan-out of 50 synthetic customers on one linked account will queue; that is a
  scheduling concern for the fan-out milestone, not a bug here.
- Token refresh happens in the API/worker process, never inside the agent subprocess —
  the subprocess receives a token and has no path to rotate or exfiltrate a refresh
  token.
- Linking is a single-operator action in the MVP. Per-tenant credential scoping is a
  later concern and is why `ModelSpec.credential` is an indirection rather than a
  string.
