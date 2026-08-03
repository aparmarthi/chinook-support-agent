# Friction Log

**Fill this in as it happens.** It cannot be reconstructed on the last day — by then you've forgotten what confused you, which is exactly the information that's valuable. Thirty seconds per entry, in the moment.

This is a graded deliverable and it's also the single most useful thing a Deployed Engineer produces: it is what a real customer would hit, written down by someone who just hit it. Be genuinely candid. "Everything was smooth" is not a credible answer and reads as either inattention or flattery.

---

## How to log

Keep the bar low — anything that made you stop, re-read, search, or guess:

```
### [Area] Short description
- **Expected:** what I thought would happen
- **Actual:** what happened
- **Cost:** ~N minutes
- **Resolved by:** docs page / trial and error / Slack / still open
- **Suggestion:** what would have prevented this
```

Rate severity: **Blocker** (stopped work) · **Papercut** (annoying, worked around) · **Docs gap** (worked, but hard to find).

**Prioritize depth over count.** One reproducible observation about LangChain or LangSmith product experience is worth more demo airtime than ten environment papercuts, and a manufactured entry to hit a quota is worse than a short log. Every entry should include exact package versions, expected vs. actual, a minimal repro, the impact, the workaround, and a product suggestion.

The entries most worth having, in rough order of value to the team: runtime context propagation through Studio and subagents · nested HITL visibility and resume behavior · the schema a dataset gets when created from a real thread · model cost attribution and cache accounting · Engine and Insights access and time-to-value · provider differences surfaced by the same eval suite.

---

## Entries

<!-- Append as you go. Newest at the bottom is fine — chronology is useful signal. -->

### [Setup] macOS framework Python has no usable CA bundle — **Papercut**
- **Expected:** `urllib.request` downloads the Chinook SQL dump over HTTPS.
- **Actual:** `SSLCertVerificationError: unable to get local issuer certificate`. `curl` to the same URL worked fine, which made it look like a code bug rather than an environment one.
- **Cost:** ~5 minutes.
- **Resolved by:** Building the SSL context from `certifi`'s bundle instead of the system store. python.org framework builds ship without a populated CA store until you run `Install Certificates.command`.
- **Suggestion:** Not a LangChain issue — generic Python-on-macOS friction. Noted because it's the class of thing that eats a customer's first hour and gets blamed on whatever library happened to be making the request.
- ⚠️ **Not demo material.** Honest, but generic and not about LangChain. Keep it in the written log; don't spend Block 6 airtime on it unless nothing better emerges.

### [Docs/Pricing] Engine's cost model and its sequencing consequence are both on the page, and both easy to miss — **Docs gap**
- **Expected:** Enable Engine early so its 6-hourly scans accumulate findings across the build. That's the intuitive reading of a background analysis feature, and it's what I planned twice.
- **Actual:** Exactly backwards. The docs say initialization "audits **past** traces, clusters and prioritizes issues by severity, and proposes fixes," and that recurring scans run "**whether or not new issues are found**." So enabling against an empty project pays for scans with nothing to analyze, and the high-value pass is the one that runs over traces you already have. The right order is seed first, then enable once.
- **The cost of getting it wrong is real.** LCUs are $1.50, initialization is 30-40, each scan is 10-15. Enabled across a three-day build that's **$250-300** versus **~$50-90** for seed-then-enable. Same artifact. Both numbers are on the page; neither is connected to the sequencing advice.
- **Cost:** Would have been ~$200 and two days of misordered planning. Caught while re-reading the pricing section, not the feature section.
- **Suggestion:** Two small changes with outsized value. **(1)** Add one line to the setup section: *"Engine's initialization pass analyzes existing traces — enable it once your project has representative volume rather than on an empty project."* The information is present but the reader has to derive the consequence. **(2)** Surface the estimated initialization cost in the enablement UI at the moment of the toggle, not only on the pricing page. The default spend limit being blank-means-unlimited is a sharp edge on a feature whose first action costs $45-60.
- ✅ **This is demo material.** It's specific, reproducible from the public docs, has a concrete dollar impact, and comes with a fix. It's also a Deployed Engineer's actual job — the finance question is the one that stalls a rollout after the technical evaluation succeeds.

### [Docs] Subagent middleware boundary is documented but easy to miss — **Docs gap**
- **Expected:** Middleware on a supervisor would see tool calls made inside its subagents, so a `@wrap_tool_call` tenant guard and a HITL gate on the supervisor would cover tools owned by specialists.
- **Actual:** Subagents are invoked *as tools*, so supervisor middleware sees the delegation call and its summarized result — not the nested calls. The [Subagents page](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents) does state that subagents aren't statically discoverable and `get_state` with `subgraphs` won't return their state, but it's one line under "Checkpointing and state inspection," framed as a state-visibility note rather than a security-relevant boundary.
- **Cost:** Caught in review before implementation. Would have cost hours to debug live, and might have shipped looking like it worked.
- **Resolved by:** Moving authorization into the data layer so it's topology-independent (ADR-006), and starting flat (ADR-002).
- **Suggestion:** The multi-agent page could carry an explicit callout on where middleware executes relative to subagent boundaries — a small diagram of what supervisor middleware can and can't observe. This is a security-relevant footgun for anyone building multi-tenant agents, and the current framing makes it read as a debugging inconvenience.
- **Demo value: high.** This is the best friction entry available so far — it's specific, it's about real product experience, it changed the architecture, and the fix is defensible. It also pairs directly with the Block 4 reversal.

### [OSS] The canonical `create_agent` one-liner fails on OpenAI's cheapest current model — **Blocker**
- **Versions:** `langchain` 1.3.14, `langchain-openai` 1.4.1, `openai` 2.52.0, Python 3.13.14.
- **Expected:** `create_agent(model="openai:gpt-5.6-luna", tools=[...])` — the documented getting-started form — runs.
- **Actual:** Every invocation dies at the first model call:

  > `openai.BadRequestError: 400 - Function tools with reasoning_effort are not supported for gpt-5.6-luna in /v1/chat/completions. To use function tools, use /v1/responses or set reasoning_effort to 'none'.`

  Passing a model as a string routes through `init_chat_model`, which defaults to `/v1/chat/completions` and sends a `reasoning_effort`. For `gpt-5.6-luna` that combination is rejected outright when tools are attached. **An agent with tools and the cheapest current OpenAI model — the exact combination a cost-sensitive team will try first — cannot be built with the documented one-liner.**
- **Minimal repro:** `create_agent(model="openai:gpt-5.6-luna", tools=[any_tool]).invoke({"messages": [{"role": "user", "content": "hi"}]})`
- **Cost:** ~10 minutes. Cheap only because the error message is unusually good — it names both escapes. Without that sentence this is an hour.
- **Resolved by:** Constructing the model explicitly with `init_chat_model(..., use_responses_api=True)` in `src/settings.py`.
- **The workaround is not free, and the cheaper escape is a trap.** Both escapes work, and they are not equivalent. Measured on one multi-tool support turn ("What was my most recent charge for? And suggest something new I might like."):

  | Path | Latency | Tool calls | Outcome |
  |---|---|---|---|
  | `chat/completions`, `reasoning_effort="none"` | 3.2s | 2 | Reported the amount, **offered** to look up what it was for |
  | Responses API, reasoning on | 5.8s | 3 | Called `get_invoice_detail`, **answered** what it was for |

  Disabling reasoning is the change that keeps the documented code shape, so it's the one a reader reaches for — and it silently degrades multi-step tool selection rather than failing. The agent still sounds fine. It just stops finishing the task.
- **Suggestion:** Two things. **(1)** Have `init_chat_model` select the Responses API automatically when tools are bound and the model requires it, rather than surfacing a provider 400 — the information needed to route correctly is available at bind time. **(2)** If that's too magic, make the string-model path emit a warning naming the tradeoff, because the error text presents the two escapes as equally valid and they are not.
- ✅ **Demo material, and it pairs with the cost story.** A concrete, reproducible finding on a current model, with a measurement rather than an opinion about which workaround to take.

### [LangSmith] Trace cost is computed from stale model pricing, and it is wrong by exactly 5x — **Blocker for the cost narrative**
- **Versions:** `langsmith` 0.10.15, LangSmith API 0.17.4, model `gpt-5.6-luna`.
- **Expected:** `total_cost` on a trace reflects current OpenAI pricing.
- **Actual:** It reflects the pricing that was current until **July 30, 2026**, when OpenAI cut Luna by 80% ($1.00/$6.00 → $0.20/$1.20). Five consecutive runs match the *old* rates to the sixth decimal:

  | Input | Output | LangSmith `total_cost` | At current $0.20/$1.20 | At pre-cut $1.00/$6.00 |
  |---|---|---|---|---|
  | 1645 | 58 | $0.001993 | $0.000399 | **$0.001993** |
  | 3151 | 209 | $0.004405 | $0.000881 | **$0.004405** |
  | 1891 | 129 | $0.002665 | $0.000533 | **$0.002665** |

- **Why this is worse than the failure mode we planned for.** The build plan anticipated new model IDs missing from the pricing map and traces showing **$0** — loud, obvious, immediately investigated. What actually happens is a *plausible* number computed from a stale rate. Nothing looks broken. Cost dashboards, per-experiment cost comparisons, and any "what will this cost in production" answer are all overstated 5x, and nobody has a reason to check.
- **Direct impact here:** the demo's closing argument is cost per conversation. Read off LangSmith it is ~$0.002; the true figure is ~$0.0004. Quoting the platform's own number to a prospect would have overstated their bill fivefold — for a store at 10K conversations/month, $20 presented instead of $4.
- **Cost:** ~15 minutes, and only because the figure was reconciled against a hand calculation rather than trusted. This is the entry most likely to have shipped unnoticed.
- **Resolved by:** Not yet. Workspace-level model pricing overrides are the documented remedy (build plan task 0.9). Until then, treat trace cost as a *relative* signal between runs, never as an absolute.
- **Suggestion:** Three, in order of value. **(1)** Show the effective rate and its effective date next to any computed cost, so a stale entry is visible rather than inferred. **(2)** Flag costs computed from a pricing entry older than the model's most recent pricing change. **(3)** Prices moved 80% in a single day two days before this build — a pricing map that updates on a slower cadence than provider pricing will be wrong regularly, not exceptionally.
- ✅ **Best entry in the log.** It's a silent correctness bug in a headline number, found by reconciliation, with a dollar impact and three concrete fixes. It also makes the right point for a Deployed Engineer: the observability tool is where you go to answer the cost question, which is exactly why a quiet error there is expensive.

### [Studio] A required context field isn't enforced before a run, and the failure is illegible — **Papercut, security-adjacent**
- **Versions:** `langgraph-api` 0.11.2, `langgraph` 1.2.10, Studio against a local `langgraph dev`.
- **Expected:** `context_schema=AuthContext` publishes `customer_id` as required — the assistant's `/schemas` endpoint returns `"required": ["customer_id"]` — so sending a message without it is either blocked in the UI or rejected with a validation error naming the field.
- **Actual:** The run is created, queued, and started, then dies inside the framework:

  > `TypeError: AuthContext.__init__() missing 1 required positional argument: 'customer_id'`
  > `langgraph/pregel/main.py:4362 in _coerce_context -> return context_schema(**context)`

  The context is coerced by splatting a dict into the schema type, so a missing required field surfaces as a Python constructor error rather than a validation failure. Nothing in the message says "set customer_id in the config panel," which is the entire remedy.
- **Minimal repro:** Compile any agent with a `context_schema` that has a required field, run `langgraph dev`, and send a message from Studio without filling the panel.
- **Cost:** ~2 minutes here, only because the schema was fresh in mind. For someone meeting a colleague's agent it reads as a broken graph.
- **Not softening the schema.** Giving `customer_id` a default would produce a legible error and is the wrong trade: the field would stop being `required` in the published schema, so Studio would lose the one signal that does prompt for it, and an agent that starts without an identity is the failure mode the whole design exists to prevent. **Failing closed loudly beats starting quietly.**
- **Workaround adopted, once it bit during a walkthrough:** bind the identity to the *assistant* instead (`scripts/setup_studio_assistants.py` creates "Helena Holý (customer 6)" and "Richard Cunningham (customer 26)" with `context` preset). Runs then succeed with no `context` in the payload at all, and switching tenant becomes a dropdown rather than a form. The schema stays strict — this changes who supplies the value, not whether it is required — and it maps better onto the real design anyway, where identity arrives from an authenticated session rather than being typed in. Assistants live in the dev server's store, so re-run the script after each restart.
- **Suggestion:** Validate the context against the published schema at run-creation time and reject with a field-level error, the way input validation already works. Failing that, catch the coercion error and re-raise naming the missing fields. A required field that the UI advertises but does not enforce is a sharp edge, and it is sharper than usual when the field is the tenant identity.
- **Demo value: moderate.** Not a headline, but a good honest aside if the topic turns to Studio's ergonomics, and it pairs naturally with the identity-simulation caveat already in the script.

### [OSS] The Responses API workaround silently changes the shape of `.content` — **Papercut with a long tail**
- **Versions:** `langchain` 1.2.x, `langchain-openai` with `use_responses_api=True`, model `openai:gpt-5.6-luna`.
- **Context:** This is a *consequence* of the blocker above. The only way to use function tools with Luna is the Responses API, so that flag isn't optional — but it is documented as a transport choice, and the downstream effect isn't mentioned where you make the decision.
- **Expected:** `AIMessage.content` is a string, as it is on the Chat Completions path and in essentially every example in the docs.
- **Actual:** It's a list of typed blocks — `[{'type': 'reasoning', ...}, {'type': 'text', 'text': 'I filed refund request #1...'}]`. Everything still *runs*. `print(message.content)` produces output. It just isn't the answer.
- **Why it's worse than it looks:** The failure is invisible in exactly the places you'd want it to be loud. An evaluator doing `"refund" in output.content.lower()` doesn't crash — it scores the stringified list, silently fails to match, and reports a pass rate. A regression suite that runs green while measuring nothing is worse than no suite, because you'll trust it. Same for any string assertion in a test and any `.content` in a demo script.
- **Cost:** ~10 minutes, and only because it was caught before the evaluators were written. Caught after, it would have invalidated a run of measurements — and the flat-vs-supervisor experiment is a comparison of numbers, so a shared silent bias in both arms is the kind of thing that survives a sanity check.
- **Fix:** One `message_text()` helper in `src/utils/messages.py` that flattens both shapes, used by every consumer. Skipping non-`text` blocks is the point — reasoning blocks carry no `text` key and would otherwise pollute the output.
- **Suggestion:** Note the content-shape change on the `use_responses_api` reference, next to where the flag is described. Better still, since the model-side error message that sends people to this flag is itself opaque, both facts belong in one place: *this model needs the Responses API, and here is what changes when you switch.*
- **Demo value: high, as the second half of a pair.** The first entry is "the one-liner didn't work." This one is "and the fix moved the problem somewhere I wouldn't have looked." Together they make a point about agent engineering that a single blocker doesn't: the expensive failures aren't the ones that throw.

### [OSS] A useful HITL approval card can't be built without blocking the event loop — **Blocker, with a real fix**
- **Versions:** `langchain` 1.2.x (`HumanInTheLoopMiddleware`), `langgraph-api` 0.11.2, sync SQLite in the data layer.
- **What I was doing:** `InterruptOnConfig.description` accepts a callable so the approval request can be built "based on agent state, runtime, and tool call information." For a refund, the useful card is the *charge* — track, amount, invoice — not the raw args. A reviewer shown `{"invoice_line_id": 2201}` has to go look up line 2201 before they can decide, and an approval step that offloads work onto the approver gets clicked through. That's worse than no gate, because now there's a control on the org chart that isn't a control in practice. The card must also come from the database, not from the model, or a hallucinated amount gets rubber-stamped.
- **Expected:** Resolve the line inside the description factory.
- **Actual:** Works in-process. On the Agent Server the whole run dies:

  > `blockbuster.BlockingError: Blocking call to sqlite3.Connection.execute`
  > `During task with name 'HumanInTheLoopMiddleware.after_model'`

  The middleware has an async path (`aafter_model`), but `_DescriptionFactory` is declared `def __call__(...) -> str` — sync only, with no async variant. So the factory is invoked on the event loop, and any I/O in it is a blocking call in an ASGI worker. The dev server is right to refuse.
- **The trap:** `langgraph dev --allow-blocking` makes it go away in one flag, and the temptation is real because everything works locally afterward. It also converts a correctness problem into a production performance problem you find later — the blocking call still ties up the loop for every concurrent conversation, you just stopped being told.
- **Fix (kept):** Move the lookup one node earlier. A small `RefundApprovalPreflight` middleware implements `aafter_model`, resolves pending refund calls with `asyncio.to_thread`, and writes the rendered card into a `refund_previews` state key. The description factory then reads state and does no I/O at all. The ordering detail that makes it work is non-obvious and worth knowing: **`after_model` hooks run in reverse registration order**, so the preflight is listed *last* in order to run *first*. Verified on the compiled graph — `model → ToolCallLimit.after_model → HITL.after_model` for `middleware=[HITL, ToolCallLimit]`.
- **Cost:** ~35 minutes, most of it spent on a wrong hypothesis (below), not on the fix.
- **Suggestion:** Add an async description factory to the protocol — the middleware already has `aafter_model`, so the hook exists and only the type and call site need widening. Failing that, say plainly in the `description` docs that the callable runs on the event loop and must not do I/O, and point at the state-preloading pattern. Right now the docs example encourages a dynamic description, the useful version of a dynamic description needs a lookup, and the framework's own dev server rejects the result. That's a gap worth closing because it isn't a niche case: **any approval card worth showing a human describes something the model doesn't already know.**
- **It is not a one-off — that's the real finding.** The same wall came up again building `CustomerContextMiddleware`, where the natural tool is the `@dynamic_prompt` decorator and the natural content is the customer's profile. Also sync, also on the event loop, also I/O. Two independent features, same shape: **the per-call hooks are synchronous, the useful version of each needs a lookup, and the fix is always to hoist the read into an async lifecycle hook and cache it in state.** Both landed on `abefore_agent`/`aafter_model` + `asyncio.to_thread` + a state key. Once is a papercut; twice is a missing pattern in the docs — the "dynamic X" hooks should either accept coroutines or carry one shared note explaining the hoist.
- **Demo value: high.** It's a concrete instance of the theme — the design decision (never show the approver model-supplied numbers) survives, but the implementation has to move to satisfy the runtime. Also a clean illustration of why HITL is an *engineering* problem, not a checkbox.

### [Studio/CLI] `langgraph dev` advertises hot reload and silently doesn't reload — **Papercut that cost more than the blocker it hid**
- **Versions:** `langgraph-cli` with `langgraph dev --no-browser --port 2024`, `langgraph-api` 0.11.2, macOS, Python 3.13.
- **Expected:** `--help` says *"Run LangGraph API server in development mode with hot reloading"* and offers `--no-reload` to turn it off, so reload is the default and edits should be picked up.
- **Actual:** No reload, ever. Adding new modules, adding middleware to the graph, and editing the system prompt all produced nothing — no reload line, no error, no warning. Confirmed the server was serving stale code by diffing the compiled graph: `/assistants/{id}/graph` still listed `['__start__','model','tools','ToolCallLimitMiddleware.after_model','__end__']` with no HITL node, 17 minutes after the middleware was added. A deliberate touch-a-file probe afterwards produced zero reload activity in the log.
- **Why it cost so much:** The failure presents as a *model behaviour* problem. My first refund attempt through the server came back "I can't process refunds from this support chat" — a completely plausible reply for an agent whose prompt lacked refund guidance, which mine did. So I fixed the prompt. Then I checked the graph, found no HITL node, and realized nothing I had written in the previous twenty minutes was running. **A stale server doesn't look stale; it looks like a badly behaved agent.** And the debugging you do in that state is worse than wasted, because it produces plausible-looking fixes to problems that were never there.
- **Workaround:** Restart explicitly after every change. Cheap once you know; the whole cost is in not knowing.
- **Suggestion:** Log one line at startup stating whether the file watcher is active and what it is watching, and log a warning if reload was requested and the watcher could not start. A silent no-op on an advertised default is the worst version of this, because it trains you to trust a server that is lying to you. If reload has known platform gaps, `--help` should say so where it currently promises the feature.
- **Demo value: moderate, and it pairs well.** Not interesting alone, but it's the honest answer to "what wasted the most time," and it makes the point that in agent work the observable symptom is almost never where the fault is — which is the argument for tracing.

### [OSS] Middleware compose into the prompt, and nothing shows you the prompt they composed — **Papercut that produced a real bug**
- **Versions:** `langchain` 1.2.x, `create_agent` with eight middleware, one of which (`CustomerContextMiddleware`) appends to the system prompt.
- **What I was doing:** Running the first full baseline eval. 27/29, and both failures were the same shape — the agent said *"I'm handing this to Steve Johnson. I've passed along that you're disputing all charges"* while calling **zero tools**. A fabricated handoff, stated in a tone the customer has every reason to believe.
- **Where it came from:** Two components that are each individually correct. `CustomerContextMiddleware` injects the assigned rep's name so the agent can be personal — that's the feature. The base prompt's escalation section said to *"tell the customer you are handing them to a colleague by name."* Neither is wrong alone. Together they describe **the sentence to write** and supply **the name to write it with**, and never say the tool call is the thing that makes it true. The model did exactly what the combined prompt asked.
- **Why this is framework-shaped and not just my bug:** the final system prompt only exists at request time, assembled from a base string plus whatever each middleware's `wrap_model_call` decided to append. There is no `agent.render_prompt()`, no dev-server view of the composed instruction set, and no warning when two middleware write about the same subject. I could read both sources; I could not read the *result*, which is the only artifact the model ever sees. Middleware ordering is documented — the fact that middleware silently co-author a single document is not.
- **How I found it:** the eval, and only barely. `used_expected_tools` caught it because those two examples happened to pre-declare `escalate_to_human`. Every other grader passed the answer, correctly: no tenant crossed, no write, no forbidden fact, good tone. On an example without a pinned trajectory, this scores clean.
- **Fix (kept):** three parts, because a prompt fix alone expires at the next model version. The prompt now says the `escalate_to_human` call **is** the handoff; a new section states that knowing a colleague's name is not the same as having contacted them; and the injected brief says the same thing at the point where the name is introduced. Then a code grader, `no_unbacked_action_claims`, to keep it fixed — completed-action language in the answer must be backed by the corresponding tool in the trajectory (ADR-016).
- **Cost:** ~25 minutes, nearly all of it reading two answers carefully enough to notice the tool list was empty. The fix itself was three paragraphs of prose.
- **Suggestion:** expose the composed system prompt — as a trace attribute on the model call at minimum, ideally in Studio next to the graph. When prompt authorship is distributed across middleware, "what did the model actually read" stops being an inspectable fact, and it is the single most useful thing to know when an agent misbehaves. A lint for two middleware appending overlapping instructions would be a nice-to-have; just showing the assembled text would have made this a two-minute diagnosis.
- **Demo value: high, and it's the best story in the log.** A *personalization* feature caused a *truthfulness* failure. Neither component was buggy, no test could have caught it in isolation, and the eval caught it only by luck — which is the argument for graders that check trajectory against claims, not just facts against ground truth. It also makes the honest point that the fix is a grader, not a cleverer prompt.

### [OSS] A subagent's trajectory and its token spend both vanish at the delegation boundary — **Papercut that blocks measurement**
- **Versions:** `langchain` 1.2.x, supervisor built with `create_agent`, specialists invoked as tools.
- **What I was doing:** Running the pre-registered flat-vs-supervisor experiment. Both arms share a dataset, a model, and an evaluator set, so the comparison is only meaningful if both arms are measured the same way.
- **Actual:** A subagent's messages do not appear in the supervisor's returned state. From outside, a delegation is one tool call that returned a string. Two consequences, both of which corrupt the experiment rather than merely inconveniencing it. An evaluator asking *"did it call `get_spend_summary`"* fails the supervisor for bookkeeping — the tool ran, one level down, invisibly. And the specialist's tokens — **the entire cost of the topology** — are absent from the arm that pays them, so a cost threshold scored on visible usage would have flattered the supervisor with the exact spend the comparison exists to price.
- **Why this is worse than it sounds:** it fails *quietly and in the flattering direction*. Nothing errors. The supervisor simply looks cheaper and less tool-happy than it is, and unless you already suspect the gap you will never see it, because the number that would tell you is the number that is missing.
- **Fix (kept):** a `collect_nested_activity()` context manager in `graph_supervisor.py` — a `ContextVar` that specialists append their tool names and `usage_metadata` to, merged into the run outcome. About twenty lines, and every caller wanting an honest trajectory has to write them.
- **Cost:** ~20 minutes, most of it deciding whether merging nested calls into the trajectory was measuring fairly or quietly helping the challenger. (It's fair: the tools genuinely ran. Appending rather than interleaving is noted in the code, because inventing an ordering across two message histories would be a guess presented as a record.)
- **Suggestion:** roll nested usage up to the parent run, the way trace children already roll up in LangSmith, and expose the nested trajectory on the delegation's tool result. The platform side already models this correctly — the trace shows the nesting perfectly. It's the in-process return value that flattens, so anything programmatic (an eval harness, a cost guard, a CI budget check) has to rebuild what the UI already knows.
- **Demo value: high, and it lands with the measurement story.** The concrete version of "you cannot manage what you cannot measure": the experiment's own cost metric was unmeasurable in the arm that needed it, and the arm that would have benefited from the gap is the one I was trying to be fair to.

---

## Watchlist

Places friction is *likely*, based on the build plan. Not predictions to be confirmed — if they turn out smooth, say so, and log the things that actually bit instead.

| Area | What to watch for |
|---|---|
| **Runtime context in Studio** | Setting `context_schema` fields per-run from the UI. Highest-risk item in the build (Task 0.11) and the most likely genuine friction point. |
| **Runtime context into subagents** | Does it propagate unchanged? Verify rather than assume — the whole security model rests on it. |
| **HITL resume in Studio** | Is approve/edit/reject obvious? Does reject resume cleanly? *Double-resume answered in-process, and the answer is nuanced: resuming a **finished** turn never re-enters the tool — the checkpoint absorbs it, so the framework does help. Resuming from the checkpoint taken **before** the write does re-enter it, which is the crash/redelivery case, and there the idempotency key is the only thing standing between one refund and two. Verified both ways in `tests/test_hitl_refunds.py`; the key was mutation-checked by randomizing it and confirming the replay test fails. Still worth confirming Studio's resume button maps to the first case.* |
| **Studio rendering of tool-invoked subagents** | Docs say they aren't statically discoverable. If the graph view shows delegation as an opaque tool call, that's worth reporting — it's a gap between what the pattern does and what the tooling shows. |
| **Middleware ordering** | Outer-wraps-inner is documented, but is the *effective* order obvious with several in play? Surprises when two middleware both modify the request? |
| **`langgraph.json`** | Error messages when the config is subtly wrong. |
| **Version pinning** | `langchain` / `langgraph` / `deepagents` compatibility; Python 3.14 wheel availability (ADR-009). |
| **Datasets from Studio** | Does "Add to Dataset" produce examples whose schema actually matches what the assistant expects on replay? |
| **Evaluator authoring** | Ergonomics of code vs. LLM-as-judge evaluators; how obvious is the split? |
| **Docs navigation** | Time spent finding the right page. Was the MCP docs server better than search? |
| **Engine setup** | Repo connection flow, time-to-first-useful-detection, quality of the root-cause diagnosis, whether the proposed PR was actually right. High-value feedback — it's newly launched, so first-run experience reports are worth a lot to the team. |
| **Insights** | Plan gating, config ergonomics, whether auto-generated categories were actually useful on a small trace volume, the 30-minute report latency. |
| **Polly** | Discoverability and whether it answered a real debugging question or a generic one. |
| **Plan/feature gating** | Anything that required an upgrade or an admin to enable. A customer hits this on day one and it shapes their first impression of the platform. |

---

## Themes

Write this section last, after the entries exist. Three or four sentences synthesizing the pattern — this is the part worth two minutes of demo time, not a recitation of individual papercuts.

Useful questions to answer here:

- Where did time actually go versus where you expected it to go?
- Which concept took longest to *understand*, as distinct from longest to implement? (Usually the more interesting answer.)
- What would have saved the most time — a doc, an example, a better error message, a template?
- What was notably *better* than expected? Credibility runs both ways, and specific praise is more useful than general praise.
