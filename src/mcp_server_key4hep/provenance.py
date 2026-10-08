"""Portable provenance for externally executed commands. Never executes a run."""

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import __version__

SCHEMA = "key4hep-external-run-v1"


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_new(path, value):
    with Path(path).open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.PIPE)


def committed_input(repo, path):
    repo = Path(repo).resolve()
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Inputs must be repository-relative paths")
    source = (repo / relative).resolve(strict=True)
    source.relative_to(repo)
    commit = git(repo, "rev-parse", "HEAD").decode().strip()
    blob = git(repo, "show", f"{commit}:{relative.as_posix()}")
    if source.read_bytes() != blob or git(repo, "diff", "--cached", "HEAD", "--", str(relative)):
        raise ValueError(f"Commit input changes first: {relative}")
    return {
        "repository": str(repo),
        "path": relative.as_posix(),
        "commit": commit,
        "sha256": hashlib.sha256(blob).hexdigest(),
    }, blob


def prepare(directory, spec):
    """Snapshot committed inputs before the user runs their normal command."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", spec["release"]):
        raise ValueError("An explicit dated Key4hep release is required")
    seeds = spec["seeds"]
    if (
        not isinstance(seeds, dict)
        or not seeds
        or any(type(seed) is not int or not 1 <= seed <= 900_000_000 for seed in seeds.values())
    ):
        raise ValueError("Declare explicit positive seeds (1..900000000)")
    if (
        not isinstance(spec["command"], list)
        or not spec["command"]
        or any(not isinstance(arg, str) or not arg for arg in spec["command"])
    ):
        raise ValueError("command must be a nonempty argument list")
    if not spec["inputs"]:
        raise ValueError("At least one committed input is required")
    inputs = [committed_input(item["repository"], item["path"]) for item in spec["inputs"]]
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidental reuse of a previous run directory.
    (directory / "inputs").mkdir()
    records = []
    for index, (record, blob) in enumerate(inputs):
        snapshot = f"inputs/{index}-{Path(record['path']).name}"
        (directory / snapshot).write_bytes(blob)
        records.append({**record, "snapshot": snapshot})
    value = {
        "schema": SCHEMA,
        "state": "PREPARED",
        "tool_version": __version__,
        "prepared_at": now(),
        "specification": spec,
        "inputs": records,
        "assurance": "Operator-declared command and environment; hashes are not execution attestation",
    }
    write_new(directory / "provenance.pending.json", value)
    return value


def now():
    return datetime.now(timezone.utc).isoformat()


def inside(directory, relative):
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Artifact paths must be relative to the provenance directory")
    resolved = (directory / path).resolve(strict=True)
    resolved.relative_to(directory.resolve())
    return resolved


def record(directory, output, expected, exit_code, artifacts=()):
    """Validate an external result and seal its manifest beside the output."""
    from .validator import validate

    directory = Path(directory).resolve()
    if (directory / "provenance.json").exists():
        raise ValueError("Final provenance already exists")
    data = json.loads((directory / "provenance.pending.json").read_text())
    if data["schema"] != SCHEMA or data["state"] != "PREPARED":
        raise ValueError("Unsupported pending manifest")
    if exit_code != 0:
        raise ValueError("Cannot seal a successful run with nonzero declared exit code")
    for item in data["inputs"]:
        current, _ = committed_input(item["repository"], item["path"])
        if current["commit"] != item["commit"] or current["sha256"] != item["sha256"]:
            raise ValueError("Input changed since preparation")
        if digest(inside(directory, item["snapshot"])) != item["sha256"]:
            raise ValueError("Input snapshot changed")
    path = inside(directory, output)
    if path.parent != directory or not path.name.endswith(".e4h.root"):
        raise ValueError("Output .e4h.root must sit beside provenance.json")
    before = digest(path)
    validation = validate(path, expected)
    if before != digest(path):
        raise ValueError("Output changed during validation")
    hashes = {output: before}
    for name in artifacts:
        if name in {"provenance.json", "provenance.pending.json"}:
            raise ValueError("Manifest cannot be its own artifact")
        hashes[name] = digest(inside(directory, name))
    data.update(
        state="RECORDED",
        recorded_at=now(),
        output=output,
        declared_exit_code=exit_code,
        validation=validation,
        artifacts=hashes,
    )
    write_new(directory / "provenance.json", data)
    return verify(directory / "provenance.json")


def verify(manifest):
    """Verify archived bytes without needing the original Git checkouts or ROOT."""
    manifest = Path(manifest).resolve()
    errors = []
    try:
        data = json.loads(manifest.read_text())
        if data["schema"] != SCHEMA or data["state"] != "RECORDED":
            raise ValueError("Unsupported or unfinished manifest")
        if not data["inputs"] or data["output"] not in data["artifacts"]:
            raise ValueError("Missing inputs or output hash")
        if data["validation"]["valid"] is not True:
            raise ValueError("Missing successful validation")
        hashes = [(item["snapshot"], item["sha256"]) for item in data["inputs"]]
        hashes.extend(data["artifacts"].items())
        for name, expected in hashes:
            if digest(inside(manifest.parent, name)) != expected:
                errors.append(f"SHA256 mismatch: {name}")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(str(exc))
    return {
        "valid": not errors,
        "errors": errors,
        "manifest": str(manifest),
        "scope": "Archived input and artifact integrity; no signature or execution attestation",
    }
