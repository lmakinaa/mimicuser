# Synthetic Customer Research Platform

Put your product in front of **AI customers** drawn from your target audience and get
back evidence-based feedback in minutes — before you involve real customers.

Each synthetic customer opens your real website in a real browser, decides what someone
like them would want, tries to get it, and narrates what they experienced. A separate
evaluator then turns that recorded session into structured, evidence-cited findings.

The question this answers is *"what would someone in my target audience experience,
think, struggle with, and do when they encounter my product?"* — **not** *"does this
functionality work?"*. A technically successful task can still produce a damning
report, and that case is the reason the system exists:

```
Task completed: YES

"I didn't understand why I needed a Workspace."
"Pricing felt difficult to understand."
"I would probably try an alternative before committing."
```

---

## Why it matters — business impact

Product teams make expensive decisions on thin evidence. Real user research is slow
(weeks to recruit and schedule), costly, and usually happens *after* the thing is built.
This platform moves a first round of customer-perspective evidence to the start of the
cycle.

| Problem today | What this changes | Impact |
|---|---|---|
| User research takes weeks to recruit, schedule and synthesise | A study runs in minutes, on demand, per release or per landing-page change | Faster iteration; research stops being a bottleneck |
| Research budget limits how often teams test | Marginal cost of one more "customer" is near zero | Test every launch, not just the big ones |
| Teams discover confusing pricing, onboarding or positioning *after* launch, via churn | Surface it pre-launch, from the audience's point of view | Fewer failed launches, higher activation and conversion |
| Feedback is anecdotal — one loud opinion drives the roadmap | Studies report only what **multiple** customers hit, with counts per segment | Prioritise by evidence, not by the loudest voice |
| Synthetic output is hard to trust | Every observed claim cites a recorded event; speculation is labelled as such | Findings a PM can defend in a review |
| Funnels pass QA yet still lose people | Catches "task completed but I'd still leave" failures that functional tests cannot | Finds the conversion loss that dashboards only show as a number |

**Who uses it:** product managers validating a feature or pricing page, founders
sanity-checking positioning before spending on acquisition, designers and growth teams
comparing audiences, and agencies auditing client sites.

**What it is not:** a replacement for talking to real customers. It is a fast, cheap
first pass that tells you *where to point* real research, and catches the obvious
problems before real people have to. Calibrating synthetic output against real customer
behaviour is on the roadmap.

---

## The minimum input

Two things. Everything else is optional customisation.

```http
POST /v1/runs
{
  "product_url": "https://example.com",
  "audience": "Freelance designers"
}
```

No persona to author, no task to script. The audience string resolves to a synthetic
customer, and with no task the customer decides for themselves what someone like them
would want from a product like this.

---

## Documentation

| Doc | What's in it |
|---|---|
| [`docs/01-reference-study.md`](docs/01-reference-study.md) | UXAgent, Harness, Scenario, BrowserGym, WebArena + the internal agents-service, compared. What to take, what **not** to copy, and why. |
| [`docs/02-domain-model.md`](docs/02-domain-model.md) | Entities, aggregate boundaries, invariants. |
| [`docs/03-contracts.md`](docs/03-contracts.md) | The seven contracts: persona schema, `AgentEngine`, `Tool`, session/event, evaluation, `StorageProvider`, `ContextProvider`. |
| [`docs/04-mvp-slice.md`](docs/04-mvp-slice.md) | The smallest end-to-end slice, run lifecycle, stopping rules, what's excluded. |
| [`docs/05-anthropic-oauth.md`](docs/05-anthropic-oauth.md) | PKCE copy-code flow, token storage, the refresh-concurrency trap. |

---

## Architecture

```
Product / SaaS layer     app/            HTTP only: validate → call → serialise
        ↓
Research orchestration   orchestration/  run lifecycle · prompts · evaluator
        ↓
Agent engine abstraction engines/        AgentEngine protocol + adapters
        ↓
Tool / environment       tools/          browser (Playwright) + research tools
        ↓
Target product
```

Supporting layers: `domain/` (pure types), `context/` (ContextProvider),
`storage/` (StorageProvider), `repositories/` (all SQL), `services/`, `worker/`.

Three things are load-bearing and worth knowing before reading the code:

**1. The engine owns its loop; the platform owns the context and the tools.**
`AgentEngine` receives a system prompt, an opening message, a `ToolRegistry` and an
event sink. `engines/claude_agent/tools_adapter.py` is the *only* engine-specific
code any tool ever touches. `tests/unit/test_layering.py` proves that no module
outside `engines/claude_agent/` imports `claude_agent_sdk` — so no single agent
runtime has become the platform's abstraction.

**2. The customer agent and the research evaluator are different roles.**
The agent emits first-person `note` events while it works; it never writes a
`Finding`. The evaluator reads the recorded event stream afterwards — never the
browser — which is what makes re-evaluation, alternative evaluators and cross-run
aggregation possible without re-driving anything.

**3. Findings separate what was observed from what was inferred.**
Every finding carries `claim_type ∈ {observed_behavior, interpretation, hypothesis}`.
An `observed_behavior` finding **must** cite an `event_seq` that resolves on that run,
and `orchestration/evaluator.py` demotes any that doesn't. Speculation never renders
as fact.

---

## Running it

**Prerequisites:** Docker (for Postgres + Redis), Python 3.12+, [uv](https://docs.astral.sh/uv/), Node 20+, and either a Claude subscription or an API key (an OpenAI key also works via `LLM_PROVIDER=openai`).

```bash
# infrastructure
docker compose up -d                    # postgres:16 on 5433, redis:7 on 6380

# backend
cd backend
cp .env.example .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#   ↑ paste into ENCRYPTION_KEY in .env

uv venv && uv pip install -e ".[dev]"
uv run playwright install chromium
uv run alembic upgrade head

uv run uvicorn app.main:app --reload --port 8000   # API
uv run dramatiq worker.tasks                        # worker (separate shell)

# frontend
cd ../frontend && npm install && npm run dev        # http://localhost:3000
```

### Connecting a Claude subscription

The platform authenticates with **Anthropic OAuth** so it can run on a Claude
subscription rather than a metered API key.

Open the dashboard and press **Connect Claude**. Anthropic opens in a new tab;
approve, copy the code it shows you, and paste it back. That's the whole flow —
there is no redirect URI to register, because Anthropic's copy-code flow doesn't
use one. The PKCE state is held in the page (and mirrored to `sessionStorage`), so
switching tabs or reloading mid-flow doesn't strand you.

The same flow is available on the API (`POST /v1/auth/anthropic/start`, then
`/submit-code` with the code and state) if you'd rather script it.

Setting `ANTHROPIC_API_KEY` instead switches to key auth, and the dashboard says so.
Exactly one credential is ever used — a stray key cannot silently redirect billing
away from a connected account.

---

## What a run produces

```
storage/projects/{project_id}/runs/{run_id}/
├── screenshots/0007-look.png     clean captures — what a real user would have seen
├── recording.webm
├── events.json                   the full ordered event stream
└── report.json                   RunReport + Finding[]
```

Postgres stores metadata and structured findings; artifacts stay on the
`StorageProvider` behind relative, provider-agnostic keys, so an `S3StorageProvider`
changes nothing above the storage layer.

---

## Demo script (≈5 minutes)

1. Open `http://localhost:3000/settings` → **Connect Claude**.
2. **Studies** tab → enter a product URL and two audiences (e.g. *Freelance designers*, *Startup founders*), 2–3 customers each.
3. Watch runs execute live; open a run to replay its transcript and screenshots.
4. Read the study report: ranked findings, `N of M customers` agreement, per-segment breakdown.

---

## Tests

```bash
cd backend && uv run pytest -m unit          # no services needed
uv run pytest                                # includes integration
```

The unit suite covers the things that are expensive to get wrong: the persona
catalogue and audience resolution, the evidence contract, tool recording and budgets,
storage-key safety, engine resolution, and the OAuth state codec.

---

## Deliberately not built

Calibration against real customer behaviour, validity
hardening (bot walls, client-render races), persona variation within a segment,
billing, teams, permissions, vector search, S3, Kubernetes.

Calibration — the long-term moat — reads exactly the study output described below
and needs no change to it.

---

## Studies

One synthetic customer is an anecdote. A **study** runs several customers across
several audiences and reports only what more than one of them hit.

```http
POST /v1/studies
{
  "product_url": "https://example.com",
  "segments": [
    {"audience": "Freelance designers", "count": 3},
    {"audience": "Startup founders",    "count": 3}
  ]
}
```

Or use the **Studies** tab in the dashboard.

### The rule that makes aggregation trustworthy

**The LLM clusters; code counts.** The model is asked only to group
semantically-equivalent findings and return their ids. Every number a reader sees —
`8 of 9 customers`, the per-segment breakdown, the severity rollup — is computed in
Python from those ids. A model that miscounts cannot invent an agreement figure,
because it never writes one. A cluster citing a finding id that isn't in the study
is dropped before it can reach a report.

Two related guards:

- **A cluster of interpretations does not become an observation by being popular.**
  The weakest `claim_type` in a cluster wins.
- **A "blank page" reported by every customer is probably our headless browser being
  blocked, not a defect.** Those cluster into `environment_warnings`, shown apart
  from the ranked findings so automation failures cannot masquerade as research.

### One persona per segment

A segment's audience resolves once and every run in it shares that frozen persona,
so *"3 of 3 freelancers hit this"* describes the product rather than three
differently-invented people. Varying personas within a segment is a separate axis,
deliberately not built.

### Models are tiered

| Role | Default | Why |
|---|---|---|
| Customer agent | `claude-sonnet-5` | Long, tool-heavy, run many times per study |
| Evaluator | `claude-opus-5` | One call per run; judgement |
| Aggregator | `claude-opus-5` | One call per study; judgement |

**Rate limit, not money, is the binding constraint** — subscription auth reports no
dollar cost at all (`cost_usd` is null on most runs), so `max_cost_usd` is inert.
Concurrency is capped by a Redis semaphore (`STUDY_CONCURRENCY`, default 3) held for
the life of each run, so the cap holds across worker processes.

### Recovering a study

`POST /v1/studies/{id}/aggregate` re-clusters a finished study without re-running
any customer — the study-level analogue of `POST /v1/runs/{id}/evaluate`. Both exist
because the evaluator and aggregator only ever read stored data, never the browser.
