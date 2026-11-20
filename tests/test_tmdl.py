import pytest

from pbip_doc.tmdl import TmdlSyntaxError, parse, split_column_ref, unquote


def test_unquote_and_column_refs() -> None:
    assert unquote("'Net Revenue'") == "Net Revenue"
    assert unquote("'Customer''s Share'") == "Customer's Share"
    assert unquote("Sales") == "Sales"
    assert split_column_ref("Sales.'Order Date'") == ("Sales", "Order Date")
    assert split_column_ref("'Fact.Sales'.Amount") == ("Fact.Sales", "Amount")
    with pytest.raises(ValueError):
        split_column_ref("NoDot")


def test_objects_properties_flags_and_descriptions() -> None:
    (table,) = parse(
        "/// The fact table.\n"
        "/// Second line.\n"
        "table Sales\n"
        "\tlineageTag: abc\n"
        "\tisHidden\n"
        "\n"
        "\tcolumn 'Order Date'\n"
        "\t\tdataType: dateTime\n"
        "\t\tsourceColumn: order_date\n"
    )
    assert (table.kind, table.name) == ("table", "Sales")
    assert table.description == "The fact table.\nSecond line."
    assert table.flag("isHidden")
    assert table.prop("lineageTag") == "abc"
    (column,) = table.all("column")
    assert column.name == "Order Date"
    assert column.prop("dataType") == "dateTime"
    assert column.description is None


def test_multiline_expression_stops_at_properties() -> None:
    (table,) = parse(
        "table T\n"
        "\tmeasure 'Net' =\n"
        "\t\t\tCALCULATE (\n"
        "\t\t\t    SUM ( T[x] )\n"
        "\n"
        "\t\t\t)\n"
        "\t\tformatString: #,0\n"
    )
    measure = table.child("measure")
    assert measure is not None
    assert measure.expression == "CALCULATE (\n    SUM ( T[x] )\n\n)"
    assert measure.prop("formatString") == "#,0"


def test_single_line_expression_with_continuation() -> None:
    (table,) = parse("table T\n\tmeasure M = 1 +\n\t\t\t2\n\t\tformatString: 0\n")
    measure = table.child("measure")
    assert measure is not None and measure.expression == "1 +\n2"


def test_fenced_expression() -> None:
    (table,) = parse(
        "table T\n"
        "\tmeasure M = ```\n"
        "\t\t\tVAR x = 1\n"
        "\t\t\t\tRETURN x\n"
        "\t\t\t```\n"
        "\t\tdisplayFolder: F\n"
    )
    measure = table.child("measure")
    assert measure is not None
    assert measure.expression == "VAR x = 1\n\tRETURN x"
    assert measure.prop("displayFolder") == "F"


def test_expression_properties_and_bare_blocks() -> None:
    (table,) = parse(
        "table C\n"
        "\tcalculationGroup\n"
        "\t\tprecedence: 1\n"
        "\n"
        "\t\tcalculationItem YTD = CALCULATE ( SELECTEDMEASURE () )\n"
        '\t\t\tformatStringDefinition = "0.0%"\n'
        "\n"
        "\tpartition P = m\n"
        "\t\tmode: import\n"
        "\t\tsource =\n"
        "\t\t\t\tlet a = 1 in a\n"
    )
    group = table.child("calculationGroup")
    assert group is not None and group.prop("precedence") == "1"
    item = group.child("calculationItem")
    assert item is not None and item.prop("formatStringDefinition") == '"0.0%"'
    partition = table.child("partition")
    assert partition is not None
    assert partition.expression == "m"
    assert partition.prop("source") == "let a = 1 in a"


def test_ref_lines_and_comments_are_ignored() -> None:
    nodes = parse("model Model\n\tculture: en-US\n\n// comment\nref table Sales\n")
    assert [n.kind for n in nodes] == ["model"]


def test_bom_and_crlf() -> None:
    (table,) = parse("﻿table T\r\n\tisHidden\r\n")
    assert table.flag("isHidden")


def test_errors_report_file_and_line() -> None:
    with pytest.raises(TmdlSyntaxError, match=r"x\.tmdl:1: property outside"):
        parse("dataType: string\n", "x.tmdl")
    with pytest.raises(TmdlSyntaxError, match="unterminated"):
        parse("table T\n\tmeasure M = ```\n\t\t\t1\n", "x.tmdl")
    with pytest.raises(TmdlSyntaxError, match="cannot parse"):
        parse("table T\n\t!!!\n", "x.tmdl")
