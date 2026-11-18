# Core Domain Model

The vocabulary of the product. Every name here appears verbatim in the code.

---

## The simulation, stated once

```
Product  +  Audience  +  (Persona overrides)  +  (Task)
                          │
                          ▼
                   SyntheticCustomer          ← resolved, immutable, stored on the Run
                          │
                   CustomerAgent              ← decides what to do
                          │
                   Tool / Browser             ← does it, against the real product
                          │
                   SessionEvent stream        ← what actually happened (evidence)
                          │
                   ResearchEvaluator          ← separate pass, separate prompt
                          │
                   RunReport + Finding[]      ← structured product feedback
```

The two required inputs are **Product** and **Audience**. Everything else is
optional customisation, and every optional thing has a resolution rule that lets the
system run without it.

---

## Entities

### Project
A product under study. Owns a URL, a name, and defaults that runs inherit.

| Field | Notes |
|---|---|
| `id`, `name` | |
| `product_url` | The live product. Required. |
| `product_description` | Optional. Free text the operator supplies. **Not** scraped — if absent the customer discovers what the product is, which is itself a finding. |
| `default_audience` | Optional. Pre-fills the run form. |

### Audience
The free-text target-market string the operator types (`"Freelance designers"`).
It is *not* a foreign key — it is the raw intent, resolved to a Persona at run time
by the `AudienceResolver`. Storing the raw string means we can re-resolve it later
when the catalogue improves, and we can compare "what they asked for" against "who we
simulated".

### Persona
A named, versioned set of **simulation parameters**. Ships as a data file
(`backend/personas/catalogue.json`), not as code. Three provenances:

| `source` | Meaning |
|---|---|
| `catalogue` | Verbatim from the built-in catalogue |
| `customized` | A catalogue entry with operator overrides applied |
| `generated` | Synthesised by an LLM when no catalogue entry matches the audience |

Schema in `docs/03-contracts.md`. **These attributes are not claimed to be
scientifically calibrated.** They are knobs that steer a simulation, and the persona
carries that disclaimer in its own prompt block so the model does not over-trust them.

### SyntheticCustomer
The **resolved, frozen** persona for one run: catalogue entry + overrides + the
resolution trace (which entry matched, why, with what confidence). Frozen at run
creation so a run is reproducible even after the catalogue changes underneath it.

### Task *(optional)*
An explicit research objective (`"Create and send your first invoice."`). Its presence
selects the run mode; its absence is not a missing input.

| Mode | Trigger | Agent's objective |
|---|---|---|
| `autonomous` | No task | Decide, as this customer, what you'd want from this product — then pursue it |
| `directed` | Task present | Pursue this objective **as this customer**, with all the persona's reactions intact |

Directed mode does not turn the agent into a test script. The persona still governs
patience, terminology, and objections; the task only fixes the goal.

### Run
One synthetic customer, one session, one report. The unit of work and of billing.

States: `queued → running → evaluating → completed`, with `failed` and `cancelled`
as terminal escapes.

Carries: the frozen `SyntheticCustomer`, the resolved `ModelSpec` and engine name,
the `RunBudget`, timing, token/cost accounting, and the storage prefix for artifacts.

### SessionEvent
One durable, ordered record of something that happened. The full stream is the
evidence base; nothing downstream may assert anything it cannot cite an event for.
Append-only, `seq`-ordered, `run_id`-scoped.

### Artifact
A binary produced during a run — screenshot, video, trace. Lives on the
`StorageProvider` under `projects/{project_id}/runs/{run_id}/…`; Postgres stores only
the relative key. Never inline in the database.

### Finding
One structured research conclusion. The atom of the product's output:

```
observation · evidence[] · impact · severity · confidence · recommendation
        + dimension          (what aspect of the experience)
        + claim_type         (observed_behavior | interpretation | hypothesis)
```

`claim_type` is the epistemic honesty guarantee the brief demands. It is enforced
mechanically, not by prose instruction: a finding with `claim_type=observed_behavior`
**must** cite at least one `event_seq`, and the evaluator's structured output schema
requires it.

### RunReport
The run-level narrative: first impression, would-continue, purchase/retention intent,
abandonment reason, verbatim quotes, plus the `Finding[]`. One per run.

### OAuthAccount
A linked Anthropic account (subscription auth). Encrypted access + refresh token,
expiry, provider account id. See `docs/05-anthropic-oauth.md`.

---

## Aggregate boundaries

```
Project ─┬─ Run ─┬─ SessionEvent[]     (append-only, no updates)
         │       ├─ Artifact[]         (metadata only; bytes on StorageProvider)
         │       └─ RunReport ── Finding[]
         └─ (defaults)

Persona catalogue    — data file, versioned with the repo, not a table
OAuthAccount         — independent; referenced by ModelSpec at run start
```

A `Run` is immutable once `completed`. Re-evaluating a session produces a **new**
`RunReport` version against the same event stream — which is the point of separating
the evaluator from the agent: evaluation methodology can improve retroactively.

---

## Invariants

1. **A run is reproducible from its own row.** Persona, model, engine, budget and
   prompt version are frozen onto the run. Changing the catalogue never rewrites
   history.
2. **Every finding cites evidence.** `Finding.evidence[].event_seq` must resolve to a
   real `SessionEvent` on that run.
3. **The evaluator never touches the browser.** It reads the event stream and the
   artifacts. This is what allows re-evaluation, alternative evaluators, and
   cross-run aggregation.
4. **The agent never writes a `Finding`.** It emits `note` events — first-person,
   in-the-moment reactions. Turning reactions into findings is the evaluator's job,
   and keeping the roles apart is what stops the customer from "helpfully" writing a
   UX report mid-session.
5. **Task completion is a field, not a verdict.** `RunReport.task_completed` sits
   beside nineteen other dimensions and has no privileged position.
6. **Artifacts never enter Postgres.** Keys only.
7. **No engine type crosses `engines/`.** Nothing outside an adapter may import
   `anthropic` or `claude_agent_sdk`.

---

## Deliberate non-entities (MVP)

Organisations, users, teams, roles, billing, quotas, vector stores, cohorts, and
experiment groups. Each has an obvious later home; none is on the path to the first
milestone. The one concession to the future is `Run.segment_key` — a nullable string
that lets a later aggregation layer group runs without a migration.
