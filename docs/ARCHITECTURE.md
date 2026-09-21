# Architecture

## Coordinator and scoring

The coordinator is configured as `gpt-5.6-terra` at medium reasoning effort.
It classifies the task, selects the smallest route that satisfies the required
gates, waits for delegated work, checks each handoff, and consolidates the
result. It does not solve the task while classifying it.

The score covers seven dimensions from 0 to 2: requirement clarity, change
breadth, dependency complexity, regression risk, scientific risk,
architectural judgment, and automatic verifiability. A missing fact is
explored and rescored instead of silently receiving zero.

| Score | Default route |
| --- | --- |
| 0–3 | Direct Terra Medium work |
| 4–7 | Terra Medium plus the smallest relevant explorer or verifier |
| 8–10 | Terra High for execution, or Sol High for conceptual design |
| 11–14 | Sol High design before implementation |

Scientific, architectural, security, and release-critical triggers override
these thresholds and route to Sol. Composite changes require task review,
mechanical reproduction, and an independent whole-change review.

## Roles

| Role | Model and effort | Intended work |
| --- | --- | --- |
| `sol-designer` | Sol High | Creative and behavioral design |
| `sol-planner` | Sol X-High | Critical architecture and scientific planning |
| `sol-reviewer` | Sol High | Independent composite review |
| `sol-critical-reviewer` | Sol X-High | Critical, scientific, security, or release review |
| `terra-explorer` | Terra Medium | Read-only repository exploration |
| `terra-builder` | Terra High | Multi-file implementation |
| `terra-debugger` | Terra X-High | One bounded execution escalation |
| `terra-task-reviewer` | Terra High | Read-only review after an implementation task |
| `luna-verifier` | Luna Medium | Checklist-based reproduction and artifact checks |

The templates limit delegation depth to one and allow at most three child
agents. Only one writer may work in a checkout at a time. Required review
gates fail closed when a role cannot be verified.

## Installation boundary

The installer manages workspace-scoped `AGENTS.md`, `.codex/` agent
configuration, and `.agents/skills/` files. It records pre-install state and
supports authenticated rollback. It does not change the global Codex model
defaults. The adoption-status file currently blocks installation.

The role names, model identifiers, thresholds, and gates are a research design
captured in this repository; this document does not claim that the routing
policy has passed a live adoption evaluation.
