"""P1 contract tests for immutable Research → unverified Wiki staging packages."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from research_tool.infrastructure.export.wiki_stage import (
    PackageStageError,
    build_stage_package,
    plan_stage_package,
)
from research_tool.infrastructure.export.wiki_publisher import publish
from research_tool.presentation.cli import app


def fixture(root: Path, *, secret: str = "") -> Path:
    source = root / "topic"
    (source / "raw").mkdir(parents=True)
    (source / "tree").mkdir()
    (source / "raw" / "01-source.md").write_text(
        "<!-- source: https://example.test/article -->\n"
        "<!-- fetched: 2026-07-20T10:00:00Z -->\n\n"
        f"source body {secret}", encoding="utf-8"
    )
    (source / "tree" / "00-主表.md").write_text("# 主表\n", encoding="utf-8")
    (source / "report.md").write_text("# Report\n", encoding="utf-8")
    return source


def test_plan_is_read_only_and_every_source_is_traceable(tmp_path: Path) -> None:
    source = fixture(tmp_path)
    destination = tmp_path / "packages"
    plan = plan_stage_package(source, destination)
    assert not destination.exists()
    assert plan["verification"] == "unverified"
    assert plan["target"] == "isolated_research_draft"
    assert plan["files"] == 3
    assert len(plan["sources"]) == 1
    record = plan["sources"][0]
    assert record["url"] == "https://example.test/article"
    assert record["fetchedAt"] == "2026-07-20T10:00:00Z"
    assert len(record["sha256"]) == 64
    assert record["archiveRef"].startswith("research-archive://sha256/")


def test_build_is_content_addressed_immutable_and_idempotent(tmp_path: Path) -> None:
    source = fixture(tmp_path)
    destination = tmp_path / "packages"
    first = build_stage_package(source, destination)
    second = build_stage_package(source, destination)
    assert first.package_path == second.package_path
    assert second.already_exists is True
    manifest = json.loads((first.package_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["packageId"] == first.package_id
    assert manifest["packageHash"] == first.package_hash
    assert manifest["classification"]["isolationRequired"] is True
    assert manifest["promotion"] == "forbidden"
    assert (first.package_path / "report.md").is_file()
    source_manifest = (first.package_path / "source-manifest.md").read_text(encoding="utf-8")
    assert "verification: unverified" in source_manifest
    assert "research-archive://sha256/" in source_manifest


def test_source_change_creates_new_package_without_deleting_old_one(tmp_path: Path) -> None:
    source = fixture(tmp_path)
    destination = tmp_path / "packages"
    first = build_stage_package(source, destination)
    (source / "report.md").write_text("# Changed\n", encoding="utf-8")
    second = build_stage_package(source, destination)
    assert first.package_path != second.package_path
    assert first.package_path.is_dir()
    assert second.package_path.is_dir()


def test_idempotent_retry_revalidates_archives_and_intake_documents(tmp_path: Path) -> None:
    source = fixture(tmp_path)
    destination = tmp_path / "packages"
    built = build_stage_package(source, destination)
    manifest = json.loads((built.package_path / "manifest.json").read_text(encoding="utf-8"))
    archive = built.package_path / manifest["artifacts"][0]["archivePath"]
    archive.chmod(0o644)
    archive.write_bytes(b"tampered")
    with pytest.raises(PackageStageError, match="integrity"):
        build_stage_package(source, destination)


def test_intake_report_contains_classification_and_traceable_sources(tmp_path: Path) -> None:
    source = fixture(tmp_path)
    built = build_stage_package(source, tmp_path / "packages")
    report = (built.package_path / "report.md").read_text(encoding="utf-8")
    assert "privacy: unknown" in report
    assert "https://example.test/article" in report
    assert "research-archive://sha256/" in report


@pytest.mark.parametrize("secret", [
    "-----BEGIN PRIVATE KEY-----",
    "api_key=abcdefghijklmnopqrstuvwxyz123456",
    "Bearer abcdefghijklmnopqrstuvwxyz123456",
])
def test_secret_like_content_is_rejected_with_zero_writes(tmp_path: Path, secret: str) -> None:
    source = fixture(tmp_path, secret=secret)
    destination = tmp_path / "packages"
    with pytest.raises(PackageStageError, match="secret"):
        build_stage_package(source, destination)
    assert not destination.exists()


def test_symlinks_are_rejected(tmp_path: Path) -> None:
    source = fixture(tmp_path)
    (source / "linked.md").symlink_to(source / "report.md")
    with pytest.raises(PackageStageError, match="symlink"):
        plan_stage_package(source, tmp_path / "packages")


def test_legacy_publish_wiki_has_zero_writes_for_active_vault(tmp_path: Path) -> None:
    source = fixture(tmp_path)
    active_vault = tmp_path / "xbpd_obsidian"
    (active_vault / ".obsidian").mkdir(parents=True)
    with pytest.raises(ValueError, match="active Vault"):
        publish(source.name, active_vault, research_output=tmp_path)
    assert not (active_vault / "05-wiki").exists()


def test_wiki_stage_cli_defaults_to_read_only_plan_and_build_is_explicit(tmp_path: Path) -> None:
    source = fixture(tmp_path)
    destination = tmp_path / "packages"
    runner = CliRunner()
    preview = runner.invoke(app, ["wiki-stage", str(source), "--package-output", str(destination)])
    assert preview.exit_code == 0, preview.output
    assert json.loads(preview.output)["writes"] == []
    assert not destination.exists()
    built = runner.invoke(app, [
        "wiki-stage", str(source), "--package-output", str(destination), "--build",
    ])
    assert built.exit_code == 0, built.output
    payload = json.loads(built.output)
    assert payload["verification"] == "unverified"
    assert Path(payload["packagePath"]).is_dir()
