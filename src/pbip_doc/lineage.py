"""Dependencies between model objects and where the report uses them."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from pbip_doc.dax import references
from pbip_doc.model import SemanticModel
from pbip_doc.report import Report

Kind = Literal["measure", "column"]


@dataclass(frozen=True, order=True)
class ObjRef:
    kind: Kind
    table: str
    name: str

    def __str__(self) -> str:
        return f"[{self.name}]" if self.kind == "measure" else f"'{self.table}'[{self.name}]"


@dataclass(frozen=True)
class BrokenRef:
    owner: str  # human-readable name of the expression that has the problem
    reference: str


@dataclass
class Lineage:
    depends_on: dict[ObjRef, set[ObjRef]] = field(default_factory=lambda: defaultdict(set))
    used_by: dict[ObjRef, set[ObjRef]] = field(default_factory=lambda: defaultdict(set))
    visuals: dict[ObjRef, set[str]] = field(default_factory=lambda: defaultdict(set))
    structural: set[ObjRef] = field(default_factory=set)  # keys, sort-by, hierarchies, RLS...
    broken: list[BrokenRef] = field(default_factory=list)
    has_report: bool = False
    used: set[ObjRef] = field(default_factory=set)

    def is_used(self, obj: ObjRef) -> bool:
        return obj in self.used


class _Resolver:
    def __init__(self, model: SemanticModel) -> None:
        self.columns = {(c.table.lower(), c.name.lower()): c for c in model.columns}
        self.measures = {m.name.lower(): m for m in model.measures}
        self.tables = {t.name.lower(): t for t in model.tables}

    def column(self, table: str, name: str) -> ObjRef | None:
        c = self.columns.get((table.lower(), name.lower()))
        return ObjRef("column", c.table, c.name) if c else None

    def measure(self, name: str) -> ObjRef | None:
        m = self.measures.get(name.lower())
        return ObjRef("measure", m.table, m.name) if m else None

    def resolve(
        self, expression: str, home_table: str | None, owner: str
    ) -> tuple[set[ObjRef], list[BrokenRef]]:
        refs = references(expression)
        found: set[ObjRef] = set()
        broken: list[BrokenRef] = []
        for table, name in refs.qualified:
            obj = self.column(table, name)
            if obj is None and table.lower() in self.tables:
                obj = self.measure(name)  # 'Table'[Measure] is legal DAX
            if obj is None:
                broken.append(BrokenRef(owner, f"{table}[{name}]"))
                continue
            found.add(obj)
        for name in refs.bare:
            obj = self.measure(name)
            if obj is None and home_table:
                obj = self.column(home_table, name)
            if obj is None:
                if name in refs.local_names or name.startswith("@"):
                    continue  # column created inside the expression (ADDCOLUMNS, SELECTCOLUMNS...)
                broken.append(BrokenRef(owner, f"[{name}]"))
                continue
            found.add(obj)
        return found, broken


def analyse(model: SemanticModel, report: Report | None = None) -> Lineage:
    lineage = Lineage(has_report=report is not None)
    resolver = _Resolver(model)

    def link(owner: ObjRef, deps: set[ObjRef]) -> None:
        for dep in deps:
            if dep != owner:
                lineage.depends_on[owner].add(dep)
                lineage.used_by[dep].add(owner)

    for table in model.tables:
        for measure in table.measures:
            ref = ObjRef("measure", table.name, measure.name)
            deps, broken = resolver.resolve(measure.expression, table.name, str(ref))
            link(ref, deps)
            lineage.broken += broken
        for column in table.columns:
            ref = ObjRef("column", table.name, column.name)
            if column.expression:
                deps, broken = resolver.resolve(column.expression, table.name, str(ref))
                link(ref, deps)
                lineage.broken += broken
            if column.sort_by and (target := resolver.column(table.name, column.sort_by)):
                lineage.structural.add(target)
        for hierarchy in table.hierarchies:
            for _, column_name in hierarchy.levels:
                if obj := resolver.column(table.name, column_name):
                    lineage.structural.add(obj)
        for item in table.calculation_items:
            owner = f"'{table.name}'[{item.name}]"
            for text in (item.expression, item.format_string_expression or ""):
                deps, broken = resolver.resolve(text, table.name, owner)
                lineage.structural |= deps
                lineage.broken += broken

    for rel in model.relationships:
        for table, column in ((rel.from_table, rel.from_column), (rel.to_table, rel.to_column)):
            if obj := resolver.column(table, column):
                lineage.structural.add(obj)
    for role in model.roles:
        for table, expression in role.table_filters.items():
            deps, broken = resolver.resolve(expression, table, f"role {role.name}")
            lineage.structural |= deps
            lineage.broken += broken

    roots: set[ObjRef] = set(lineage.structural)
    if report is not None:
        hierarchy_columns = {
            (t.name.lower(), h.name.lower(), lvl.lower()): col
            for t in model.tables
            for h in t.hierarchies
            for lvl, col in h.levels
        }
        for where, f in report.all_fields():
            obj: ObjRef | None
            if f.kind == "Measure":
                obj = resolver.measure(f.name)
            elif f.kind == "HierarchyLevel":
                col = hierarchy_columns.get(
                    (f.table.lower(), f.name.lower(), (f.level or "").lower())
                )
                obj = resolver.column(f.table, col) if col else None
            else:
                obj = resolver.column(f.table, f.name) or resolver.measure(f.name)
            if obj is None:
                lineage.broken.append(BrokenRef(f"report: {where}", f"{f.table}[{f.name}]"))
                continue
            lineage.visuals[obj].add(where)
            roots.add(obj)

    # Everything reachable from what the report or the model structure needs is "used".
    stack = list(roots)
    while stack:
        obj = stack.pop()
        if obj in lineage.used:
            continue
        lineage.used.add(obj)
        stack.extend(lineage.depends_on.get(obj, ()))
    lineage.broken = sorted(set(lineage.broken), key=lambda b: (b.owner, b.reference))
    return lineage
