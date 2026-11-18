# The Smallest End-to-End MVP

One synthetic customer, one product, one report. Everything else is deferred.

---

## The slice

```
target audience  →  default persona  →  (optional task)
        ↓
synthetic customer agent   (claude_agent engine, OAuth subscription auth)
        ↓
Playwright  →  the real website
        ↓
session recording   (events.json + screenshots + video)
        ↓
research evaluator  (separate pass, structured outputs)
        ↓
structured product feedback   (report.json + findings)
        ↓
dashboard
```

**Milestone:** a user enters a product URL and a target audience, optionally specifies
a task, and receives evidence-based product feedback after an AI customer has actually
used the product.

---

## Run lifecycle

```
        POST /v1/runs
             │
             ▼
        ┌──────────┐   dramatiq enqueue
        │  queued  │──────────────────────┐
        └──────────┘                      │
                                          ▼
   ┌────────────────────────────────────────────────────┐
   │ running                                            │
   │  1. resolve audience → SyntheticCustomer (frozen)   │
   │  2. resolve credential → OAuth token (refresh)      │
   │  3. build context blocks → system prompt            │
   │  4. open browser session, start recording           │
   │  5. engine.run(...) — agent loop, tools, events      │
   │  6. stop on finish() | budget | timeout | error      │
   │  7. close browser, flush events.json                 │
   └────────────────────────────────────────────────────┘
             │
             ▼
   ┌────────────────────────────────────────────────────┐
   │ evaluating                                          │
   │  8. evaluator reads events + artifacts (no browser)  │
   │  9. structured output → RunReport + Finding[]        │
   │ 10. evidence validation, persist, write report.json  │
   └────────────────────────────────────────────────────┘
             │
             ▼
        ┌───────────┐
        │ completed │      failed · cancelled are terminal escapes
        └───────────┘
```

A run that ends because the customer gave up is **`completed`**. `failed` means *we*
broke — engine error, browser crash, credential failure — never that the customer had
a bad time.

---

## Stopping rules

In priority order. Only the first is a *result*; the rest are safety nets and are
recorded as such on the report.

1. The agent calls `finish(...)` — the customer decided to stop.
2. `budget.max_tool_calls` exhausted.
3. `budget.max_wall_seconds` exceeded.
4. Engine error or repeated tool failure past threshold.

When a run stops on 2–4, the evaluator is told so explicitly and instructed not to
infer abandonment from a truncated session — a budget cut-off is our artifact, not the
customer's judgement.

---

## Budgets (defaults)

| Knob | Default | Why |
|---|---|---|
| `max_tool_calls` | 60 | Enough for sign-up + a first real action on most SaaS |
| `max_wall_seconds` | 600 | Bounds a wedged page |
| `max_cost_usd` | 3.00 | Advisory; reported, not enforced mid-turn |
| viewport | 1280×900 | Desktop first; mobile is a later persona axis |

---

## What the slice deliberately excludes

| Excluded | Why it is safe to defer |
|---|---|
| Multi-customer fan-out, consensus | Reads the same event stream and reports; additive |
| Second engine adapter | Protocol + registry exist; adding one touches no tool |
| Vector retrieval | `ContextProvider` protocol makes it a new implementation |
| S3 / object storage | `StorageProvider` protocol; keys already provider-agnostic |
| Teams, billing, permissions, quotas | No dependency in either direction |
| Real-time streaming to the UI | Events are durable and `seq`-ordered; polling now, SSE later, same data |
| Calibration against real customers | Needs runs to exist first; that is what this builds |

---

## Definition of done

- `POST /v1/runs` with only `{product_url, audience}` produces a `completed` run.
- `storage/projects/{project_id}/runs/{run_id}/` contains `screenshots/`,
  `recording.webm`, `events.json`, `report.json`.
- The event stream contains at least one `note` event — the customer reacted to
  something, which is the whole point.
- Every `claim_type=observed_behavior` finding cites an `event_seq` that resolves.
- A run with `task_completed=true` can still carry `would_continue=false` and
  `severity≠positive` findings, and the dashboard renders that without contradiction.
