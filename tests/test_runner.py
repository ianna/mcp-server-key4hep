import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

import mcp_server_key4hep.runner as runner_module
from mcp_server_key4hep.runner import Runner, check_whizard, committed_inputs, sha256, write_json


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo with spaces"
    path.mkdir()
    git(path, "init")
    (path / "steering.py").write_text("# committed steering\n")
    (path / "card.cmd").write_text("Beams:idA = 11\n")
    git(path, "add", ".")
    git(
        path,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "Test inputs",
    )
    return path.resolve()


@pytest.fixture
def runner(repo, tmp_path, monkeypatch):
    runner = Runner(
        {
            "input_repository": str(repo),
            "output_root": str(tmp_path / "runs"),
            "releases": {},
            "stage_timeout_seconds": 1,
        }
    )
    setup = tmp_path / "setup with spaces.sh"
    setup.write_text(":\n")
    monkeypatch.setattr(
        runner,
        "environment",
        lambda tag: {"release": tag, "setup_script": str(setup), "setup_sha256": sha256(setup)},
    )
    return runner


@pytest.fixture
def run_spec():
    return dict(
        generator="pythia8",
        process_name="ee_mumu",
        nevents=2,
        random_seed=42,
        ecm_gev=91.2,
        cvmfs_release="test-release",
        steering_path="steering.py",
        card_path="card.cmd",
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("random_seed", 0),
        ("random_seed", True),
        ("random_seed", 900000001),
        ("random_seed", 1.5),
        ("nevents", 0),
        ("nevents", -1),
        ("nevents", True),
        ("ecm_gev", float("nan")),
        ("ecm_gev", float("inf")),
        ("ecm_gev", -1),
        ("ecm_gev", True),
        ("process_name", "$(touch injected)"),
        ("process_name", "../outside"),
        ("card_path", "../card.cmd"),
    ],
)
def test_invalid_requests(runner, run_spec, field, value):
    run_spec[field] = value
    with pytest.raises(ValueError):
        runner.prepare(**run_spec)
    assert not list(runner.output.iterdir())


def test_commit_required(repo):
    commit, files = committed_inputs(repo, ["card.cmd"])
    assert len(commit) == 40
    assert files["card.cmd"] == (repo / "card.cmd").read_bytes()
    (repo / "card.cmd").write_text("changed")
    with pytest.raises(ValueError, match="Commit input changes"):
        committed_inputs(repo, ["card.cmd"])
    git(repo, "add", "card.cmd")
    with pytest.raises(ValueError, match="Commit input changes"):
        committed_inputs(repo, ["card.cmd"])


def test_untracked_and_symlink_inputs(repo):
    (repo / "untracked").write_text("data")
    with pytest.raises(ValueError, match="Commit input"):
        committed_inputs(repo, ["untracked"])
    (repo / "link").symlink_to(repo / "card.cmd")
    with pytest.raises(ValueError, match="Symlink"):
        committed_inputs(repo, ["link"])


def test_releases_fail_closed(repo, tmp_path):
    runner = Runner(
        {
            "input_repository": str(repo),
            "output_root": str(tmp_path / "out"),
            "releases": {
                "2025-05-29": {
                    "setup_script": "/cvmfs/missing/2025-05-29/setup.sh",
                    "setup_sha256": "0" * 64,
                }
            },
        }
    )
    for tag in ("latest", "nightlies", "unknown", "../escape"):
        with pytest.raises(ValueError):
            runner.environment(tag)
    with pytest.raises(FileNotFoundError):
        runner.environment("2025-05-29")


@pytest.mark.parametrize(
    "setup_args",
    [
        [],
        ["-r", "2026-04-08"],
        ["-r", "latest"],
        ["-r", "other"],
        ["-r", "2026-04-08", "--extra"],
        "-r 2026-04-08",
    ],
)
def test_release_selector_policy(runner, monkeypatch, setup_args):
    tag = "2026-04-08"
    runner.releases = {
        tag: {
            "setup_script": "/cvmfs/sw.hsf.org/key4hep/setup.sh",
            "setup_args": setup_args,
            "setup_sha256": "a" * 64,
        }
    }
    original_resolve = Path.resolve
    monkeypatch.setattr(
        Path,
        "resolve",
        lambda p, **kw: p if str(p).startswith("/cvmfs/") else original_resolve(p, **kw),
    )
    monkeypatch.setattr(runner_module, "sha256", lambda _: "a" * 64)
    if setup_args == ["-r", tag]:
        assert Runner.environment(runner, tag)["setup_args"] == ["-r", tag]
    else:
        with pytest.raises(ValueError):
            Runner.environment(runner, tag)


async def test_selector_receives_release_without_leaking_stage_arguments(runner, tmp_path):
    directory = tmp_path / "job"
    directory.mkdir()
    setup = tmp_path / "selector.sh"
    setup.write_text(
        '[[ "$#" == 2 && "$1" == -r && "$2" == 2026-04-08 ]] || return 17\necho "release=$2"\n'
    )
    profile = {
        "release": "2026-04-08",
        "setup_script": str(setup),
        "setup_args": ["-r", "2026-04-08"],
        "setup_sha256": sha256(setup),
    }
    await runner.stage(
        directory,
        {"environment": profile, "stages": []},
        "selector",
        [sys.executable, "-c", 'print("stage ran")'],
        {"PATH": "/usr/bin:/bin"},
    )
    assert (directory / "selector.stdout.log").read_text() == "release=2026-04-08\nstage ran\n"


async def test_failed_selector_does_not_execute_stage(runner, tmp_path):
    directory = tmp_path / "job"
    directory.mkdir()
    setup = tmp_path / "selector.sh"
    setup.write_text("return 17\n")
    profile = {
        "release": "2026-04-08",
        "setup_script": str(setup),
        "setup_args": ["-r", "2026-04-08"],
        "setup_sha256": sha256(setup),
    }
    with pytest.raises(RuntimeError, match="exited 17"):
        await runner.stage(
            directory,
            {"environment": profile, "stages": []},
            "selector",
            [sys.executable, "-c", 'print("must not run")'],
            {"PATH": "/usr/bin:/bin"},
        )
    assert not (directory / "selector.stdout.log").read_text()


@pytest.fixture
def sindarin():
    return (Path(__file__).parents[1] / "examples" / "ee_mumu.sin").read_bytes()


def test_whizard_literal_settings(sindarin):
    check_whizard(sindarin, 42, 100, 91.2)
    for content in (
        sindarin + b"\nseed = 43\n",
        sindarin + b'\ninclude("other.sin")\n',
        sindarin.replace(b"seed = 42", b"seed = 0"),
        sindarin.replace(b"n_events = 100", b"n_events = 1000"),
        sindarin.replace(b"91.2 GeV", b"240 GeV"),
    ):
        with pytest.raises(ValueError):
            check_whizard(content, 42, 100, 91.2)


def fake_stages(runner, monkeypatch, fail=None):
    async def stage(directory, manifest, name, args, environment):
        (directory / f"{name}.stdout.log").write_text("test log")
        if name == fail:
            raise RuntimeError("simulated failure")
        if name == "environment":
            write_json(directory / "environment.json", {"test_double": True})
        elif name in ("generation", "conversion"):
            (directory / "events.e4h.root").write_bytes(b"test fixture, not a ROOT file")
            write_json(
                directory / "effective-settings.json",
                {
                    key: manifest["specification"][key]
                    for key in ("generator", "nevents", "random_seed", "ecm_gev")
                },
            )
        elif name == "whizard":
            (directory / "events.hepmc").write_bytes(b"test HepMC fixture")
        elif name == "validation":
            write_json(directory / "validation.json", {"valid": True, "entries": 2})

    monkeypatch.setattr(runner, "stage", stage)


async def test_full_lifecycle_and_tamper_detection(runner, run_spec, monkeypatch):
    fake_stages(runner, monkeypatch)
    first = runner.submit(**run_spec)
    second = runner.submit(**run_spec)
    assert first["job_id"] != second["job_id"]
    await asyncio.gather(*runner.tasks.values())
    for job in (first, second):
        manifest = runner.status(job["job_id"])
        assert manifest["status"] == "SUCCESS"
        assert runner.verify(job["job_id"])["valid"]
    (Path(first["directory"]) / "events.e4h.root").write_bytes(b"corrupted")
    assert not runner.verify(first["job_id"])["valid"]
    assert runner.verify(second["job_id"])["valid"]


async def test_whizard_includes_conversion(runner, run_spec, monkeypatch, sindarin):
    path = runner.repo / "input.sin"
    path.write_bytes(sindarin.replace(b"n_events = 100", b"n_events = 2"))
    git(runner.repo, "add", "input.sin")
    git(
        runner.repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "WHIZARD fixture",
    )
    run_spec.update(generator="whizard", card_path="input.sin")
    fake_stages(runner, monkeypatch)
    stages = []
    original = runner.stage

    async def stage(*args):
        stages.append(args[2])
        await original(*args)

    monkeypatch.setattr(runner, "stage", stage)
    job = runner.submit(**run_spec)
    await runner.tasks[job["job_id"]]
    assert stages == ["environment", "whizard", "conversion", "validation"]
    assert runner.status(job["job_id"])["status"] == "SUCCESS"
    assert "events.hepmc" in runner.status(job["job_id"])["artifacts"]


async def test_live_input_edits_do_not_change_snapshot(runner, run_spec, monkeypatch):
    fake_stages(runner, monkeypatch)
    job = runner.submit(**run_spec)
    (runner.repo / "card.cmd").write_text("edited after submission")
    await runner.tasks[job["job_id"]]
    assert runner.verify(job["job_id"])["valid"]
    assert (Path(job["directory"]) / "inputs" / "card.cmd").read_text() == "Beams:idA = 11\n"


@pytest.mark.parametrize("failure", ["environment", "generation", "validation"])
async def test_failed_stages_preserve_manifest(runner, run_spec, monkeypatch, failure):
    fake_stages(runner, monkeypatch, fail=failure)
    job = runner.submit(**run_spec)
    await runner.tasks[job["job_id"]]
    manifest = runner.status(job["job_id"])
    assert manifest["status"] == "FAILED"
    assert "simulated failure" in manifest["error"]
    assert manifest["git_commit"]
    assert not runner.verify(job["job_id"])["valid"]


async def test_missing_output_cannot_succeed(runner, run_spec, monkeypatch):
    fake_stages(runner, monkeypatch)
    original = runner.stage

    async def stage(*args):
        await original(*args)
        if args[2] == "validation":
            (args[0] / "events.e4h.root").unlink()

    monkeypatch.setattr(runner, "stage", stage)
    job = runner.submit(**run_spec)
    await runner.tasks[job["job_id"]]
    assert runner.status(job["job_id"])["status"] == "FAILED"


async def test_effective_energy_mismatch_fails(runner, run_spec, monkeypatch):
    fake_stages(runner, monkeypatch)
    original = runner.stage

    async def stage(*args):
        await original(*args)
        if args[2] == "generation":
            write_json(args[0] / "effective-settings.json", {"ecm_gev": 240})

    monkeypatch.setattr(runner, "stage", stage)
    job = runner.submit(**run_spec)
    await runner.tasks[job["job_id"]]
    assert runner.status(job["job_id"])["status"] == "FAILED"


async def test_immediate_cancel(runner, run_spec):
    job = runner.submit(**run_spec)
    assert (await runner.cancel(job["job_id"]))["status"] == "CANCELLED"


async def test_stage_argument_safety_and_logs(runner, tmp_path):
    directory = tmp_path / "job"
    directory.mkdir()
    manifest = {"environment": runner.environment("test"), "stages": []}
    payload = 'a b; $(touch injected) " quote'
    await runner.stage(
        directory,
        manifest,
        "safe",
        [sys.executable, "-c", "import sys; print(sys.argv[1])", payload],
        {"PATH": "/usr/bin:/bin"},
    )
    assert (directory / "safe.stdout.log").read_text().strip() == payload
    assert not (directory / "injected").exists()


async def test_timeout_kills_process(runner, tmp_path):
    directory = tmp_path / "job"
    directory.mkdir()
    manifest = {"environment": runner.environment("test"), "stages": []}
    with pytest.raises(TimeoutError):
        await runner.stage(
            directory,
            manifest,
            "slow",
            [sys.executable, "-c", "import time; time.sleep(30)"],
            {"PATH": "/usr/bin:/bin"},
        )
    assert manifest["stages"][0]["returncode"] < 0
    with pytest.raises(ProcessLookupError):
        os.kill(manifest["stages"][0]["pid"], 0)


async def test_running_cancel_preserves_state(runner, run_spec, monkeypatch):
    started = asyncio.Event()
    original = runner.stage

    async def slow_stage(directory, manifest, name, args, environment):
        started.set()
        await original(
            directory,
            manifest,
            name,
            [sys.executable, "-c", "import time; time.sleep(30)"],
            environment,
        )

    monkeypatch.setattr(runner, "stage", slow_stage)
    job = runner.submit(**run_spec)
    await started.wait()
    # Wait for subprocess creation, rather than cancelling the test's fake stage.
    for _ in range(100):
        if runner.status(job["job_id"])["stages"][0].get("pid"):
            break
        await asyncio.sleep(0.01)
    manifest = await runner.cancel(job["job_id"])
    assert manifest["status"] == "CANCELLED"
    assert manifest["stages"][0]["returncode"] < 0
