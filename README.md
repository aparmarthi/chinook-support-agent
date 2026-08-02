# Chinook Music Store Support Agent

A customer support agent for an online music store, built on LangChain's `create_agent` and instrumented end-to-end with LangSmith. Built for the final round of the LangChain Deployed Engineer process — the deliverable is a live 35-minute demo to a mixed business/technical audience.

**Status:** planning complete; data setup validated (row counts and demo facts verified against the built database); agent build not started.

---

## What it does

Three workflows against the [Chinook](https://github.com/lerocha/chinook-database) sample database (59 customers, 3,503 tracks, 412 invoices):

1. **Account & billing** — invoice lookups and spend summaries, aggregated in SQL rather than by the model
2. **Music discovery** — recommendations grounded in the customer's own purchase history, excluding tracks they already own
3. **Refunds & handoff** — drafts a refund *request ticket*, pauses for human approval, and prepares a handoff summary for the customer's actual assigned support rep

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
| [`docs/BRIEF.md`](docs/BRIEF.md) | **The brief verbatim + compliance matrix** — source of truth for every "the brief says…" claim |
| [`docs/PRD.md`](docs/PRD.md) | Problem, users, scope, success metrics, security model, ROI |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Cognitive architecture, tools, middleware, evaluation matrix, data layer |
| [`docs/decisions.md`](docs/decisions.md) | 15 ADRs — the choices, the reversals, and what would change our mind |
| [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md) | Day-by-day plan, budget, cut order, risk register |
| [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) | Audience read, run of show, hot takes, statements to avoid, Q&A prep |
| [`docs/COMPETITIVE.md`](docs/COMPETITIVE.md) | Positioning and objection handling — read the morning of |
| [`docs/FRICTION_LOG.md`](docs/FRICTION_LOG.md) | Friction log — filled during the build |
| [`docs/slack-kickoff.md`](docs/slack-kickoff.md) | Plan-of-attack post and the two questions worth asking |

**Read order:** PRD → ARCHITECTURE → decisions → BUILD_PLAN → DEMO_SCRIPT → COMPETITIVE.

## Design decisions that reversed

Three positions changed during review, before any code was written. Each is recorded as an ADR with the superseded reasoning intact, because a decision you can explain reversing is worth more than one that happened to be right first time.

**Authorization moved out of middleware into the data layer** ([ADR-006](docs/decisions.md)). Supervisor-level middleware cannot see tool calls nested inside subagents, so a tenant guard placed there would have been watching the wrong boundary. The fix makes the boundary topology-independent: it holds no matter how the graph is arranged.

**A tenant-scoped query is not a tenant-scoped conversation** ([ADR-013](docs/decisions.md)). Scoping every SQL statement doesn't help if a thread carrying one customer's history is resumed under another's identity. Threads are now bound to a tenant and ownership is validated before the checkpoint loads.

**The supervisor became a hypothesis instead of a commitment** ([ADR-002](docs/decisions.md)). A flat agent is the baseline; specialization has to clear pre-registered thresholds to ship.

## Engine sequencing and cost

Two things worth getting right, both of which this plan initially got wrong.

**Seed before enabling.** Engine's initialization pass audits *past* traces and clusters them into prioritized issues, while recurring scans run every six hours whether or not anything is found. Enabling on an empty project pays for scans with nothing to analyze. Build → seed → then enable once.

**It's metered.** LangChain Compute Units are $1.50 each — 30-40 for initialization, 10-15 per scan. Enabled across a three-day build that's $250-300; seed-then-enable is ~$50-90 for the same artifact. Set a spend limit on Day 0, because blank means unlimited. Engine is upside rather than a dependency: the demo works without it.

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

_To be filled in after the build: containment rate, evaluator scores, experiment comparison, cost per conversation._
