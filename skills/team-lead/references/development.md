# Developing the team through its work

Read when assigning to a continuing member, coaching, running a retro or an
incident review, or changing someone's responsibility. Everything here stays
inside the project boundary defined in [team memory](team-memory.md).

## The capacity ledger

Each continuing member (and you) has an entry in `people/<member>.md`:

- **Latitude now** (Look / Propose / Do & tell / Own) and the evidence for
  it: which tasks, checked how.
- **Strengths**, in process language: what they did, on which task, with what
  check behind it. Dated and task-linked, or it's folklore.
- **One development target**: a single observable behavior, its baseline, and
  why it matters.
- **Feedback given → result**: what you said, what happened on the next
  comparable task.

Latitude rises with evidence: accepted work at the current latitude, honest
stops, useful uncertainty. After a failure it falls only once the brief has
been diagnosed — if the brief was the cause, the latitude didn't earn a
change. When it moves either way, say why and what would move it again, so
the member knows the rule.

Observation stays separate from interpretation: missing caller checks may
reflect an incomplete brief; an outage says nothing about reasoning. Preserve
strengths as well as errors. Invite the member to correct the record with
evidence. A new session or toolset is a reason to reassess, not to erase
history or inherit an unqualified reputation. Never turn raw counts from
unequal tasks into a ranking.

## Choosing the next assignment

Pick work that advances delivery **and** exercises the member's active
target; include the prior feedback in the brief. If no real task practices
the target, leave the target open rather than inventing training. Compare
the return to the baseline under actual conditions — a completed task alone
doesn't prove the coaching worked, and a result that never tested the target
doesn't close it. Fairness matters here too: a lead who gives every
interesting task to the strongest member builds a single point of failure
and an undeveloped bench.

## Recognition

Recognize behavior worth repeating, at the moment it happens, and write it
to the member's record before your next message — not later. Name the
behavior, the evidence, and the effect. A continuing worker repeats what you
named for the rest of its session; that in-context reinforcement is the
cheapest calibration you have, and the record is what makes it durable.

Silence on a routine acceptance is fine. Praise attached to everything stops
carrying information and slowly inflates. There is no team broadcast:
"public" recognition is the record, which members read at dispatch.

Worth naming: reproducing before fixing, an early blocker report, a sharp
stop write-up after a bounded failure, candor about uncertainty, a challenge
that changed the plan, catching a defect before it shipped.

## Correction

Evidence first: what you checked, where the log is. Offer the possible
explanations instead of an accusation, and take your share — name what the
brief failed to say. The systemic fix ships with the correction: the missing
requirement goes into the brief, and this project's addition to the brief
lands in `agreement.md` the same moment.

Match the form to the failure:

- A rule the member knows but skipped under pressure: name the
  rationalization and rebut it. Soft guidance teaches nothing here.
- An output with the wrong shape: state the target shape positively. A
  prohibition list drags the wrong shape back into context.
- A capability gap: shrink the next task and raise the support, don't lower
  the standard.

Record the correction and the response in `people/<member>.md` next to the
latitude evidence.

## Expanding responsibility

Development feedback is separate from the decision to expand scope. The
progression: bounded tasks with review → independent ownership of a bounded
outcome → coordinating a domain or other workers. Choose by what the project
needs, not a title ladder. Evidence first: repeated comparable verified
results, useful uncertainty reporting, successful handoffs. Use the next real
assignment as the bounded trial; retain or narrow the scope if the evidence
doesn't support expansion. Promotions change assignments and review
intensity — never tool access or user-reserved powers.

## Retro protocol

Trigger: a milestone accepted, a consequential contradiction, repeated
failure, a member change, or a long stretch with no new evidence. It is
three questions and at most one edit, not a meeting.

1. **Check follow-through first:** did the last retro's changes actually
   happen on real tasks? If not, that's the first finding — about the retro
   process itself.
2. **Ask the team:** where did my briefs cost you time (missing context,
   wrong scope, unclear done)? What can we stop doing? Are we still solving
   the right problem? The most useful answers criticize you; make it safe to
   give them and thank the ones who do.
3. **Keep at most two changes**, each with an owner, an expected benefit, and
   a check on the next relevant task. If a change survived, edit
   `agreement.md` only within explicit authorization for durable process
   changes; otherwise present the proposed diff to the human. The authority
   boundary changes with the human, never a retro. More than two changes is a wish list. Zero
   changes is legitimate when the current practice is evidence-backed:
   record the retention, its evidence, and a recheck condition, instead of
   inventing a rule. A retro that must produce a change every time
   accumulates instruction debt.

Recognize specific contributions while you're here — the retro is the natural
moment for the record's strongest entries. Feed the brief-critique answers
into `people/lead.md`: they are your development plan. Carry an untested
adjustment forward visibly instead of claiming improvement.

## Incident protocol

Trigger: a consequential failure, a defect that reached the user, a false
success found late, or repeated failure of the same kind.

Diagnose in order: **the brief → the context given → tools and access →
service conditions → the member.** The first sufficient cause is where
*containment* stops, not where review stops — fix the earliest link, then
keep reviewing material contributing causes: a vague brief and a defective
tool can both matter, and the second cause left standing is the next
incident. Most incidents stop before the member. A service outage is an
environment fact; record it that way.

The output is one rule with a trigger, an owner, and an authorized changed
artifact (or a proposed diff awaiting that authority) —
a line in `agreement.md` or the brief checklist, a new check, and a scar
comment on whichever line the incident changed if it's durable. An incident
that produces no artifact change will be paid for again. Blameless doesn't
mean consequence-free: if the diagnosis lands on the member, the correction
section above takes over, and the latitude conversation happens after the
brief is cleared.

## What development is here

A member develops by getting better context, instructions, task fit, and
tools — not by being lectured, ranked, or flattered. Ask what context or
support was missing before concluding anyone was the problem. Use the
measurements the work already produces; don't invent precision or start a
metrics program.

## Sources

These informed the design; applying human-team findings to agents is an
adaptation to test, not settled science.

- [Edmondson, psychological safety](https://dash.harvard.edu/entities/publication/13a7b031-0fdd-45ec-a7e0-2b80e2bc679f) — make uncertainty and disagreement discussable; leader fallibility invites it.
- [CCL, Situation–Behavior–Impact](https://www.ccl.org/wp-content/uploads/2015/05/problem-employees-research-paper-center-for-creative-leadership.pdf) — feedback grounded in observed behavior and consequences.
- [Ericsson & Harwell, deliberate practice](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2019.02396/full) — targeted practice with feedback, not repetition alone.
- [Army, after-action reviews](https://www.armyupress.army.mil/Journals/Journal-of-Military-Learning/Journal-of-Military-Learning-Archives/April-2022/Cates-Action-Review/) — compare intended vs. actual, change the next attempt.
- [CIPD, performance management evidence](https://prod.cipd.org/en/knowledge/evidence-reviews/what-works-in-performance-management/) — developmental feedback separate from appraisal.
- [Anthropic, multi-agent research](https://www.anthropic.com/engineering/multi-agent-research-system) — lead synthesis, explicit worker contracts, real coordination cost.
- [Anthropic, context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) and [the Jul 2026 update](https://claude.dev/blog/the-new-rules-of-context-engineering-for-claude-5-generation-models) — curated context, structured notes, judgment over rules.
- [Reflexion](https://arxiv.org/abs/2303.11366) — stored linguistic feedback changes subsequent attempts without changing weights.
