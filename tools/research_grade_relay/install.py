from __future__ import annotations

import argparse
import hmac
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


BEGIN_MARKER = "<!-- BEGIN RESEARCH-GRADE-RELAY -->"
END_MARKER = "<!-- END RESEARCH-GRADE-RELAY -->"
BACKUP_ROOT = ".research-grade-relay-backups"
TEMPLATES = Path(__file__).resolve().parent / "templates"
DEPLOYMENT_STATUS = Path(__file__).resolve().parent / "deployment-status.json"
DEPLOYMENT_STATUS_KEYS = {
    "version",
    "adoption_eligible",
    "reason",
    "evidence_required",
    "date",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MANAGED_SCHEMA = (
    ("AGENTS.md", "file"),
    (".codex/config.toml", "file"),
    (".agents/skills/research-grade-relay", "directory"),
    (".codex/agents/luna-verifier.toml", "file"),
    (".codex/agents/sol-critical-reviewer.toml", "file"),
    (".codex/agents/sol-designer.toml", "file"),
    (".codex/agents/sol-planner.toml", "file"),
    (".codex/agents/sol-reviewer.toml", "file"),
    (".codex/agents/terra-builder.toml", "file"),
    (".codex/agents/terra-debugger.toml", "file"),
    (".codex/agents/terra-explorer.toml", "file"),
    (".codex/agents/terra-task-reviewer.toml", "file"),
)
MANIFEST_KEYS = {
    "version",
    "workspace",
    "source",
    "global_config",
    "global_config_sha256",
    "created_at",
    "managed",
}
ENTRY_KEYS = {
    "path",
    "before_exists",
    "before_kind",
    "before_sha256",
    "after_kind",
    "after_sha256",
    "backup_relative",
}
RECEIPT_KEYS = {
    "version",
    "manifest",
    "manifest_sha256",
    "workspace",
    "global_config",
    "created_at",
}


class InstallConflict(RuntimeError):
    """Raised when installation or rollback would overwrite unmanaged state."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _tree_sha256(path: Path) -> str:
    records: list[str] = []
    for child in sorted(path.rglob("*"), key=lambda item: item.as_posix()):
        relative = child.relative_to(path).as_posix()
        if child.is_symlink():
            raise InstallConflict(f"symlink is not allowed in managed tree: {child}")
        if child.is_dir():
            records.append(f"D\0{relative}\n")
        elif child.is_file():
            records.append(f"F\0{relative}\0{_file_sha256(child)}\n")
        else:
            raise InstallConflict(f"unsupported managed path type: {child}")
    return _sha256_bytes("".join(records).encode("utf-8"))


def _path_state(path: Path) -> tuple[str, str] | None:
    if path.is_symlink():
        raise InstallConflict(f"managed path is a symlink: {path}")
    if not path.exists():
        return None
    if path.is_file():
        return "file", _file_sha256(path)
    if path.is_dir():
        return "directory", _tree_sha256(path)
    raise InstallConflict(f"unsupported managed path type: {path}")


def _global_sha256(path: Path) -> str | None:
    return _file_sha256(path) if path.exists() else None


def _strict_json_bytes(payload: bytes, label: str) -> dict[str, object]:
    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InstallConflict(f"duplicate key in {label}: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(payload, object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallConflict(f"invalid {label}") from exc
    if not isinstance(value, dict):
        raise InstallConflict(f"invalid {label}")
    return value


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _trusted_receipt_path(
    workspace: Path, manifest_path: Path, global_config: Path
) -> Path:
    workspace_key = _sha256_bytes(str(workspace).encode("utf-8"))
    return (
        global_config.parent
        / ".research-grade-relay-receipts"
        / workspace_key
        / f"{manifest_path.parent.name}.json"
    )


def _write_private_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_replace(source: Path, target: Path) -> None:
    os.replace(source, target)


def _assert_safe_target(workspace: Path, relative: Path) -> None:
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise InstallConflict(f"managed target escapes workspace: {relative}")
    candidate = workspace
    for index, part in enumerate(relative.parts):
        candidate /= part
        if candidate.is_symlink():
            raise InstallConflict(f"managed target contains a symlink: {candidate}")
        if (
            index < len(relative.parts) - 1
            and candidate.exists()
            and not candidate.is_dir()
        ):
            raise InstallConflict(f"managed target parent is not a directory: {candidate}")


def _rollback_backup_path(manifest_path: Path, value: object) -> Path:
    if not isinstance(value, str):
        raise InstallConflict("rollback backup path is missing")
    relative = Path(value)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise InstallConflict(f"rollback backup path escapes backup directory: {value}")
    _assert_safe_target(manifest_path.parent, relative)
    return manifest_path.parent / relative


def _source_targets(source: Path) -> list[tuple[Path, Path, str]]:
    targets: list[tuple[Path, Path, str]] = [
        (Path(".codex/config.toml"), source / ".codex/config.toml", "file"),
        (
            Path(".agents/skills/research-grade-relay"),
            source / ".agents/skills/research-grade-relay",
            "directory",
        ),
    ]
    agent_root = source / ".codex/agents"
    if not agent_root.is_dir():
        raise InstallConflict(f"missing template directory: {agent_root}")
    targets.extend(
        (Path(".codex/agents") / path.name, path, "file")
        for path in sorted(agent_root.glob("*.toml"))
    )
    for _relative, path, expected_kind in targets:
        state = _path_state(path)
        if state is None or state[0] != expected_kind:
            raise InstallConflict(f"missing or invalid template target: {path}")
    return targets


def _agents_after(current: str, block: str) -> str:
    if block.count(BEGIN_MARKER) != 1 or block.count(END_MARKER) != 1:
        raise InstallConflict("AGENTS.block.md has malformed relay markers")
    if block.index(END_MARKER) < block.index(BEGIN_MARKER):
        raise InstallConflict("AGENTS.block.md has malformed relay markers")
    begin_count = current.count(BEGIN_MARKER)
    end_count = current.count(END_MARKER)
    if begin_count == 0 and end_count == 0:
        separator = "\n" if current and not current.endswith("\n") else ""
        return current + separator + block
    if begin_count != 1 or end_count != 1:
        raise InstallConflict("AGENTS.md has malformed relay markers")
    begin = current.index(BEGIN_MARKER)
    end_start = current.index(END_MARKER)
    if end_start < begin:
        raise InstallConflict("AGENTS.md has malformed relay markers")
    end = end_start + len(END_MARKER)
    if current[begin:end] != block.rstrip("\n"):
        raise InstallConflict("AGENTS.md contains a different managed relay block")
    return current


def _copy_backup(path: Path, backup: Path, kind: str) -> None:
    backup.parent.mkdir(parents=True, exist_ok=True)
    if kind == "file":
        shutil.copy2(path, backup)
    else:
        shutil.copytree(path, backup)


def _copy_target(source: Path, target: Path, kind: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return
    if kind == "file":
        shutil.copy2(source, target)
    else:
        shutil.copytree(source, target)


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _manifest_entry(
    relative: Path,
    before: tuple[str, str] | None,
    after_kind: str,
    after_sha256: str,
    backup_relative: str | None,
) -> dict[str, object]:
    return {
        "path": relative.as_posix(),
        "before_exists": before is not None,
        "before_kind": before[0] if before else None,
        "before_sha256": before[1] if before else None,
        "after_kind": after_kind,
        "after_sha256": after_sha256,
        "backup_relative": backup_relative,
    }


def _validate_manifest_schema(
    manifest: dict[str, object],
    *,
    workspace: Path,
    manifest_path: Path,
    global_config: Path,
) -> list[dict[str, object]]:
    if set(manifest) != MANIFEST_KEYS or manifest.get("version") != 1:
        raise InstallConflict("invalid rollback manifest schema")
    if manifest.get("workspace") != str(workspace):
        raise InstallConflict("rollback workspace does not match manifest")
    if manifest.get("global_config") != str(global_config):
        raise InstallConflict("rollback global config does not match manifest")
    if manifest.get("created_at") != manifest_path.parent.name:
        raise InstallConflict("rollback manifest timestamp does not match its path")
    if not isinstance(manifest.get("source"), str):
        raise InstallConflict("invalid rollback source")
    global_hash = manifest.get("global_config_sha256")
    if global_hash is not None and (
        not isinstance(global_hash, str) or not SHA256_RE.fullmatch(global_hash)
    ):
        raise InstallConflict("invalid global config digest")

    entries = manifest.get("managed")
    if not isinstance(entries, list) or len(entries) != len(MANAGED_SCHEMA):
        raise InstallConflict("rollback manifest has an unexpected managed-path set")
    validated: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, (entry, (expected_path, expected_kind)) in enumerate(
        zip(entries, MANAGED_SCHEMA)
    ):
        if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
            raise InstallConflict(f"invalid rollback entry schema at index {index}")
        path = entry.get("path")
        if path != expected_path or path in seen:
            raise InstallConflict("rollback manifest has an unexpected managed-path set")
        seen.add(path)
        if entry.get("after_kind") != expected_kind:
            raise InstallConflict(f"invalid installed kind for managed path: {path}")
        after_sha = entry.get("after_sha256")
        if not isinstance(after_sha, str) or not SHA256_RE.fullmatch(after_sha):
            raise InstallConflict(f"invalid installed digest for managed path: {path}")
        before_exists = entry.get("before_exists")
        if not isinstance(before_exists, bool):
            raise InstallConflict(f"invalid before_exists for managed path: {path}")
        expected_backup = f"payload/{index:02d}" if before_exists else None
        if entry.get("backup_relative") != expected_backup:
            raise InstallConflict(f"invalid backup path for managed path: {path}")
        if before_exists:
            before_kind = entry.get("before_kind")
            before_sha = entry.get("before_sha256")
            if before_kind not in {"file", "directory"}:
                raise InstallConflict(f"invalid original kind for managed path: {path}")
            if not isinstance(before_sha, str) or not SHA256_RE.fullmatch(before_sha):
                raise InstallConflict(f"invalid original digest for managed path: {path}")
        elif entry.get("before_kind") is not None or entry.get("before_sha256") is not None:
            raise InstallConflict(f"unexpected original state for managed path: {path}")
        validated.append(entry)
    return validated


def _authenticated_manifest(
    workspace: Path, manifest_path: Path, global_config: Path
) -> tuple[dict[str, object], list[dict[str, object]]]:
    try:
        relative_manifest = manifest_path.relative_to(workspace)
    except ValueError as exc:
        raise InstallConflict("rollback manifest is outside the workspace") from exc
    if (
        len(relative_manifest.parts) != 3
        or relative_manifest.parts[0] != BACKUP_ROOT
        or relative_manifest.parts[2] != "manifest.json"
    ):
        raise InstallConflict("rollback manifest has an unexpected path")
    _assert_safe_target(workspace, relative_manifest)

    receipt_path = _trusted_receipt_path(workspace, manifest_path, global_config)
    if _is_within(receipt_path, workspace):
        raise InstallConflict("trusted rollback receipt must be outside the workspace")
    try:
        manifest_bytes = manifest_path.read_bytes()
        receipt = _strict_json_bytes(receipt_path.read_bytes(), "rollback receipt")
    except OSError as exc:
        raise InstallConflict("rollback authentication receipt is missing") from exc
    if set(receipt) != RECEIPT_KEYS or receipt.get("version") != 1:
        raise InstallConflict("invalid rollback authentication receipt")
    expected_receipt = {
        "manifest": str(manifest_path),
        "workspace": str(workspace),
        "global_config": str(global_config),
        "created_at": manifest_path.parent.name,
    }
    if any(receipt.get(key) != value for key, value in expected_receipt.items()):
        raise InstallConflict("rollback authentication receipt does not match request")
    actual_digest = _sha256_bytes(manifest_bytes)
    trusted_digest = receipt.get("manifest_sha256")
    if not isinstance(trusted_digest, str) or not hmac.compare_digest(
        actual_digest, trusted_digest
    ):
        raise InstallConflict("rollback manifest authentication failed")
    manifest = _strict_json_bytes(manifest_bytes, "rollback manifest")
    entries = _validate_manifest_schema(
        manifest,
        workspace=workspace,
        manifest_path=manifest_path,
        global_config=global_config,
    )
    return manifest, entries


def _assert_adoption_eligible(status_path: Path = DEPLOYMENT_STATUS) -> None:
    try:
        payload = _strict_json_bytes(status_path.read_bytes(), "deployment status")
    except OSError as exc:
        raise InstallConflict(
            "relay is not adoption eligible: deployment status is missing"
        ) from exc
    if set(payload) != DEPLOYMENT_STATUS_KEYS or payload.get("version") != 1:
        raise InstallConflict(
            "relay is not adoption eligible: invalid deployment status"
        )
    if payload.get("adoption_eligible") is not True:
        reason = payload.get("reason")
        detail = f": {reason}" if isinstance(reason, str) and reason else ""
        raise InstallConflict(f"relay is not adoption eligible{detail}")


def install(
    workspace: Path,
    source: Path,
    global_config: Path,
    *,
    enforce_adoption_gate: bool = True,
) -> Path:
    if enforce_adoption_gate:
        _assert_adoption_eligible()
    workspace = workspace.expanduser().resolve()
    source = source.expanduser().resolve()
    global_config = global_config.expanduser().absolute()
    if not workspace.is_dir():
        raise InstallConflict(f"workspace is not a directory: {workspace}")

    targets = _source_targets(source)
    generated_schema = [("AGENTS.md", "file")]
    generated_schema.extend(
        (relative.as_posix(), kind) for relative, _template, kind in targets
    )
    if tuple(generated_schema) != MANAGED_SCHEMA:
        raise InstallConflict("template target set does not match installer schema")
    block_path = source / "AGENTS.block.md"
    if not block_path.is_file() or block_path.is_symlink():
        raise InstallConflict(f"missing or invalid AGENTS block: {block_path}")
    block = block_path.read_text(encoding="utf-8")
    agents_path = workspace / "AGENTS.md"

    _assert_safe_target(workspace, Path("AGENTS.md"))
    _assert_safe_target(workspace, Path(BACKUP_ROOT))
    backup_root = workspace / BACKUP_ROOT
    if backup_root.exists() and not backup_root.is_dir():
        raise InstallConflict(f"backup parent is not a directory: {backup_root}")
    for relative, _template, _kind in targets:
        _assert_safe_target(workspace, relative)

    if agents_path.exists() and not agents_path.is_file():
        raise InstallConflict("managed AGENTS.md path is not a file")
    current_agents = agents_path.read_text(encoding="utf-8") if agents_path.exists() else ""
    after_agents = _agents_after(current_agents, block)
    planned: list[tuple[Path, Path | None, str, str]] = [
        (
            Path("AGENTS.md"),
            None,
            "file",
            _sha256_bytes(after_agents.encode("utf-8")),
        )
    ]
    for relative, template, kind in targets:
        expected = _path_state(template)
        assert expected is not None
        current = _path_state(workspace / relative)
        if current is not None and current != expected:
            raise InstallConflict(f"managed target differs from template: {relative}")
        planned.append((relative, template, kind, expected[1]))

    global_before = _global_sha256(global_config)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup_dir = workspace / BACKUP_ROOT / timestamp
    if backup_dir.exists():
        raise InstallConflict(f"backup path already exists: {backup_dir}")
    (backup_dir / "payload").mkdir(parents=True)

    entries: list[dict[str, object]] = []
    for index, (relative, _template, kind, after_sha256) in enumerate(planned):
        target = workspace / relative
        before = _path_state(target)
        backup_relative = f"payload/{index:02d}" if before else None
        if before:
            _copy_backup(target, backup_dir / str(backup_relative), before[0])
        entries.append(
            _manifest_entry(relative, before, kind, after_sha256, backup_relative)
        )

    manifest_path = backup_dir / "manifest.json"
    manifest = {
        "version": 1,
        "workspace": str(workspace),
        "source": str(source),
        "global_config": str(global_config),
        "global_config_sha256": global_before,
        "created_at": timestamp,
        "managed": entries,
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    receipt_path = _trusted_receipt_path(workspace, manifest_path, global_config)
    if _is_within(receipt_path, workspace):
        raise InstallConflict("trusted rollback receipt must be outside the workspace")
    _write_private_json(
        receipt_path,
        {
            "version": 1,
            "manifest": str(manifest_path),
            "manifest_sha256": _file_sha256(manifest_path),
            "workspace": str(workspace),
            "global_config": str(global_config),
            "created_at": timestamp,
        },
    )

    agents_path.write_text(after_agents, encoding="utf-8")
    for relative, template, kind, _after_sha256 in planned[1:]:
        assert template is not None
        _copy_target(template, workspace / relative, kind)

    for entry in entries:
        state = _path_state(workspace / str(entry["path"]))
        expected = (str(entry["after_kind"]), str(entry["after_sha256"]))
        if state != expected:
            raise InstallConflict(f"post-install verification failed: {entry['path']}")
    if _global_sha256(global_config) != global_before:
        raise InstallConflict("global Codex config changed during installation")
    return manifest_path


def validate_workspace(workspace: Path, source: Path = TEMPLATES) -> list[str]:
    workspace = workspace.expanduser().resolve()
    source = source.expanduser().resolve()
    errors: list[str] = []
    if not workspace.is_dir():
        return [f"workspace is not a directory: {workspace}"]
    try:
        targets = _source_targets(source)
        block = (source / "AGENTS.block.md").read_text(encoding="utf-8")
    except (InstallConflict, OSError) as exc:
        return [str(exc)]

    agents_path = workspace / "AGENTS.md"
    try:
        _assert_safe_target(workspace, Path("AGENTS.md"))
        if not agents_path.is_file():
            errors.append("missing managed target: AGENTS.md")
        else:
            current = agents_path.read_text(encoding="utf-8")
            if _agents_after(current, block) != current:
                errors.append("AGENTS.md is missing the managed relay block")
    except (InstallConflict, OSError) as exc:
        errors.append(str(exc))

    for relative, template, _kind in targets:
        try:
            _assert_safe_target(workspace, relative)
            actual = _path_state(workspace / relative)
            expected = _path_state(template)
            if actual is None:
                errors.append(f"missing managed target: {relative}")
            elif actual != expected:
                errors.append(f"managed target differs from template: {relative}")
        except (InstallConflict, OSError) as exc:
            errors.append(str(exc))
    return errors


def rollback(workspace: Path, manifest_path: Path, global_config: Path) -> None:
    workspace = workspace.expanduser().resolve()
    manifest_path = manifest_path.expanduser().absolute()
    global_config = global_config.expanduser().absolute()
    manifest, entries = _authenticated_manifest(
        workspace, manifest_path, global_config
    )
    global_expected = manifest.get("global_config_sha256")
    if _global_sha256(global_config) != global_expected:
        raise InstallConflict("global Codex config changed after installation")
    for entry in entries:
        relative = Path(str(entry["path"]))
        _assert_safe_target(workspace, relative)
        current = _path_state(workspace / relative)
        expected = (str(entry["after_kind"]), str(entry["after_sha256"]))
        if current != expected:
            raise InstallConflict(
                f"managed path changed after installation: {relative}"
            )
        if entry.get("before_exists"):
            backup = _rollback_backup_path(
                manifest_path, entry.get("backup_relative")
            )
            backup_state = _path_state(backup)
            before = (str(entry["before_kind"]), str(entry["before_sha256"]))
            if backup_state != before:
                raise InstallConflict(f"rollback backup failed integrity check: {relative}")

    transaction = Path(
        tempfile.mkdtemp(prefix=".rollback-transaction-", dir=manifest_path.parent)
    )
    staged_root = transaction / "staged"
    original_root = transaction / "installed"
    staged_root.mkdir()
    original_root.mkdir()
    try:
        for index, entry in enumerate(entries):
            if entry.get("before_exists"):
                backup = _rollback_backup_path(
                    manifest_path, entry.get("backup_relative")
                )
                staged = staged_root / f"{index:02d}"
                _copy_backup(backup, staged, str(entry["before_kind"]))
                expected = (str(entry["before_kind"]), str(entry["before_sha256"]))
                if _path_state(staged) != expected:
                    raise InstallConflict(
                        f"rollback staging failed integrity check: {entry['path']}"
                    )

        applied: list[tuple[int, dict[str, object]]] = []
        try:
            for index, entry in enumerate(entries):
                target = workspace / str(entry["path"])
                installed = original_root / f"{index:02d}"
                _atomic_replace(target, installed)
                applied.append((index, entry))
                if entry.get("before_exists"):
                    _atomic_replace(staged_root / f"{index:02d}", target)

            for entry in entries:
                target = workspace / str(entry["path"])
                expected = (
                    (str(entry["before_kind"]), str(entry["before_sha256"]))
                    if entry.get("before_exists")
                    else None
                )
                if _path_state(target) != expected:
                    raise InstallConflict(
                        f"rollback post-transaction verification failed: {entry['path']}"
                    )
            if _global_sha256(global_config) != global_expected:
                raise InstallConflict("global Codex config changed during rollback")
        except (InstallConflict, OSError) as exc:
            recovery_errors: list[str] = []
            for index, entry in reversed(applied):
                target = workspace / str(entry["path"])
                installed = original_root / f"{index:02d}"
                try:
                    if target.exists() or target.is_symlink():
                        _remove_path(target)
                    _atomic_replace(installed, target)
                except OSError as recovery_exc:
                    recovery_errors.append(f"{entry['path']}: {recovery_exc}")
            if recovery_errors:
                raise InstallConflict(
                    "rollback transaction and recovery failed: "
                    + "; ".join(recovery_errors)
                ) from exc
            raise InstallConflict(
                "rollback transaction failed; installed state was restored"
            ) from exc
    finally:
        shutil.rmtree(transaction, ignore_errors=True)


def _json_result(
    status: str,
    workspace: Path,
    manifest: Path | None = None,
    receipt: Path | None = None,
    errors: Iterable[str] = (),
) -> None:
    print(
        json.dumps(
            {
                "status": status,
                "workspace": str(workspace.expanduser().resolve()),
                "manifest": str(manifest) if manifest else None,
                "trusted_receipt": str(receipt) if receipt else None,
                "validation_errors": list(errors),
            },
            sort_keys=True,
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the research-grade relay")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install_parser = subparsers.add_parser("install")
    install_parser.add_argument("--workspace", type=Path, required=True)
    install_parser.add_argument("--global-config", type=Path, required=True)
    install_parser.add_argument("--source", type=Path, default=TEMPLATES)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--workspace", type=Path, required=True)
    validate_parser.add_argument("--source", type=Path, default=TEMPLATES)

    rollback_parser = subparsers.add_parser("rollback")
    rollback_parser.add_argument("--workspace", type=Path, required=True)
    rollback_parser.add_argument("--manifest", type=Path, required=True)
    rollback_parser.add_argument("--global-config", type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            manifest = install(args.workspace, args.source, args.global_config)
            receipt = _trusted_receipt_path(
                args.workspace.expanduser().resolve(),
                manifest,
                args.global_config.expanduser().absolute(),
            )
            errors = validate_workspace(args.workspace, args.source)
            status = "ok" if not errors else "invalid"
            _json_result(status, args.workspace, manifest, receipt, errors)
            return 0 if not errors else 2
        if args.command == "validate":
            errors = validate_workspace(args.workspace, args.source)
            status = "ok" if not errors else "invalid"
            _json_result(status, args.workspace, errors=errors)
            return 0 if not errors else 2
        rollback(args.workspace, args.manifest, args.global_config)
        _json_result("rolled_back", args.workspace, args.manifest)
        return 0
    except InstallConflict as exc:
        _json_result("conflict", args.workspace, errors=[str(exc)])
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
