# Slack Kickoff

Post this on Day 0, before writing agent code. The brief allots 15-30 minutes and says engagement is evaluated.

**Cut to ~300 words from the previous 800.** The earlier draft was a design document pasted into a chat window. Nobody reads that in Slack, and pre-committing to a detailed architecture before writing code signals the opposite of what this exercise rewards. State the shape, name the hard gates, flag what would change your mind, ask the two questions that actually affect scope.

---

## The message

**Slack formatting:** single asterisks for bold, not double. Blank lines between the numbered questions — Slack's list rendering is unreliable when they're packed together, and those are the lines you most want read.

> *Plan of attack — Chinook support agent*
>
> Hey Neil and Conrad — sharing my plan before I start building.
>
> I'm treating this as a small customer POC rather than a feature showcase.
>
> *The assumption I'm working from:* billing questions are common and straightforward, but still sensitive because they involve customer data — and the same conversation is an unused music-discovery opportunity. Chinook has no support-ticket history, so this is a hypothesis about a store like this, not something the data proves.
>
> *Scope:* I'll start with two areas — account and billing questions, and recommendations based on the customer's purchase history. If those are solid, I'll add refund-request intake with human approval as a third.
>
> My two hard requirements are that a customer never sees another customer's information, and that the agent never writes anything without approval.
>
> *Approach:* I'll start with one agent and a small set of clearly defined tools. The signed-in customer will come from the application, not from anything typed in chat or selected by the model. I'm avoiding text-to-SQL so customer isolation doesn't depend on SQL written by the model.
>
> I'll also keep each conversation tied to the customer who started it, so a conversation containing one customer's history can't later be reopened as someone else.
>
> If one agent struggles to choose the right tools, I'll compare it with a version that separates billing and recommendations. I'll use the same test cases, write down the success criteria before running the comparison, and keep the simpler version if the results are effectively tied.
>
> *LangSmith:* I want to show one real improvement loop rather than a feature tour — find a failure in a trace → save it as a test case → evaluate it → make a change → compare the result. I'll use Studio for the interactions instead of building a custom UI.
>
> *Two questions:*
>
> 1. I'm planning to use my own LangSmith account and run the application locally. Is there an account or project you'd prefer me to use? I'm asking now because access to Engine, Insights, and Polly may depend on the account — and Engine's first run is metered, around 30–40 compute units, so I'd rather not put that on the wrong bill. No problem if they aren't available; I'll plan the demo without relying on them.
>
> 2. Is there a particular support or reliability problem you'd like me to optimize for? Otherwise, I'll continue with billing questions as the assumed high-volume case.
>
> I'll share the first working Studio trace once the end-to-end flow is running, along with anything I learn that changes the plan.

**Voice note.** This is deliberately plainer than an engineering doc — no backticks, no "exfiltration path," no `create_agent`. Neil is a director and the post is a first impression, so readable beats dense. The technical precision is still there ("no argument for it to set," "resumed as someone else"), just carried in ordinary words.

---

## Why these two questions to open with

Both are things **only they can answer**, and both change what gets built.

**Q1 — workspace, which is really an access-and-cost question.** Provisioning takes time, so this affects three days of sequencing. Fold the "which account?" logistics into it rather than asking them separately: on a personal account, Insights needs Plus/Enterprise and Engine's initialization pass is 30-40 LCUs at $1.50 each — **$45-60 out of your own pocket**. Naming that number does real work. It's the reflex you'd want from someone deploying LangSmith at a customer, it shows you read the pricing page and not just the feature list, and it gives them an easy way to say "plan without Engine."

**Q2 — the buyer's actual pain.** ⚠️ **This replaced "do you want me treating you as the prospect team?", which was a bad opening question.** The brief already answers it: *"we will simulate working with a music store," "10 minutes for customer questions," "tell the business a compelling story."* They're the prospect. Asking anyway is the exact failure this doc warns against everywhere else — burning your first impression on something a re-read settles.

The replacement is better on four counts. It's genuinely open — the brief gives a setup but no specific failure. It carries its own default, so you're inviting correction rather than asking to be unblocked. It's what a Deployed Engineer actually does on a first call, which is the role being evaluated. And it's *coherent with the post it sits in*: you state an assumption, flag that Chinook can't validate it, and then ask the only people who can. A stated assumption with no attempt to test it is just a hedge.

If the answer is a real failure mode, Block 1 opens on their problem instead of your inference — a materially better demo. If they decline to invent one, you've lost nothing and shown you'd have asked.

Kept out of the *opening post*, not out of the channel: `context_schema` in Studio, subagent idioms, HITL resume UX. Each is answerable by twenty minutes with the documented pattern, so asking before trying wastes the one impression you get. Ask them after the attempt, where they become a much better artifact:

> "On langchain 1.3.x / langgraph 1.2.x, setting `customer_id` via `context_schema` from Studio's config panel — I expected it readable via `ToolRuntime` inside a subagent, but I'm seeing [X]. Minimal repro in thread. This decides whether the supervisor variant is viable at all. Is [X] intended?"

Version, expected, observed, repro, and the decision it blocks.

**The strongest one available is the gateway question**, once you've attempted it (Day 0 task 0.13):

> "Thread ownership has to be validated before the checkpointer loads state — a `before_agent` hook is too late, since the other tenant's messages are already in memory by then. I've put the check in an application layer in front of the Agent Server, which means Studio bypasses it. Is there a supported way to run Studio through a custom pre-invoke layer, or is proving this with a test the expected pattern?"

That one is worth asking because it's a real production concern, it demonstrates you understand where the boundary has to sit and why, and the answer changes whether a demo moment exists. Note it also **does not ask them to design it for you** — you've already built it and you're checking the idiom.

---

## Post throughout — the brief asks for this explicitly

This is worth being deliberate about, because the instinct to look self-sufficient works against you here. The brief says twice that engagement is wanted:

> *"Use the slack channel to ask questions along the way! We know we aren't providing any starter code, but we encourage questions and will provide guidance in slack!"*
>
> *"You are strongly encouraged to use the Slack channel throughout the exercise to: ask clarifying questions · share assumptions or proposed approaches · surface friction points · engage collaboratively with the team as you work through the assignment."*

Note that only one of those four is a question. **Sharing assumptions and surfacing friction are invited on their own terms** — you don't need to be blocked to post, and a channel with one message in it reads as someone working around the team rather than with them. What's being evaluated is "how you think, communicate, learn, and navigate ambiguity," and the channel is the only place that's visible before demo day.

**These are triggers, not a quota.** The table below is a list of moments that are *worth* a post if they happen — not a schedule to satisfy. Posting because a slot is empty produces exactly the manufactured content that makes a channel worse, and an experienced reader can tell. If a half-day passes with nothing genuine, say nothing.

⚠️ **One specific trap: do not post the subagent middleware boundary as a fresh discovery.** It's already a planning decision (ADR-006) — presenting a conclusion you reached by reading docs as friction you hit while building is a small dishonesty that costs a lot if anyone checks the timeline. Post it only if you *reproduce* it during implementation, and then post it properly: version, expected, observed, minimal repro.

| When | Post | Which category |
|---|---|---|
| Day 0 | Plan of attack + the two questions | Clarifying |
| Day 0 end | First Studio trace working, or whatever blocked it | Collaborative |
| Day 1 | The gateway question above, once you've attempted it | Clarifying |
| Day 1 | A friction point **you actually hit** — not one you anticipated in planning | Friction |
| Day 2 | Flat vs. supervisor result **if you ran it** — including "I scoped it out to protect W2" | Assumption tested |
| Day 2-3 | "Docs say initialization audits past traces — is there a rough volume floor below which clustering isn't useful?" | Clarifying |
| Day 3 | Anything else headed for the friction log | Friction |

**Post friction live rather than saving it for Block 6.** It's more useful to them in the moment, it's a better artifact than a retrospective list, and it proves the log wasn't reconstructed on Thursday morning.

The one filter that still applies: nothing answerable in five minutes with the docs or chat.langchain.com. That's the difference between engaging and outsourcing.

---

## Tone notes

- **State decisions with rationale; ask only about genuine uncertainty.** "I'm doing X because Y" invites correction without outsourcing the choice. "What should I do about X?" hands them your job.
- **Name the tradeoff you accepted**, not just the choice. That's what makes it a decision instead of a preference.
- **Flag reversals loudly.** "I was wrong about X, here's what changed my mind" is the single strongest thing you can post in that channel — the brief names *learning agility* as something strong submissions demonstrate, and a reversal is the only hard evidence of it.
- **Don't ask anything the docs answer in five minutes.** The cheapest possible way to lose credibility with the team that wrote them.
- **Don't go silent for two days.** The failure mode here isn't asking a weak question; it's an empty channel that leaves them nothing to evaluate but the final demo.
