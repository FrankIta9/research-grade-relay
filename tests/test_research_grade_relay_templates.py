from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

from tools.research_grade_relay.manifest import ROLE_SPECS

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "tools/research_grade_relay/templates"


def test_project_config_uses_bounded_terra_default():
    config = tomllib.loads((TEMPLATES / ".codex/config.toml").read_text())
    assert config["model"] == "gpt-5.6-terra"
    assert config["model_reasoning_effort"] == "medium"
    assert config["agents"] == {"max_depth": 1, "max_threads": 4}


def test_all_agent_templates_match_manifest():
    for name, expected in ROLE_SPECS.items():
        payload = tomllib.loads(
            (TEMPLATES / f".codex/agents/{name}.toml").read_text()
        )
        assert payload["name"] == name
        assert payload["model"] == expected["model"]
        assert payload["model_reasoning_effort"] == expected["effort"]
        assert payload["sandbox_mode"] == expected["sandbox"]
        assert "Do not spawn" in payload["developer_instructions"]


def test_skill_is_thin_and_contains_non_bypassable_triggers():
    text = (TEMPLATES / ".agents/skills/research-grade-relay/SKILL.md").read_text()
    assert len(text.split()) < 1_000
    for phrase in (
        "Classify only", "Creative Work Trigger", "Critical Sol Trigger",
        "Composite Implementation Trigger", "NEEDS_USER_DECISION",
        "terra-explorer", "sol-critical-reviewer", "terra-debugger",
        "Luna Medium mechanical reproduction", "ordinary fallback",
        "destructive", "external action", "remediation recheck",
    ):
        assert phrase in text


def test_composite_skill_route_requires_luna_between_task_and_sol_review():
    text = (TEMPLATES / ".agents/skills/research-grade-relay/SKILL.md").read_text()
    task_review = text.index("terra-task-reviewer")
    luna = text.index("Luna Medium mechanical reproduction", task_review)
    sol_review = text.index("sol-reviewer", luna)
    assert task_review < luna < sol_review


def test_skill_metadata_enables_automatic_routing():
    text = (
        TEMPLATES
        / ".agents/skills/research-grade-relay/agents/openai.yaml"
    ).read_text()
    assert 'display_name: "Research Grade Relay"' in text
    assert "allow_implicit_invocation: true" in text
