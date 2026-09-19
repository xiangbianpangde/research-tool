"""Contract and integrity tests for documentation restructuring and AGENTS.md elevation.

Verifies:
1. Root AGENTS.md elevation with 9-stage closed loop, dual-mode identity, and directory structure.
2. Root CLAUDE.md archived to docs/archive/CLAUDE.md with deprecation/redirect notice.
3. docs/archive/ hierarchy (README.md, deliverables/, project_management/, plans_and_notes/).
4. Security: local worklogs remain strictly ignored by gitignore.
5. Conventions and templates align with AGENTS.md rather than deprecated root CLAUDE.md.
"""

from __future__ import annotations

from pathlib import Path
import subprocess

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_root_agents_md_contract() -> None:
    """R1: Root AGENTS.md exists and contains native 9-stage pipeline and identity specs."""
    agents_path = REPO_ROOT / "AGENTS.md"
    assert agents_path.is_file(), "Root AGENTS.md must exist"

    content = agents_path.read_text(encoding="utf-8")
    assert len(content) > 1000, "AGENTS.md must be populated"

    # Must specify native 9-stage loop
    for stage in [
        "collect",
        "clean",
        "extract",
        "knowledge",
        "inspect",
        "targeted",
        "merge",
        "qgate",
        "report",
    ]:
        assert stage in content, f"AGENTS.md must mention 9-stage phase: {stage}"

    # Must specify dual-mode identity engine (Rust + Python fallback)
    assert "rt-identity" in content or "rt_identity" in content, "AGENTS.md must mention identity engine"
    assert "Python" in content or "fallback" in content, "AGENTS.md must mention Python fallback"


def test_claude_md_archived_contract() -> None:
    """R1: Root CLAUDE.md is archived to docs/archive/CLAUDE.md with redirect header."""
    root_claude = REPO_ROOT / "CLAUDE.md"
    assert not root_claude.exists(), "Root CLAUDE.md must not exist in repository root"

    archived_claude = REPO_ROOT / "docs" / "archive" / "CLAUDE.md"
    assert archived_claude.is_file(), "docs/archive/CLAUDE.md must exist"

    header_text = archived_claude.read_text(encoding="utf-8")[:600]
    assert "AGENTS.md" in header_text, "Archived CLAUDE.md header must point to root AGENTS.md"


def test_docs_archive_structure_contract() -> None:
    """R2: docs/archive/ contains README.md and required archive categories."""
    archive_dir = REPO_ROOT / "docs" / "archive"
    assert archive_dir.is_dir(), "docs/archive/ directory must exist"

    archive_readme = archive_dir / "README.md"
    assert archive_readme.is_file(), "docs/archive/README.md must exist"
    readme_content = archive_readme.read_text(encoding="utf-8")
    assert "deliverables" in readme_content.lower(), "Archive README must document deliverables"
    assert "project_management" in readme_content.lower(), "Archive README must document project_management"
    assert "plans_and_notes" in readme_content.lower(), "Archive README must document plans_and_notes"

    # Subdirectories
    assert (archive_dir / "deliverables").is_dir(), "docs/archive/deliverables/ must exist"
    assert (archive_dir / "project_management").is_dir(), "docs/archive/project_management/ must exist"
    assert (archive_dir / "plans_and_notes").is_dir(), "docs/archive/plans_and_notes/ must exist"


def test_worklog_security_gitignore_contract() -> None:
    """R2: Any local worklogs under docs/archive/ are strictly ignored by git."""
    sample_worklog = REPO_ROOT / "docs" / "archive" / "project_management" / "worklog" / "audit.md"
    res = subprocess.run(
        ["git", "check-ignore", "-v", str(sample_worklog)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"Worklog path must be ignored by git: {res.stderr}"
    assert "worklog" in res.stdout, "Git ignore rule must match worklog pattern"


def test_conventions_and_templates_reference_agents_md() -> None:
    """R3: Active conventions and templates reference AGENTS.md without broken CLAUDE.md links."""
    conventions_nav = REPO_ROOT / "docs" / "conventions" / "AGENTS-规范导航.md"
    assert conventions_nav.is_file(), "docs/conventions/AGENTS-规范导航.md must exist"

    templates_nav = REPO_ROOT / "docs" / "templates" / "AGENTS模板.md"
    assert templates_nav.is_file(), "docs/templates/AGENTS模板.md must exist"

    # Check active conventions do not have broken [CLAUDE.md](...) links
    for md in (REPO_ROOT / "docs" / "conventions").rglob("*.md"):
        content = md.read_text(encoding="utf-8")
        assert "[CLAUDE.md](../CLAUDE.md)" not in content, f"Found unmigrated CLAUDE link in {md}"
