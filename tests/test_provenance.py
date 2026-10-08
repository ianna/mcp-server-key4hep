import json
import shutil
import subprocess

import pytest

from mcp_server_key4hep import provenance, validator
from mcp_server_key4hep.cli import main


@pytest.fixture
def prepared(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "card.cmd").write_text("Random:seed=42\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "Fixture",
        ],
        check=True,
    )
    spec = dict(
        release="2026-04-08",
        seeds={"pythia": 42},
        command=["k4run", "pythia.py"],
        inputs=[dict(repository=str(repo), path="card.cmd")],
    )
    return tmp_path / "run", repo, spec


def test_portable_integrity_and_mutation(prepared, monkeypatch, tmp_path):
    run, repo, spec = prepared
    provenance.prepare(run, spec)
    (run / "events.e4h.root").write_bytes(b"fake ROOT content")
    monkeypatch.setattr(validator, "validate", lambda *args: dict(valid=True, entries=10))
    assert provenance.record(run, "events.e4h.root", 10, 0)["valid"]
    moved = tmp_path / "moved"
    shutil.copytree(run, moved)
    shutil.rmtree(repo)
    assert provenance.verify(moved / "provenance.json")["valid"]
    (moved / "events.e4h.root").write_bytes(b"changed")
    assert not provenance.verify(moved / "provenance.json")["valid"]


@pytest.mark.parametrize("change", ["dirty", "staged", "floating", "seed", "traversal"])
def test_rejects_unreproducible_inputs(prepared, change):
    run, repo, spec = prepared
    if change in ("dirty", "staged"):
        (repo / "card.cmd").write_text("changed")
        if change == "staged":
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    elif change == "floating":
        spec["release"] = "latest"
    elif change == "seed":
        spec["seeds"] = {"pythia": 0}
    else:
        spec["inputs"][0]["path"] = "../card.cmd"
    with pytest.raises(ValueError):
        provenance.prepare(run, spec)


@pytest.mark.parametrize("change", ["input", "snapshot", "output", "exit"])
def test_record_rejects_changed_or_failed_run(prepared, monkeypatch, change):
    run, repo, spec = prepared
    provenance.prepare(run, spec)
    output = run / "events.e4h.root"
    output.write_bytes(b"fixture")

    def validate(*args):
        if change == "output":
            output.write_bytes(b"changed")
        return {"valid": True}

    monkeypatch.setattr(validator, "validate", validate)
    if change == "input":
        (repo / "card.cmd").write_text("modified")
    elif change == "snapshot":
        next((run / "inputs").iterdir()).write_text("modified")
    with pytest.raises(ValueError):
        provenance.record(run, output.name, 10, 1 if change == "exit" else 0)
    assert not (run / "provenance.json").exists()


def test_manifest_traversal_and_reuse(prepared, monkeypatch):
    run, repo, spec = prepared
    provenance.prepare(run, spec)
    with pytest.raises(FileExistsError):
        provenance.prepare(run, spec)
    (run / "events.e4h.root").write_bytes(b"fixture")
    monkeypatch.setattr(validator, "validate", lambda *args: {"valid": True})
    provenance.record(run, "events.e4h.root", 1, 0)
    with pytest.raises(ValueError, match="already exists"):
        provenance.record(run, "events.e4h.root", 1, 0)
    manifest = run / "provenance.json"
    data = json.loads(manifest.read_text())
    data["artifacts"]["../secret"] = "abc"
    manifest.write_text(json.dumps(data))
    assert not provenance.verify(manifest)["valid"]


def test_cli_mismatch_exit_and_exclusive_report(monkeypatch, tmp_path, capsys):
    from mcp_server_key4hep import cli

    monkeypatch.setattr(cli, "compare_files", lambda *args: {"valid": True, "identical": False})
    report = tmp_path / "comparison.json"
    args = ["compare", "left", "right", "--report", str(report)]
    assert main(args) == 2
    assert json.loads(report.read_text())["identical"] is False
    assert main(args) == 1
    capsys.readouterr()


def test_core_cli_needs_no_site_packages():
    import sys
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "src"
    proc = subprocess.run(
        [sys.executable, "-S", "-m", "mcp_server_key4hep.cli", "--help"],
        cwd=source,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "prepare" in proc.stdout and "compare" in proc.stdout


def test_multiple_source_repositories(prepared, tmp_path):
    run, repo, spec = prepared
    other = tmp_path / "upstream"
    subprocess.run(["git", "clone", "-q", str(repo), str(other)], check=True)
    spec["inputs"].append(dict(repository=str(other), path="card.cmd"))
    result = provenance.prepare(run, spec)
    assert len(result["inputs"]) == 2
    assert result["inputs"][0]["snapshot"] != result["inputs"][1]["snapshot"]
    assert all((run / item["snapshot"]).exists() for item in result["inputs"])


def test_cli_failure_still_writes_structured_report(tmp_path, capsys):
    report = tmp_path / "validation.json"
    assert (
        main(["validate", str(tmp_path / "missing.root"), "--events", "3", "--report", str(report)])
        == 1
    )
    result = json.loads(report.read_text())
    assert result["valid"] is False and result["error"]
    capsys.readouterr()
