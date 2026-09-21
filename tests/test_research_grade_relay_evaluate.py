import json
import os

import pytest

from tools.research_grade_relay.evaluate import (
    aggregate_microtest,
    main,
    parse_usage,
    run_codex,
    score_decision,
    score_decision_batch,
)
from tools.research_grade_relay.manifest import load_cases


def test_parse_usage_computes_noncached_tokens():
    lines = [
        '{"type":"thread.started","thread_id":"t"}',
        '{"type":"turn.completed","usage":{"input_tokens":24763,'
        '"cached_input_tokens":24448,"output_tokens":122,'
        '"reasoning_output_tokens":7}}',
    ]
    usage = parse_usage(lines)
    assert usage.input_tokens == 24763
    assert usage.cached_input_tokens == 24448
    assert usage.noncached_input_tokens == 315
    assert usage.output_tokens == 122
    assert usage.reasoning_output_tokens == 7


def test_parse_usage_sums_only_completed_turns():
    lines = [
        '{"type":"item.completed","usage":{"input_tokens":999}}',
        '{"type":"turn.completed","usage":{"input_tokens":10,'
        '"cached_input_tokens":3,"output_tokens":2,'
        '"reasoning_output_tokens":1}}',
        '{"type":"turn.completed","usage":{"input_tokens":20,'
        '"cached_input_tokens":5,"output_tokens":4,'
        '"reasoning_output_tokens":2}}',
    ]
    usage = parse_usage(lines)
    assert usage.input_tokens == 30
    assert usage.cached_input_tokens == 8
    assert usage.noncached_input_tokens == 22
    assert usage.output_tokens == 6
    assert usage.reasoning_output_tokens == 3


def test_parse_usage_rejects_impossible_individual_turn():
    lines = [
        '{"type":"turn.completed","usage":{"input_tokens":1,'
        '"cached_input_tokens":5,"output_tokens":0,'
        '"reasoning_output_tokens":0}}',
        '{"type":"turn.completed","usage":{"input_tokens":10,'
        '"cached_input_tokens":0,"output_tokens":0,'
        '"reasoning_output_tokens":0}}',
    ]

    with pytest.raises(ValueError, match="cached input tokens exceed"):
        parse_usage(lines)


def test_score_decision_requires_exact_ordered_roles():
    case = next(c for c in load_cases() if c["id"] == "composite-normal")
    decision = {
        "dimensions": case["dimensions"],
        "triggers": case["triggers"],
        "roles": ["terra-builder", "sol-reviewer"],
        "gate": case["expected"]["gate"],
    }
    assert any("roles" in error for error in score_decision(case, decision))


def test_score_decision_accepts_exact_case():
    case = next(c for c in load_cases() if c["id"] == "creative-scientific")
    decision = {
        "classification": "critical scientific creative work",
        "dimensions": case["dimensions"],
        "triggers": list(reversed(case["triggers"])),
        "roles": case["expected"]["roles"],
        "gate": case["expected"]["gate"],
        "rationale": "Critical scientific and SOTA triggers apply.",
    }
    assert score_decision(case, decision) == []


def test_score_decision_rejects_incomplete_structured_shape():
    case = next(c for c in load_cases() if c["id"] == "mechanical-direct")
    decision = {
        "dimensions": case["dimensions"],
        "triggers": case["triggers"],
        "roles": case["expected"]["roles"],
        "gate": case["expected"]["gate"],
    }

    errors = score_decision(case, decision)

    assert any("classification" in error for error in errors)
    assert any("rationale" in error for error in errors)


def test_score_decision_batch_scores_three_frozen_vignettes():
    wanted = {"mechanical-direct", "creative-scientific", "composite-normal"}
    cases = [case for case in load_cases() if case["id"] in wanted]
    decisions = []
    for case in cases:
        decisions.append(
            {
                "id": case["id"],
                "classification": case["summary"],
                "dimensions": case["dimensions"],
                "triggers": case["triggers"],
                "roles": case["expected"]["roles"],
                "gate": case["expected"]["gate"],
                "rationale": "frozen expected route",
            }
        )

    assert score_decision_batch(cases, {"decisions": decisions}) == []


def test_score_decision_batch_rejects_duplicate_and_missing_case_ids():
    cases = [case for case in load_cases() if case["id"] in {
        "mechanical-direct", "creative-scientific"
    }]
    case = cases[0]
    decision = {
        "id": case["id"],
        "classification": case["summary"],
        "dimensions": case["dimensions"],
        "triggers": case["triggers"],
        "roles": case["expected"]["roles"],
        "gate": case["expected"]["gate"],
        "rationale": "duplicate",
    }

    errors = score_decision_batch(cases, {"decisions": [decision, decision]})

    assert any("duplicate" in error for error in errors)
    assert any("missing" in error for error in errors)


def test_score_decision_batch_rejects_reordered_vignettes():
    cases = load_cases()[:2]
    decisions = []
    for case in reversed(cases):
        decisions.append(
            {
                "id": case["id"],
                "classification": case["summary"],
                "dimensions": case["dimensions"],
                "triggers": case["triggers"],
                "roles": case["expected"]["roles"],
                "gate": case["expected"]["gate"],
                "rationale": "deliberately reordered",
            }
        )

    errors = score_decision_batch(cases, {"decisions": decisions})

    assert any("order mismatch" in error for error in errors)


def test_run_codex_writes_sanitized_raw_and_summary_evidence(tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    fake_codex = tmp_path / "fake-codex"
    fake_codex.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        "out = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])\n"
        "out.write_text(json.dumps({'classification': 'mechanical', "
        "'dimensions': {k: 0 for k in 'ABCDEFG'}, 'triggers': [], "
        "'roles': [], 'gate': 'focused check', 'rationale': 'exact task'}))\n"
        "print(json.dumps({'type': 'turn.completed', 'usage': {"
        "'input_tokens': 100, 'cached_input_tokens': 60, "
        "'output_tokens': 10, 'reasoning_output_tokens': 2}}))\n"
    )
    fake_codex.chmod(0o755)
    output_dir = tmp_path / "evidence"
    prompt = "sensitive synthetic prompt"

    summary = run_codex(
        mode="microtest",
        prompt=prompt,
        workdir=workdir,
        output_dir=output_dir,
        model="gpt-test",
        effort="medium",
        sandbox="read-only",
        schema_path=tmp_path / "schema.json",
        ignore_user_config=True,
        codex_bin=str(fake_codex),
    )

    assert summary["returncode"] == 0
    assert summary["success"] is True
    assert summary["usage"]["noncached_input_tokens"] == 40
    assert summary["decision"]["roles"] == []
    assert summary["decision"]["gate"] == "focused check"
    assert summary["workspace_state"]["changed"] is False
    assert summary["workspace_state"]["before"] == summary["workspace_state"]["after"]
    assert prompt not in json.dumps(summary)
    assert prompt not in (output_dir / "raw.jsonl").read_text()
    assert (output_dir / "summary.json").exists()
    assert "--ignore-user-config" in summary["argv"]
    assert "--skip-git-repo-check" in summary["argv"]


def test_no_guidance_rejects_inherited_project_layers(tmp_path):
    parent = tmp_path / "parent"
    workdir = parent / "child"
    workdir.mkdir(parents=True)
    (parent / "AGENTS.md").write_text("inherited guidance\n")

    with pytest.raises(ValueError, match="guidance layers"):
        run_codex(
            mode="microtest",
            prompt="route this",
            workdir=workdir,
            output_dir=tmp_path / "evidence",
            model="gpt-test",
            effort="medium",
            sandbox="read-only",
            schema_path=tmp_path / "schema.json",
            ignore_user_config=True,
            codex_bin="/usr/bin/true",
        )


def test_missing_structured_output_fails_closed_and_keeps_evidence(tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    output_dir = tmp_path / "evidence"

    summary = run_codex(
        mode="microtest",
        prompt="route this",
        workdir=workdir,
        output_dir=output_dir,
        model="gpt-test",
        effort="medium",
        sandbox="read-only",
        schema_path=tmp_path / "schema.json",
        ignore_user_config=True,
        codex_bin="/usr/bin/true",
    )

    assert summary["returncode"] == 0
    assert summary["success"] is False
    assert summary["decision_error"] == "missing structured final message"
    assert (output_dir / "summary.json").exists()


def test_launch_failure_writes_fail_closed_summary(tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    output_dir = tmp_path / "evidence"

    summary = run_codex(
        mode="capability-smoke",
        prompt="route this",
        workdir=workdir,
        output_dir=output_dir,
        model="gpt-test",
        effort="medium",
        sandbox="read-only",
        schema_path=tmp_path / "schema.json",
        codex_bin=str(tmp_path / "missing-codex"),
    )

    assert summary["returncode"] == 127
    assert summary["success"] is False
    assert (output_dir / "raw.jsonl").exists()
    assert (output_dir / "stderr.txt").exists()
    assert (output_dir / "summary.json").exists()


def test_evidence_output_directory_must_be_exclusively_created(tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    output_dir = tmp_path / "evidence"
    output_dir.mkdir()

    with pytest.raises(ValueError, match="not fresh"):
        run_codex(
            mode="paired-pilot",
            prompt="route this",
            workdir=workdir,
            output_dir=output_dir,
            model="gpt-test",
            effort="medium",
            sandbox="read-only",
            schema_path=tmp_path / "schema.json",
            codex_bin="/usr/bin/true",
        )


def test_malformed_jsonl_keeps_summary_and_fails_closed(tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    fake_codex = tmp_path / "fake-codex"
    fake_codex.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        "out = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])\n"
        "out.write_text(json.dumps({'classification': 'mechanical', "
        "'dimensions': {k: 0 for k in 'ABCDEFG'}, 'triggers': [], "
        "'roles': [], 'gate': 'focused check', 'rationale': 'exact task'}))\n"
        "print('{truncated')\n"
    )
    fake_codex.chmod(0o755)
    output_dir = tmp_path / "evidence"

    summary = run_codex(
        mode="microtest",
        prompt="route this",
        workdir=workdir,
        output_dir=output_dir,
        model="gpt-test",
        effort="medium",
        sandbox="read-only",
        schema_path=tmp_path / "schema.json",
        codex_bin=str(fake_codex),
    )

    assert summary["success"] is False
    assert "invalid JSONL" in summary["usage_error"]
    assert (output_dir / "summary.json").exists()


def test_cli_reports_failure_when_structured_output_is_missing(
    tmp_path, monkeypatch, capsys
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "codex").symlink_to("/usr/bin/true")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    workdir = tmp_path / "work"
    workdir.mkdir()
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("route this\n")

    exit_code = main(
        [
            "microtest",
            "--prompt-file",
            str(prompt_file),
            "--workdir",
            str(workdir),
            "--output-dir",
            str(tmp_path / "evidence"),
            "--model",
            "gpt-test",
            "--effort",
            "medium",
            "--sandbox",
            "read-only",
            "--ignore-user-config",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert output["status"] == "failed"


def test_missing_git_probe_still_writes_fail_closed_evidence(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    workdir = tmp_path / "work"
    workdir.mkdir()
    output_dir = tmp_path / "evidence"

    summary = run_codex(
        mode="microtest",
        prompt="route this",
        workdir=workdir,
        output_dir=output_dir,
        model="gpt-test",
        effort="medium",
        sandbox="read-only",
        schema_path=tmp_path / "schema.json",
        codex_bin="/usr/bin/true",
    )

    assert summary["success"] is False
    assert "--skip-git-repo-check" in summary["argv"]
    assert (output_dir / "summary.json").exists()


def test_aggregate_microtest_recomputes_tokens_ordered_scores_and_hashes(tmp_path):
    cases = load_cases()[:2]
    evidence_root = tmp_path / "raw"
    for condition in ("control", "relay"):
        run = evidence_root / condition / "1"
        run.mkdir(parents=True)
        decisions = []
        for case in cases:
            decisions.append(
                {
                    "id": case["id"],
                    "classification": case["summary"],
                    "dimensions": case["dimensions"],
                    "triggers": case["triggers"],
                    "roles": case["expected"]["roles"],
                    "gate": case["expected"]["gate"],
                    "rationale": "exact route",
                }
            )
        summary = {
            "returncode": 0,
            "success": True,
            "usage": {
                "input_tokens": 100,
                "cached_input_tokens": 40,
                "noncached_input_tokens": 60,
                "output_tokens": 10,
                "reasoning_output_tokens": 2,
                "completed_turns": 1,
            },
            "decision": {"decisions": decisions},
            "workspace_state": {"changed": False},
        }
        (run / "summary.json").write_text(json.dumps(summary))
        (run / "raw.jsonl").write_text('{"type":"turn.completed"}\n')

    output = tmp_path / "aggregate.json"
    aggregate = aggregate_microtest(
        evidence_root=evidence_root,
        cases=cases,
        output_path=output,
        registration="registration.json",
    )

    assert aggregate["conditions"]["control"]["exact_conformance_runs"] == 1
    assert aggregate["conditions"]["relay"]["token_totals"]["noncached_input_tokens"] == 60
    assert aggregate["workspace_edit_evidence"] == "recorded_unchanged"
    assert all(item["sha256"] for item in aggregate["evidence_manifest"])
    assert output.exists()
