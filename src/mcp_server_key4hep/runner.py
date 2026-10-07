"""Execution policy lives here, independently of the MCP transport.

Steering scripts are trusted executable code. Git checks provide provenance, not
a sandbox. The server never edits or commits physics inputs during a run.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import __version__


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def relative_file(name: str) -> Path:
    path = Path(name)
    if not name or path.is_absolute() or ".." in path.parts or ".git" in path.parts:
        raise ValueError("Input paths must be repository-relative, without '..' or '.git'")
    return path


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, timeout=30
    ).stdout


def committed_inputs(repo: Path, names: list[str]) -> tuple[str, dict[str, bytes]]:
    """Read exactly HEAD blobs, rejecting modified, staged, untracked or symlink inputs."""
    commit = git(repo, "rev-parse", "HEAD").decode().strip()
    inputs = {}
    for name in sorted(set(names)):
        rel = relative_file(name)
        path = repo / rel
        if any(p.is_symlink() for p in [path, *path.parents] if p != repo.parent):
            raise ValueError(f"Symlink inputs are unsupported: {name}")
        try:
            mode = git(repo, "ls-tree", commit, "--", rel.as_posix()).split()[0]
            if mode not in (b"100644", b"100755"):
                raise ValueError(f"Input must be a regular Git file: {name}")
            data = git(repo, "show", f"{commit}:{rel.as_posix()}")
        except (subprocess.CalledProcessError, IndexError) as exc:
            raise ValueError(f"Commit input before generation: {name}") from exc
        if path.read_bytes() != data or git(repo, "diff", "--cached", commit, "--", name):
            raise ValueError(f"Commit input changes before generation: {name}")
        inputs[rel.as_posix()] = data
    return commit, inputs


def check_whizard(data: bytes, seed: int, nevents: int, energy: float) -> None:
    """Accept only a small, self-contained Sindarin dialect with literal settings.

    Deliberately excludes includes, scripts, control flow, local blocks and extra
    assignments. This prevents a later command from overriding verified settings.
    More advanced generation should get a separately reviewed adapter.
    """
    lines = [line.split("#", 1)[0].strip() for line in data.decode().splitlines()]
    lines = [line for line in lines if line]
    settings = {}
    process = None
    integrated = simulated = False
    for line in lines:
        match = re.fullmatch(r"(seed|n_events|sqrts)\s*=\s*(\d+(?:\.\d+)?)\s*(GeV)?", line)
        if match:
            key, value, unit = match.groups()
            if key in settings or integrated or simulated:
                raise ValueError("WHIZARD settings must occur once, before integrate/simulate")
            if (key == "sqrts") != bool(unit):
                raise ValueError("Use GeV for sqrts and integer seed/n_events")
            if key != "sqrts" and "." in value:
                raise ValueError("seed and n_events must be integers")
            settings[key] = float(value)
        elif line == "model = SM" and not integrated:
            pass
        elif re.fullmatch(
            r'process [A-Za-z][A-Za-z0-9_]* = "[A-Za-z0-9+\-]+", "[A-Za-z0-9+\-]+" => "[A-Za-z0-9+\-]+", "[A-Za-z0-9+\-]+"',
            line,
        ):
            if process or integrated:
                raise ValueError("Exactly one WHIZARD process is supported")
            process = line.split()[1]
        elif line in ('$sample = "events"', "sample_format = hepmc") and not simulated:
            pass
        elif re.fullmatch(r"iterations = \d+:\d+(?:,\s*\d+:\d+)*", line) and not integrated:
            pass
        elif process and line == f"integrate ({process})" and not integrated:
            if set(settings) != {"seed", "n_events", "sqrts"}:
                raise ValueError("Set seed, n_events and sqrts before integration")
            integrated = True
        elif process and line == f"simulate ({process})" and integrated and not simulated:
            simulated = True
        else:
            raise ValueError(f"Unsupported Sindarin statement: {line}")
    if settings != {"seed": seed, "n_events": nevents, "sqrts": energy}:
        raise ValueError("Committed Sindarin seed, event count or energy differs from request")
    if not simulated or '$sample = "events"' not in lines or "sample_format = hepmc" not in lines:
        raise ValueError("Sindarin must integrate, simulate and write events.hepmc")


# Arguments are passed positionally; no caller text is interpolated into shell code.
# Setup output goes into the stage log, never the MCP stdout channel.
SETUP_WRAPPER = (
    'key4hep_setup=$1; key4hep_release=$2; shift 2; key4hep_argv=("$@"); set --; '
    'if [[ -n "$key4hep_release" ]]; then '
    'source "$key4hep_setup" -r "$key4hep_release" || exit $?; '
    'else source "$key4hep_setup" || exit $?; fi; '
    'exec "${key4hep_argv[@]}"'
)
ACTIVE = {"QUEUED", "RUNNING", "VALIDATING"}


class Runner:
    def __init__(self, config: dict):
        self.repo = Path(config["input_repository"]).resolve(strict=True)
        if Path(git(self.repo, "rev-parse", "--show-toplevel").decode().strip()) != self.repo:
            raise ValueError("input_repository must be the Git repository root")
        self.output = Path(config["output_root"]).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.releases = config["releases"]
        self.timeout = int(config.get("stage_timeout_seconds", 3600))
        self.max_events = int(config.get("max_events", 1_000_000))
        if self.timeout <= 0 or self.max_events <= 0:
            raise ValueError("Limits must be positive")
        self.slots = asyncio.Semaphore(int(config.get("max_parallel_jobs", 1)))
        if int(config.get("max_parallel_jobs", 1)) < 1:
            raise ValueError("max_parallel_jobs must be positive")
        self.tasks: dict[str, asyncio.Task] = {}

    def environment(self, release: str) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", release):
            raise ValueError("Invalid release tag")
        if any(word in release.lower() for word in ("latest", "nightly", "nightlies")):
            raise ValueError("Floating releases are forbidden")
        if release not in self.releases:
            raise ValueError(f"Release is not configured: {release}")
        profile = self.releases[release]
        setup_args = profile.get("setup_args", [])
        if setup_args not in ([], ["-r", release]):
            raise ValueError("setup_args must be omitted/empty or exactly ['-r', declared release]")
        path = Path(profile["setup_script"])
        if not path.is_absolute() or not str(path).startswith("/cvmfs/"):
            raise ValueError("Setup must be an explicit absolute CVMFS path")
        resolved = path.resolve(strict=True)
        if not setup_args and release not in path.parts and release not in resolved.parts:
            raise ValueError("Setup must identify the declared release in its path or with -r")
        if not str(resolved).startswith("/cvmfs/"):
            raise ValueError("Resolved setup must remain within CVMFS")
        if any(
            word in str(path).lower() or word in str(resolved).lower()
            for word in ("latest", "nightly", "nightlies")
        ):
            raise ValueError("Floating setup paths are forbidden")
        expected = profile["setup_sha256"]
        if not re.fullmatch(r"[0-9a-f]{64}", expected) or sha256(resolved) != expected:
            raise ValueError("Pinned setup script checksum mismatch")
        return {
            "release": release,
            "setup_script": str(path),
            "setup_resolved_path": str(resolved),
            "setup_args": setup_args,
            "setup_sha256": expected,
        }

    def prepare(
        self,
        generator: str,
        process_name: str,
        nevents: int,
        random_seed: int,
        ecm_gev: float,
        cvmfs_release: str,
        steering_path: str,
        card_path: str,
        extra_inputs: list[str] | None = None,
    ) -> dict:
        if generator not in ("pythia8", "whizard"):
            raise ValueError("Unsupported generator")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", process_name):
            raise ValueError("Use a simple process label (letters, digits, '.', '_' or '-')")
        if type(random_seed) is not int or not 1 <= random_seed <= 900_000_000:
            raise ValueError("random_seed must be an integer from 1 to 900000000")
        if type(nevents) is not int or not 1 <= nevents <= self.max_events:
            raise ValueError(f"nevents must be an integer from 1 to {self.max_events}")
        if type(ecm_gev) not in (int, float) or not math.isfinite(ecm_gev) or ecm_gev <= 0:
            raise ValueError("ecm_gev must be finite and positive")
        environment = self.environment(cvmfs_release)
        commit, inputs = committed_inputs(
            self.repo, [steering_path, card_path, *(extra_inputs or [])]
        )
        if generator == "whizard":
            check_whizard(
                inputs[relative_file(card_path).as_posix()], random_seed, nevents, ecm_gev
            )
        specification = dict(
            generator=generator,
            process_name=process_name,
            nevents=nevents,
            random_seed=random_seed,
            ecm_gev=ecm_gev,
            cvmfs_release=cvmfs_release,
            steering_path=steering_path,
            card_path=card_path,
        )
        hashes = {name: hashlib.sha256(data).hexdigest() for name, data in inputs.items()}
        identity = dict(
            specification=specification,
            environment=environment,
            input_sha256=hashes,
            git_commit=commit,
        )
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        return dict(**identity, configuration_sha256=digest, _inputs=inputs)

    def submit(self, **kwargs) -> dict:
        if sum(not task.done() for task in self.tasks.values()) >= 32:
            raise ValueError("Job queue is full (32 active/queued jobs)")
        prepared = self.prepare(**kwargs)
        inputs = prepared.pop("_inputs")
        job_id = uuid.uuid4().hex
        directory = self.output / job_id
        directory.mkdir(mode=0o700)
        for name, data in inputs.items():
            path = directory / "inputs" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o444)
        manifest = dict(
            schema_version=1,
            server_version=__version__,
            job_id=job_id,
            status="QUEUED",
            created_at=now(),
            input_repository=str(self.repo),
            **prepared,
            stages=[],
            artifacts={},
        )
        (directory / "execution.log").write_text(
            f"Git commit: {manifest['git_commit']}\n"
            f"Configuration SHA-256: {manifest['configuration_sha256']}\n"
            f"Release: {manifest['environment']['release']}\n"
        )
        write_json(directory / "provenance.json", manifest)
        self.tasks[job_id] = asyncio.create_task(self._execute(directory, manifest))
        return {"job_id": job_id, "status": "QUEUED", "directory": str(directory)}

    def directory(self, job_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise ValueError("Invalid job ID")
        path = self.output / job_id
        if path.is_symlink() or not path.is_dir():
            raise ValueError("Unknown job ID")
        return path

    def status(self, job_id: str) -> dict:
        path = self.directory(job_id) / "provenance.json"
        manifest = json.loads(path.read_text())
        if manifest["status"] in ACTIVE and job_id not in self.tasks:
            # Never resume automatically after a server crash; old workers may still exist.
            manifest["status"] = "INTERRUPTED"
            manifest["error"] = "Server restarted; inspect worker processes before retrying"
            write_json(path, manifest)
        return manifest

    async def cancel(self, job_id: str) -> dict:
        self.directory(job_id)
        task = self.tasks.get(job_id)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            # Cancellation can arrive before the coroutine's first instruction.
            path = self.directory(job_id) / "provenance.json"
            manifest = json.loads(path.read_text())
            if manifest["status"] in ACTIVE:
                manifest.update(status="CANCELLED", finished_at=now())
                write_json(path, manifest)
        return self.status(job_id)

    async def close(self):
        for job_id in list(self.tasks):
            await self.cancel(job_id)

    async def stage(
        self,
        directory: Path,
        manifest: dict,
        name: str,
        args: list[str],
        environment: dict[str, str],
    ) -> None:
        profile = manifest["environment"]
        if sha256(Path(profile["setup_script"])) != profile["setup_sha256"]:
            raise ValueError("Setup script changed since submission")
        command = [
            "/bin/bash",
            "--noprofile",
            "--norc",
            "-c",
            SETUP_WRAPPER,
            "key4hep-setup",
            profile["setup_script"],
            profile["release"] if profile.get("setup_args") else "",
            *args,
        ]
        record = {
            "name": name,
            "argv": command,
            "started_at": now(),
            "stdout": f"{name}.stdout.log",
            "stderr": f"{name}.stderr.log",
        }
        manifest["stages"].append(record)
        write_json(directory / "provenance.json", manifest)
        with (
            (directory / record["stdout"]).open("wb") as out,
            (directory / record["stderr"]).open("wb") as err,
        ):
            creation = asyncio.create_task(
                asyncio.create_subprocess_exec(
                    *command,
                    cwd=directory,
                    env=environment,
                    stdout=out,
                    stderr=err,
                    start_new_session=True,
                )
            )
            try:
                proc = await asyncio.shield(creation)
            except asyncio.CancelledError:
                # A cancellation while the OS is spawning must not orphan the child.
                proc = await creation
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await proc.wait()
                record.update(pid=proc.pid, returncode=proc.returncode, finished_at=now())
                write_json(directory / "provenance.json", manifest)
                raise
            record["pid"] = proc.pid
            write_json(directory / "provenance.json", manifest)
            try:
                await asyncio.wait_for(proc.wait(), timeout=self.timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                # Kill the process group, including children started by generators.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await proc.wait()
                raise
            finally:
                record.update(returncode=proc.returncode, finished_at=now())
                write_json(directory / "provenance.json", manifest)
        if proc.returncode:
            raise RuntimeError(f"Stage {name} exited {proc.returncode}; see {name}.stderr.log")

    async def _execute(self, directory: Path, manifest: dict) -> None:
        try:
            async with self.slots:
                manifest.update(status="RUNNING", started_at=now())
                write_json(directory / "provenance.json", manifest)
                spec = manifest["specification"]
                request = dict(
                    **spec,
                    card_file=str(directory / "inputs" / spec["card_path"]),
                    output_file=str(directory / "events.e4h.root"),
                    hepmc_file=str(directory / "events.hepmc"),
                    effective_settings_file=str(directory / "effective-settings.json"),
                )
                write_json(directory / "request.json", request)
                # Do not inherit PYTHONPATH, LD_LIBRARY_PATH, BASH_ENV or a previously
                # sourced stack. Each stage loads only the configured environment.
                home = directory / "home"
                home.mkdir()
                environment = {
                    "PATH": "/usr/bin:/bin",
                    "HOME": str(home),
                    "LANG": "C",
                    "LC_ALL": "C",
                    "PYTHONHASHSEED": "0",
                    "OMP_NUM_THREADS": "1",
                    "OPENBLAS_NUM_THREADS": "1",
                    "MKL_NUM_THREADS": "1",
                    "KEY4HEP_RUN_CONFIG": str(directory / "request.json"),
                }
                manifest["execution_environment"] = environment
                manifest["runner_sha256"] = sha256(Path(__file__))
                probe = Path(__file__).with_name("probe.py")
                validator = Path(__file__).with_name("validator.py")
                # Archive the exact helper sources used for this execution.
                for source in (probe, validator):
                    (directory / source.name).write_bytes(source.read_bytes())
                await self.stage(
                    directory, manifest, "environment", ["python3", "probe.py"], environment
                )
                manifest["resolved_software"] = json.loads(
                    (directory / "environment.json").read_text()
                )
                if spec["generator"] == "whizard":
                    await self.stage(
                        directory,
                        manifest,
                        "whizard",
                        ["whizard", request["card_file"]],
                        environment,
                    )
                    if not (directory / "events.hepmc").is_file():
                        raise ValueError("WHIZARD did not produce events.hepmc")
                await self.stage(
                    directory,
                    manifest,
                    "generation" if spec["generator"] == "pythia8" else "conversion",
                    ["k4run", str(directory / "inputs" / spec["steering_path"])],
                    environment,
                )
                effective = json.loads((directory / "effective-settings.json").read_text())
                for key in ("generator", "random_seed", "nevents", "ecm_gev"):
                    if effective.get(key) != spec[key]:
                        raise ValueError(f"Effective steering setting differs from request: {key}")
                manifest["effective_settings"] = effective
                manifest["status"] = "VALIDATING"
                write_json(directory / "provenance.json", manifest)
                await self.stage(
                    directory,
                    manifest,
                    "validation",
                    [
                        "python3",
                        "validator.py",
                        "events.e4h.root",
                        str(spec["nevents"]),
                        "validation.json",
                    ],
                    environment,
                )
                validation = json.loads((directory / "validation.json").read_text())
                if (
                    validation.get("valid") is not True
                    or validation.get("entries") != spec["nevents"]
                ):
                    raise ValueError("EDM4hep validation failed")
                manifest["validation"] = validation
                for name, expected in manifest["input_sha256"].items():
                    if sha256(directory / "inputs" / name) != expected:
                        raise ValueError(f"Input changed during execution: {name}")
                manifest.update(status="SUCCESS", finished_at=now())
        except asyncio.CancelledError:
            manifest.update(status="CANCELLED", finished_at=now())
        except Exception as exc:
            manifest.update(
                status="FAILED", error=f"{type(exc).__name__}: {exc}", finished_at=now()
            )
        finally:
            # Preserve and checksum logs and partial artifacts on failure as well.
            for path in directory.iterdir():
                if (
                    path.is_file()
                    and not path.is_symlink()
                    and path.name not in ("provenance.json", "provenance.tmp")
                ):
                    manifest["artifacts"][path.name] = {
                        "sha256": sha256(path),
                        "bytes": path.stat().st_size,
                    }
            write_json(directory / "provenance.json", manifest)
            if manifest["status"] == "SUCCESS":
                result = self.verify(manifest["job_id"])
                if not result["valid"]:
                    manifest.update(
                        status="FAILED", error="Manifest verification failed", verification=result
                    )
                    write_json(directory / "provenance.json", manifest)

    def verify(self, job_id: str) -> dict:
        directory = self.directory(job_id)
        manifest = json.loads((directory / "provenance.json").read_text())
        errors = []
        if manifest.get("schema_version") != 1 or manifest.get("job_id") != job_id:
            errors.append("Manifest identity/schema mismatch")
        if manifest.get("status") != "SUCCESS":
            errors.append("Run is not successful")
        required = {
            "events.e4h.root",
            "request.json",
            "effective-settings.json",
            "validation.json",
            "environment.json",
            "probe.py",
            "validator.py",
        }
        if not required <= manifest.get("artifacts", {}).keys():
            errors.append("Required artifacts are missing from manifest")
        for name, record in manifest.get("artifacts", {}).items():
            path = directory / relative_file(name)
            if (
                path.is_symlink()
                or not path.is_file()
                or sha256(path) != record["sha256"]
                or path.stat().st_size != record["bytes"]
            ):
                errors.append(f"Artifact missing or changed: {name}")
        for name, expected in manifest.get("input_sha256", {}).items():
            path = directory / "inputs" / relative_file(name)
            if path.is_symlink() or not path.is_file() or sha256(path) != expected:
                errors.append(f"Input missing or changed: {name}")
        identity = {
            key: manifest[key]
            for key in ("specification", "environment", "input_sha256", "git_commit")
        }
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        if digest != manifest.get("configuration_sha256"):
            errors.append("Configuration digest mismatch")
        if manifest.get("validation", {}).get("valid") is not True:
            errors.append("Missing successful validation")
        if manifest.get("validation", {}).get("entries") != manifest["specification"]["nevents"]:
            errors.append("Validation count differs from request")
        for name, field in (
            ("validation.json", "validation"),
            ("effective-settings.json", "effective_settings"),
            ("environment.json", "resolved_software"),
        ):
            path = directory / name
            if path.is_file() and json.loads(path.read_text()) != manifest.get(field):
                errors.append(f"Manifest differs from {name}")
        return {
            "valid": not errors,
            "errors": errors,
            "manifest": str(directory / "provenance.json"),
        }
