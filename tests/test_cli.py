import json
from pathlib import Path

import pytest

from pbip_doc.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "Mini" / "Mini.pbip"


def test_build_writes_markdown_and_json(tmp_path: Path) -> None:
    md, js = tmp_path / "docs" / "MODEL.md", tmp_path / "model.json"
    assert main(["build", str(FIXTURE), "-o", str(md), "--json", str(js)]) == 0
    text = md.read_text(encoding="utf-8")
    assert text.startswith("# Mini — semantic model")
    for heading in (
        "## Diagram",
        "## Tables",
        "## Measures",
        "## Calculation groups",
        "## Relationships",
        "## Row-level security",
        "## Report",
        "## Quality checks",
    ):
        assert heading in text
    assert "```mermaid" in text and '<-- "*:1 · Store ID" -->' in text
    assert "#### Customer's Share" in text and "**not used**" in text
    assert "[Missing Measure]" in text
    assert "[Contents]" not in text and "(#quality-checks)" in text
    data = json.loads(js.read_text(encoding="utf-8"))
    assert data["model"]["name"] == "Mini"
    assert data["lineage"]["[Total Amount]"]["visuals"] == ["Main › Amount by month"]
    assert any(f["code"] == "PBD001" for f in data["findings"])


def test_build_to_stdout_without_report(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["build", str(FIXTURE), "--no-report"]) == 0
    out = capsys.readouterr().out
    assert "## Report" not in out and "PBD008" not in out


@pytest.mark.parametrize(
    ("args", "code"),
    [
        (["--fail-on", "error"], 1),
        (["--fail-on", "never"], 0),
        (["--ignore", "PBD001", "--fail-on", "error"], 0),
        (["--ignore", "PBD001", "--fail-on", "warning"], 1),
        (["--ignore", "PBD001,PBD002,PBD003,PBD005,PBD006", "--fail-on", "warning"], 0),
    ],
)
def test_check_exit_codes(args: list[str], code: int, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", str(FIXTURE), *args]) == code
    assert "Mini: 4 measures" in capsys.readouterr().out


def test_rules_listing(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["rules"]) == 0
    assert "PBD001" in capsys.readouterr().out


def test_bad_path_returns_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", str(tmp_path)]) == 2
    assert "no *.SemanticModel" in capsys.readouterr().err
