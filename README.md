# Research-Grade Relay

Research prototype for routing Codex work to project-scoped agents by task
uncertainty, implementation risk, and scientific risk. The package contains a
routing skill, role-specific agent templates, a conformance matrix, an
installer with rollback support, and an evidence evaluator.

## Current status

The relay is **not approved for adoption**. Pilot v1 was rolled back after exact
route conformance failed in all five evaluated cases. The v2 candidate includes
implementation and evidence-handling remediation, but it has not completed the
new validation required for adoption. See
[`docs/PILOT_STATUS.md`](docs/PILOT_STATUS.md) and
[`tools/research_grade_relay/deployment-status.json`](tools/research_grade_relay/deployment-status.json).

The installer enforces this status and fails closed while adoption remains
ineligible. The templates are research artifacts; do not install them into a
working Codex workspace based on this snapshot.

## Routing design

The proposed coordinator is Terra Medium. It scores requirement clarity,
change breadth, dependency complexity, regression risk, scientific risk,
architectural judgment, and verifiability. Hard triggers for scientific,
architectural, security, or release-critical work override numeric thresholds.
The relay fails closed when a required review role is unavailable. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the role map and gates.

## Repository contents

- `tools/research_grade_relay/` — installer, rollback and workspace validation,
  conformance contracts, decision schemas, agent templates, and evidence tools.
- `tests/` — focused tests for the installer, templates, contract, and evaluator.
- `docs/` — portable design notes, adoption status, and source provenance.

Raw Codex event logs, session identifiers, machine-specific installation
manifests, and the original path-bound pilot records are not included in this
repository snapshot.

## Requirements

- Python 3.10 or newer for the runtime tools.
- `pytest` to run the test suite.
- Codex CLI is needed only for the evaluation runner; it is not needed to
  inspect the schemas or validate an existing workspace.

Install the test dependency and run the suite from the repository root:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

To validate a workspace against the packaged templates:

```bash
python -m tools.research_grade_relay.install validate --workspace /path/to/workspace
```

The live install command is blocked by the current adoption gate. This project
does not change global Codex defaults.

## Model identifiers

The checked-in templates pin the `gpt-5.6-sol`, `gpt-5.6-terra`, and
`gpt-5.6-luna` identifiers used by the original pilot. Update and revalidate
them for any other Codex model catalog or runtime.

## License

No license is assigned in this snapshot.
