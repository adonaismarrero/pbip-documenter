"""Semantic objects built from the generic TMDL tree."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from pbip_doc.tmdl import Node, parse_folder, split_column_ref, unquote


@dataclass
class Column:
    table: str
    name: str
    data_type: str | None = None
    description: str | None = None
    is_hidden: bool = False
    is_key: bool = False
    format_string: str | None = None
    display_folder: str | None = None
    source_column: str | None = None
    expression: str | None = None  # calculated column
    sort_by: str | None = None
    data_category: str | None = None

    @property
    def is_calculated(self) -> bool:
        return self.expression is not None


@dataclass
class Measure:
    table: str
    name: str
    expression: str
    description: str | None = None
    format_string: str | None = None
    display_folder: str | None = None
    is_hidden: bool = False


@dataclass
class CalculationItem:
    name: str
    expression: str
    format_string_expression: str | None = None
    ordinal: int | None = None


@dataclass
class Hierarchy:
    name: str
    levels: list[tuple[str, str]]  # (level name, column)


@dataclass
class Partition:
    name: str
    kind: str | None  # m, calculated, calculationGroup, entity...
    mode: str | None
    source: str | None


@dataclass
class Table:
    name: str
    description: str | None = None
    is_hidden: bool = False
    data_category: str | None = None
    columns: list[Column] = field(default_factory=list)
    measures: list[Measure] = field(default_factory=list)
    hierarchies: list[Hierarchy] = field(default_factory=list)
    partitions: list[Partition] = field(default_factory=list)
    calculation_items: list[CalculationItem] = field(default_factory=list)
    calculation_group_precedence: int | None = None

    @property
    def is_calculation_group(self) -> bool:
        return bool(self.calculation_items) or any(
            p.kind == "calculationGroup" for p in self.partitions
        )

    @property
    def is_measure_table(self) -> bool:
        """Only measures (a placeholder column at most)."""
        return bool(self.measures) and all(c.is_hidden for c in self.columns)

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name.lower() == name.lower()), None)


@dataclass
class Relationship:
    name: str
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    is_active: bool = True
    cross_filter: str = "oneDirection"
    from_cardinality: str = "many"
    to_cardinality: str = "one"

    @property
    def cardinality(self) -> str:
        symbol = {"many": "*", "one": "1"}
        return f"{symbol.get(self.from_cardinality, '?')}:{symbol.get(self.to_cardinality, '?')}"


@dataclass
class Role:
    name: str
    description: str | None
    permission: str | None
    table_filters: dict[str, str]


@dataclass
class NamedExpression:
    name: str
    expression: str
    description: str | None
    is_parameter: bool
    kind: str  # parameter | function | query


@dataclass
class SemanticModel:
    name: str
    culture: str | None = None
    compatibility_level: str | None = None
    tables: list[Table] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    roles: list[Role] = field(default_factory=list)
    expressions: list[NamedExpression] = field(default_factory=list)

    def table(self, name: str) -> Table | None:
        return next((t for t in self.tables if t.name.lower() == name.lower()), None)

    @property
    def measures(self) -> list[Measure]:
        return [m for t in self.tables for m in t.measures]

    @property
    def columns(self) -> list[Column]:
        return [c for t in self.tables for c in t.columns]

    def measure(self, name: str) -> Measure | None:
        return next((m for m in self.measures if m.name.lower() == name.lower()), None)


def _as_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _column(table: str, node: Node) -> Column:
    return Column(
        table=table,
        name=node.name or "",
        data_type=node.prop("dataType"),
        description=node.description,
        is_hidden=node.flag("isHidden"),
        is_key=node.flag("isKey"),
        format_string=node.prop("formatString"),
        display_folder=node.prop("displayFolder"),
        source_column=node.prop("sourceColumn"),
        expression=node.expression,
        sort_by=_unquote_opt(node.prop("sortByColumn")),
        data_category=node.prop("dataCategory"),
    )


def _unquote_opt(value: str | None) -> str | None:
    return unquote(value) if value else None


def _table(node: Node) -> Table:
    name = node.name or ""
    table = Table(
        name=name,
        description=node.description,
        is_hidden=node.flag("isHidden"),
        data_category=node.prop("dataCategory"),
    )
    for child in node.children:
        if child.kind == "column":
            table.columns.append(_column(name, child))
        elif child.kind == "measure":
            table.measures.append(
                Measure(
                    table=name,
                    name=child.name or "",
                    expression=child.expression or "",
                    description=child.description,
                    format_string=child.prop("formatString"),
                    display_folder=child.prop("displayFolder"),
                    is_hidden=child.flag("isHidden"),
                )
            )
        elif child.kind == "hierarchy":
            levels = [
                (lvl.name or "", _unquote_opt(lvl.prop("column")) or "")
                for lvl in child.all("level")
            ]
            table.hierarchies.append(Hierarchy(child.name or "", levels))
        elif child.kind == "partition":
            source = child.prop("source")
            table.partitions.append(
                Partition(child.name or "", child.expression, child.prop("mode"), source)
            )
        elif child.kind == "calculationGroup":
            table.calculation_group_precedence = _as_int(child.prop("precedence"))
            for item in child.all("calculationItem"):
                table.calculation_items.append(
                    CalculationItem(
                        name=item.name or "",
                        expression=item.expression or "",
                        format_string_expression=item.prop("formatStringDefinition"),
                        ordinal=_as_int(item.prop("ordinal")),
                    )
                )
    return table


_PARAMETER = re.compile(r"\bIsParameterQuery\s*=\s*true\b", re.IGNORECASE)


def _expression(node: Node) -> NamedExpression:
    text = node.expression or ""
    is_parameter = bool(_PARAMETER.search(text))
    if is_parameter:
        kind = "parameter"
    elif re.match(r"^\s*\(.*\)\s*(as\s+\w+\s*)?=>", text, re.DOTALL):
        kind = "function"
    else:
        kind = "query"
    return NamedExpression(node.name or "", text, node.description, is_parameter, kind)


def build(nodes: list[Node], name: str = "Model") -> SemanticModel:
    model = SemanticModel(name=name)
    for node in nodes:
        if node.kind == "model":
            model.name = node.name or name
            model.culture = node.prop("culture")
        elif node.kind == "database":
            model.compatibility_level = node.prop("compatibilityLevel")
        elif node.kind == "table":
            model.tables.append(_table(node))
        elif node.kind == "relationship":
            from_table, from_column = split_column_ref(node.prop("fromColumn") or "")
            to_table, to_column = split_column_ref(node.prop("toColumn") or "")
            model.relationships.append(
                Relationship(
                    name=node.name or "",
                    from_table=from_table,
                    from_column=from_column,
                    to_table=to_table,
                    to_column=to_column,
                    is_active=node.prop("isActive", "true") != "false",
                    cross_filter=node.prop("crossFilteringBehavior", "oneDirection") or "",
                    from_cardinality=node.prop("fromCardinality", "many") or "many",
                    to_cardinality=node.prop("toCardinality", "one") or "one",
                )
            )
        elif node.kind == "role":
            model.roles.append(
                Role(
                    name=node.name or "",
                    description=node.description,
                    permission=node.prop("modelPermission"),
                    table_filters={
                        tp.name or "": tp.expression or "" for tp in node.all("tablePermission")
                    },
                )
            )
        elif node.kind == "expression":
            model.expressions.append(_expression(node))
    return model


def load_model(definition: Path) -> SemanticModel:
    model = build(
        parse_folder(definition), name=definition.parent.name.removesuffix(".SemanticModel")
    )
    if model.name in ("Model", ""):
        model.name = definition.parent.name.removesuffix(".SemanticModel")
    return model
