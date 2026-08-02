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

---

## Watchlist

Places friction is *likely*, based on the build plan. Not predictions to be confirmed — if they turn out smooth, say so, and log the things that actually bit instead.

| Area | What to watch for |
|---|---|
| **Runtime context in Studio** | Setting `context_schema` fields per-run from the UI. Highest-risk item in the build (Task 0.11) and the most likely genuine friction point. |
| **Runtime context into subagents** | Does it propagate unchanged? Verify rather than assume — the whole security model rests on it. |
| **HITL resume in Studio** | Is approve/edit/reject obvious? Does reject resume cleanly? **Does a double resume create two tickets?** That last one is what the idempotency key exists for and it's worth knowing whether the framework helps. |
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
