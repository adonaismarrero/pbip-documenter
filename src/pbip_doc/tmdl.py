"""A small, dependency-free parser for TMDL (Tabular Model Definition Language).

It does not try to understand every TMDL keyword. It turns the indentation structure into a
generic tree of `Node`s (objects with a kind, a name, an optional expression, properties and
children), which `model.py` then maps to semantic objects. Unknown keywords are kept, so new
TMDL features do not break parsing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_NAME = r"'(?:[^']|'')*'|[^\s=']+"
_OBJECT = re.compile(rf"^(?P<kind>[A-Za-z]\w*)\s+(?P<name>{_NAME})\s*(?:=\s*(?P<expr>.*))?$")
_PROPERTY = re.compile(r"^(?P<key>[A-Za-z]\w*)\s*:\s*(?P<value>.*)$")
_EXPR_PROPERTY = re.compile(r"^(?P<key>[A-Za-z]\w*)\s*=\s*(?P<expr>.*)$")
_FLAG = re.compile(r"^(?P<key>[A-Za-z]\w*)$")
_FENCE = "```"


class TmdlSyntaxError(ValueError):
    def __init__(self, path: str, line: int, message: str) -> None:
        super().__init__(f"{path}:{line}: {message}")
        self.path, self.line = path, line


@dataclass
class Node:
    kind: str
    name: str | None = None
    expression: str | None = None
    description: str | None = None
    properties: dict[str, str | bool] = field(default_factory=dict)
    children: list[Node] = field(default_factory=list)
    source: str = ""
    line: int = 0

    def child(self, kind: str) -> Node | None:
        return next((c for c in self.children if c.kind == kind), None)

    def all(self, kind: str) -> list[Node]:
        return [c for c in self.children if c.kind == kind]

    def prop(self, key: str, default: str | None = None) -> str | None:
        value = self.properties.get(key)
        if value is None:
            return default
        return "true" if value is True else str(value)

    def flag(self, key: str) -> bool:
        value = self.properties.get(key)
        return value is True or (isinstance(value, str) and value.lower() == "true")


def unquote(name: str) -> str:
    """'Net Revenue' -> Net Revenue ; 'It''s' -> It's ; Sales -> Sales"""
    name = name.strip()
    if len(name) >= 2 and name[0] == name[-1] == "'":
        return name[1:-1].replace("''", "'")
    return name


def split_column_ref(ref: str) -> tuple[str, str]:
    """Sales.'Order Date' -> (Sales, Order Date). The dot that separates is outside quotes."""
    in_quote = False
    for i, ch in enumerate(ref):
        if ch == "'":
            in_quote = not in_quote
        elif ch == "." and not in_quote:
            return unquote(ref[:i]), unquote(ref[i + 1 :])
    raise ValueError(f"not a Table.Column reference: {ref!r}")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip("\t"))


def _dedent(lines: list[str]) -> str:
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return ""
    depth = min(_indent(x) for x in lines if x.strip())
    return "\n".join(x[depth:] if x.strip() else "" for x in lines)


def parse(text: str, source: str = "<string>") -> list[Node]:
    """Parse one TMDL document into its top-level nodes."""
    lines = text.lstrip("﻿").replace("\r\n", "\n").split("\n")
    roots: list[Node] = []
    stack: list[tuple[int, Node]] = []  # (indent, node)
    description: list[str] = []
    i = 0

    def attach(node: Node, indent: int) -> None:
        while stack and stack[-1][0] >= indent:
            stack.pop()
        (stack[-1][1].children if stack else roots).append(node)
        stack.append((indent, node))

    def owner(indent: int, lineno: int) -> Node:
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if not stack:
            raise TmdlSyntaxError(source, lineno, "property outside of an object")
        return stack[-1][1]

    def read_expression(first: str, indent: int) -> str:
        """`first` is the text after '='; continuation lines are indented 2+ levels deeper."""
        nonlocal i
        first = first.strip()
        if first.startswith(_FENCE):
            body: list[str] = []
            rest = first[len(_FENCE) :]
            if rest.strip():
                body.append("\t" * (indent + 2) + rest)
            while i < len(lines) and lines[i].strip() != _FENCE:
                body.append(lines[i])
                i += 1
            if i == len(lines):
                raise TmdlSyntaxError(source, i, "unterminated ``` expression")
            i += 1
            return _dedent(body)
        body = []
        while i < len(lines):
            nxt = lines[i]
            if nxt.strip() and _indent(nxt) < indent + 2:
                break
            body.append(nxt)
            i += 1
        continuation = _dedent(body)
        if first and continuation:
            return first + "\n" + continuation
        return first or continuation

    while i < len(lines):
        raw = lines[i]
        lineno = i + 1
        i += 1
        stripped = raw.strip()
        if not stripped:
            continue
        indent = _indent(raw)
        if stripped.startswith("///"):
            description.append(stripped[3:].strip())
            continue
        if stripped.startswith("//"):
            continue
        if stripped.startswith("ref "):  # `ref table X` only fixes ordering in model.tmdl
            description = []
            continue

        if m := _PROPERTY.match(stripped):
            owner(indent, lineno).properties[m["key"]] = m["value"].strip()
            description = []
            continue

        if m := _OBJECT.match(stripped):
            node = Node(m["kind"], unquote(m["name"]), source=source, line=lineno)
            if m["expr"] is not None:
                node.expression = read_expression(m["expr"], indent)
            node.description = "\n".join(description) or None
            description = []
            attach(node, indent)
            continue

        if m := _EXPR_PROPERTY.match(stripped):
            owner(indent, lineno).properties[m["key"]] = read_expression(m["expr"], indent)
            description = []
            continue

        if m := _FLAG.match(stripped):
            nxt = next((x for x in lines[i:] if x.strip()), "")
            if nxt and _indent(nxt) > indent:  # opens a block: calculationGroup, database...
                node = Node(m["key"], source=source, line=lineno)
                node.description = "\n".join(description) or None
                attach(node, indent)
            else:
                owner(indent, lineno).properties[m["key"]] = True
            description = []
            continue

        raise TmdlSyntaxError(source, lineno, f"cannot parse line: {stripped[:80]!r}")

    return roots


def parse_folder(definition: Path) -> list[Node]:
    """Parse every .tmdl file of a semantic model `definition` folder (sorted for stable output)."""
    nodes: list[Node] = []
    for path in sorted(definition.rglob("*.tmdl")):
        rel = path.relative_to(definition).as_posix()
        nodes.extend(parse(path.read_text(encoding="utf-8-sig"), rel))
    return nodes
