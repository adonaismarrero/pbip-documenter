"""pbip-doc command line."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pbip_doc import __version__
from pbip_doc.lineage import analyse
from pbip_doc.model import load_model
from pbip_doc.project import ProjectError, locate
from pbip_doc.render import to_json, to_markdown
from pbip_doc.report import load_report
from pbip_doc.rules import RULES, Severity, check
from pbip_doc.tmdl import TmdlSyntaxError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pbip-doc",
        description="Document and lint Power BI projects (PBIP: TMDL model + PBIR report).",
    )
    parser.add_argument("--version", action="version", version=f"pbip-doc {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("path", type=Path, help=".pbip file, project folder or *.SemanticModel")
    common.add_argument("--no-report", action="store_true", help="ignore the report")
    common.add_argument(
        "--ignore", default="", help="comma-separated rule codes to skip, e.g. PBD004,PBD009"
    )

    build = sub.add_parser("build", parents=[common], help="write the documentation")
    build.add_argument("-o", "--output", type=Path, help="Markdown file (default: stdout)")
    build.add_argument("--json", type=Path, help="also write a machine-readable JSON file")

    lint = sub.add_parser("check", parents=[common], help="run the quality checks (for CI)")
    lint.add_argument(
        "--fail-on",
        choices=["error", "warning", "info", "never"],
        default="error",
        help="lowest severity that makes the command exit with code 1 (default: error)",
    )

    sub.add_parser("rules", help="list the quality rules")
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # legacy Windows consoles cannot print → or ›
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = _parser().parse_args(argv)
    if args.command == "rules":
        for code, (severity, text) in RULES.items():
            print(f"{code}  {severity!s:<7}  {text}")
        return 0

    try:
        project = locate(args.path)
        model = load_model(project.model_definition)
        report = None
        if project.report_definition and not args.no_report:
            report = load_report(project.report_definition)
    except (ProjectError, TmdlSyntaxError, OSError) as exc:
        print(f"pbip-doc: {exc}", file=sys.stderr)
        return 2

    lineage = analyse(model, report)
    ignored = {c.strip().upper() for c in args.ignore.split(",") if c.strip()}
    findings = [f for f in check(model, lineage) if f.code not in ignored]

    if args.command == "build":
        markdown = to_markdown(model, lineage, findings, report)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(markdown, encoding="utf-8", newline="\n")
            print(f"wrote {args.output}", file=sys.stderr)
        else:
            sys.stdout.write(markdown)
        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(
                to_json(model, lineage, findings, report), encoding="utf-8", newline="\n"
            )
            print(f"wrote {args.json}", file=sys.stderr)
        return 0

    for f in findings:
        print(f"{f.severity!s:<7} {f.code}  {f.obj}  {f.message}")
    counts = {s: sum(f.severity == s for f in findings) for s in Severity}
    print(
        f"\n{model.name}: {len(model.measures)} measures, {len(model.columns)} columns - "
        f"{counts[Severity.ERROR]} errors, {counts[Severity.WARNING]} warnings, "
        f"{counts[Severity.INFO]} info"
    )
    if args.fail_on == "never":
        return 0
    threshold = Severity[args.fail_on.upper()]
    return 1 if any(f.severity >= threshold for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
