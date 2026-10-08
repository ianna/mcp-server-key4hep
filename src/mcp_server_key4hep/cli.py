"""Provenance and validation for the existing Key4hep command-line workflow."""

import argparse
import json
from pathlib import Path

from . import provenance
from .compare import compare_files
from .validator import validate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    prepare = commands.add_parser(
        "prepare", help="Snapshot committed inputs; never runs generation"
    )
    prepare.add_argument("directory", type=Path)
    prepare.add_argument("--spec", required=True, type=Path)
    record = commands.add_parser("record", help="Validate an external output and seal provenance")
    record.add_argument("directory", type=Path)
    record.add_argument("--output", required=True)
    record.add_argument("--events", type=int, required=True)
    record.add_argument("--exit-code", type=int, required=True)
    record.add_argument("--artifact", action="append", default=[])
    verify = commands.add_parser("verify")
    verify.add_argument("manifest", type=Path)
    check = commands.add_parser("validate")
    check.add_argument("file", type=Path)
    check.add_argument("--events", type=int, required=True)
    compare = commands.add_parser("compare")
    compare.add_argument("left", type=Path)
    compare.add_argument("right", type=Path)
    for sub in (check, compare):
        sub.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.operation == "prepare":
            result = provenance.prepare(args.directory, json.loads(args.spec.read_text()))
        elif args.operation == "record":
            result = provenance.record(
                args.directory, args.output, args.events, args.exit_code, args.artifact
            )
        elif args.operation == "verify":
            result = provenance.verify(args.manifest)
        elif args.operation == "validate":
            result = validate(args.file, args.events)
        else:
            result = compare_files(args.left, args.right)
    except Exception as exc:
        result = {"valid": False, "error": f"{type(exc).__name__}: {exc}"}
    # Failures are reported too, so callers (MCP adapter, scripts) always get a
    # structured result rather than having to scrape stdout.
    if getattr(args, "report", None):
        try:
            provenance.write_new(args.report, result)
        except Exception as exc:
            result = {"valid": False, "error": f"Report not written: {type(exc).__name__}: {exc}"}
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result.get("valid", True):
        return 1
    return 2 if result.get("identical") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
