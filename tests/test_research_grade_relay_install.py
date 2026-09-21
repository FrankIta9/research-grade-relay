import json
from pathlib import Path

import pytest

import tools.research_grade_relay.install as relay_install

from tools.research_grade_relay.install import (
    BEGIN_MARKER,
    InstallConflict,
    install as guarded_install,
    main,
    rollback,
    validate_workspace,
)

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "tools/research_grade_relay/templates"


def install(workspace, source, global_config):
    return guarded_install(
        workspace, source, global_config, enforce_adoption_gate=False
    )


def test_install_preserves_existing_agents_text_and_is_idempotent(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("keep me\n")
    manifest = install(workspace, TEMPLATES, tmp_path / "global.toml")
    assert (workspace / "AGENTS.md").read_text().startswith("keep me\n")
    assert (workspace / "AGENTS.md").read_text().count(BEGIN_MARKER) == 1
    install(workspace, TEMPLATES, tmp_path / "global.toml")
    assert (workspace / "AGENTS.md").read_text().count(BEGIN_MARKER) == 1
    assert validate_workspace(workspace, TEMPLATES) == []
    assert manifest.exists()


def test_install_refuses_unmanaged_project_config(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / ".codex").mkdir(parents=True)
    (workspace / ".codex/config.toml").write_text('model = "other"\n')
    with pytest.raises(InstallConflict, match="config.toml"):
        install(workspace, TEMPLATES, tmp_path / "global.toml")


def test_rollback_restores_original_agents_and_removes_created_paths(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("original\n")
    manifest = install(workspace, TEMPLATES, tmp_path / "global.toml")
    rollback(workspace, manifest, tmp_path / "global.toml")
    assert (workspace / "AGENTS.md").read_text() == "original\n"
    assert not (workspace / ".codex/config.toml").exists()
    assert not (workspace / ".agents/skills/research-grade-relay").exists()


def test_rollback_refuses_post_install_user_changes(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = install(workspace, TEMPLATES, tmp_path / "global.toml")
    (workspace / "AGENTS.md").write_text(
        (workspace / "AGENTS.md").read_text() + "user change\n"
    )
    with pytest.raises(InstallConflict, match="changed after installation"):
        rollback(workspace, manifest, tmp_path / "global.toml")


def test_install_refuses_symlink_target_and_preserves_global_config(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / ".codex").mkdir(parents=True)
    global_config = tmp_path / "global.toml"
    global_config.write_text('model = "gpt-5.6-sol"\n')
    (workspace / ".codex/config.toml").symlink_to(global_config)
    before = global_config.read_bytes()

    with pytest.raises(InstallConflict, match="symlink"):
        install(workspace, TEMPLATES, global_config)

    assert global_config.read_bytes() == before


def test_validate_reports_changed_managed_agent(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    install(workspace, TEMPLATES, tmp_path / "global.toml")
    agent = workspace / ".codex/agents/terra-builder.toml"
    agent.write_text(agent.read_text() + "# changed\n")

    errors = validate_workspace(workspace, TEMPLATES)

    assert any("terra-builder.toml" in error for error in errors)


def test_install_refuses_reversed_agents_markers(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text(
        "<!-- END RESEARCH-GRADE-RELAY -->\n"
        "<!-- BEGIN RESEARCH-GRADE-RELAY -->\n"
    )

    with pytest.raises(InstallConflict, match="malformed relay markers"):
        install(workspace, TEMPLATES, tmp_path / "global.toml")


def test_rollback_authentication_rejects_forged_managed_entry(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = install(workspace, TEMPLATES, tmp_path / "global.toml")
    user_file = workspace / "thesis.txt"
    user_file.write_text("publication draft\n")
    payload = json.loads(manifest.read_text())
    payload["managed"].append(
        {
            "path": "thesis.txt",
            "before_exists": False,
            "before_kind": None,
            "before_sha256": None,
            "after_kind": "file",
            "after_sha256": relay_install._file_sha256(user_file),
            "backup_relative": None,
        }
    )
    manifest.write_text(json.dumps(payload))

    with pytest.raises(InstallConflict, match="authentication"):
        rollback(workspace, manifest, tmp_path / "global.toml")

    assert user_file.read_text() == "publication draft\n"


def test_install_preflights_non_directory_parent_before_mutation(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    agents = workspace / "AGENTS.md"
    agents.write_text("original\n")
    (workspace / ".codex").write_text("not a directory\n")

    with pytest.raises(InstallConflict, match="parent is not a directory"):
        install(workspace, TEMPLATES, tmp_path / "global.toml")

    assert agents.read_text() == "original\n"
    assert not (workspace / ".research-grade-relay-backups").exists()


def test_rollback_refuses_backup_path_outside_manifest_directory(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("original\n")
    manifest = install(workspace, TEMPLATES, tmp_path / "global.toml")
    outside = tmp_path / "outside.txt"
    outside.write_text("original\n")
    payload = json.loads(manifest.read_text())
    payload["managed"][0]["backup_relative"] = "../../../outside.txt"
    manifest.write_text(json.dumps(payload))

    with pytest.raises(InstallConflict, match="authentication"):
        rollback(workspace, manifest, tmp_path / "global.toml")

    assert BEGIN_MARKER in (workspace / "AGENTS.md").read_text()


def test_rollback_detects_retargeted_global_config_symlink(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_a = tmp_path / "global-a.toml"
    config_b = tmp_path / "global-b.toml"
    config_a.write_text('model = "a"\n')
    config_b.write_text('model = "b"\n')
    global_link = tmp_path / "global.toml"
    global_link.symlink_to(config_a)
    manifest = install(workspace, TEMPLATES, global_link)
    global_link.unlink()
    global_link.symlink_to(config_b)

    with pytest.raises(InstallConflict, match="global Codex config changed"):
        rollback(workspace, manifest, global_link)


def test_rollback_is_atomic_when_a_replace_fails(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("original\n")
    global_config = tmp_path / "global.toml"
    manifest = install(workspace, TEMPLATES, global_config)
    installed_agents = (workspace / "AGENTS.md").read_bytes()
    real_replace = relay_install._atomic_replace
    calls = 0

    def fail_once(source, target):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("simulated replace failure")
        return real_replace(source, target)

    monkeypatch.setattr(relay_install, "_atomic_replace", fail_once)

    with pytest.raises(InstallConflict, match="transaction"):
        rollback(workspace, manifest, global_config)

    assert (workspace / "AGENTS.md").read_bytes() == installed_agents
    assert validate_workspace(workspace, TEMPLATES) == []


def test_repeated_install_preserves_original_rollback(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("original\n")
    global_config = tmp_path / "global.toml"

    first = install(workspace, TEMPLATES, global_config)
    second = install(workspace, TEMPLATES, global_config)
    rollback(workspace, second, global_config)
    assert validate_workspace(workspace, TEMPLATES) == []
    rollback(workspace, first, global_config)

    assert (workspace / "AGENTS.md").read_text() == "original\n"
    assert not (workspace / ".codex/config.toml").exists()


def test_default_install_and_cli_fail_closed_while_v2_is_unvalidated(
    tmp_path, capsys
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(InstallConflict, match="not adoption eligible"):
        guarded_install(workspace, TEMPLATES, tmp_path / "global.toml")

    exit_code = main(
        [
            "install",
            "--workspace",
            str(workspace),
            "--global-config",
            str(tmp_path / "global.toml"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 3
    assert payload["status"] == "conflict"
    assert "not adoption eligible" in payload["validation_errors"][0]
