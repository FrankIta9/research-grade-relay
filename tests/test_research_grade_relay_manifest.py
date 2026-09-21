import copy

from tools.research_grade_relay.manifest import (
    REQUIRED_CASE_IDS,
    REQUIRED_COVERAGE,
    ROLE_SPECS,
    load_cases,
    validate_case_matrix,
)


def test_case_matrix_covers_every_required_route():
    cases = load_cases()
    assert {case["id"] for case in cases} == REQUIRED_CASE_IDS
    assert validate_case_matrix(cases) == []


def test_case_matrix_rejects_an_unknown_role():
    cases = copy.deepcopy(load_cases())
    cases[0]["expected"]["roles"] = ["invented-role"]
    errors = validate_case_matrix(cases)
    assert any("invented-role" in error for error in errors)


def test_case_matrix_rejects_duplicate_ids():
    cases = copy.deepcopy(load_cases())
    cases.append(copy.deepcopy(cases[0]))
    errors = validate_case_matrix(cases)
    assert any("duplicate case id" in error for error in errors)


def test_case_matrix_covers_boundaries_fallback_and_review_rechecks():
    cases = load_cases()
    observed = {
        tag
        for case in cases
        for tag in case["coverage"]
    }
    assert observed >= REQUIRED_COVERAGE


def test_every_case_records_exact_model_effort_and_sandbox():
    for case in load_cases():
        expected = case["expected"]
        assert [assignment["role"] for assignment in expected["assignments"]] == expected["roles"]
        for assignment in expected["assignments"]:
            assert {
                "model": assignment["model"],
                "effort": assignment["effort"],
                "sandbox": assignment["sandbox"],
            } == ROLE_SPECS[assignment["role"]]


def test_role_specs_pin_all_nine_agents():
    assert set(ROLE_SPECS) == {
        "sol-designer",
        "sol-planner",
        "sol-reviewer",
        "sol-critical-reviewer",
        "terra-explorer",
        "terra-builder",
        "terra-debugger",
        "terra-task-reviewer",
        "luna-verifier",
    }
    assert ROLE_SPECS["sol-planner"]["effort"] == "xhigh"
    assert ROLE_SPECS["sol-reviewer"]["effort"] == "high"
    assert ROLE_SPECS["luna-verifier"]["model"] == "gpt-5.6-luna"
