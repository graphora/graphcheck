from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_repository_uses_only_the_published_graphcheck_action():
    assert not (ROOT / ".github" / "actions" / "graphcheck-action").exists()
    workflow = (ROOT / ".github" / "workflows" / "graphcheck.yml").read_text(encoding="utf-8")
    assert "uses: graphora/graphcheck-action@v1" in workflow
    assert "uses: ./.github/actions/graphcheck-action" not in workflow
