"""Locate the semantic model and report folders of a Power BI project."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


class ProjectError(ValueError):
    pass


@dataclass(frozen=True)
class Project:
    model_definition: Path
    report_definition: Path | None


def _definition(folder: Path) -> Path:
    definition = folder / "definition"
    if not definition.is_dir():
        raise ProjectError(
            f"{folder} has no 'definition' folder. Save the project in Power BI Desktop with the "
            "TMDL (semantic model) and PBIR (report) formats enabled."
        )
    return definition


def _model_of_report(report: Path) -> Path | None:
    pointer = report / "definition.pbir"
    if not pointer.exists():
        return None
    data = json.loads(pointer.read_text(encoding="utf-8-sig"))
    by_path = data.get("datasetReference", {}).get("byPath", {}).get("path")
    return (report / by_path).resolve() if by_path else None


def locate(path: Path) -> Project:
    """Accepts a .pbip file, a project folder, a *.SemanticModel folder or a *.Report folder."""
    path = path.resolve()
    if path.is_file() and path.suffix == ".pbip":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        for artifact in data.get("artifacts", []):
            if "report" in artifact:
                return locate(path.parent / artifact["report"]["path"])
        path = path.parent

    if path.name.endswith(".Report"):
        model = _model_of_report(path)
        if model is None:
            raise ProjectError(f"{path} does not reference a local semantic model (byPath)")
        report_def = path / "definition"
        return Project(_definition(model), report_def if report_def.is_dir() else None)

    if path.name.endswith(".SemanticModel"):
        siblings = [r for r in path.parent.glob("*.Report") if _model_of_report(r) == path]
        report = siblings[0] / "definition" if siblings else None
        return Project(_definition(path), report if report and report.is_dir() else None)

    if path.name == "definition" and path.parent.name.endswith(".SemanticModel"):
        return locate(path.parent)

    models = sorted(path.glob("*.SemanticModel"))
    if len(models) == 1:
        return locate(models[0])
    if not models:
        raise ProjectError(f"no *.SemanticModel folder found in {path}")
    names = ", ".join(m.name for m in models)
    raise ProjectError(f"several semantic models in {path} ({names}); pass one of them")
