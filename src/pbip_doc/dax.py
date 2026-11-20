"""Lightweight DAX reference extraction (no full parser needed for lineage)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pbip_doc.tmdl import unquote

_STRING = re.compile(r'"(?:[^"]|"")*"')
_COMMENT = re.compile(r"--[^\n]*|//[^\n]*|/\*.*?\*/", re.DOTALL)
_QUALIFIED = re.compile(r"('(?:[^']|'')+'|[A-Za-z_][\w.]*)\s*\[([^\]]+)\]")
_BARE = re.compile(r"\[([^\]]+)\]")


@dataclass(frozen=True)
class DaxRefs:
    qualified: frozenset[tuple[str, str]] = field(default_factory=frozenset)  # (table, name)
    bare: frozenset[str] = field(default_factory=frozenset)  # [name]
    local_names: frozenset[str] = field(default_factory=frozenset)  # "Name" columns built in DAX


def references(expression: str) -> DaxRefs:
    """Return the objects a DAX expression points to.

    String literals are collected first: names such as "Year Total" in ADDCOLUMNS create local
    columns that can later be used as [Year Total] without being model objects.
    """
    local = {s[1:-1].replace('""', '"') for s in _STRING.findall(expression)}
    code = _COMMENT.sub(" ", expression)
    code = _STRING.sub('""', code)

    qualified = set()
    for table, name in _QUALIFIED.findall(code):
        qualified.add((unquote(table), name.strip()))
    without_qualified = _QUALIFIED.sub(" ", code)
    bare = {name.strip() for name in _BARE.findall(without_qualified)}
    return DaxRefs(frozenset(qualified), frozenset(bare), frozenset(local))
