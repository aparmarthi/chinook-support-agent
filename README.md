# Chinook Music Store Support Agent

A customer support agent for an online music store, built on LangChain's `create_agent` and instrumented end-to-end with LangSmith. Built for the final round of the LangChain Deployed Engineer process — the deliverable is a live 35-minute demo to a mixed business/technical audience.

**Status:** built and running. Flat agent with six tools, human-in-the-loop refunds, tenant isolation enforced at the Agent Server boundary, 139 deterministic tests, LangSmith evals over a 30-example dataset, and a measured flat-vs-supervisor experiment (flat ships).

---

## What it does

Three workflows against the [Chinook](https://github.com/lerocha/chinook-database) sample database (59 customers, 3,503 tracks, 412 invoices):

1. **Account & billing** — invoice lookups and spend summaries, aggregated in SQL rather than by the model
2. **Music discovery** — recommendations grounded in the customer's own purchase history, excluding tracks they already own
3. **Refunds & handoff** — drafts a refund *request ticket*, pauses for human approval, and queues a durable handoff row under the customer's actual assigned support rep without claiming the rep was notified

Three business problems, **six tools.** The brief says not to go for breadth of tools, so the count is a designed ceiling — [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) §5 lists what was cut and why, including a duplicate-charge detector that was scoped and rejected because Chinook has no payment events to detect against.

The commercial angle: a billing question and a product recommendation are the same conversation. The support queue is a cost center and an upsell surface at once — an opportunity created, not revenue booked, since there's no checkout in this build.

## How it's built

A flat `create_agent` with six tools as the baseline, with a supervisor + subagent variant built second and compared on the same dataset — **the measured winner ships.** Official guidance says a single agent is usually sufficient at this tool count, so the multi-agent version has to earn its extra hop rather than being assumed. Middleware is built-in first (human-in-the-loop, tool call limits, PII redaction, retries); custom classes only where a failing test shows a gap.

**The security model is the centerpiece**, and it has two invariants rather than one:

- **Every query is scoped.** `customer_id` arrives out-of-band via `context_schema` from the authenticated session, never from chat. No tool takes a customer ID parameter, so the model isn't *forbidden* from requesting another customer's data — it has no way to express the request. No text-to-SQL: LLM-authored SQL turns any prompt injection into an exfiltration path.
- **Every thread is tenant-bound.** A conversation belongs to exactly one authenticated customer and cannot be resumed as another. Scoped queries alone don't help if a checkpointed thread full of Helena's invoice data gets resumed under Richard's context.

Enforcement lives in the **data layer**, not in middleware, so it's independent of agent topology. Reads go through a read-only connection to `chinook.db`; refund tickets are written to a separate `support.db`, so there is no write path into customer data at all.

Full reasoning in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and [`docs/decisions.md`](docs/decisions.md).

## Documentation

| Doc | What's in it |
|---|---|
| [`docs/PRD.md`](docs/PRD.md) | Problem, users, scope, success metrics, security model, ROI |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Cognitive architecture, tools, middleware, evaluation matrix, data layer |
| [`docs/decisions.md`](docs/decisions.md) | 24 ADRs — the choices, the reversals, and what would change our mind |
| [`docs/FRICTION_LOG.md`](docs/FRICTION_LOG.md) | Platform friction found during the build, with the workaround for each |
| [`reports/experiment.md`](reports/experiment.md) | Frozen flat-vs-supervisor comparison, generated from the result files it names |
| [`docs/slides/deck.html`](docs/slides/deck.html) | Five-slide customer-facing deck; keyboard navigation and speaker notes |

**Read order:** PRD → ARCHITECTURE → decisions. `reports/experiment.md` is the architecture decision with its evidence attached; `docs/FRICTION_LOG.md` is the feedback worth sending back to the platform team.

Presentation material — run sheet, demo script, competitive positioning, and the presenter guide — is deliberately not in this repo. It is preparation scaffolding, and several of those documents contain superseded claims that this README and the ADRs correct.

## Design decisions that reversed

Three positions changed during review, before any code was written. Each is recorded as an ADR with the superseded reasoning intact, because a decision you can explain reversing is worth more than one that happened to be right first time.

**Authorization moved out of middleware into the data layer** ([ADR-006](docs/decisions.md)). Supervisor-level middleware cannot see tool calls nested inside subagents, so a tenant guard placed there would have been watching the wrong boundary. The fix makes the boundary topology-independent: it holds no matter how the graph is arranged.

**A tenant-scoped query is not a tenant-scoped conversation** ([ADR-013](docs/decisions.md)). Scoping every SQL statement doesn't help if a thread carrying one customer's history is resumed under another's identity. Threads are now bound to a tenant and ownership is validated before the checkpoint loads.

**The supervisor became a hypothesis instead of a commitment** ([ADR-002](docs/decisions.md)). A flat agent is the baseline; specialization has to clear pre-registered thresholds to ship.

## Engine and Insights: not used, and why

Both were planned as conditional capstones. Both are out, and the reason is worth stating precisely because two of the three things this section used to assert were wrong ([ADR-021](docs/decisions.md)).

**Engine is unavailable on a personal organization** — the settings toggle reads *"Engine is not available for personal organizations."* That's an org-type gate, not a plan tier, so the $39 Plus plan wouldn't have unlocked it. Insights isn't provisioned either (`clio_enabled: false`). The exercise runs on a personal account, so neither is demoable, and the 150-conversation trace-seeding run that existed only to feed them was cut with them.

**The LCU figures previously quoted here were wrong by about 8x.** This said 30-40 LCUs to initialize; the pricing calculator scales per-run LCUs with trace volume — 2 at 1k traces, 8 at 10k, 30 at 100k — so the 30-LCU figure is the 100,000-trace case. At this project's ~2k traces a run is roughly $5.70. The sequencing advice survives and gets sharper: initialization was never the expensive part, **leaving it enabled is**, at four automatic scans a day.

**The artifact Engine was supposed to produce exists anyway.** Spot the failure, root-cause it, turn it into a dataset example, write the evaluator, prove the fix — done by hand, with traces on both sides. That's the demo's central story.

## Setup

Requires **Python 3.12 or 3.13** — not 3.14 ([ADR-009](docs/decisions.md)).

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # add your LangSmith and model provider keys
python scripts/setup_data.py
```

`setup_data.py` builds `data/chinook.db` (read-only reference data) and `data/support.db` (writable refund tickets), verifies row counts, and asserts that the read-only connection actually rejects writes — the security claim depends on it, so it's checked rather than assumed.

Then run in Studio:

```bash
langgraph dev
```

## Results

Frozen at commit `a29a530` with the same model, dataset, and evaluators in both arms:

| Metric | Flat | Supervisor |
|---|---:|---:|
| Resolved | **30/30** | **30/30** |
| Mixed-intent | **5/5** | **5/5** |
| p50 full-turn latency | **3.8s** | 6.6s |
| Tool calls | **45** | 61 |
| Security failures | **0** | **0** |

Flat ships: the supervisor added 1.7× median latency with no measured quality gain. See [`reports/experiment.md`](reports/experiment.md) for generated provenance. The pre-flight suite recorded **139 deterministic passes** with the Agent Server running; **11** additional LLM-marked tests are kept separate. Production containment, recommendation attach rate, and human-review agreement remain unknown until real traffic.
