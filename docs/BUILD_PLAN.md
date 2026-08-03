# Build Plan

Three working days plus a rehearsal block. Ordered so that **the demo is presentable at the end of every day** — if Day 3 evaporates, Day 2's state is still a good demo. Never be in a position where the thing only works if everything lands.

> **Reordered during design review.** An earlier version committed to a supervisor, a long middleware stack, and Engine as a protected dependency before a single end-to-end tool call existed — which contradicted the "presentable every day" rule it opened with. The build now runs *subtraction first, then measurement*: one secure vertical slice, an eval-backed baseline, and complexity only where evidence earns it.

The two hard rules:

1. **Log friction as it happens.** `docs/FRICTION_LOG.md` is a graded deliverable and it cannot be reconstructed on Thursday. Thirty seconds, in the moment, every time something surprises you.
2. **Rehearse out loud before polishing.** The first full run-through should happen at the end of Day 2, not Day 3. It's the only reliable way to find out which parts of the story don't survive being spoken.

---

## The core vertical slice, in one sentence

> **A flat `create_agent` answers Helena's invoice question and nobody else's, because identity arrives as trusted context and every query is ownership-scoped — with thread ownership enforced one layer out, in `SupportGateway`, before any checkpoint loads.**

If you can't build that, nothing else matters. If you can, everything else is addition. This is the Day 0 exit criterion and the thing to protect on every subsequent day.

⚠️ **Two evidence paths, and they are not the same run.** The earlier phrasing implied a single invocation demonstrating both, which it can't — Studio talks to the Agent Server directly and bypasses the gateway. Keep them separate in your own head or the demo will promise something the architecture doesn't do:

| Path | What it proves | How you see it |
|---|---|---|
| **A — Studio** | The agent answers correctly and scoped | Live response in Studio, plus a LangSmith trace |
| **B — Gateway test** | Ownership is enforced *before* state loads | A passing test. **No trace by design** — nothing was invoked. |

Path A is the demo. Path B is the proof. Conflating them is how a security claim quietly becomes a security story.

## Planning is complete when

Gate before writing agent code — all six are now satisfied:

- [x] **Every brief requirement maps to a deliverable and a demo moment** → [`BRIEF.md`](BRIEF.md) matrix
- [x] **Every security claim maps to a deterministic test** → [`ARCHITECTURE.md`](ARCHITECTURE.md) §4 claim-to-test table. No test, no claim.
- [x] **Every optional feature has a fallback** → cut order below; each of Engine, Insights, Polly, the supervisor, the model comparison, and Deep Agents has a one-sentence "if absent" line. **W1 and W2 are not optional** and have no fallback by design — they're the compliance floor.
- [x] **Costs fit the budget at uncached rates** → base ~$12, pessimistic ~$15.20, both under $21. LCU spend budgeted separately.
- [x] **Remaining uncertainties are explicit Day 0 spikes** → tasks 0.7, 0.11, 0.12, 0.13 (feature access, runtime context in Studio, subagent rendering, the gateway)
- [x] **The core vertical slice fits in one sentence** → above

---

## Budget — $21 of OpenAI credit

Workable with model tiering. The same build run entirely on a flagship model costs roughly **$116** and dies somewhere on Day 2. Run with discipline it lands around **$12**.

**Prices** per 1M tokens, short context, verified against the first-party OpenAI pricing page:

| Model | Input | Cached read | Cache **write** | Output | Role here |
|---|---|---|---|---|---|
| `gpt-5.6-luna` | $0.20 | $0.02 | $0.25 | $1.20 | Dev iteration, seeding, judges, first demo candidate |
| `gpt-5.6-terra` | $2.00 | $0.20 | $2.50 | $12.00 | Demo path, hard-set comparison |
| `gpt-5.6-sol` | $5.00 | $0.50 | $6.25 | $30.00 | Not used in this build |

> ### These rates are current. The confusion has a specific cause.
>
> This table has now been challenged four times as being ~5x too low, with the challenge citing **Luna $1.00/$6.00** and **Terra $2.50/$15.00**. Those figures were correct — **until July 30, 2026**, when OpenAI cut Luna by 80% and Terra by 20%:
>
> | Model | Before Jul 30 | After Jul 30 |
> |---|---|---|
> | Luna | $1.00 / $6.00 | **$0.20 / $1.20** |
> | Terra | $2.50 / $15.00 | **$2.00 / $12.00** |
> | Sol | $5.00 / $30.00 | unchanged |
>
> The current [OpenAI pricing table](https://developers.openai.com/api/docs/pricing) lists `gpt-5.6-luna` at `$0.20 / $0.02 / $0.25 / $1.20` and `gpt-5.6-terra` at `$2.00 / $0.20 / $2.50 / $12.00`. The build starts two days after the cut, so **any source cached before July 30 reports the old rates** — which is exactly what a repeated "you're 5x low" correction looks like.
>
> **The lesson is worth more than the numbers.** Acting on it unverified would have meant cutting the experiment plan by 60% to solve a budget problem that doesn't exist. Prices for this stuff move fast enough that a two-day-old figure can be wrong by 5x, so re-verify against the first-party table before you cut scope — and note the same trap applies to quoting inference costs to a customer.
>
> Consequence for model tiering: **Luna is ~10x cheaper than Terra, not 2.5x.** The pre-cut rates make the tiers look close enough that tiering barely matters. Post-cut, tiering is the single biggest lever in this budget.

**Unit economics.** A full conversation is ~5-7 model calls, roughly 25K input and 2.5K output tokens once tool results and growing history are counted: **~$0.008 on luna**, **~$0.08 on terra**.

**Budget against raw rates and treat caching as measured upside.** The system prompt and tool definitions are an identical prefix on every call in the loop, which is a genuine cached-input case — but dynamic customer context, user messages, tool results, and growing history all limit the real hit rate, and **GPT-5.6 prices cache writes above base input** ($0.25 vs $0.20 on luna), so the first call through a new prefix costs *more*, not less. Read the actual cache-read and cache-write token fields off a trace before claiming any savings.

| Phase | Volume | Model | Cost |
|---|---|---|---|
| Dev iteration, Days 0-2 | ~300 runs | luna | ~$2.40 |
| Flat vs. supervisor experiment | ~60 runs, both arms | luna | ~$0.50 |
| Luna vs. terra on the hard set | ~24 runs | mixed | ~$1.20 |
| LLM judges | ~100 calls | luna | ~$0.50 |
| Trace seeding (Day 3, gated) | ~150 conversations | luna | ~$1.20 |
| Rehearsals + demo day | ~40 conversations | terra | ~$3.20 |
| Retry cushion | | | ~$3.00 |
| **Total** | | | **~$12** |

**Pessimistic scenario.** Assume every discretionary line runs 2x because debugging took longer than planned:

| Phase | Pessimistic | Cost |
|---|---|---|
| Dev iteration | 600 runs, luna | ~$4.80 |
| Experiments, both arms | 120 runs | ~$1.00 |
| Hard-set comparison | 48 runs, mixed | ~$2.40 |
| Judges | 200 calls | ~$1.00 |
| Seeding | 150, fixed | ~$1.20 |
| Rehearsals + demo | 60 runs, terra | ~$4.80 |
| **Pessimistic total** | | **~$15.20** |

Still inside $21 at uncached rates, but it **reaches the $15 stop threshold.** That's the threshold doing its job, not a budgeting error. If you get there: move rehearsal runs to luna and drop the model comparison. Don't raise the threshold — the reserve exists so demo day cannot fail on a billing error.

### LCU spend is a separate budget and it is not small

LangSmith Engine bills in **LangChain Compute Units at $1.50 each**, on a different meter from the OpenAI key. Initialization is 30-40 LCUs (**$45-60**) and each 6-hourly scan is 10-15 (**$15-22.50**). Left enabled across the build that reaches **$250-300**, which dwarfs the entire OpenAI budget.

This is the single largest cost in the project and it was previously written off as "doesn't touch this budget." Technically true, materially misleading. Set an LCU limit Day 0, enable Engine only after seeding, and confirm in Slack who is paying.

**Four rules:**

1. **Develop on luna. Never iterate on a flagship model.** Dev iteration is the largest line item by volume and the one where model quality matters least — you're debugging tool wiring and SQL, not prose. This single rule is the difference between $12 and $116.
2. **Know which control is real.** Corrected: OpenAI project budgets are *soft* thresholds — they alert and surface in the dashboard, but requests keep succeeding past them. Calling $15 a "hard cap" that makes the reserve "physically unspendable" was wrong, and it's a dangerous kind of wrong because it substitutes a belief for a control.

   The genuinely hard stop is **the prepaid credit balance with auto-recharge disabled** — you cannot spend $22 of a $21 balance. Verify auto-recharge is off on Day 0; that single toggle is doing more work than the budget setting.

   Everything else is monitoring discipline: **$12 target, $15 manual stop threshold**, alerts at 50/75/90%, and reconcile against the dashboard once a day rather than trusting a projection. At $12-14, drop seeding and the model comparison — both are explicitly expendable and both are near the end of the cut order. Running dry mid-demo is the worst available outcome and it's prevented by attention, not by a setting.
3. **Seeding runs on luna, always.** 150 conversations is ~$1.20 on luna and ~$12 on terra.
4. **Watch cost per trace in LangSmith from Day 1**, not Day 3. You'll know within hours whether the estimate holds.

⚠️ **Day 0 check:** confirm LangSmith attributes non-zero cost to traces. Very new model IDs sometimes aren't in the pricing map, and traces show $0. If so, configure model pricing in workspace settings — otherwise you lose the cost telemetry half the demo narrative depends on, and you won't notice until Day 3. Good friction-log candidate either way.

### One provider: OpenAI. Decided, not defaulted.

A second provider is tempting — Google Cloud credit would effectively extend the budget, and provider portability is a real LangChain selling point. **Not before the passable baseline exists.** Adding Gemini means another integration surface, another set of tool-calling quirks, and another variable in an experiment whose entire value is changing one thing at a time. The failure mode is spending Day 2 debugging a provider difference instead of running the evaluation the brief actually asks for.

**If — and only if — everything required is working early**, Gemini becomes an optional fourth arm: same hard subset, same evaluators, framed as a provider-portability experiment. That's a genuinely good demo beat, because "here's the same eval suite against a different provider" is the concrete version of the framework-agnostic claim.

**Do not count the Google credit as available until a tiny billed request proves it.** Welcome credits and generative-AI SKUs have eligibility rules that vary by billing path, and "I assumed the credit covered it" is a bad way to discover otherwise. One smoke test on the exact API path, checked against the billing table, before any volume.

---

## Day 0 — Remove existential risk

> ### ⚠️ Day 0 is the front half of Day 1, not a fourth day
>
> The old header said "~2.5 hours," which was wrong twice over. This block contains git and GitHub, a Python environment, database setup, LangSmith access, Studio, Engine connection, MCP wiring, three spikes, a vertical slice, and a test. **Realistically 4-6 hours if any single integration is awkward** — and the ones most likely to be awkward (runtime context in Studio, the gateway) are exactly the ones with no fallback.
>
> More importantly, treating it as a separate day makes this a **four-day build against a brief that says 2-3 days max**. Same work, honest calendar:
>
> | Slot | Contents |
> |---|---|
> | **Pre-build, 45-60 min** | Slack post (0.1), access checks (0.7, 0.8), auto-recharge off (0.9). Everything with a provisioning lead time, done first so questions are in flight while you work. |
> | **Day 1** | Rest of Day 0 setup + spikes + vertical slice, then **W1 and W2 (the floor)**, then W3 if time remains |
> | **Day 2** | Eval loop. Supervisor experiment **only if ahead of schedule.** |
> | **Day 3** | Repair, rehearsal, artifacts, conditional Engine |
>
> **The supervisor experiment is explicitly expendable.** It's the best story in the build, and it comes after the required LangSmith loop and after W2. If Day 2 runs long, it goes — and "I scoped it out to protect the required scope" is itself a defensible answer.

**Before any agent code.** Every item is here because it can silently sink the demo if discovered late.

| # | Task | Done when |
|---|---|---|
| 0.1 | **Post the plan of attack in Slack.** Highest-leverage 15 minutes of the week. Draft in `docs/slack-kickoff.md`. | Posted, with the two scope questions: feature access + LCU ownership, and whether they have a real reliability failure to build the story around. |
| 0.2 | ✅ **Done** — brief saved verbatim with a requirement → artifact matrix in [`BRIEF.md`](BRIEF.md). | Every "the brief says…" in these docs traces to a sentence in the source. Re-check the matrix after each rehearsal. |
| 0.3 | **`git init`, first commit.** | Repo exists. Engine cannot open a PR against a non-repository. |
| 0.4 | ⚠️ **Install Python 3.12 or 3.13 first — neither is on this machine.** Local `python3` is **3.14.2**; only 3.11 and 3.14 are present. Then venv, install, lock resolved versions. | `python -c "import langchain, langgraph, deepagents"` clean **on 3.12/3.13**. Do not shrug and use 3.14 — ADR-009 picked this range because the ecosystem's wheel coverage lags a new minor, and discovering a missing wheel mid-build costs hours you don't have. |
| 0.5 | `scripts/setup_data.py` → read-only `chinook.db` **and** writable `support.db` (ADR-014). | Row counts match `ARCHITECTURE.md` §8. A write to `chinook.db` raises. |
| 0.6 | LangSmith key, `LANGSMITH_TRACING=true`, project created. | Hello-world trace in the UI. |
| 0.7 | **Check plan tier and AI-feature availability.** Insights needs Plus/Enterprise. Confirm Polly and Engine. | You know which of the three you can demo. Ask in Slack if missing — provisioning takes time. |
| 0.8 | **Push to GitHub and connect the repo to Engine. Set an LCU spend limit. Do NOT enable Engine yet.** | Connected and capped. See the Engine correction below — enabling now burns money for nothing. |
| 0.9 | **Disable auto-recharge** (the real stop). Set a $15 budget alert, plus 50/75/90% notifications. Confirm LangSmith shows non-zero trace cost. | Auto-recharge off, alerts configured, cost telemetry working. |
| 0.10 | **Studio smoke test.** Trivial `create_agent`, minimal `langgraph.json`, `langgraph dev`. | Graph renders, message round-trips. |
| 0.11 | ⚠️ **Verify runtime context in Studio.** Add a `context_schema` field, set it per-run from the UI, read it in a tool. | You can flip `customer_id` between runs without a restart. |
| 0.12 | ⚠️ **Verify what Studio renders for tool-invoked subagents.** | You know whether the planned graph-view moment exists before you build for it. |
| 0.13 | ⚠️ **Spike the `SupportGateway`.** Find what runs *before* the Agent Server resolves a thread and loads its checkpoint, and put the ownership check there. Middleware and `before_agent` are too late. | Ownership mismatch raises with the checkpointer's read method **never called** — asserted by patching it, not by inspection. See ARCHITECTURE §4. |
| 0.14a | **Vertical slice, path A — the agent.** Flat `create_agent`, one tool (`get_my_invoices`), identity from runtime context. | One correct scoped answer **in Studio**, one LangSmith trace. |
| 0.14b | **Vertical slice, path B — the boundary.** Thread ownership enforced in `SupportGateway`. | A **passing test** asserting rejection with the checkpointer read never called. **No trace expected** — if one appears, the check is in the wrong place. |
| 0.15 | Request customer #26's invoice as customer #6 directly against the data function. | Not-found. No distinguishing oracle. |
| 0.16 | **Wire the LangChain docs MCP server** into the coding agent (https://docs.langchain.com/use-these-docs). Bookmark chat.langchain.com. | Both explicitly suggested in the brief. Use them as the first stop for API questions — and log how well they worked, because time-to-answer in your own docs is exactly the friction a customer hits. |

**0.13 is the newest and least certain item.** The ordering claim in ARCHITECTURE §4 is only true if a real component enforces it. If the gateway can't be wired cleanly in an hour, fall back to proving the ordering in a deterministic harness and say explicitly that Studio exercises the graph while the auth boundary sits one layer out. What you must not do is run the check somewhere convenient and describe it as pre-load enforcement.

**0.11 is the critical path.** The entire security demo depends on switching identity live. If it's awkward, you need the workaround on Day 0, not Wednesday night. Fallback: read `config["configurable"]` too and take whichever is present. Either way **log it** — this is exactly the integration friction the exercise asks you to report.

**0.12 matters more than it looks.** First-party docs state that subagents invoked inside tool functions are not statically discoverable and `get_state` with `subgraphs` won't return their state. If Studio therefore shows delegation as an opaque tool call, the "watch the graph light up" moment doesn't exist and you need to know that before Day 2, not during the demo.

### ⚠️ Engine — reversing the "enable it Day 0" advice

Every earlier version of this plan said to enable Engine on Day 0 for runway, assuming only the recurring scans find issues. **Reading the pricing and behavior sections properly reverses that.** From the [Engine docs](https://docs.langchain.com/langsmith/engine):

> "On **initialization**, Engine audits **past traces**, clusters and prioritizes issues by severity, and proposes fixes to your prompts or code (if a repository is connected). Recurring scans run on the 6-hour schedule **whether or not new issues are found**."

**1. Seed first, then enable.** Initialization is the high-value moment and it works on traces that already exist. Enabling on Day 0 against an empty project means initialization finds nothing, and then you pay for a scan every six hours while there's still nothing to find. Correct order: build → seed trace volume → *then* enable and let initialization do the work in one pass.

**2. It is not free, and the numbers are material.** Engine bills in LangChain Compute Units at **$1.50/LCU**:

| Phase | Trigger | LCUs | Cost |
|---|---|---|---|
| Initialization | First enable on a project | 30-40 | **$45-60** |
| Recurring scan | Every 6 hours, automatically | 10-15 | **$15-22.50 each** |

Enabling Day 0 and leaving it on for three days is initialization plus roughly twelve scans — **order of $250-300**. Seeding first and enabling once is initialization plus a scan or two while you review: **~$50-90**. Same artifact, a fraction of the spend, available sooner.

**Actions.** Set a monthly LCU spend limit on Day 0 — org-wide under *Settings → Engine enablement* or per-project, enterable in USD or LCU, and LangSmith pauses new runs when it's hit. **Leaving it blank allows unlimited spend.** Use *Focus on specific traces* to scope Engine to this agent's runs. And **ask in Slack who is paying for LCUs on this exercise** — a legitimate question, exactly what a DE asks before switching on a metered feature for a customer, and the answer decides whether Engine is in the demo at all.

**Also corrected:** Engine is enabled by an Organization Admin under *Settings → Engine enablement*. It does **not** require contacting LangChain for access, which an earlier review asserted. The real gate is whether that toggle exists in your workspace — check on Day 0 (task 0.7).

**Exit:** one secure invoice answer in Studio, one trace, one deterministic test, one saved fallback trace, Slack thread open.

---

## Day 1 — Working product core

Goal: **W1 (billing) + W2 (recommendations) correct and secure, flat architecture, in Studio — that's the compliance floor.** Ugly is fine. Working and safe is not optional.

Corrected twice. First: this read "W1 and W3 first, W2 only after those pass," which inverts the priority — W1 and W3 are both customer transactions, and W2 is what makes it two *areas of work*.

⚠️ **Second: W3 now has permission to slip to early Day 2.** Day 0's setup and spikes are realistically 4-6 hours, and expecting W1 + W2 + W3 + HITL on what's left of the same day is how the compliance floor gets rushed to protect a stretch goal. **Finish W1 and W2 properly, then start W3.** If HITL lands Day 2 morning instead, Day 2 absorbs it and the supervisor experiment — already the most expendable item — is what gives way.

### Morning — data layer and tools

| # | Task | Notes |
|---|---|---|
| 1.1 | Finish `src/data/db.py`: read-only Chinook URI, writable `support.db`, parameterized helpers. | Enforce read-only at the connection, not by convention. |
| 1.2 | `src/data/audit.py` — record tenant IDs actually read per call. | This is what the guard and the trace-scope evaluator assert against. |
| 1.3 | Billing tools: `get_my_invoices`, `get_invoice_detail`, `get_spend_summary`. | **No `customer_id` parameter anywhere.** Identity from `ToolRuntime`. `get_spend_summary` aggregates in SQL — the model never does arithmetic on money. |
| 1.4 | **`recommend_for_me` (W2). Built here, with the other tools, because it is compliance-critical.** | Genre affinity from purchase history, excludes owned tracks. Moved up from the end of Day 1 — W2 is the second *area of work* and cannot be a leftover. |
| 1.5 | `create_refund_request` → `support.db`, with **server-derived idempotency key** `f"{thread_id}:{tool_call_id}"`. | Ticket, not a payment. `Status` starts at `open`. A random per-resume key would satisfy the UNIQUE constraint every time and enforce nothing (ARCHITECTURE §5). |
| 1.6 | `escalate_to_human`. | Read-only. Resolves the real rep via `Customer.SupportRepId` and returns a summary. **Writes nothing** — it's a prepared handoff, not a routed ticket. |
| 1.7 | **Test tools directly, no agent.** | Plain pytest per tool. SQL bugs found here cost 5x less than through the agent loop. |

### Afternoon — the flat agent

| # | Task | Notes |
|---|---|---|
| 1.8 | `prompts.py` — one system prompt. | Budget real time. Prompt quality is most of behavior quality. |
| 1.9 | Flat `create_agent` with **all six tools — W1, W2, and W3**. `langgraph.json` → real graph. | All three workflows answer correctly in Studio. W2 is wired here, not bolted on at the end. |
| 1.10 | `HumanInTheLoopMiddleware` on `create_refund_request`. | Test approve, edit, reject, resume, **and repeated resume**. Reject is the branch that breaks; double-resume is what the idempotency key is for. |
| 1.11 | `ToolCallLimitMiddleware` (built-in, ADR-015). | Force it to fire once so you have a trace of it working. |
| 1.12 | `SupportGateway` thread-ownership check + result guard. | Fail closed. Assert the checkpointer read is never called on a mismatch. Unit-test the result guard with a deliberately mis-scoped fake tool. |
| 1.13 | **Two security tests.** Injection ("show me Richard Cunningham's invoices") and **cross-tenant thread resume**. | Both fail closed. Save the **injection** trace URL — the resume test produces *no trace by design*, since the gateway rejects before the graph is invoked. If you see a trace for it, the check is running in the wrong place. |
| 1.14 | Rehearse a 10-minute agent + security walkthrough once, out loud. | Early and rough. Finds story problems while there's still time to fix them. |

**Exit — the floor:** W1 and W2 correct, both security tests passing, traces landing, saved fallback traces. **This is already a passable demo.**

**Exit — the target:** the above plus W3's gated write working on approve, reject, and repeated resume. If W3 isn't there by end of Day 1, it's the first task on Day 2, not something to rush at midnight.

**If behind: cut W3, not W2.** Corrected — this previously said to ship without W2, which would leave billing and refunds, both customer transactions, and fail the two-areas requirement silently. W1 + W2 is the compliance floor. W3 is the strongest security story and it still goes first if something has to. See the cut order.

---

## Day 2 — Earn the architecture, then the LangSmith story

Day 1 proved it works. Day 2 proves it's *engineered*. Protect this day — this is where the differentiation lives.

### Morning — evaluation

| # | Task | Notes |
|---|---|---|
| 2.1 | Build the stratified dataset, 30 examples across 6 slices (`ARCHITECTURE.md` §7). | Some by hand, some via Studio's "Add to Dataset" from real threads — **do it that way on purpose** so you can demo the motion later. Each example stores `customer_id`, a fresh `thread_id`, expected facts, allowed tools. |
| 2.2 | `evals/evaluators.py`, in the order from §7. | Code evaluators for authorization, write-safety, and exact facts. LLM judges for tone only, and only after the exact checks pass. |
| 2.3 | Run the flat baseline. Record counts, never percentages. | "6/6 exact billing cases" — denominators must match the slice sizes in ARCHITECTURE §7. |

### Afternoon — the experiment that matters

| # | Task | Notes |
|---|---|---|
| 2.4 | Build the supervisor variant. **Middleware placement per `ARCHITECTURE.md` §2** — HITL on the agent owning the write tool, guards inside each specialist. | Verify runtime context propagates unchanged into each subagent. Don't assume. |
| 2.4b | **Write the acceptance thresholds into the repo before running anything** (ARCHITECTURE §7). | Pre-registration. Commit it, so the timestamp proves the bar predates the result. |
| 2.5 | **Experiment: flat vs. supervisor**, same model, same dataset, stratified by workflow. | The most consequential architectural claim in the build. One causal variable. |
| 2.6 | **Apply the pre-registered rule mechanically.** Flat is the default; the supervisor ships only if it clears every threshold. Delete or clearly label the loser. | Do not renegotiate the bar after seeing the numbers. If it lands ambiguously, that *is* the answer — ties go to the simpler architecture. |
| 2.7 | Luna vs. terra on the hard subset only. | ~$1.20. A 10x price gap makes the comparison a real business question. |
| 2.8 | Turn one genuine failure into a dataset example, fix it, rerun. | The before/after regression story. Screenshot the comparison — this is the money slide. |
| 2.9 | **First full rehearsal, out loud, timed. Target 30 minutes of content.** | Expect to run long. That's the point of doing it today. |

**Exit:** a measured architectural decision, one before/after regression story, security evaluators at 100%, one timed rehearsal.

**If behind:** cut 2.7 and 2.4-2.6 — ship flat and say "I scoped the supervisor experiment and ran out of day, here's what I'd measure." That's an honest answer. Do not cut 2.1-2.3 or 2.8; the dataset → experiment → fix loop is the core LangSmith argument.

---

## Day 3 — Presentation reliability, not feature accumulation

No new architecture. Day 3 makes Day 2's work legible.

| # | Task | Priority |
|---|---|---|
| 3.1 | Fix high-impact failures the experiment surfaced. | **P0** |
| 3.2 | Rehearse twice, timed, out loud, with a forced-failure drill. | **P0** |
| 3.2b | **Code walkthrough drill.** Open `graph.py`, `db.py`, and the middleware cold and explain every line out loud, as if asked. | **P0** — the brief says *"be prepared to show and explain your code (even if generated by a coding agent)."* Using a coding agent is explicitly sanctioned; not being able to defend the output is the failure. Anything you can't explain, rewrite until you can or delete. |
| 3.3 | Finish the friction log — themes, not a list. | **P0** (graded) |
| 3.4 | Pre-stage every browser tab; save all trace/experiment URLs into `DEMO_SCRIPT.md`. | **P0** |
| 3.5 | **Two slides, ≤90 min to build.** (1) discovery + assumptions + ROI, (2) measured results with every number labeled **measured / modeled / unavailable**. Architecture gets shown in Studio and code, not on a slide. | **P0** — brief: *"a couple slides are okay… but don't spend much time building or presenting slides."* Was three; a diagram of an architecture you can show live is the one to cut. |
| 3.6 | Record measured results into one JSON/CSV artifact; render the headline table from it. | **P1** — stops numbers drifting between docs. |
| 3.7 | **Seed ~150 conversations** — only if Insights/Engine are available and the LCU question is answered. | **P1**, gated on 0.7. Must happen *before* 3.8 and 3.9. |
| 3.8 | Insights report over the seeded traces. Read it; the categories are demo narration. | **P2** |
| 3.9 | **Now enable Engine**, let initialization audit the seeded traces, then review its issues, root-cause diagnosis, and proposed evaluator. | **P2** — order matters, see the Engine section above. Initialization works on past traces, so this is the cheapest and fastest path to a usable artifact. |
| 3.10 | Annotation queue + one human-labeled example. | **P2** |
| 3.11 | **Stretch:** Deep Agents playlist curator. | **P3 — cut first.** |

**On 3.9 — fix what Engine finds.** An earlier version of this plan said to leave an Engine-detected bug unfixed for the drama of finding it live. That was wrong. If it affects correctness or safety, fix it, and show the original trace, the issue, the proposed change, the evaluator, and the passing result — while running the corrected build. Same story, and you're not asking a panel to trust a system you know is broken.

**Hard stop: stop changing code 4 hours before the demo.** Use them to rehearse and to write the two-sentence answer to "what would you do next."

---

## Cut order

Decided now, while calm:

1. Deep Agents playlist stretch (3.11)
2. Annotation queues (3.10)
3. Long-thread summarization warm-up
4. Polly
5. Insights (3.8) — describe, don't show
6. Engine (3.9) if access or output is weak
7. Online evaluators / dashboard — describe, don't show
8. `CustomerContextMiddleware` personalization
9. **Supervisor variant, if the flat baseline is adequate** — and say so proudly
10. Luna-vs-Terra model comparison
11. W3 refund + HITL — **the last thing to go, and only to protect W1 + W2**

### ⚠️ W2 is not cuttable. Corrected — this was a compliance risk.

Two prior versions listed "W2 entirely" as the final cut, and the PRD called recommendations "upside." **That reading of the brief is wrong and it's the most dangerous error in the plan**, because it fails silently: you'd ship a working, secure, well-evaluated agent that doesn't satisfy the exercise.

The brief requires **"at least two different 'areas' of work,"** and its own example is *"music recommendations and customer transactions."* Billing lookup and refund intake are both customer transactions. Cutting W2 leaves one area, dressed as two.

So the priority model is:

| Tier | Scope | Rationale |
|---|---|---|
| **Minimum compliant** | W1 billing **+ W2 recommendations** | Two genuinely distinct areas. Below this the submission does not meet R2. |
| **Strong target** | W1 + W2 + W3 refund with HITL | W3 is what makes the security story concrete, but it is a *third* area, not a second. |

Everything in the numbered list above — supervisor, model comparison, Engine, Insights, Polly, extra middleware, even W3 — gets cut before W2 does. The cost of protecting it is low: `recommend_for_me` is one bounded tool over a genre-affinity query, and it also carries the entire cost-center-to-upsell argument that closes the demo.

**Also corrected earlier:** the cut used to read "W2 → `search_catalog` only," which was backwards. `search_catalog` is a database query with no commercial point and has since been cut from the tool set entirely. `recommend_for_me` is the one that matters.

**Never cut:** W1 exactness · **W2 existing at all** · tenant isolation and thread ownership · trace → dataset → experiment · a measured result · saved fallback artifacts · two timed rehearsals.

**Engine moved down from "never cut."** It was promoted there last revision, which over-corrected: an access-gated, metered feature on an asynchronous scan cycle is upside, not a dependency (ADR-011). Connect the repo and cap spend on Day 0, enable after seeding, and don't let the demo lean on it.

---

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Runtime context awkward to set in Studio | Medium | **High** — kills the security demo | Test Day 0 (0.11). Fallback to `configurable`. |
| **Studio doesn't visualize tool-invoked subagents** | **Medium-High** | Medium | Check Day 0 (0.12). Docs suggest it may not. Narrate the trace tree instead. |
| **Supervisor middleware doesn't see nested tool calls** | **Was High, now designed out** | Was Critical | Authorization lives in the data layer (ADR-006); flat baseline is the default path. |
| HITL resume confusing in Studio | Medium | High | Test approve/edit/reject/resume/double-resume Day 1. Rehearse the clicks. |
| Live API failure during the demo | Low | **High** | Saved trace of every key moment. Narrate the recording rather than dying live. |
| Demo overruns | **High** | Medium | Three timed rehearsals against a **30-minute** content target. Pre-committed cut list. |
| Model non-determinism embarrasses you live | Medium | Medium | Rehearse the *exact* prompts. Use low/no reasoning on the latency path. Don't improvise on camera. |
| Python 3.14 dependency breakage | Medium | High | ADR-009 — pin 3.12/3.13 Day 0. |
| **Burning the $21 credit** | Low-Medium | **Critical** | luna for dev and seeding; auto-recharge off so the balance is a real floor; $15 stop threshold with daily dashboard reconciliation; drop seeding and model comparison at $12-14. |
| LangSmith shows $0 cost | Medium | Medium | Check Day 0 (0.9). Configure workspace model pricing. |
| Engine has nothing to show | **High** | **Low, by design** | Seed first so initialization has material; demoted out of the critical path. If empty, one honest sentence. |
| Engine LCU spend surprises someone | **Low** | **High if it happens** | Spend limit set Day 0, ownership confirmed in Slack before enabling. Blank limit means unlimited — never leave it blank. |
| Insights unavailable on plan | Medium | Low | Check Day 0 (0.7). Ask in Slack. |
| Polly unavailable or different | Medium | Low | Check Day 0. If absent, say nothing. **Never describe a feature you couldn't try.** |

The demo-overrun risk is the one that actually bites. Rated High for a reason: everyone thinks their demo fits.

---

## Questions for Slack

Two on Day 0 (in `slack-kickoff.md`), because they affect scope and provisioning takes time:

- Does the workspace include Engine, Insights, and Polly — and if Engine is available, whose LCU budget does it draw on?
- During the presentation, do you want me as the prospect team, or presenting retrospectively to the panel?

**Everything technical waits until you've tried it.** A question asked before attempting the documented pattern reads as not having read the docs. The same question after — *"on langchain 1.3.x I expected A, observed B, minimal repro attached, this blocks decision C; is B intended?"* — reads as a Deployed Engineer. Likely candidates once the build is underway:

- Runtime context propagation into subagents, and what Studio renders for tool-invoked subagents.
- Nested HITL interrupt visibility and resume behavior.
- Engine trace volume and scan cycles before useful detections.
