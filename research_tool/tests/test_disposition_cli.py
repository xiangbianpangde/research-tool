"""Use the actual CLI JSON contract; views must not create a registry implicitly."""

import json
from typer.testing import CliRunner
from research_tool.presentation.cli import app


def test_issue_create_view_and_no_implicit_write(tmp_path):
    runner = CliRunner()
    target = tmp_path / "issues"
    absent = runner.invoke(app, ["issues", "view", "--registry", str(target)])
    assert absent.exit_code != 0 and not target.exists()
    request = tmp_path / "create.json"
    request.write_text(json.dumps({"registry_id": "cli-fixture", "roots": {"data": str(tmp_path)}}))
    created = runner.invoke(
        app, ["issues", "create", "--registry", str(target), "--request", str(request)]
    )
    assert created.exit_code == 0, created.stdout
    before = {str(p.relative_to(target)): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    viewed = runner.invoke(app, ["issues", "view", "--registry", str(target)])
    assert viewed.exit_code == 0, viewed.stdout
    assert json.loads(viewed.stdout)["counts"]["original_observations"] == 0
    assert before == {
        str(p.relative_to(target)): p.read_bytes() for p in target.rglob("*") if p.is_file()
    }
