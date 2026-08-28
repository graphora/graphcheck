from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_repository_uses_only_the_published_graphcheck_action():
    assert not (ROOT / ".github" / "actions" / "graphcheck-action").exists()
    workflow = (ROOT / ".github" / "workflows" / "graphcheck.yml").read_text(encoding="utf-8")
    assert "uses: graphora/graphcheck-action@v1" in workflow
    assert "uses: ./.github/actions/graphcheck-action" not in workflow


def test_graphcheck_workflow_stages_the_example_project_before_the_action():
    workflow = (ROOT / ".github" / "workflows" / "graphcheck.yml").read_text(encoding="utf-8")
    stage = "cp -R examples/fraud-ring/graphcheck.yml examples/fraud-ring/checks ."
    action = "uses: graphora/graphcheck-action@v1"
    assert workflow.index(stage) < workflow.index(action)
    assert (ROOT / "examples" / "fraud-ring" / "graphcheck.yml").is_file()
    assert (ROOT / "examples" / "fraud-ring" / "checks" / "smoke.yml").is_file()
