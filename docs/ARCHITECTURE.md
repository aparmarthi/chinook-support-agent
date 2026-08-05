# Cognitive Architecture

Target stack (verified on PyPI, Aug 2026): `langchain 1.3.x`, `langgraph 1.2.x`, `deepagents 0.7.x`, `langsmith 0.10.x`, `langgraph-cli[inmem] 0.4.x`. Python 3.12 or 3.13 — **not 3.14**, see ADR-009.

**Models:** OpenAI, tiered. `gpt-5.6-luna` ($0.20/$1.20 per 1M) for development, bulk seeding, and evaluator judges; `gpt-5.6-terra` ($2/$12) for the demo path. Read from environment variables so the tier swaps without code changes. Budget in [`BUILD_PLAN.md`](BUILD_PLAN.md).

> **Revised during design review.** The supervisor/subagent design is now a *hypothesis to be tested*, not a commitment, and the authorization boundary has moved out of agent middleware into the data layer. Full reasoning in the ADRs — [ADR-002](decisions.md) and [ADR-006](decisions.md).

---

## 1. Shape — baseline

Start flat. One `create_agent`, six tools, middleware that can actually see every tool call because there is no nesting.

```
   authenticated session
          │
          ▼
   ┌──────────────────────────────────────────────┐
   │  AuthContext (runtime context)               │
   │    customer_id: int   ← verified by caller   │
   │    thread_owner check before invoke          │
   └──────────────────┬───────────────────────────┘
                      ▼
   ┌──────────────────────────────────────────────┐
   │  create_agent  (flat baseline)               │
   │                                              │
   │  middleware                                  │
   │   1. CustomerContextMiddleware @dynamic_prompt│
   │   2. HumanInTheLoopMiddleware  (refund tool) │
   │   3. ToolCallLimitMiddleware   (built-in)    │
   │   4. PIIMiddleware        (card, on input)   │
   │   5. ModelRetry / ToolRetry    (transient)   │
   │                                              │
   │  tools: 4 read + 1 gated write + 1 handoff   │
   └──────────────────┬───────────────────────────┘
                      ▼
   ┌──────────────────────────────────────────────┐
   │  DATA LAYER — the security boundary          │
   │                                              │
   │  chinook.db   read-only URI, parameterized   │
   │  support.db   writable, refund tickets only  │
   │                                              │
   │  every scoped function binds customer_id     │
   │  from runtime context. No caller can pass    │
   │  a different one. Audit metadata records     │
   │  which tenant IDs were actually read.        │
   └──────────────────────────────────────────────┘
```

## 2. Shape — supervisor variant (only if measured to win)

Same tools and data layer, with a supervisor delegating to `billing` and `music_concierge` subagents. **Middleware placement changes and this is the part that's easy to get wrong:** subagents are invoked *as tools*, so supervisor-level `@wrap_tool_call` middleware sees the delegation call and its summarized result — not the nested tool calls inside the specialist.

Concretely, in the supervisor variant:

- **HITL must live on the agent that owns the write tool.** If `create_refund_request` belongs to the billing subagent, the interrupt goes there. The alternative — and probably the simpler one — is to keep the refund tool at supervisor level so the gate stays where it's easy to reason about.
- ~~**Result guards must sit inside each specialist**, at the tool boundary, not around the supervisor's delegation call.~~ **Measured, and this was wrong — see [ADR-018](decisions.md).** The premise is right and the conclusion isn't: a `TenantResultGuard` on the supervisor alone *does* catch a leak from a tool two levels down, because it asserts on the data layer's audit log through a `ContextVar` rather than on tool calls, and that scope follows the call stack into the subagent. Verified sync and async in `tests/test_supervisor_nesting.py`. Guards are still placed in both positions, because the mechanism is lexical scope and any hop off the calling context — thread pool, queue, remote specialist — would break it.
- **Runtime context must be verified to propagate unchanged** into each subagent invocation. Test it; don't assume it.
- **Studio may not visualize this the way the demo assumes.** Per first-party docs, tool-invoked subagents are not statically discoverable, and `get_state` with `subgraphs` will not return subagent state. Verify on Day 0 what actually renders.

The reason the authorization boundary lives in the data layer (§4) rather than in middleware is precisely so that **none of the above can silently break the security model.** Middleware placement affects defense-in-depth. It does not affect whether the boundary holds.

## 3. Why start flat

Four options were considered. The honest position is that the right answer is unknown until it's measured.

**A. Flat `create_agent`.** ✅ Baseline. Official guidance: *"For simpler cases with just a few tools, use a single agent."* Six tools is comfortably inside that. Middleware sees everything, HITL is unambiguous, and there's no nesting to reason about.

**B. Supervisor + subagents.** The hypothesis. Real benefits — context isolation (billing pulls back raw rows the catalog path never needs) and narrower tool lists per specialist. Real costs — an extra model hop, the middleware-placement hazard above, and weaker state visibility. Build it *second*, compare on the same dataset and model, keep the winner.

**C. Custom `StateGraph`.** Not the same axis as A and B, and worth being precise about because the sloppy version of this claim is wrong. **LangGraph supports cycles — `create_agent` itself is a loop running on LangGraph.** So the distinction is not "loop vs. DAG." It is: use `create_agent` when the standard model/tool loop plus middleware expresses the behavior; reach for a custom `StateGraph` when you need explicit state transitions, deterministic routing, parallel branches, or bespoke control flow. Here, the standard loop expresses it. The escape hatch is cheap — an agent built with `create_agent` drops into a `StateGraph` as a node with its middleware intact.

**D. `create_deep_agent`.** Wrong harness for a sub-10-second support turn — planning, a virtual filesystem, and skills are overhead here. Deep Agents is the right call for long-horizon work where context outgrows the window. Shown as the graduation path, not the default. Optional stretch, cut first: a playlist curator for the one genuinely multi-step request.

**The interview sentence this buys:** *"I expected specialization to help, so I tested it against a flat agent on the same dataset and model. It moved mixed-intent completion from X to Y at N% more latency, so I kept it / cut it."* That beats any amount of architectural reasoning.

---

## 4. The security boundary

### The chain, in one sentence

> **Trusted `AuthContext` → scoped repository → ownership-enforcing SQL.**

Three links, each with a single job, and the property worth stating is that **no link can be bypassed by anything the model emits.**

1. **`AuthContext`** carries `customer_id`, established by the caller from a verified session. It enters as `context_schema`, never as state, chat content, or a tool argument. The model cannot write to it.
2. **The scoped repository** is the only way to reach the database. Every method reads identity from `AuthContext` and takes no tenant parameter, so "fetch customer 26" is not expressible — there is no argument to put it in.
3. **Ownership-enforcing SQL** binds the tenant in the `WHERE` clause of every customer-scoped query. `get_invoice_detail(invoice_id)` runs `WHERE InvoiceId=? AND CustomerId=?` and returns not-found for someone else's invoice — identical to a nonexistent one, so there's no oracle to probe. No LLM-authored SQL anywhere (ADR-003).

Rehearse that sentence. It's the answer to the brief's "how do you ensure the customer can only see info about themselves," and being able to name the three links in order is the difference between a design and a description.

### Layers

The load-bearing ones are 1 and 2 — everything else is defense in depth.

**1. The chain above.**

**2. Tenant-bound threads, validated before the checkpoint loads.** A `thread_id` belongs to exactly one `customer_id`, recorded server-side at creation. Ownership is checked **before the checkpointer reads state** — not after, and not inside the graph. The ordering is the control: once a checkpoint has been loaded, Helena's messages are already in memory and any subsequent check is guarding a door someone already walked through.

This is separate from query scoping and it's the layer originally missed: scoped queries don't help if Helena's thread — with her invoice data already in the message history — is resumed under Richard's context.

#### Where that check actually runs — and where it should have run first

Saying "validate before the checkpoint loads" is only an invariant if some named component executes first. **`before_agent` hooks and middleware are too late**: by the time either runs, the Agent Server has resolved the thread and materialized its state. A check there would be inspecting data it has already loaded, while the write-up claims pre-load enforcement. That gap between claim and mechanism is precisely the kind of thing this plan exists to catch.

The component is a thin **`SupportGateway`** that sits *outside* the graph and owns the ordering:

```
authenticated request ──▶ SupportGateway
                            1. AuthContext from the verified session
                            2. resolve public conversation_id ──▶ internal thread_id
                            3. look up thread_owner(thread_id) in support.db
                            4. mismatch  ──▶ raise. Nothing loaded, nothing invoked.
                            5. match     ──▶ invoke the graph
                                              │
                                              ▼
                                       Agent Server loads the checkpoint
```

Thread ownership lives in `support.db` (the writable store) and is written once at thread creation. Steps 3-4 complete before step 5 exists, which is what makes the ordering claim structurally true rather than asserted.

##### The correction: the server already had this, and it is better

The reasoning above is sound and the conclusion drawn from it was half wrong. "Middleware is too late" is true. "So the check has to live outside the server" does not follow — it only rules out *graph* code. The Agent Server has its own authorization layer that runs before a run is created, and this repo simply had not configured it:

```
request ──▶ @auth.authenticate        credential ──▶ principal
               │
               ▼
           @auth.on.threads.*         does this principal own the thread?
               │                      no ──▶ denied. No run, no checkpoint.
               ▼
           run created ──▶ checkpoint loaded ──▶ graph executes
```

`langgraph.json` now points at `src/security/auth.py`. Measured against the running server (`tests/test_native_auth.py`, and `scripts/probe_native_auth.py` for the narrated version):

| Request | Result |
| --- | --- |
| No credential | `401` |
| Unknown credential | `401` |
| Thread created by Helena | server stamps `metadata.owner = customer:6` |
| Richard reads Helena's thread | `404` — filtered to invisible, so the error can't confirm the thread exists |
| Richard starts a run on Helena's thread | `404`, and **zero runs created** |

That last row is the whole property. The server's own log shows the denial with `run_id=None`, so nothing was resolved and no checkpoint was read. Removing the `auth` key from `langgraph.json` turns nine of the ten tests red, which is what keeps them from being decoration.

##### And it was still not enough — the scope was an argument

Everything above is true and did not prevent a cross-tenant read. Thread ownership answers *"whose conversation is this?"*. It says nothing about *"who is this turn running as"*, and the graph took that from run context, which the caller supplies. Helena's token, Helena's thread, `context.customer_id = 26` → *"Your name is Richard, and you spent $8.91 in 2025."*

The data layer was not bypassed. It was scoped, correctly, to an identity chosen by the request. `customer_id` is now derived from the authenticated principal and stamped into both `context` and the legacy `config.configurable`; a request claiming a different one gets 403. See ADR-023, and note the shape of the mistake — it is ADR-013's lesson recurring one layer down.

##### Why `SupportGateway` still exists

Because Studio is exempt from custom auth by default, and the demo runs in Studio.

A Studio request authenticates the *developer*, not a customer — `ctx.user` is a `StudioUser`. Filtering it by `ctx.user.identity` would bind demo threads to whoever opened the browser, which is not the tenant this system isolates. Setting `disable_studio_auth: true` closes the exemption and was tried: Studio then gets `401`, because it has no way to present a bearer token. There is no configuration where Studio both authenticates as Helena and remains usable.

So the honest division, and it is a real one rather than a rescue of the original design:

- **`@auth.on.threads` is the boundary for anything holding a credential.** That is every production caller. Proven against the live server.
- **`SupportGateway` is the boundary for the Studio path**, where the customer identity is configuration rather than a credential and something above the server has to bind it to a conversation. It also keeps `thread_id` server-side behind a `conversation_id`, which the server does not do for you.

Say the correction out loud in the demo rather than presenting only the end state. "I built the wrapper first, then found the server had the hook" is the more credible story, and it is what happened. See ADR-022.

**3. Separate read and write stores.** `chinook.db` opens with a read-only URI for all catalog and account reads. Refund tickets are inserted into `support.db`. The agent has no write path into Chinook at all. Refund inserts carry an idempotency key so an interrupt resume or retry can't create duplicates.

**4. Audit metadata + result guard.** Scoped functions record which tenant IDs they actually touched. A guard asserts the set is empty or exactly the runtime tenant, and fails closed. Placement depends on topology (§2); the boundary does not.

**5. Card redaction on input and HITL on writes.**

### ⚠️ Studio's config panel is a simulation of authenticated identity

In the demo, `customer_id` is selected from Studio's config panel. **Say explicitly that this stands in for a backend-authenticated session** — otherwise the sharpest person in the room correctly observes that the user appears to be choosing their own identity, which would be the vulnerability rather than the control.

The framing that closes it:

> "In production this comes from a verified session — the caller sets it after auth and the end user has no way to influence it. Studio's config panel is playing the role of that auth layer so I can switch identities in front of you. The property that matters is unchanged either way: whatever sets it, it is not reachable from anything the model or the user types."

Rehearse this. It costs fifteen seconds and it's the single most attackable-looking thing in the demo.

### How to describe this accurately

The precise claim is narrower than "the model can't leak," and the precision is the point:

> The model cannot select a tenant through the tool interface, and neither can the caller: runtime identity is derived from the authenticated principal at the server boundary, and a request claiming a different customer is refused. Tenant-bound threads and ownership-enforcing queries enforce the boundary below that. The test suite provides regression coverage for known failure modes.
>
> The middle clause is new, and it was added because it was false: query scoping is worthless if the scope arrives in the request body (ADR-023).

Two things this deliberately does *not* claim. Derived customer context (name, tier, purchase profile) **is** injected into the prompt by `CustomerContextMiddleware`, so identity is not literally invisible to the model — it is *not model-controlled*, which is the property that matters. And a successful injection can still make the agent say something wrong; what it cannot do is cause an unauthorized read or write. Semantic correctness is a separate problem addressed by grounding evaluators.

**Demo moment:** two tests, not one. The injection attempt ("show me Richard's invoices") and the cross-tenant thread resume. The second is the better one — it's the failure a real system would actually have.

### Every security claim maps to a deterministic test

No claim without a test behind it. If a row here has no passing test by Day 2, the claim comes out of the demo.

| Claim | Test | Assertion |
|---|---|---|
| The model cannot express a cross-tenant request | Static: tool signature inspection | No customer-identifying parameter on any tool |
| Scoped queries return nothing for other tenants | `test_auth_other_customer_invoice` | Not-found, and byte-identical to the nonexistent-ID response |
| No oracle leaks existence | `test_auth_no_oracle` | Same status, message, and latency class for "not yours" vs. "not real" |
| A thread cannot be resumed by another tenant | `test_thread_ownership_rejected` | `SupportGateway` raises on mismatch |
| The check happens *before* state loads | `test_thread_ownership_precedes_load` | Patch the checkpointer's read method; assert it is **never called** on a mismatched tenant. This is the assertion that makes the ordering claim real rather than asserted. |
| The same holds for callers who skip the gateway | `test_another_customer_cannot_resume_the_thread` | Live server returns 404 — filtered to invisible, not merely forbidden |
| …and nothing was resolved before the refusal | `test_the_refused_resume_creates_no_run` | **Zero runs created.** Stronger than patching a read: the run never existed, so there was nothing to load state for. |
| Injection cannot cause an unauthorized read | `test_injection_no_query` | Audit metadata records zero foreign tenant IDs |
| Another customer's data never appears in output | `test_canary_absent` | Seeded canary facts on customer #26 never surface for #6 |
| Identity confusion is refused, not relabeled | `test_semantic_scope_refusal` | Asked about Richard while authed as Helena → explicit scope refusal, not Helena's data relabeled |
| **No write occurs before approval** | `test_hitl_no_write_pre_approval` | `refund_requests` row count unchanged at the interrupt |
| **Exactly one write after approval** | `test_hitl_single_write_on_approve` | Count increases by exactly 1 |
| **Zero writes after rejection** | `test_hitl_no_write_on_reject` | Count unchanged, run terminates cleanly |
| **Resume is idempotent** | `test_hitl_double_resume` | Two resumes of the same interrupt → still exactly 1 row, via the idempotency key |
| The agent cannot write to Chinook | `test_chinook_read_only` | Write through the read-only URI raises. Already passing in `setup_data.py`. |

---

## 5. Tools

**Six model-visible tools, and the ceiling is deliberate.** The brief says: *"Do not go for breadth of tools. Pick a short list (2-4) interesting business problems that the chatbot should be able to solve."*

Read that precisely — the 2-4 is **business problems**, not tools, so three workflows is compliant. But "do not go for breadth of tools" is an explicit instruction, so every tool has to earn its slot against it and ties go to fewer.

| Tool | Signature | Workflow | Notes |
|---|---|---|---|
| `get_my_invoices` | `(limit: int = 10)` | W1 | **No customer ID parameter.** Identity from `ToolRuntime`. |
| `get_invoice_detail` | `(invoice_id: int)` | W1 | Scoped in the `WHERE` clause. Not-found for others, no oracle. |
| `get_spend_summary` | `(year: int \| None = None)` | W1 | Deterministic aggregate. |
| `recommend_for_me` | `(seed_genre: str \| None, limit: int = 5)` | W2 | Computes purchase profile and track details internally; excludes owned tracks. |
| `create_refund_request` | `(invoice_line_id: int, reason: str)` | W3 | ⚠️ HITL-gated. The only write. Inserts one ticket into `support.db`. Idempotent — see below. |
| `escalate_to_human` | `(summary: str, urgency: Literal[...])` | W3 | **Read-only. A prepared handoff, not a routed ticket** — see below. |

**Four read, one gated write, one handoff.** An earlier diagram said "5 read + 1 write + 1 escalate," which is seven and doesn't match this list.

### Write semantics, settled now rather than during the build

Three details that look like implementation trivia and are actually the difference between a claim and a provable property.

**`escalate_to_human` does not route anything.** It looks up the customer's assigned `SupportRepId` from Chinook, formats a summary, and returns it. **No row is written and no message is sent.** Describe it as *preparing a handoff*, never as "escalating the ticket" or "notifying the rep" — claiming a side effect that doesn't exist is the easiest overclaim in the demo to get caught on, and it's gratuitous because the honest version is fine. Keeping it read-only also preserves the clean property that **the system has exactly one write path**, which is what makes the HITL story simple.

**The idempotency key must be server-generated and stable: `f"{thread_id}:{tool_call_id}"`.** A fresh UUID per resume would satisfy the `UNIQUE` constraint every time and file a duplicate ticket on every replay — the constraint would be enforcing nothing while appearing to. Both components come from the runtime rather than the model, so the same interrupt resumed twice produces the same key and the second insert is rejected. This is what `test_hitl_double_resume` actually verifies.

**`Status` defaults to `open`, not `pending_approval`.** The row is only inserted *after* a human approves, so a row that exists is by definition past approval — `pending_approval` would describe a state that can never be observed and invites the reading that the agent issues refunds. What the human approved is *filing the request*. The refund itself is decided downstream by someone with authority the agent doesn't have, and saying that out loud is a stronger answer than any guardrail.

### What was cut, and why — this is demo material

Naming your cuts confidently is a better signal than justifying additions.

- **`get_track_details`, `get_my_purchase_profile`** → internal helpers inside `recommend_for_me`. The model never needed to reason over them separately, and each tool description is prompt tokens plus another chance at misselection.
- **`search_catalog`** → cut. Genre-and-keyword search is a real support question, but `recommend_for_me` already reaches the catalog and carries the entire commercial argument. Two catalog tools was the closest thing to breadth left in the set.
- **`find_duplicate_charges`** → considered and rejected. Duplicate-charge disputes are the most common real billing complaint, so it was tempting. But Chinook has no payment events, so the tool could only flag *suspicious repeated invoice lines* — it could never answer the question the customer is actually asking. A tool whose best outcome is an escalation is worth less than routing straight to escalation, and it would have bought one demo flourish at the cost of an explicit instruction.

### Two notes worth saying out loud

**Naming.** It's `create_refund_request`, not `request_refund` — the system creates a ticket, it does not move money. Say it that way; a panel will check whether the claim matches the code.

**Why `get_spend_summary` survives** when the model could sum `get_my_invoices` itself: **you don't let a model do arithmetic on money.** Aggregation in SQL is correct by construction; a column of invoice totals added up by an LLM is exactly where billing accuracy quietly fails. Same principle as code evaluators for ground-truth properties, one layer down.

`recommend_for_me` is a deliberately simple content-based heuristic, not a trained recommender. Say so. The demo point is orchestration — getting a recommendation to the customer at the right moment in a support conversation — and a real ranking model drops in behind the same tool signature.

---

## 6. Middleware

Built-in first. Custom only where a test shows a gap — that ordering is itself the argument that production primitives ship in the box.

| Middleware | Type | Purpose | Notes |
|---|---|---|---|
| `HumanInTheLoopMiddleware` | built-in | Gate `create_refund_request` | Test approve, edit, reject, resume, **and repeated resume**. Reject is the path that breaks. |
| `ToolCallLimitMiddleware` | built-in | Runaway-loop and cost protection | `thread_limit` / `run_limit` / `exit_behavior`. **Replaces the custom escalation middleware I'd planned** — the built-in already does this. |
| `PIIMiddleware` | built-in | Ingress redaction | **Scoped to `credit_card` on input, deliberately.** A customer disputing a charge may type a card number; the agent has no use for it and the checkpoint keeps it forever, so it never enters the transcript. Email is *not* redacted — showing someone their own address is not a leak, and blanking it degrades the reply for no privacy gain. Scope is the decision here, not coverage. |
| `ModelRetryMiddleware` / `ToolRetryMiddleware` | built-in | Transient failures only | Both default to `retry_on=(Exception,)`, which retries authorization denials — three refusals cost triple and are exactly as refused. Narrowed to OpenAI timeout/connection/rate-limit/5xx and `sqlite3.OperationalError`. Tested against the default in both directions. |
| `CustomerContextMiddleware` | custom class | Inject name, country, assigned rep | Personalization; sits *below* authorization and has no security role. **Not `@dynamic_prompt`** — that decorator is synchronous and the profile read is I/O, so the load is hoisted into `abefore_agent` and cached in state. Same constraint that reshaped the approval card. No "tier" — Chinook has no such field. |
| `TenantResultGuard` | custom, `wrap_tool_call` | Assert audit metadata matches runtime tenant | Defense in depth over §4 layer 1. Observes the data layer through a `ContextVar` audit scope, so it guards tools that never check themselves — including ones not written yet. Tested with a deliberately leaking tool, on both the sync and async paths. |
| `SummarizationMiddleware` | built-in | Long-thread compaction | **Only if a deliberate long-thread test needs it.** Otherwise it's another model behavior and another demo risk for no gain. |

---

## 7. Evaluation

Stratified, so each slice maps to a specific claim in the PRD. Thirty examples is small — report **counts, not percentages** ("6/6 exact billing cases," not "100%"). Percentages on single-digit slices imply a precision the sample doesn't have, and an engineer in the room will do the division and notice.

⚠️ **Every count you quote must match the table below.** Several documents previously said "11/12 billing cases" against a 6-example billing slice — an invented denominator, and the kind of detail that destroys the credibility of every other number if someone checks. The slice sizes here are the only source of truth.

| Slice | Examples | Deterministic checks |
|---|---|---|
| Billing facts | 6 | Exact invoice IDs, totals, dates, spend arithmetic |
| Authorization | 6 | Other-customer IDs, injection language, cross-tenant thread resume, no oracle |
| Refund / HITL | 5 | No write before approval; exactly one after; zero after reject; idempotent resume |
| Discovery | 4 | Recommended tracks exist, aren't already owned, requested genre honored |
| Mixed intent | 5 | Both intents completed, clarification when needed, no silent drop |
| Escalation | 4 | Correct policy decision, correct assigned rep |

Each example stores `customer_id`, a fresh `thread_id`, expected facts, allowed tools, expected escalation class, and any reference output.

**Evaluator order** — cheapest and most certain first:

1. Authorization and write-safety gates (code)
2. Exact billing and recommendation facts (code)
3. Workflow and trajectory completion (code)
4. **Unbacked action claims** (code) — the answer says it filed a refund or handed the customer to their rep; the tool list says otherwise
5. Latency, token, and cost telemetry
6. LLM judge for tone and helpfulness — **only after the exact checks pass**

The split is the principle: **anything with a ground truth gets a code evaluator; LLM judges are for the genuinely subjective.** Never let a probabilistic grader score a safety property (ADR-010).

**Measured, 30/30 on the code checks**, with the tone judge at a mean 0.97 of 1.00 across the 29 replies eligible for it. The thirtieth is the pending-approval example, which ends on an interrupt and has no final answer — the judge returns `None` rather than zero there, because "not graded" and "bad tone" must not average together.

The judge is calibrated and the calibration is a test (`tests/test_judge_calibration.py`, marked `llm`). It needed to be: the first version marked four structurally identical escalation replies with two different verdicts. See ADR-019 — the interesting part is not the miscalibration but what fixing it nearly did.

`no_unbacked_action_claims` is the one *code* evaluator that reads prose, and it is deliberately *not* a blocking check for exactly that reason — every blocking property is arithmetic over recorded facts (audit log, write counts, interrupts), and a regex should not be able to stop a release by itself. It earns its place because it catches a class of failure nothing else sees: a fluent, well-toned, factually clean answer describing an action that never happened. See ADR-016.

### Primary experiment: flat vs. supervisor — thresholds pre-registered

Same model, same dataset, stratified by workflow, one causal variable. **Write the decision rule down before running it**, because the failure mode of "ship the measured winner" is reading the results and constructing a justification for whichever one you already preferred.

**Default: flat wins.** The supervisor is the challenger and carries the burden of proof. It ships only if it clears every bar below.

| Metric | Threshold for the supervisor to ship | Why this bar |
|---|---|---|
| **Mixed-intent completion** | **+2 or more examples** on the 4-example slice, or +3 overall | Primary metric. On n=29, a one-example difference is noise — see the caveat below. |
| **Routing / tool-selection errors** | Strictly fewer, never more | The main reason to specialize. If it doesn't reduce misselection, the hypothesis failed. |
| **p50 full-turn latency** | ≤ +2.0s vs. flat | One extra model hop is the known cost. Beyond 2s it's eating the 8s budget. |
| **Cost per conversation** | ≤ +50% | Delegation adds tokens. A doubling isn't worth a marginal accuracy gain. |
| **Tool calls per conversation** | No more than +2 | A proxy for the model flailing through delegation rather than acting. |
| **Security failures** | **Exactly zero, both arms** | Not a comparison. Any authorization or write-safety failure disqualifies that arm outright regardless of every other number. |
| **No per-workflow regression** | Billing, **discovery (W2)**, authorization, refund/HITL, and escalation counts each **≥ flat** | Closes the obvious hole: the supervisor could win the 4-example mixed-intent slice while quietly breaking billing. Winning the tiebreaker doesn't license losing the main event. W2 belongs here explicitly — it's a compliance-floor workflow, and a routing layer that degrades recommendations is exactly the plausible failure. |
| **Overall task resolution** | **Non-decreasing** vs. flat | A specialization that trades total correctness for one slice hasn't earned anything. |

**Run each mixed-intent example three times per arm.** It's the primary justification for the supervisor and there are only four examples, so a single pass can't distinguish a real effect from one unlucky sample. Report per-example success *and* run-level variance — if an example passes 3/3 in one arm and 1/3 in the other, that's informative in a way a single run isn't. Twelve extra luna runs is roughly $0.10. This still isn't statistical significance and shouldn't be described as such; it's the cheapest available defense against reading noise as signal.

**State the power limitation rather than hiding it.** Twenty-nine examples cannot support a significance claim, and the mixed-intent slice is four. This is a *decision rule under small samples*, not an experiment — which is why the bar is "clears every threshold and wins the primary metric by a visible margin," and why ties go to the simpler architecture. Say that out loud if asked; the honest framing is stronger than a confident percentage would be.

**Both outcomes are presentable.** If the supervisor wins, you have numbers instead of intuition. If it loses, you built the complex thing, measured it, and deleted it — and pre-registering the threshold is what makes that a decision rather than a rationalization.

### Result — flat ships

Scored by `python -m evals.compare` and frozen in [`reports/experiment.md`](../reports/experiment.md), which is generated from the two result files and is the only place these numbers should be read from. Both arms ran at commit `12cefb1`, clean tree, same dataset digest, same evaluators, `gpt-5.6-luna`.

| | Flat | Supervisor |
|---|---|---|
| Resolved | 29/30 | **30/30** |
| Mixed-intent | 5/5 | 5/5 |
| p50 latency | 4.0s | 6.2s (**+2.2s**, over the +2.0s bar) |
| Cost per conversation | $0.0010 | $0.0011 (1.12x) |
| Security failures | 0 | 0 |

The supervisor **won the headline number and still loses**, failing three bars: mixed-intent margin, routing errors, and latency. That is the pre-registration doing its job — a decision rule you cannot revise while looking at the results is the only defense against reading a +1 as vindication.

**A one-example margin does not establish a quality difference, and the demo should say exactly that.** Across the full-dataset runs in `evals/results/`, flat has scored both 29/30 and 30/30 and so has the supervisor, failing a different example each time — `mixed-spend-and-recommend` in one, `refund-foreign-line-refused` in another.

⚠️ **Do not call that "run-to-run variance."** Those earlier runs predate provenance capture and carry **no recorded commit**, so they differ in prompt and evaluator code as well as in sampling. They demonstrate that the resolution count is *sensitive*; they do not measure variance at fixed code, because nothing pins the code. The supportable sentence is: the one-example advantage is not enough to establish a quality difference, while the supervisor's latency and tool-call overhead are unambiguous in the controlled comparison.

⚠️ **An earlier version of this paragraph claimed a specific reversed pair — flat 30/30 against supervisor 29/30, both failing `escalation-payment-method`.** No such pair exists on disk. The variance conclusion survives; the evidence for it is the table above, not a remembered run. It is worth noting where the claim came from, because it is the same failure this document spends a section on: a number that sounded right, was never read off an artifact, and would have been said out loud to the people most likely to ask for the file.

What reproduces in the same direction every time is the cost of the extra hop: roughly half again the median latency and substantially more tool calls. **The verdict rests on that, not on the one-example gap**, which is what makes it a verdict rather than a reading.

One bar was mis-specified: "routing errors, strictly fewer" is unreachable when flat commits zero. It is reported as a failure because that is how it was written, and the correction belongs in the next pre-registration, not in this one after seeing results. The verdict does not turn on it.

`graph_supervisor.py` stays as evidence and is imported by nothing but the experiment and its tests.

Only after this settles, compare Luna vs. Terra on the hard subset. Same discipline: **model selection is an experiment output, not a fixed choice** — if luna clears the billing-accuracy and tool-selection bars, demo on luna and the cost story improves by an order of magnitude.

---

## 8. Data

`data/chinook.db` (read-only) and `data/support.db` (writable), built by `scripts/setup_data.py` from the canonical SQL dump.

Verified: 59 customers · 275 artists · 347 albums · 3,503 tracks · 25 genres · 412 invoices · 2,240 invoice lines · 8 employees (3 Sales Support Agents).

| | Customer | Detail |
|---|---|---|
| **Primary** | **#6 Helena Holý** (Czech Republic) | 7 invoices, $49.62 lifetime. Rock (10), TV Shows (6), Latin (6). Latest invoice **#404, 2025-11-13, $25.86**. Rep: **Steve Johnson (#5)**. |
| **The other tenant** | **#26 Richard Cunningham** (USA) | The customer Helena must never see. Rep: Margaret Park (#4). Seed a canary fact here for leakage regression tests. |

**What the schema does and doesn't support** — worth knowing before claiming it in a demo. Chinook has invoices, not shipments, so this is a digital store with no "where's my order." It has no payment-processor events, so a "was I charged twice" question cannot be answered — the invoice table doesn't know what a card was charged, and building a tool that eyeballs repeated line items would dress up a guess as a capability. It has no download or entitlement events, so "I never received this track" can be *collected* and ticketed but not *verified*. Each of those ends in `escalate_to_human`, which prepares a handoff summary for the assigned rep — it does not route or file anything. They are handoff paths, not capabilities, and the agent should say so plainly to the customer. The brief anticipates this: the dataset is not a perfect fit, and the job is to weave a narrative rather than fight it.

---

## 9. Repo layout

```
.
├── langgraph.json            # `auth` key wires the server-level boundary below
├── src/security/auth.py      # @auth.authenticate + @auth.on.threads  ← the real boundary, runs before a run exists
├── src/gateway.py            # SupportGateway — same check for the Studio path, which is exempt from custom auth
├── src/agent/
│   ├── graph.py              # flat baseline  ← the file to show
│   ├── graph_supervisor.py   # variant, only if it wins
│   ├── context.py            # AuthContext
│   ├── prompts.py
│   ├── middleware/
│   └── tools/{billing,catalog,escalation}.py
├── src/data/
│   ├── db.py                 # read-only chinook + writable support  ← the security boundary
│   └── audit.py              # tenant-access metadata
├── evals/
│   ├── datasets/             # stratified, per §7
│   ├── evaluators.py
│   └── run_experiment.py
├── tests/                    # authorization + thread-ownership regression
├── scripts/setup_data.py
└── docs/
```

No `notebooks/`, `artifacts/`, or `serving/` — nothing is trained and Studio is the interface (ADR-008).

**Note `gateway.py` and `security/` sit in `src/`, not `src/agent/`.** That's deliberate and worth being able to explain: neither is part of the agent, they're what decides whether the agent gets invoked at all. Putting either inside the agent package would be the same category error as putting the ownership check in middleware — which is the error this design started with, one layer up (ADR-022).

**Naming:** the runtime context object is **`AuthContext`** everywhere. Earlier drafts called it `SupportContext` in the diagram and ADR-007 while the security chain called it `AuthContext` — two names for one object, which is a small thing that gets embarrassing when you're explaining code live and the file disagrees with your sentence. `AuthContext` won because it names what the object *is* rather than what app it belongs to.
