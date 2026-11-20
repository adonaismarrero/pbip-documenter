"""Best-practice checks. Each finding has a stable code so it can be discussed in reviews."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum

from pbip_doc.lineage import Lineage, ObjRef
from pbip_doc.model import SemanticModel


class Severity(IntEnum):
    INFO = 1
    WARNING = 2
    ERROR = 3

    def __str__(self) -> str:
        return self.name.lower()


@dataclass(frozen=True, order=True)
class Finding:
    severity: Severity
    code: str
    obj: str
    message: str


RULES: dict[str, tuple[Severity, str]] = {
    "PBD001": (Severity.ERROR, "DAX or report reference to an object that does not exist"),
    "PBD002": (Severity.WARNING, "Visible measure without a description"),
    "PBD003": (Severity.WARNING, "Measure without a format string"),
    "PBD004": (Severity.INFO, "Measure outside a display folder"),
    "PBD005": (Severity.WARNING, "Visible foreign-key column (filter through the dimension)"),
    "PBD006": (Severity.WARNING, "Bidirectional relationship (ambiguity and performance risk)"),
    "PBD007": (Severity.INFO, "Visible table without a description"),
    "PBD008": (Severity.INFO, "Measure not used by the report or by other used objects"),
    "PBD009": (Severity.INFO, "Visible column not used anywhere (candidate to hide or remove)"),
    "PBD010": (Severity.WARNING, "Inactive relationship never activated with USERELATIONSHIP"),
}


def _finding(code: str, obj: str, detail: str = "") -> Finding:
    severity, text = RULES[code]
    return Finding(severity, code, obj, f"{text}{': ' + detail if detail else ''}")


def check(model: SemanticModel, lineage: Lineage) -> list[Finding]:
    findings: list[Finding] = []

    for broken in lineage.broken:
        findings.append(_finding("PBD001", broken.owner, broken.reference))

    for table in model.tables:
        visible_columns = [c for c in table.columns if not c.is_hidden]
        documentable = not table.is_hidden and not table.is_calculation_group
        if documentable and not table.description and (visible_columns or table.measures):
            findings.append(_finding("PBD007", f"'{table.name}'"))
        for m in table.measures:
            ref = str(ObjRef("measure", table.name, m.name))
            if not m.is_hidden and not m.description:
                findings.append(_finding("PBD002", ref))
            if not m.format_string:
                findings.append(_finding("PBD003", ref))
            if not m.display_folder and len(model.measures) > 5:
                findings.append(_finding("PBD004", ref))
            if lineage.has_report and not lineage.is_used(ObjRef("measure", table.name, m.name)):
                findings.append(_finding("PBD008", ref))
        if lineage.has_report and not table.is_calculation_group:
            for c in visible_columns:
                obj = ObjRef("column", table.name, c.name)
                if not lineage.is_used(obj) and not lineage.used_by.get(obj):
                    findings.append(_finding("PBD009", str(obj)))

    all_dax = "\n".join(
        [m.expression for m in model.measures]
        + [c.expression or "" for c in model.columns]
        + [i.expression for t in model.tables for i in t.calculation_items]
    )
    for rel in model.relationships:
        label = f"{rel.from_table}[{rel.from_column}] → {rel.to_table}[{rel.to_column}]"
        source = model.table(rel.from_table)
        column = source.column(rel.from_column) if source else None
        if column and not column.is_hidden and rel.from_cardinality == "many":
            findings.append(_finding("PBD005", f"'{rel.from_table}'[{rel.from_column}]"))
        if rel.cross_filter.lower() == "bothdirections":
            findings.append(_finding("PBD006", label))
        if not rel.is_active and not _activated(all_dax, rel.from_column, rel.to_column):
            findings.append(_finding("PBD010", label))

    return sorted(findings, key=lambda f: (-f.severity, f.code, f.obj))


def _activated(dax: str, *columns: str) -> bool:
    for match in re.finditer(r"USERELATIONSHIP\s*\(([^)]*)\)", dax, re.IGNORECASE):
        if all(f"[{c}]".lower() in match.group(1).lower() for c in columns):
            return True
    return False
