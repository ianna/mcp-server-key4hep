"""Run a small Pythia sample through a real stdio MCP client connection."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def call(session, name, arguments):
    result = await session.call_tool(name, arguments)
    if result.isError:
        message = "\n".join(item.text for item in result.content if hasattr(item, "text"))
        raise RuntimeError(f"{name}: {message}")
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(next(item.text for item in result.content if hasattr(item, "text")))


async def exercise(session, spec, timeout, poll_seconds=1, completed_jobs=None):
    """Keep the connection alive until completion, cancelling on timeout or interruption."""
    preflight = {**spec, "generator": "pythia8", "card_path": spec["cmd_card_path"]}
    del preflight["cmd_card_path"]
    await call(session, "validate_steering_config", preflight)
    job = await call(session, "run_pythia8_generation", spec)
    job_id = job["job_id"]
    print(f"Job: {job_id}\nDirectory: {job['directory']}", flush=True)
    terminal = False
    try:
        async with asyncio.timeout(timeout):
            previous = None
            while True:
                status = await call(session, "get_job_status", {"job_id": job_id})
                state = status["status"]
                if state != previous:
                    print(state, flush=True)
                    previous = state
                if state not in {"QUEUED", "RUNNING", "VALIDATING"}:
                    terminal = True
                    break
                await asyncio.sleep(poll_seconds)
            if state != "SUCCESS":
                print(json.dumps(status, indent=2), file=sys.stderr)
                print(
                    f"Inspect stage *.stdout.log and *.stderr.log in {job['directory']}",
                    file=sys.stderr,
                )
                return 1
            verification = await call(session, "verify_provenance", {"job_id": job_id})
            if verification.get("valid") is not True:
                raise RuntimeError(f"Provenance verification failed: {verification}")
            validation = await call(session, "validate_edm4hep_file", {"job_id": job_id})
            if validation.get("valid") is not True:
                raise RuntimeError(f"EDM4hep validation failed: {validation}")
            print(json.dumps(validation, indent=2))
            print(f"Output: {Path(job['directory']) / 'events.e4h.root'}")
            print(f"Provenance: {Path(job['directory']) / 'provenance.json'}")
            if completed_jobs is not None:
                completed_jobs.append(job_id)
            return 0
    finally:
        if not terminal:
            await call(session, "cancel_job", {"job_id": job_id})


async def replay(session, spec, timeout, poll_seconds=1):
    """Run the identical request twice, then compare verified content through MCP."""
    jobs = []
    for attempt in (1, 2):
        print(f"Replay run {attempt}/2", flush=True)
        code = await exercise(session, dict(spec), timeout, poll_seconds, completed_jobs=jobs)
        if code:
            return code
    async with asyncio.timeout(timeout):
        comparison = await call(
            session, "compare_event_content", {"left_job_id": jobs[0], "right_job_id": jobs[1]}
        )
    print(json.dumps(comparison, indent=2))
    return 0 if comparison.get("valid") is True and comparison.get("identical") is True else 1


async def run(args):
    config = args.config.resolve(strict=True)
    spec = {
        "process_name": "ee_mumu_smoke",
        "nevents": args.events,
        "random_seed": args.seed,
        "ecm_gev": 91.2,
        "cvmfs_release": args.release,
        "steering_path": "examples/pythia.py",
        "cmd_card_path": "examples/ee_mumu.cmd",
    }
    server = StdioServerParameters(
        command=sys.executable,
        args=["-I", "-m", "mcp_server_key4hep.server", "--config", str(config)],
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            if args.replay:
                return await replay(session, spec, args.timeout)
            return await exercise(session, spec, args.timeout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--release", required=True, help="Explicit configured Key4hep release")
    parser.add_argument("--seed", required=True, type=int, help="Explicit Pythia random seed")
    parser.add_argument("--events", default=10, type=int, help="Small sample size (1–100)")
    parser.add_argument("--timeout", default=300, type=int, help="Job timeout in seconds")
    parser.add_argument(
        "--replay",
        action="store_true",
        help="Generate two identical requests and compare their event content",
    )
    args = parser.parse_args()
    if not 1 <= args.seed <= 900_000_000:
        parser.error("--seed must be between 1 and 900000000")
    if not 1 <= args.events <= 100:
        parser.error("--events must be between 1 and 100 for this smoke test")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Interrupted; server shutdown cancels active jobs.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Smoke test failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
