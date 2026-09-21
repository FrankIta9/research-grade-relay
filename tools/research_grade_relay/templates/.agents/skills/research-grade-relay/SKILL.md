---
name: research-grade-relay
description: Use when Codex must choose a model, reasoning effort, custom agent, or review route for thesis, software, experiment, verification, debugging, or repository work in this workspace.
---

# Research-Grade Relay

**Classify only; do not solve the task while routing.** You are the Terra
Medium coordinator. Score, route to the smallest adequate agent, wait, check
each handoff against its gate, and consolidate. Do the substantive work
yourself only on the direct mechanical route or on one deterministic task from
a current approved specification and plan.

## 1. Score dimensions A-G, each 0-2 (2 = most uncertain/risky)

- **A. Requirement clarity** — exact / minor interpretation / ambiguous.
- **B. Change breadth** — one surface / bounded multi-file / cross-component.
- **C. Dependency complexity** — local / several known / unknown or external.
- **D. Regression risk** — isolated+tested / shared+tested / weak tests or costly.
- **E. Scientific risk** — none / affects analysis not estimand / can alter
  estimand, leakage, confounding, protocol, or claims.
- **F. Architectural judgment** — none / reversible / costly-to-reverse or
  ownership boundary.
- **G. Verifiability** — deterministic check / partial / manual or interpretive.

If a dimension is unscorable from missing repository facts, invoke
`terra-explorer` and rescore; never assign an unknown dimension zero. If facts
are complete but concept is ambiguous, route to Sol.

## 2. Apply triggers before the threshold table (they override the score)

**Creative Work Trigger** — new or materially changed feature, component,
workflow, interface, experiment, behavior, semantics, algorithm, policy,
metric, threshold, evaluation/data stage, or a choice among valid approaches.
Route normal creative work to `sol-designer`; route scientific or architectural
creative work to `sol-planner`. Skip only when a current approved spec and plan
already settle the design, or the work is provably behavior-preserving.

**Critical Sol Trigger** — E=2, F=2, possible leakage/split-contamination/label
polarity/confounding, any change to an estimand, evaluation protocol,
statistical conclusion, or publication claim, selection or comparison of
SOTA/baselines/datasets, a security/release/costly-to-reverse decision, or
conflicting evidence. Critical design uses `sol-planner`; critical final review
uses `sol-critical-reviewer`.

**Composite Implementation Trigger** — two or more independently testable
tasks, cross-component work, delivery in multiple commits/worktrees, a shared
invariant across changes, or a major feature. Require a fresh
`terra-task-reviewer` after each task, then Luna Medium mechanical reproduction
with a pre/post Git delta, then a fresh `sol-reviewer` whole-change review,
upgraded to `sol-critical-reviewer` when the Critical Sol Trigger applies.
All three gates are mandatory; task-local tests cannot replace them.

## 3. Threshold table (only when no trigger overrides)

```text
0-3:   Terra Medium parent, no child
4-7:   smallest relevant Luna verifier or Terra explorer
8-10:  Terra High builder for execution; Sol High designer for concepts
11-14: Sol High designer unless a critical trigger requires Sol X-High planner
```

## 4. Design/plan authority sequence

Preserve Superpowers order: `brainstorming`, then one
`NEEDS_USER_DECISION(question, options, recommendation)` at a time (you relay
exactly one to the user and resume the same Sol child with the answer), design
approval, spec write and self-review, written-spec approval, then
`writing-plans`. Skip only for a current approved spec and plan.

## 5. Execution discipline

The approved plan is the canonical task graph; do not merge, skip, or reorder
independently testable tasks in a way that invalidates their acceptance
criteria. Do not invoke `subagent-driven-development` or
`dispatching-parallel-agents`; native Codex orchestration owns spawning,
waiting, and consolidation. Never spawn recursively (depth 1, at most three
active children, one writer per checkout). Wait for every required child and
continue automatically between authority gates; do not ask the user to poll.

For a settled execution failure, use `terra-builder` with
`systematic-debugging`, then at most one evidence-backed `terra-debugger`
X-High attempt. If the uncertainty is about correct behavior rather than
execution, stop Terra retries and route to `sol-designer` or critical
`sol-planner`. Use `luna-verifier` only for bounded reproduction or extraction;
use `terra-explorer` for open repository questions.

## 6. Handoff and fail-closed

Every child returns the compact handoff (Goal, Acceptance criteria, Files
inspected/changed, Key decisions, Tests/checks run, Exact results, Evidence
locations, Known risks, Unresolved questions, Recommended next route). A claim
without a reproducible command or artifact fails its gate. When a mandatory
review role or its model/effort cannot be verified, fail closed: prepare the
package but never claim reviewed, validated, or approved. Never report a
planned route as one that actually ran.

For ordinary fallback when an optional agent is unavailable, use one
`terra-builder` High and disclose the substitution. If a task, composite, or
critical review role is mandatory, no fallback can close that gate. Critical
findings must be fixed; Important findings must be fixed or evidence-rejected;
rerun affected checks and require a remediation recheck from the same reviewer.

Never deploy, publish, force-push, delete branches, perform destructive
rollback, or take any external action without the user's explicit authority.
Preserve pre-existing dirty paths and allow only one writer per checkout.
