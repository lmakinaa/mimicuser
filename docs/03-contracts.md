# Contracts

The seven interfaces the platform is built on. Each is defined once here and
implemented in exactly one module; nothing else in the codebase may redefine them.

| Contract | Definition | Implementations (MVP) |
|---|---|---|
| Persona schema | `backend/domain/persona.py` + `backend/personas/catalogue.json` | 16 catalogue entries |
| `AgentEngine` | `backend/engines/base.py` | `anthropic_api`, `claude_agent` |
| `Tool` | `backend/tools/base.py` | browser group, research group |
| `SessionEvent` | `backend/domain/events.py` | — |
| Evaluation | `backend/domain/findings.py` | `ResearchEvaluator` |
| `StorageProvider` | `backend/storage/base.py` | `LocalStorageProvider` |
| `ContextProvider` | `backend/context/base.py` | persona / product / task / workspace |

---

## 1. Persona schema

Data-driven. The catalogue is a JSON file validated against the Pydantic model at
import; adding an audience is a data change, never a code change.

```jsonc
{
  "id": "freelancer",
  "version": 1,
  "label": "Freelancer",
  "aliases": ["freelance designer", "contractor", "solo consultant", "indie"],
  "summary": "Independent professional billing a handful of clients directly.",

  "background": "Works alone or with one collaborator. Wears every hat …",
  "goals": ["Get paid faster", "Look professional to clients"],
  "motivation": "Spend less time on admin and more on billable work.",
  "typical_objections": ["Is this worth a monthly fee for 4 invoices?"],
  "preferences": ["Clear flat pricing", "Works on mobile"],
  "vocabulary": ["retainer", "scope creep", "net-30"],
  "context_notes": ["Often evaluating during a gap between client calls"],

  "traits": {
    "technical_ability": "medium",     // low | medium | high | expert
    "domain_knowledge":  "medium",     // none | low | medium | high
    "patience":          "low",        // very_low | low | medium | high
    "price_sensitivity": "high",       // low | medium | high | very_high
    "risk_tolerance":    "medium",     // low | medium | high
    "exploration_style": "goal_directed" // goal_directed | exploratory | skimming | methodical
  },

  "expected_behavior": [
    "Looks for pricing early, often before understanding the product",
    "Abandons sign-up flows that ask for company details they don't have"
  ]
}
```

**Traits are enumerations, not numbers.** A 0.0–1.0 "patience score" invites false
precision and arithmetic that means nothing. Named levels render cleanly into a prompt
and are honestly comparable across runs.

**Resolution** (`AudienceResolver`), in order:

1. **Exact / alias match** on the normalised audience string → `source=catalogue`.
2. **Token-overlap scoring** against label + aliases + summary, above threshold →
   `source=catalogue`, with the score recorded as `match_confidence`.
3. **LLM synthesis** — the model is given the schema, the audience string, and three
   nearby catalogue entries as few-shot, and returns a validated persona →
   `source=generated`.

Operator overrides are applied last as a shallow merge and flip `source=customized`.
The whole trace (`matched_id`, `method`, `match_confidence`, `overrides`) is frozen
onto the run.

---

## 2. `AgentEngine`

The engine owns its own loop. The platform owns the context and the tools.

```python
@runtime_checkable
class AgentEngine(Protocol):
    name: str

    def supports(self, model: ModelSpec) -> bool: ...

    async def run(
        self,
        *,
        run_id: UUID,
        system_prompt: str,
        opening_message: str,
        tools: ToolRegistry,
        model: ModelSpec,
        budget: RunBudget,
        emit: EventSink,
    ) -> EngineOutcome: ...
```

- **`tools`** — engine-neutral `Tool` objects. Each adapter translates them into its
  own native shape (`create_sdk_mcp_server` for the Claude Agent SDK; a
  `tools=[…]` array + tool-use loop for the Messages API). That translation is the
  *only* engine-specific code a new tool ever touches.
- **`emit`** — `async (EngineEvent) -> None`. Engines emit **only** the frozen
  dataclasses in `domain/events.py`. Adding a field there breaks every adapter at
  type-check time, which is the entire point.
- **`EngineOutcome`** — `stop_reason`, token counts, cost, turn count, error. Never
  a verdict about the product; engines report mechanics only.
- **`supports()` is written honestly.** No universal `return True`, no default-engine
  fallback — an unroutable `ModelSpec` raises `UnknownEngine`. (This is the debt the
  internal reference documents in `ENGINE_ABSTRACTION_DEBT.md`; we start clean
  because we ship two engines at once.)

**Tool-call events are emitted by the platform, not by engines.** `ToolRegistry`
wraps every handler with the recorder, so `tool_call` / `tool_result` /
`observation` events are byte-identical across engines. An engine that forgot to
report a tool call cannot produce a divergent trace.

**Registry.** Engines self-register at import. Resolution is
`explicit override → ModelSpec.engine → provider default → raise`.

---

## 3. `Tool`

```python
@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]           # JSON Schema
    handler: Callable[[dict], Awaitable[ToolResult]]
    group: str                             # "browser" | "research"
    records_artifact: bool = False

@dataclass(frozen=True)
class ToolResult:
    ok: bool
    payload: dict[str, Any]                # returned to the model
    observation: str | None = None         # human-readable, for the event stream
    artifacts: list[ArtifactRef] = ()
    error: str | None = None
```

### Browser group — handle-based, never coordinates

| Tool | Input | Notes |
|---|---|---|
| `look` | — | Settle, snapshot the page, screenshot. Returns url, title, and the filtered AX tree with `ref` handles. **The only way to see the page.** |
| `open_url` | `url` | Same-origin-ish navigation; blocked hosts refused |
| `click` | `ref` | |
| `type_text` | `ref`, `text`, `submit?` | Type + optional Enter in one action |
| `select_option` | `ref`, `value` | |
| `scroll` | `direction`, `amount?` | |
| `go_back` | — | |
| `wait` | `seconds` (≤10) | "I waited to see if it would load" — a real customer behaviour |

`ref` handles are minted per snapshot and stamped as `data-sc-ref` on the live DOM.
A stale ref returns `ok=false` with `"that element is no longer on the page — take
another look"`, which is both mechanically correct and exactly what a customer
experiences.

### Research group — the tools that make this not-QA

| Tool | Input | Purpose |
|---|---|---|
| `note` | `reaction`, `sentiment`, `about?` | An in-the-moment, first-person reaction. Confusion, delight, hesitation, a misread label. This is the raw material of the report. |
| `finish` | `outcome`, `reason`, `would_continue`, `would_recommend?` | The customer decides to stop. `outcome ∈ {accomplished, partially, gave_up, blocked, not_interested, satisfied_exploring}` |

`gave_up` and `not_interested` are **successful terminations**. Nothing in the system
treats them as errors, and the report's most valuable content usually follows one.

---

## 4. Session / Event schema

Append-only, `seq`-ordered per run. Persisted to Postgres and mirrored to
`events.json` in the artifact store.

```python
@dataclass(frozen=True)
class SessionEvent:
    run_id: UUID
    seq: int
    ts: datetime
    type: EventType
    url: str | None
    action: str | None            # tool name
    target: str | None            # ref handle + accessible name
    input: dict | None            # tool arguments
    result: dict | None           # tool payload
    observation: str | None       # human-readable line
    screenshot_key: str | None    # StorageProvider key
    error: str | None
    meta: dict                    # engine, tokens, sentiment, …
```

```python
class EventType(StrEnum):
    run_started       = "run_started"
    agent_message     = "agent_message"      # customer's narration
    agent_thought     = "agent_thought"      # reasoning, when the engine exposes it
    tool_call         = "tool_call"
    tool_result       = "tool_result"
    observation       = "observation"        # page snapshot taken
    navigation        = "navigation"
    note              = "note"               # customer reaction (the `note` tool)
    task_status       = "task_status"
    engine_error      = "engine_error"
    session_finished  = "session_finished"   # the `finish` tool
    run_completed     = "run_completed"
```

The stream is sufficient to reconstruct the session: every state change is either an
event or an artifact referenced by one.

---

## 5. Evaluation schema

```python
class Dimension(StrEnum):
    first_impression, value_proposition_clarity, discoverability, usability,
    friction, confusion, terminology, information_architecture, trust,
    motivation, perceived_value, pricing_perception, product_fit,
    purchase_intent, retention_intent, abandonment, feature_understanding,
    expectations_vs_reality, task_completion, customer_satisfaction

class ClaimType(StrEnum):
    observed_behavior = "observed_behavior"  # it is in the event stream
    interpretation    = "interpretation"     # the evaluator's reading of behaviour
    hypothesis        = "hypothesis"         # a proposal about cause or fix

class Severity(StrEnum):   critical | high | medium | low | positive
class Confidence(StrEnum): high | medium | low
```

```python
@dataclass(frozen=True)
class Evidence:
    event_seq: int
    url: str | None
    quote: str | None          # verbatim from a note / agent message
    screenshot_key: str | None

@dataclass(frozen=True)
class Finding:
    dimension: Dimension
    claim_type: ClaimType
    observation: str
    evidence: list[Evidence]
    impact: str
    severity: Severity
    confidence: Confidence
    recommendation: str | None
```

```python
@dataclass(frozen=True)
class RunReport:
    summary: str
    first_impression: str
    what_they_thought_it_was: str        # expectations vs reality, in their words
    task_completed: bool | None          # null in autonomous mode
    outcome: str                         # from the `finish` tool
    would_continue: bool
    purchase_intent: str                 # none | low | medium | high
    retention_intent: str
    abandonment_reason: str | None
    customer_satisfaction: int           # 1–5, a simulation parameter, not a metric
    quotes: list[str]
    findings: list[Finding]
```

Enforced rules:

- `claim_type=observed_behavior` ⟹ `len(evidence) ≥ 1` **and** every `event_seq`
  resolves on this run. Violations are rejected before persistence, not warned about.
- `severity=positive` is legal. A research system that can only report problems is a
  bug tracker.
- The evaluator is called with **structured outputs** (`output_config.format` +
  Pydantic), so schema conformance is a property of the request, not of parsing luck.

---

## 6. `StorageProvider`

```python
class StorageProvider(Protocol):
    async def write_bytes(self, key: str, data: bytes, content_type: str) -> str: ...
    async def write_json(self, key: str, obj: Any) -> str: ...
    async def read_bytes(self, key: str) -> bytes: ...
    async def read_json(self, key: str) -> Any: ...
    async def exists(self, key: str) -> bool: ...
    def url_for(self, key: str) -> str: ...
```

MVP: `LocalStorageProvider` rooted at `STORAGE_ROOT`, serving through the API.

```
storage/projects/{project_id}/runs/{run_id}/
    screenshots/0007-look.png
    recording.webm
    events.json
    report.json
```

Keys are **relative and provider-agnostic** — the day `S3StorageProvider` lands,
nothing above the storage layer changes. Path traversal is rejected at the boundary.

---

## 7. `ContextProvider`

```python
class ContextProvider(Protocol):
    name: str
    async def blocks(self, ctx: RunContext) -> list[ContextBlock]: ...

@dataclass(frozen=True)
class ContextBlock:
    title: str
    body: str
    priority: int    # ascending; stable ordering keeps the prompt prefix cacheable
```

MVP providers: `PersonaProvider`, `ProductProvider`, `TaskProvider`,
`WorkspaceProvider` (operator-supplied notes/files). A `VectorProvider` implements
the same protocol later; the composer does not learn a new shape.

Blocks are assembled in stable priority order specifically so the system-prompt
prefix is byte-identical across runs of the same persona — which is what makes
Anthropic prompt caching work across a 50-customer fan-out.

---

## Prompt/versioning note

`SYSTEM_PROMPT_VERSION` and `EVALUATOR_PROMPT_VERSION` are stamped on every run. A
finding produced under a different prompt version is not comparable to a newer one,
and calibration work (the long-term moat) is meaningless without this.
