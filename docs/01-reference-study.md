# Reference Study & Architecture Comparison

Six systems were read before any code was written. This document records what each
one actually does, what we take, and — more importantly — what we deliberately
refuse to copy.

---

## 1. UXAgent (`neuhai/UXAgent`) — Python, MIT

**What it is.** A research prototype from Northeastern HAI that simulates usability
testing with LLM agents on live web pages. Dual-process ("fast/slow") agent built on
a Generative-Agents-style memory stream.

**Architecture as built**

```
main/persona.py      → LLM-generated personas sampled from weighted demographics
agent/memory.py      → MemoryPiece{Observation, Thought, Plan, Reflection, Action}
                       with importance scoring + retrieval
agent/agent.py       → perceive → plan → act → feedback → reflect → wonder
agent/shop_prompts/  → one .txt prompt per cognitive stage
executor/env.py      → Playwright env; a JS parser script stamps `data-semantic-id`
                       on every interactive element; actions target those ids
main/survey.py       → post-run SUS-style questionnaire answered by the agent
```

**Take**

- **The persona is a *simulation parameter set*, not a prompt string.** Their
  `personas.json` carries Background / Financial Situation / Shopping Habits /
  Professional Life / Personal Style. We generalise this into a typed schema
  (§ `docs/03-contracts.md`).
- **Stable element handles injected into the DOM.** `data-semantic-id` is the right
  primitive: the agent names a *thing on the page*, not a CSS selector or a pixel.
  We do the same with `data-sc-ref`.
- **The post-run questionnaire.** Asking the synthetic customer structured questions
  after the session — separately from the acting loop — is the seed of our
  evaluator/agent split.
- **`api_trace/*.json` per LLM call.** Full request/response/latency capture makes
  runs auditable. We keep this as the session event stream.

**Do not take**

- **The hardcoded shopping domain.** Prompts live in `shop_prompts/`, the action
  prompt talks about jackets and sizes, the memory importance scale is calibrated to
  e-commerce. Our prompts are audience-parameterised, not vertical-parameterised.
- **The six-LLM-call cognitive cycle per step.** perceive + plan + action + feedback
  + reflect + wonder is ~6 calls per browser action. It is a legible research artifact
  and an unaffordable product. Modern tool-calling models do this natively inside one
  agent loop; we let the *engine* own the loop and spend our tokens on the evaluator
  instead.
- **`deny_list` string-replacement on the environment** (`"crime" → "***"`). Mutating
  the observed page to dodge refusals corrupts the very evidence we are collecting.
- **Fixed `max_steps` as the only stopping rule.** A customer stops because they are
  done, bored, or blocked — not because a counter expired. Budgets are a safety net,
  not the semantics.

---

## 2. Harness (`awizemann/harness`) — Swift, macOS, MIT

**What it is.** A native Mac app that drives an iOS Simulator, a macOS app, or a
`WKWebView` with an LLM agent. Explicitly framed as *"user tests, not scripted UI
tests"* — the closest thing to our product thesis that exists.

**Take**

- **The framing.** Persona + plain-language goal in, and three artifacts out:
  *did the goal complete*, *what was the path*, *where was the friction*. Our
  `RunReport` is a direct descendant.
- **Friction as a first-class, timestamped event.** Not a paragraph in a summary —
  a discrete record the agent emits mid-session with the screen it happened on.
  We make this the `note` tool.
- **Set-of-Mark targeting.** Numbered overlays on interactive elements; the agent
  clicks by id, never by pixel. Confirms the UXAgent/BrowserGym consensus.
- **"No agent scaffolding on disk."** The marked-up screenshot goes to the model;
  the clean screenshot goes to the artifact store. Replay and reports show what a
  *real user* would have seen. We adopt this invariant verbatim.
- **Settle gates instead of `sleep()`.** Poll for screenshot stability (dHash within
  Hamming distance 5) or DOM quiescence before observing. Fixed sleeps capture
  half-rendered pages, which the agent then honestly reports as broken UI — a
  synthetic finding with no real-world referent.

**Do not take**

- The entire implementation: Swift, WebKit-only, per-app Keychain credentials, Sparkle
  updates, xcodegen targets. Nothing here is reusable for a Python/Playwright service.
- **The single-run, single-machine model.** Harness is a desktop tool. Our unit of
  value is *N synthetic customers across M segments, aggregated* — that has to be a
  server-side, queued, multi-tenant shape from day one.

---

## 3. Scenario (`langwatch/scenario`) — Python/TS/Go, MIT

**What it is.** An agent *testing* framework built on simulation. Its whole design
premise is framework-agnosticism.

**Take**

- **`AgentAdapter.call(input) -> AgentReturnTypes` — one method.** The entire
  integration surface for an arbitrary agent is a single async method. This is the
  discipline our `AgentEngine` follows: a narrow protocol, engines behind it,
  zero engine types leaking upward.
- **`AgentRole` = USER | AGENT | JUDGE.** The judge is *an agent with a different
  role*, not a special-cased code path. Our Customer Agent / Research Evaluator split
  is the same idea, and it is what lets evaluation methodology version independently
  of the runtime.
- **`ScenarioResult` carries `reasoning`, `passed_criteria`, `failed_criteria`** —
  not a prose blob. Structured verdicts with named criteria.
- **The `script` escape hatch** — optional ordered control (`user()`, `agent()`,
  assertion, `succeed()`) layered over a free-running simulation. This is exactly the
  relationship between our Autonomous and Directed modes: same machinery, optional
  steering.

**Do not take**

- **`success: bool` as the top-level verdict.** Scenario is a *test* framework, so a
  scenario passes or fails. Our top-level result is *what the customer experienced*.
  A completed task with an unhappy customer is our most valuable output and would be
  a green test here. This is the single most important thing not to copy.
- **The message-list conversation substrate.** Scenario simulates a *chat*. Our
  substrate is a browser session; the analogue of "messages" is the event stream.
- The LangWatch event bus / telemetry coupling.

---

## 4. BrowserGym (`ServiceNow/BrowserGym`) — Python, Apache-2.0

**What it is.** The most mature open browser-agent *environment*. A Gym-style
`env.step(action)` loop over Playwright with a rigorous observation pipeline.

**Take**

- **The observation pipeline.** `_pre_extract` marks DOM elements with `bid`s and
  stamps dynamic attributes → `extract_dom_snapshot` + `extract_merged_axtree` +
  `extract_dom_extra_properties` → `_post_extract` cleans up. The *merged AX tree
  keyed by injected bid* is the state of the art for giving an LLM a page, and
  `flatten_axtree_to_str` with `filter_visible_only` is the practical serialisation.
- **The retry-on-detached-frame loop** around extraction. Real pages mutate mid-read;
  `Frame was detached` / `Execution context was destroyed` are normal, not
  exceptional. We copy the retry semantics.
- **The action-space taxonomy** (`bid` / `coord` / `nav` / `tab` / `chat` / `infeas`).
  Our tool surface is a deliberate subset: `bid` + `nav` + a research group.
- **`report_infeasible(reason)`.** A first-class way for the agent to say *this cannot
  be done*, rather than flailing until the step budget dies. Ours is `finish(outcome=
  "blocked")`.

**Do not take**

- **The Gym `step/reset/reward` framing.** Reward-per-step is a benchmarking
  abstraction. Our loop is driven by the model's own judgement about a customer's
  intent; there is no scalar reward and pretending otherwise would distort the design.
- **`coord` actions (raw x/y mouse).** They produce brittle, unreplayable traces and
  destroy the evidentiary value of a session. Handle-based only.
- **`AbstractBrowserTask` + the benchmark registry.** We are not building a benchmark.
- The `numpy`/`gymnasium` dependency weight in the observation dict.

---

## 5. WebArena (`web-arena-x/webarena`) — Python, Apache-2.0

**What it is.** A reproducible benchmark of realistic web tasks on self-hosted site
clones, with deterministic evaluators.

**Take**

- **`Trajectory = list[StateInfo | Action]`.** A session is an interleaved sequence of
  states and actions, and *that list is the artifact*. Everything downstream —
  replay, evaluation, aggregation — reads the trajectory, never the live browser.
  Our `SessionEvent` stream is this idea with the state made durable (screenshots on
  disk, snapshot text in the event).
- **`EvaluatorComb`** — composing several evaluators over one trajectory and combining
  their verdicts. Our `RunReport` is produced by an evaluator that will grow siblings
  (heuristic friction detectors, cross-run consensus) without changing the runtime.

**Do not take**

- **The evaluator implementations themselves** — `string_match`, `url_match`,
  `program_html` all answer *"did the agent reach the correct end state?"*. That is
  the QA question. Ours is *"what did this person experience?"*, which no
  string match can answer.
- **The self-hosted-clone environment model.** We evaluate the customer's real,
  live product.
- `beartype`-decorated call signatures and the config-file-per-task convention.

---

## 6. `refs/vl-app-…/agents_service` — internal reference, Python/FastAPI

**What it is.** A production multi-tenant agent service. The most directly reusable
codebase we have, because it has already solved the two problems the brief calls out.

**Take**

- **The engine registry + `AgentEngine` Protocol** (`runtime/engines/`). Duck-typed
  `Protocol`, engines self-register at import, `for_llm()` resolves override →
  llm-level → provider default. We copy the shape and simplify the resolution.
- **`runtime/engines/events.py` — typed event classes as the engine output contract.**
  This is the load-bearing idea for engine-agnosticism: adding a field to a frozen
  dataclass surfaces every unimplemented engine at type-check time. A JSON-blob
  contract could not do that. We copy it wholesale.
- **`runtime/engines/claude/tools_adapter.py`** — the *single* engine-specific seam
  between a generic `ToolSpec` and `create_sdk_mcp_server`/`@tool`. Adding a tool
  group touches no engine; adding an engine touches no tool group. We copy this
  design exactly.
- **The Anthropic OAuth PKCE copy-code flow** (`app/api/v2/endpoints/oauth_flow.py`).
  This is the piece that lets a Claude *subscription* drive the agent instead of a
  metered API key. Specifically worth copying:
  - PKCE with the state payload **carried in the state parameter itself**, HMAC-SHA256
    signed with the app key — no Redis, no server-side state, no "is Redis up?"
    failure mode on the auth path.
  - The copy-code flow (user pastes the code from
    `platform.claude.com/oauth/code/callback`) — no public redirect URI to register.
  - **`SELECT … FOR UPDATE` around token refresh + a freshness re-check.** Anthropic
    rotates the refresh token on every use and kills the previous one, so a thundering
    herd of concurrent 401s must be collapsed into exactly one rotation. This detail
    is not obvious and is expensive to rediscover.
  - Tokens encrypted at rest; `get_access_token()` decrypts at the repo boundary.
- **The layer contract** (`docs/CODING_RULES.md`): `app/ → runtime/ → repositories/`,
  strictly one-way, enforced by an import linter in CI. Endpoints do auth → call →
  serialise, and nothing else.
- **`TurnResult.events`** — persisting the emitted event list so replaying it through
  the frontend handler reconstructs the session identically to the live stream. One
  code path for live and historical rendering.

**Do not take**

- **The LLM-proxy indirection** (`ANTHROPIC_API_KEY: "ref:v2:{llm_id}"` +
  `ANTHROPIC_BASE_URL: proxy`). It buys per-request credential resolution and
  cross-provider normalisation for a fleet of services. For a single-service MVP it
  is a second deployable on the critical path of every run. We resolve credentials
  in-process and keep the seam (`ModelSpec.credential`) so a proxy can slot in later.
- **`ClaudeEngine.supports()` returning `True` unconditionally** — their own
  `docs/ENGINE_ABSTRACTION_DEBT.md` flags this as debt that becomes a bug the moment a
  second engine registers. We are shipping two engines on day one, so we write
  `supports()` honestly from the start.
- **`_DEFAULT_ENGINE` silent fallback.** Same document, same reason. Unknown provider
  raises.
- Multi-tenancy, org memory, bubblewrap sandboxing, skills symlink farms, rewind/fork,
  slash commands, delegation tokens. All correct for that product; all pure cost here.

---

## Synthesis — what the six agree on

| Concern | Consensus | Our decision |
|---|---|---|
| Element targeting | Injected stable handles (`bid` / `data-semantic-id` / SoM ids). Never CSS, never pixels. | `data-sc-ref`, assigned per snapshot |
| Page representation | Accessibility tree, visible-only, filtered, flattened to text | Same, plus screenshot |
| Session artifact | An ordered trajectory of state+action, durable, replayable | `SessionEvent` stream + artifacts on disk |
| Engine coupling | One narrow method (`call` / `run_turn`), typed output contract | `AgentEngine.run()` + frozen event dataclasses |
| Evaluation | Separate role/pass from acting | `CustomerAgent` ≠ `ResearchEvaluator` |
| Timing | Settle on quiescence, never `sleep()` | Network-idle + DOM-mutation gate |

## Synthesis — where they all fall short for us

Every one of the five open-source systems answers a **success/failure** question:
did the agent complete the task, did the assertion hold, what is the reward.

None of them can express the finding the brief names as the product's whole point:

```
Task completed: YES
"I didn't understand why I needed a Workspace."
"I would probably try an alternative before committing."
```

That gap is the design centre of everything in `docs/02-domain-model.md` and
`docs/03-contracts.md`: task completion is demoted to *one field among twenty
dimensions*, findings separate **observed behaviour** from **agent interpretation**
from **hypothesis**, and the customer agent is given tools to be confused, to
hesitate, and to give up — because those are outputs, not failures.
