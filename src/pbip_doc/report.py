"""Read a PBIR report (definition/pages/*/visuals/*/visual.json) and the fields each visual uses."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FieldRef:
    table: str
    name: str
    kind: str  # Column | Measure | Hierarchy | HierarchyLevel
    level: str | None = None  # for hierarchy levels


@dataclass
class Visual:
    page: str
    name: str
    visual_type: str
    title: str | None
    fields: list[FieldRef] = field(default_factory=list)


@dataclass
class Page:
    name: str
    display_name: str
    visuals: list[Visual] = field(default_factory=list)
    filters: list[FieldRef] = field(default_factory=list)


@dataclass
class Report:
    name: str
    pages: list[Page] = field(default_factory=list)
    filters: list[FieldRef] = field(default_factory=list)  # report-level filters

    @property
    def visuals(self) -> list[Visual]:
        return [v for p in self.pages for v in p.visuals]

    def all_fields(self) -> Iterator[tuple[str, FieldRef]]:
        """(where, field) for every field used by a visual or a filter."""
        for f in self.filters:
            yield "report filter", f
        for p in self.pages:
            for f in p.filters:
                yield f"{p.display_name} (page filter)", f
            for v in p.visuals:
                for f in v.fields:
                    yield f"{p.display_name} › {v.title or v.name}", f


def _aliases(node: Any) -> dict[str, str]:
    """Legacy queries use `From: [{Name: s, Entity: Sales}]` and `SourceRef: {Source: s}`."""
    found: dict[str, str] = {}
    if isinstance(node, dict):
        if isinstance(node.get("Name"), str) and isinstance(node.get("Entity"), str):
            found[node["Name"]] = node["Entity"]
        for value in node.values():
            found.update(_aliases(value))
    elif isinstance(node, list):
        for value in node:
            found.update(_aliases(value))
    return found


def _entity(expression: Any, aliases: dict[str, str]) -> str | None:
    if not isinstance(expression, dict):
        return None
    source = expression.get("SourceRef")
    if not isinstance(source, dict):
        return None
    if "Entity" in source:
        return str(source["Entity"])
    return aliases.get(str(source.get("Source")))


def extract_fields(node: Any, aliases: dict[str, str] | None = None) -> list[FieldRef]:
    """Find every model field referenced anywhere in a PBIR JSON document."""
    aliases = _aliases(node) if aliases is None else aliases
    found: list[FieldRef] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for kind in ("Column", "Measure"):
                ref = item.get(kind)
                if isinstance(ref, dict) and "Property" in ref:
                    table = _entity(ref.get("Expression"), aliases)
                    if table:
                        found.append(FieldRef(table, str(ref["Property"]), kind))
            level = item.get("HierarchyLevel")
            if isinstance(level, dict):
                hierarchy = level.get("Expression", {}).get("Hierarchy", {})
                table = _entity(hierarchy.get("Expression"), aliases)
                if table and "Hierarchy" in hierarchy:
                    found.append(
                        FieldRef(
                            table, str(hierarchy["Hierarchy"]), "HierarchyLevel", level.get("Level")
                        )
                    )
            for value in item.values():
                walk(value)
        elif isinstance(item, list):
            for value in item:
                walk(value)

    walk(node)
    unique: dict[FieldRef, None] = dict.fromkeys(found)
    return list(unique)


def _title(visual: dict[str, Any]) -> str | None:
    for container in ("visualContainerObjects", "objects"):
        for entry in visual.get(container, {}).get("title", []):
            text = entry.get("properties", {}).get("text", {})
            value = text.get("expr", {}).get("Literal", {}).get("Value")
            if isinstance(value, str):
                return value.strip("'").replace("''", "'")
    return None


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_report(definition: Path) -> Report:
    """`definition` is the `<name>.Report/definition` folder (PBIR format)."""
    report = Report(name=definition.parent.name.removesuffix(".Report"))
    report_json = definition / "report.json"
    if report_json.exists():
        report.filters = extract_fields(_read(report_json).get("filterConfig", {}))

    pages_dir = definition / "pages"
    order: list[str] = []
    if (pages_dir / "pages.json").exists():
        order = list(_read(pages_dir / "pages.json").get("pageOrder", []))
    page_dirs = sorted(
        (p for p in pages_dir.glob("*") if (p / "page.json").exists()),
        key=lambda p: (order.index(p.name) if p.name in order else len(order), p.name),
    )
    for page_dir in page_dirs:
        meta = _read(page_dir / "page.json")
        page = Page(
            name=meta.get("name", page_dir.name),
            display_name=meta.get("displayName", page_dir.name),
            filters=extract_fields(meta.get("filterConfig", {})),
        )
        for visual_file in sorted(page_dir.glob("visuals/*/visual.json")):
            data = _read(visual_file)
            visual = data.get("visual", {})
            page.visuals.append(
                Visual(
                    page=page.display_name,
                    name=data.get("name", visual_file.parent.name),
                    visual_type=visual.get("visualType", "unknown"),
                    title=_title(visual),
                    fields=extract_fields(data),
                )
            )
        report.pages.append(page)
    return report
