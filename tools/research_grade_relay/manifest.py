from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
CASE_PATH = PACKAGE_ROOT / "conformance_cases.json"

ROLE_SPECS = {
    "sol-designer": {
        "model": "gpt-5.6-sol",
        "effort": "high",
        "sandbox": "workspace-write",
    },
    "sol-planner": {
        "model": "gpt-5.6-sol",
        "effort": "xhigh",
        "sandbox": "workspace-write",
    },
    "sol-reviewer": {
        "model": "gpt-5.6-sol",
        "effort": "high",
        "sandbox": "read-only",
    },
    "sol-critical-reviewer": {
        "model": "gpt-5.6-sol",
        "effort": "xhigh",
        "sandbox": "read-only",
    },
    "terra-explorer": {
        "model": "gpt-5.6-terra",
        "effort": "medium",
        "sandbox": "read-only",
    },
    "terra-builder": {
        "model": "gpt-5.6-terra",
        "effort": "high",
        "sandbox": "workspace-write",
    },
    "terra-debugger": {
        "model": "gpt-5.6-terra",
        "effort": "xhigh",
        "sandbox": "workspace-write",
    },
    "terra-task-reviewer": {
        "model": "gpt-5.6-terra",
        "effort": "high",
        "sandbox": "read-only",
    },
    "luna-verifier": {
        "model": "gpt-5.6-luna",
        "effort": "medium",
        "sandbox": "workspace-write",
    },
}

COORDINATOR_SPEC = {
    "model": "gpt-5.6-terra",
    "effort": "medium",
    "sandbox": "workspace-write",
}

ROUTE_CONTRACT = {
    "direct-mechanical": {"roles": [], "gate": "focused check", "write_intent": "bounded"},
    "bounded-reproduction": {"roles": ["luna-verifier"], "gate": "pre/post Git delta", "write_intent": "verification-only"},
    "open-exploration": {"roles": ["terra-explorer"], "gate": "rescore", "write_intent": "none"},
    "normal-creative": {"roles": ["sol-designer"], "gate": "design/spec approval", "write_intent": "design-artifact-only"},
    "critical-creative": {"roles": ["sol-planner", "sol-critical-reviewer"], "gate": "critical review", "write_intent": "plan-artifact-only"},
    "critical-decision": {"roles": ["sol-planner"], "gate": "critical design approval", "write_intent": "plan-artifact-only"},
    "conceptual-design": {"roles": ["sol-designer"], "gate": "approved plan", "write_intent": "design-artifact-only"},
    "integration-implementation": {"roles": ["terra-builder"], "gate": "focused integration checks", "write_intent": "implementation"},
    "composite-normal": {"roles": ["terra-builder", "terra-task-reviewer", "luna-verifier", "sol-reviewer"], "gate": "integration review", "write_intent": "one-writer-then-readers"},
    "composite-critical": {"roles": ["sol-planner", "terra-builder", "terra-task-reviewer", "luna-verifier", "sol-critical-reviewer"], "gate": "critical integration review", "write_intent": "one-writer-then-readers"},
    "factual-unknown": {"roles": ["terra-explorer"], "gate": "mandatory rescore", "write_intent": "none"},
    "execution-escalation": {"roles": ["terra-builder", "terra-debugger"], "gate": "one X-High attempt", "write_intent": "sequential-writers"},
    "ordinary-fallback": {"roles": ["terra-builder"], "gate": "fallback disclosed", "write_intent": "implementation"},
    "mandatory-review-unavailable": {"roles": [], "gate": "blocked: mandatory review unavailable", "write_intent": "none"},
    "important-finding-remediation": {"roles": ["sol-reviewer"], "gate": "fixed or evidence-rejected, then recheck", "write_intent": "none"},
    "critical-finding-remediation": {"roles": ["sol-critical-reviewer"], "gate": "fixed, then recheck", "write_intent": "none"},
    "reviewer-recheck": {"roles": ["sol-reviewer"], "gate": "remediation recheck passed", "write_intent": "none"},
}


def _expected_for_route(route: str) -> dict[str, object]:
    contract = ROUTE_CONTRACT[route]
    roles = list(contract["roles"])
    return {
        "route": route,
        "roles": roles,
        "assignments": [
            {"role": role, **ROLE_SPECS[role]}
            for role in roles
        ],
        "coordinator": dict(COORDINATOR_SPEC),
        "write_intent": contract["write_intent"],
        "gate": contract["gate"],
    }


REQUIRED_CASE_IDS = {
    "mechanical-direct",
    "threshold-3-direct",
    "threshold-4-verifier",
    "threshold-7-explorer",
    "threshold-8-execution",
    "threshold-10-execution",
    "threshold-11-conceptual",
    "threshold-14-critical",
    "bounded-reproduction",
    "open-exploration",
    "creative-normal",
    "creative-scientific",
    "conceptual-high-score",
    "composite-normal",
    "composite-critical",
    "unknown-factual",
    "unknown-conceptual",
    "execution-escalation",
    "approved-plan-direct",
    "ordinary-fallback",
    "mandatory-review-fallback",
    "critical-e2",
    "critical-f2",
    "critical-leakage",
    "critical-estimand",
    "critical-sota",
    "critical-security",
    "critical-conflict",
    "decision-escalation-normal",
    "decision-escalation-critical",
    "important-finding-bypass",
    "critical-finding-bypass",
    "remediation-recheck",
}

REQUIRED_COVERAGE = {
    "threshold-3",
    "threshold-4",
    "threshold-7",
    "threshold-8",
    "threshold-10",
    "threshold-11",
    "threshold-14",
    "bounded-reproduction",
    "creative",
    "composite",
    "unknown-dimension",
    "fallback-ordinary",
    "fallback-review-fail-closed",
    "execution-escalation",
    "decision-escalation",
    "critical-e2",
    "critical-f2",
    "critical-leakage",
    "critical-estimand",
    "critical-sota",
    "critical-security",
    "critical-conflict",
    "important-finding-bypass",
    "critical-finding-bypass",
    "remediation-recheck",
}


def load_cases(path: Path | None = None) -> list[dict[str, object]]:
    payload = json.loads((path or CASE_PATH).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("conformance case payload must be a list")
    cases = deepcopy(payload)
    for case in cases:
        if isinstance(case, dict) and isinstance(case.get("route"), str):
            route = str(case["route"])
            if route in ROUTE_CONTRACT:
                case["expected"] = _expected_for_route(route)
    return cases


def validate_case_matrix(cases: list[dict[str, object]]) -> list[str]:
    errors: list[str] = []
    identifiers = [str(case.get("id")) for case in cases]
    duplicates = sorted(
        identifier for identifier, count in Counter(identifiers).items() if count > 1
    )
    errors.extend(f"duplicate case id: {identifier}" for identifier in duplicates)
    ids = set(identifiers)
    if ids != REQUIRED_CASE_IDS:
        errors.append(f"case ids mismatch: {sorted(ids ^ REQUIRED_CASE_IDS)}")
    observed_coverage: set[str] = set()
    for case in cases:
        case_id = str(case.get("id"))
        coverage = case.get("coverage")
        if not isinstance(coverage, list) or not all(
            isinstance(tag, str) for tag in coverage
        ):
            errors.append(f"{case_id}: coverage must be an array of strings")
        else:
            if len(coverage) != len(set(coverage)):
                errors.append(f"{case_id}: duplicate coverage tag")
            observed_coverage.update(coverage)
        dimensions = case.get("dimensions")
        if not isinstance(dimensions, dict) or set(dimensions) != set("ABCDEFG"):
            errors.append(f"{case_id}: dimensions must be A-G")
        elif any(value not in {0, 1, 2, None} for value in dimensions.values()):
            errors.append(f"{case_id}: invalid dimension value")
        route = case.get("route")
        if not isinstance(route, str) or route not in ROUTE_CONTRACT:
            errors.append(f"{case_id}: unknown route {route}")
            continue
        expected = case.get("expected")
        if not isinstance(expected, dict):
            errors.append(f"{case_id}: expected must be an object")
            continue
        for role in expected.get("roles", []):
            if role not in ROLE_SPECS:
                errors.append(f"{case_id}: unknown role {role}")
        canonical = _expected_for_route(route)
        if expected != canonical:
            errors.append(f"{case_id}: expected route metadata differs from canonical contract")
        triggers = case.get("triggers")
        if not isinstance(triggers, list) or not all(
            isinstance(trigger, str) for trigger in triggers
        ):
            errors.append(f"{case_id}: triggers must be an array of strings")
    missing_coverage = sorted(REQUIRED_COVERAGE - observed_coverage)
    if missing_coverage:
        errors.append(f"missing conformance coverage: {missing_coverage}")
    return errors
