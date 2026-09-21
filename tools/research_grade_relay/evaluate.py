from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ROUTE_SCHEMA = Path(__file__).resolve().parent / "route-decision.schema.json"
MODES = ("microtest", "capability-smoke", "paired-pilot")
USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)
DECISION_FIELDS = {
    "classification",
    "dimensions",
    "triggers",
    "roles",
    "gate",
    "rationale",
}


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    completed_turns: int = 0

    @property
    def noncached_input_tokens(self) -> int:
        return self.input_tokens - self.cached_input_tokens

    def to_dict(self) -> dict[str, int]:
        payload = asdict(self)
        payload["noncached_input_tokens"] = self.noncached_input_tokens
        return payload


def _token_count(payload: dict[str, object], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"invalid {field} in turn.completed usage")
    return value


def parse_usage(lines: Iterable[str]) -> Usage:
    totals = {field: 0 for field in USAGE_FIELDS}
    completed_turns = 0
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at line {line_number}") from exc
        if not isinstance(event, dict) or event.get("type") != "turn.completed":
            continue
        usage = event.get("usage")
        if not isinstance(usage, dict):
            raise ValueError("turn.completed event is missing usage")
        turn = {field: _token_count(usage, field) for field in USAGE_FIELDS}
        if turn["cached_input_tokens"] > turn["input_tokens"]:
            raise ValueError("cached input tokens exceed input tokens in one turn")
        for field in USAGE_FIELDS:
            totals[field] += turn[field]
        completed_turns += 1
    if totals["cached_input_tokens"] > totals["input_tokens"]:
        raise ValueError("cached input tokens exceed total input tokens")
    return Usage(**totals, completed_turns=completed_turns)


def _decision_shape_errors(decision: object) -> list[str]:
    if not isinstance(decision, dict):
        return ["structured decision must be an object"]
    errors: list[str] = []
    missing = DECISION_FIELDS - set(decision)
    extra = set(decision) - DECISION_FIELDS
    errors.extend(f"missing structured field: {field}" for field in sorted(missing))
    if extra:
        errors.append(f"unexpected structured fields: {sorted(extra)!r}")
    for field in ("classification", "gate", "rationale"):
        if field in decision and not isinstance(decision[field], str):
            errors.append(f"{field} must be a string")
    dimensions = decision.get("dimensions")
    if not isinstance(dimensions, dict):
        errors.append("dimensions must be an object")
    elif set(dimensions) != set("ABCDEFG"):
        errors.append("dimensions must contain exactly A-G")
    elif any(
        isinstance(value, bool) or value not in {0, 1, 2, None}
        for value in dimensions.values()
    ):
        errors.append("dimension values must be 0, 1, 2, or null")
    for field in ("triggers", "roles"):
        value = decision.get(field)
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            errors.append(f"{field} must be an array of strings")
    return errors


def _batch_shape_errors(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return ["structured decision batch must be an object"]
    if set(payload) != {"decisions"}:
        return ["structured decision batch must contain only decisions"]
    decisions = payload.get("decisions")
    if not isinstance(decisions, list):
        return ["decisions must be an array"]
    errors: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(decisions):
        if not isinstance(item, dict):
            errors.append(f"decisions[{index}] must be an object")
            continue
        case_id = item.get("id")
        if not isinstance(case_id, str):
            errors.append(f"decisions[{index}].id must be a string")
        elif case_id in seen:
            errors.append(f"duplicate decision id: {case_id}")
        else:
            seen.add(case_id)
        route = {key: value for key, value in item.items() if key != "id"}
        errors.extend(
            f"decisions[{index}]: {error}"
            for error in _decision_shape_errors(route)
        )
    return errors


def _structured_output_errors(payload: object) -> list[str]:
    if isinstance(payload, dict) and "decisions" in payload:
        return _batch_shape_errors(payload)
    return _decision_shape_errors(payload)


def score_decision(case: dict[str, object], decision: dict[str, object]) -> list[str]:
    errors = _decision_shape_errors(decision)
    expected = case.get("expected")
    if not isinstance(expected, dict):
        return ["case expected route is missing"]

    expected_roles = expected.get("roles")
    if decision.get("roles") != expected_roles:
        errors.append(
            f"roles mismatch: expected {expected_roles!r}, got {decision.get('roles')!r}"
        )
    expected_gate = expected.get("gate")
    if decision.get("gate") != expected_gate:
        errors.append(
            f"gate mismatch: expected {expected_gate!r}, got {decision.get('gate')!r}"
        )
    if decision.get("dimensions") != case.get("dimensions"):
        errors.append("dimensions mismatch")

    actual_triggers = decision.get("triggers")
    expected_triggers = case.get("triggers")
    if (
        not isinstance(actual_triggers, list)
        or not all(isinstance(item, str) for item in actual_triggers)
        or not isinstance(expected_triggers, list)
        or not all(isinstance(item, str) for item in expected_triggers)
    ):
        errors.append("triggers must be arrays")
    elif sorted(actual_triggers) != sorted(expected_triggers):
        errors.append(
            f"triggers mismatch: expected {expected_triggers!r}, got {actual_triggers!r}"
        )
    return errors


def score_decision_batch(
    cases: Iterable[dict[str, object]], payload: dict[str, object]
) -> list[str]:
    errors = _batch_shape_errors(payload)
    decisions = payload.get("decisions")
    if not isinstance(decisions, list):
        return errors
    expected_order = [str(case.get("id")) for case in cases]
    observed_order = [
        str(item.get("id"))
        for item in decisions
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    if observed_order != expected_order:
        errors.append(
            f"decision order mismatch: expected {expected_order!r}, got {observed_order!r}"
        )
    case_map = {str(case.get("id")): case for case in cases}
    decision_map = {
        str(item.get("id")): item
        for item in decisions
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    missing = sorted(set(case_map) - set(decision_map))
    extra = sorted(set(decision_map) - set(case_map))
    if missing:
        errors.append(f"missing decision ids: {missing!r}")
    if extra:
        errors.append(f"unexpected decision ids: {extra!r}")
    for case_id in sorted(set(case_map) & set(decision_map)):
        decision = {
            key: value
            for key, value in decision_map[case_id].items()
            if key != "id"
        }
        errors.extend(
            f"{case_id}: {error}"
            for error in score_decision(case_map[case_id], decision)
        )
    return errors


def _is_git_root(workdir: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(workdir), "rev-parse", "--show-toplevel"],
            shell=False,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    try:
        return Path(result.stdout.strip()).resolve() == workdir.resolve()
    except OSError:
        return False


def _decode_timeout_stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def _workspace_snapshot(workdir: Path, excluded: Path | None = None) -> dict[str, object]:
    excluded_relative: str | None = None
    if excluded is not None:
        try:
            excluded_relative = excluded.resolve().relative_to(workdir.resolve()).as_posix()
        except ValueError:
            pass
    try:
        root_probe = subprocess.run(
            ["git", "-C", str(workdir), "rev-parse", "--show-toplevel"],
            shell=False,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        root_probe = None
    if root_probe is not None and root_probe.returncode == 0:
        pathspec = ["--", "."]
        if excluded_relative:
            pathspec.append(f":(exclude){excluded_relative}")
        commands = {
            "head": ["git", "-C", str(workdir), "rev-parse", "HEAD"],
            "status": [
                "git", "-C", str(workdir), "status", "--porcelain=v1", "-z",
                "--untracked-files=all", *pathspec,
            ],
            "diff": ["git", "-C", str(workdir), "diff", "--binary", "HEAD", *pathspec],
        }
        result: dict[str, object] = {"kind": "git"}
        for label, argv in commands.items():
            try:
                process = subprocess.run(
                    argv,
                    shell=False,
                    capture_output=True,
                    check=False,
                    timeout=30,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return {"kind": "unavailable", "reason": type(exc).__name__}
            if process.returncode != 0:
                return {"kind": "unavailable", "reason": f"git-{label}-failed"}
            result[f"{label}_sha256"] = hashlib.sha256(process.stdout).hexdigest()
        return result

    records: list[bytes] = []
    for path in sorted(workdir.rglob("*"), key=lambda item: item.as_posix()):
        if excluded is not None and (
            path == excluded or excluded in path.parents
        ):
            continue
        relative = path.relative_to(workdir).as_posix().encode("utf-8")
        if path.is_symlink():
            records.append(b"L\0" + relative + b"\0" + os.readlink(path).encode("utf-8"))
        elif path.is_dir():
            records.append(b"D\0" + relative)
        elif path.is_file():
            records.append(b"F\0" + relative + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    digest = hashlib.sha256(b"\n".join(records)).hexdigest()
    return {"kind": "tree", "tree_sha256": digest}


def run_codex(
    *,
    mode: str,
    prompt: str,
    workdir: Path,
    output_dir: Path,
    model: str,
    effort: str,
    sandbox: str,
    schema_path: Path = ROUTE_SCHEMA,
    ignore_user_config: bool = False,
    timeout: float = 1_800,
    codex_bin: str = "codex",
) -> dict[str, object]:
    if mode not in MODES:
        raise ValueError(f"unsupported evaluator mode: {mode}")
    workdir = workdir.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    schema_path = schema_path.expanduser().resolve()
    if not workdir.is_dir():
        raise ValueError(f"workdir is not a directory: {workdir}")
    if ignore_user_config:
        search_roots = (workdir, *workdir.parents)
        forbidden = [
            directory / name
            for directory in search_roots
            for name in ("AGENTS.md", ".codex", ".agents")
        ]
        present = [str(path) for path in forbidden if path.exists()]
        if present:
            raise ValueError(
                "no-guidance workdir contains project guidance layers: "
                + ", ".join(sorted(present))
            )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        output_dir.mkdir()
    except FileExistsError as exc:
        raise ValueError(f"evidence output directory is not fresh: {output_dir}") from exc
    raw_path = output_dir / "raw.jsonl"
    stderr_path = output_dir / "stderr.txt"
    last_message_path = output_dir / "last-message.json"
    summary_path = output_dir / "summary.json"
    argv = [
        codex_bin,
        "exec",
        "--ephemeral",
        "--json",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(last_message_path),
        "--model",
        model,
        "--config",
        f'model_reasoning_effort="{effort}"',
        "--sandbox",
        sandbox,
        "-C",
        str(workdir),
    ]
    if not _is_git_root(workdir):
        argv.append("--skip-git-repo-check")
    if ignore_user_config:
        argv.append("--ignore-user-config")
    argv.append(prompt)

    prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    sanitized_argv = list(argv)
    sanitized_argv[-1] = f"sha256:{prompt_sha256}"
    started_at = datetime.now(timezone.utc).isoformat()
    workspace_before = _workspace_snapshot(workdir, output_dir)
    start = time.monotonic()
    try:
        process = subprocess.run(
            argv,
            shell=False,
            cwd=workdir,
            timeout=timeout,
            capture_output=True,
            text=True,
            check=False,
        )
        stdout = process.stdout
        stderr = process.stderr
        returncode = process.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        stdout = _decode_timeout_stream(exc.stdout)
        stderr = _decode_timeout_stream(exc.stderr) + "\nCodex evaluator timeout\n"
        returncode = 124
        timed_out = True
    except OSError as exc:
        stdout = ""
        stderr = f"{type(exc).__name__}: {exc}\n"
        returncode = 127
        timed_out = False
    wall_seconds = time.monotonic() - start
    workspace_after = _workspace_snapshot(workdir, output_dir)

    raw_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    usage_error: str | None = None
    try:
        usage = parse_usage(stdout.splitlines())
    except ValueError as exc:
        usage = Usage()
        usage_error = str(exc)
    if usage_error is None and usage.completed_turns == 0:
        usage_error = "missing turn.completed usage"
    decision: object | None = None
    decision_error: str | None = None
    if last_message_path.exists():
        try:
            decision = json.loads(last_message_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            decision_error = f"invalid structured final message: {exc.msg}"
    elif returncode == 0:
        decision_error = "missing structured final message"
    decision_errors = _structured_output_errors(decision) if decision is not None else []
    success = (
        returncode == 0
        and not timed_out
        and usage_error is None
        and usage.completed_turns > 0
        and decision_error is None
        and not decision_errors
    )

    summary: dict[str, object] = {
        "mode": mode,
        "started_at": started_at,
        "wall_seconds": wall_seconds,
        "timed_out": timed_out,
        "returncode": returncode,
        "success": success,
        "argv": sanitized_argv,
        "prompt_sha256": prompt_sha256,
        "usage": usage.to_dict(),
        "usage_error": usage_error,
        "decision": decision,
        "decision_error": decision_error,
        "decision_errors": decision_errors,
        "workspace_state": {
            "before": workspace_before,
            "after": workspace_after,
            "changed": workspace_before != workspace_after,
        },
        "raw_jsonl": raw_path.name,
        "stderr": stderr_path.name,
        "last_message": last_message_path.name if last_message_path.exists() else None,
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def _evidence_manifest(root: Path) -> list[dict[str, object]]:
    artifacts: list[dict[str, object]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file() or path.is_symlink():
            continue
        payload = path.read_bytes()
        artifacts.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return artifacts


def aggregate_microtest(
    *,
    evidence_root: Path,
    cases: list[dict[str, object]],
    output_path: Path,
    registration: str,
) -> dict[str, object]:
    evidence_root = evidence_root.expanduser().resolve()
    output_path = output_path.expanduser().resolve()
    if not evidence_root.is_dir():
        raise ValueError(f"microtest evidence root is not a directory: {evidence_root}")
    condition_results: dict[str, object] = {}
    workspace_observations: list[object] = []
    for condition in ("control", "relay"):
        condition_root = evidence_root / condition
        run_dirs = sorted(
            (path for path in condition_root.iterdir() if path.is_dir()),
            key=lambda path: (0, int(path.name)) if path.name.isdigit() else (1, path.name),
        )
        if not run_dirs:
            raise ValueError(f"missing {condition} microtest runs")
        totals = {
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "noncached_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_output_tokens": 0,
        }
        noncached: list[int] = []
        per_run: list[dict[str, object]] = []
        exact_runs = 0
        evidence_valid = 0
        exit_zero = 0
        for run_dir in run_dirs:
            summary_path = run_dir / "summary.json"
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid microtest summary: {summary_path}") from exc
            if not isinstance(summary, dict):
                raise ValueError(f"invalid microtest summary: {summary_path}")
            usage = summary.get("usage")
            if not isinstance(usage, dict):
                raise ValueError(f"missing usage in microtest summary: {summary_path}")
            for field in totals:
                value = usage.get(field)
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError(f"invalid {field} in microtest summary: {summary_path}")
                totals[field] += value
            noncached.append(int(usage["noncached_input_tokens"]))
            score_errors = score_decision_batch(cases, summary.get("decision", {}))
            exact = not score_errors
            exact_runs += int(exact)
            exit_zero += int(summary.get("returncode") == 0)
            evidence_valid += int(summary.get("success") is True)
            workspace_state = summary.get("workspace_state", "not_recorded")
            workspace_observations.append(workspace_state)
            per_run.append(
                {
                    "run": run_dir.name,
                    "summary": summary_path.relative_to(evidence_root).as_posix(),
                    "summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
                    "exact_conformance": exact,
                    "score_errors": score_errors,
                    "workspace_state": workspace_state,
                }
            )
        condition_results[condition] = {
            "codex_process_exit_zero": exit_zero,
            "evaluator_evidence_valid": evidence_valid,
            "exact_conformance_runs": exact_runs,
            "total_runs": len(run_dirs),
            "token_totals": totals,
            "mean_noncached_input_tokens": statistics.mean(noncached),
            "median_noncached_input_tokens": statistics.median(noncached),
            "runs": per_run,
        }

    recorded_states = [
        value for value in workspace_observations if isinstance(value, dict)
    ]
    if len(recorded_states) != len(workspace_observations):
        workspace_edit_evidence = "not_recorded_for_v1"
    elif all(value.get("changed") is False for value in recorded_states):
        workspace_edit_evidence = "recorded_unchanged"
    else:
        workspace_edit_evidence = "change_detected"
    control = condition_results["control"]
    relay = condition_results["relay"]
    assert isinstance(control, dict) and isinstance(relay, dict)
    control_defect = control["exact_conformance_runs"] < control["total_runs"]
    relay_pass = relay["exact_conformance_runs"] == relay["total_runs"]
    aggregate: dict[str, object] = {
        "status": "passed" if control_defect and relay_pass else "failed_closed",
        "registration": registration,
        "case_ids": [str(case.get("id")) for case in cases],
        "raw_evidence_root": os.path.relpath(evidence_root, output_path.parent),
        "evidence_manifest": _evidence_manifest(evidence_root),
        "conditions": condition_results,
        "workspace_edit_evidence": workspace_edit_evidence,
        "gate": {
            "control_defect_required": True,
            "control_defect_observed": control_defect,
            "relay_required_exact_runs": relay["total_runs"],
            "relay_observed_exact_runs": relay["exact_conformance_runs"],
            "passed": control_defect and relay_pass,
        },
        "verdict": (
            "Eligible for later gates."
            if control_defect and relay_pass
            else "Stop adoption and roll back; token totals cannot override failed routing conformance."
        ),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(aggregate, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return aggregate


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", required=True)
    parser.add_argument(
        "--sandbox",
        choices=("read-only", "workspace-write", "danger-full-access"),
        required=True,
    )
    parser.add_argument("--schema", type=Path, default=ROUTE_SCHEMA)
    parser.add_argument("--ignore-user-config", action="store_true")
    parser.add_argument("--timeout", type=float, default=1_800)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run relay evaluation evidence")
    subparsers = parser.add_subparsers(dest="mode", required=True)
    for mode in MODES:
        _add_run_arguments(subparsers.add_parser(mode))
    aggregate_parser = subparsers.add_parser("aggregate-microtest")
    aggregate_parser.add_argument("--evidence-root", type=Path, required=True)
    aggregate_parser.add_argument("--cases", type=Path, required=True)
    aggregate_parser.add_argument("--case-id", action="append", required=True)
    aggregate_parser.add_argument("--output", type=Path, required=True)
    aggregate_parser.add_argument("--registration", required=True)
    args = parser.parse_args(argv)

    if args.mode == "aggregate-microtest":
        try:
            payload = json.loads(args.cases.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise ValueError("case oracle must be a list")
            case_map = {
                str(case.get("id")): case
                for case in payload
                if isinstance(case, dict)
            }
            cases = [case_map[case_id] for case_id in args.case_id]
            aggregate = aggregate_microtest(
                evidence_root=args.evidence_root,
                cases=cases,
                output_path=args.output,
                registration=args.registration,
            )
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
            print(json.dumps({"status": "failed", "error": str(exc)}, sort_keys=True))
            return 1
        print(
            json.dumps(
                {
                    "status": aggregate["status"],
                    "output": str(args.output.expanduser().resolve()),
                },
                sort_keys=True,
            )
        )
        return 0

    prompt = args.prompt_file.read_text(encoding="utf-8")
    summary = run_codex(
        mode=args.mode,
        prompt=prompt,
        workdir=args.workdir,
        output_dir=args.output_dir,
        model=args.model,
        effort=args.effort,
        sandbox=args.sandbox,
        schema_path=args.schema,
        ignore_user_config=args.ignore_user_config,
        timeout=args.timeout,
    )
    print(
        json.dumps(
            {
                "status": "ok" if summary["success"] else "failed",
                "summary": str(args.output_dir.expanduser().resolve() / "summary.json"),
                "returncode": summary["returncode"],
            },
            sort_keys=True,
        )
    )
    return 0 if summary["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
