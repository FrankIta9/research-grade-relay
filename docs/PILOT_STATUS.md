# Pilot status

## Decision

The relay is not adoption-eligible. The live v1 pilot was rolled back, and the
runtime artifacts were removed from the target workspace. The repository keeps
the adoption gate set to `false`.

## Evidence boundary

Pilot v1 produced exact route conformance in 0 of 5 evaluated cases. The token
comparison was descriptive and confounded by cache state and run order; it does
not establish a token-saving benefit. The v2 candidate contains code and
evidence-handling remediation, but the following adoption evidence is still
required:

- a newly preregistered full-coverage conformance run that passes;
- capability checks for all nine custom roles;
- a counterbalanced paired pilot measuring quality, rework, latency, and
  scientific validity;
- an independent remediation review with no open Critical or Important
  findings.

Raw event logs, prompts, session identifiers, and path-bound installation
records are omitted from this repository. The summary above preserves the
release decision without publishing those run artifacts.
