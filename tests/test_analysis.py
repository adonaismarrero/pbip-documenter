from pathlib import Path

import pytest

from pbip_doc.dax import references
from pbip_doc.lineage import ObjRef, analyse
from pbip_doc.model import SemanticModel, build, load_model
from pbip_doc.project import Project, locate
from pbip_doc.report import Report, load_report
from pbip_doc.rules import check
from pbip_doc.tmdl import parse

FIXTURE = Path(__file__).parent / "fixtures" / "Mini"


@pytest.fixture(scope="module")
def project() -> Project:
    return locate(FIXTURE / "Mini.pbip")


@pytest.fixture(scope="module")
def model(project: Project) -> SemanticModel:
    return load_model(project.model_definition)


@pytest.fixture(scope="module")
def report(project: Project) -> Report:
    assert project.report_definition is not None
    return load_report(project.report_definition)


def test_dax_references_ignore_strings_comments_and_local_columns() -> None:
    refs = references(
        "VAR t = ADDCOLUMNS ( VALUES ( 'Date'[Year] ), \"Year Total\", [Total] )\n"
        "-- [Commented]\n/* [Block] */ // [Line]\n"
        'RETURN SUMX ( t, [Year Total] ) & "[In String]" + Fact[Amount]'
    )
    assert refs.qualified == {("Date", "Year"), ("Fact", "Amount")}
    assert refs.bare == {"Total", "Year Total"}
    assert "Year Total" in refs.local_names


def test_model_objects(model: SemanticModel) -> None:
    assert model.name == "Mini"
    assert model.compatibility_level == "1600"
    assert model.culture == "en-US"
    # model.tmdl lists only Fact: it goes first, the rest keep file order
    assert [t.name for t in model.tables] == ["Fact", "Calc", "Date", "Store"]
    fact = model.table("fact")
    assert fact is not None and fact.description == "Fact table."
    assert model.measure("Customer's Share") is not None
    big_sale = fact.column("Big Sale")
    assert big_sale is not None and big_sale.is_calculated
    date = model.table("Date")
    assert date is not None and date.data_category == "Time"
    assert date.hierarchies[0].levels == [("Year", "Year"), ("Month", "Month Name")]
    calc = model.table("Calc")
    assert calc is not None and calc.is_calculation_group
    assert calc.calculation_group_precedence == 2
    assert [i.name for i in calc.calculation_items] == ["Current", "YTD"]
    assert {r.name: (r.is_active, r.cross_filter) for r in model.relationships} == {
        "a1": (True, "oneDirection"),
        "a2": (False, "oneDirection"),
        "a3": (True, "bothDirections"),
    }
    assert model.relationships[0].cardinality == "*:1"
    assert model.roles[0].table_filters == {"Store": 'Store[Region] = "North"'}
    assert {e.name: e.kind for e in model.expressions} == {
        "Server": "parameter",
        "Helper": "function",
    }


def test_report_fields(report: Report) -> None:
    assert [p.display_name for p in report.pages] == ["Main"]
    v1, v2 = report.visuals
    assert v1.title == "Amount by month" and v1.visual_type == "lineChart"
    assert {(f.kind, f.name, f.level) for f in v1.fields} == {
        ("HierarchyLevel", "Calendar", "Month"),
        ("Measure", "Total Amount", None),
    }
    assert {(f.table, f.name) for f in v2.fields} == {("Fact", "Shipped Amount"), ("Fact", "Gone")}
    assert [(f.table, f.name) for f in report.pages[0].filters] == [("Store", "Region")]


def test_lineage(model: SemanticModel, report: Report) -> None:
    lineage = analyse(model, report)
    total = ObjRef("measure", "Fact", "Total Amount")
    shipped = ObjRef("measure", "Fact", "Shipped Amount")
    assert lineage.depends_on[shipped] == {
        total,
        ObjRef("column", "Fact", "Ship Date"),
        ObjRef("column", "Date", "Date"),
    }
    assert shipped in lineage.used_by[total]
    assert lineage.visuals[total] == {"Main › Amount by month"}
    # hierarchy level -> column, through the hierarchy definition
    assert ObjRef("column", "Date", "Month Name") in lineage.visuals
    # used transitively, structurally (sort by, RLS) or not at all
    assert lineage.is_used(ObjRef("column", "Fact", "Amount"))
    assert lineage.is_used(ObjRef("column", "Date", "Month Number"))
    assert lineage.is_used(ObjRef("column", "Store", "Region"))
    assert not lineage.is_used(ObjRef("measure", "Fact", "Broken"))
    assert {b.reference for b in lineage.broken} == {
        "Fact[No Such Column]",
        "[Missing Measure]",
        "Fact[Gone]",
    }


def test_rules_on_fixture(model: SemanticModel, report: Report) -> None:
    findings = check(model, analyse(model, report))
    got = {(f.code, f.obj) for f in findings}
    assert got == {
        ("PBD001", "[Broken]"),
        ("PBD001", "report: Main › v2"),
        ("PBD002", "[Customer's Share]"),
        ("PBD003", "[Customer's Share]"),
        ("PBD005", "'Fact'[Store ID]"),
        ("PBD006", "Fact[Store ID] → Store[Store ID]"),
        ("PBD007", "'Store'"),
        ("PBD008", "[Broken]"),
        ("PBD008", "[Customer's Share]"),
        ("PBD009", "'Fact'[Big Sale]"),
    }
    assert findings[0].code == "PBD001"  # most severe first


def test_without_report_there_is_no_usage_analysis(model: SemanticModel) -> None:
    codes = {f.code for f in check(model, analyse(model))}
    assert "PBD008" not in codes and "PBD009" not in codes


def test_inactive_relationship_without_userelationship() -> None:
    nodes = parse(
        "table A\n\tcolumn k\n\t\tisHidden\n\tmeasure M = 1\n\t\tformatString: 0\n"
        "table B\n\tcolumn k\n"
        "relationship r\n\tisActive: false\n\tfromColumn: A.k\n\ttoColumn: B.k\n"
    )
    model = build(nodes)
    assert "PBD010" in {f.code for f in check(model, analyse(model))}


def test_locate_variants(project: Project) -> None:
    model_dir = FIXTURE / "Mini.SemanticModel"
    assert locate(FIXTURE) == project
    assert locate(model_dir) == project
    assert locate(model_dir / "definition") == project
    assert locate(FIXTURE / "Mini.Report") == project
