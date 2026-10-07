"""stdio MCP interface. No logs or subprocess output are printed to stdout."""

import argparse
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .runner import Runner

Seed = Annotated[int, Field(strict=True, ge=1, le=900_000_000)]
EventCount = Annotated[int, Field(strict=True, ge=1)]
Energy = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]


def create_server(runner: Runner) -> FastMCP:
    @asynccontextmanager
    async def lifespan(_server):
        try:
            yield {}
        finally:
            await runner.close()

    mcp = FastMCP(
        "mcp-server-key4hep",
        lifespan=lifespan,
        instructions="Use only explicit seeds and configured pinned releases. "
        "Commit steering/card changes before submission. Jobs return immediately; "
        "check get_job_status and verify_provenance before using output.",
    )

    @mcp.tool()
    async def list_releases() -> dict:
        """List operator-configured release tags; availability is checked at submission."""
        return {"releases": sorted(runner.releases)}

    @mcp.tool()
    async def run_pythia8_generation(
        process_name: str,
        nevents: EventCount,
        random_seed: Seed,
        ecm_gev: Energy,
        cvmfs_release: str,
        steering_path: str,
        cmd_card_path: str,
        extra_inputs: list[str] | None = None,
    ) -> dict:
        """Submit Pythia with committed repository-relative inputs. Steering must obey
        the KEY4HEP_RUN_CONFIG contract; use the included pythia.py example.
        No input files are edited or committed by this tool.
        """
        return runner.submit(
            generator="pythia8",
            process_name=process_name,
            nevents=nevents,
            random_seed=random_seed,
            ecm_gev=ecm_gev,
            cvmfs_release=cvmfs_release,
            steering_path=steering_path,
            card_path=cmd_card_path,
            extra_inputs=extra_inputs,
        )

    @mcp.tool()
    async def run_whizard_generation(
        process_name: str,
        nevents: EventCount,
        random_seed: Seed,
        ecm_gev: Energy,
        cvmfs_release: str,
        sindarin_file_path: str,
        converter_path: str,
        extra_inputs: list[str] | None = None,
    ) -> dict:
        """Submit a self-contained, committed basic SM two-to-two Sindarin script,
        then a committed k4run HepMC-to-EDM4hep converter. Literal seed, n_events,
        sqrts must match the request. See README for the supported Sindarin dialect.
        """
        return runner.submit(
            generator="whizard",
            process_name=process_name,
            nevents=nevents,
            random_seed=random_seed,
            ecm_gev=ecm_gev,
            cvmfs_release=cvmfs_release,
            steering_path=converter_path,
            card_path=sindarin_file_path,
            extra_inputs=extra_inputs,
        )

    @mcp.tool()
    async def validate_steering_config(
        generator: Literal["pythia8", "whizard"],
        process_name: str,
        nevents: EventCount,
        random_seed: Seed,
        ecm_gev: Energy,
        cvmfs_release: str,
        steering_path: str,
        card_path: str,
        extra_inputs: list[str] | None = None,
    ) -> dict:
        """Preflight release, committed inputs and parameters without running generation.
        Does not assert physics correctness or compatibility with the chosen stack.
        """
        result = runner.prepare(
            generator=generator,
            process_name=process_name,
            nevents=nevents,
            random_seed=random_seed,
            ecm_gev=ecm_gev,
            cvmfs_release=cvmfs_release,
            steering_path=steering_path,
            card_path=card_path,
            extra_inputs=extra_inputs,
        )
        result.pop("_inputs")
        return {"valid": True, **result}

    @mcp.tool()
    async def get_job_status(job_id: str) -> dict:
        """Read persisted status, provenance, stage commands and log paths."""
        return runner.status(job_id)

    @mcp.tool()
    async def cancel_job(job_id: str) -> dict:
        """Cancel a queued/running job and terminate its active process group."""
        return await runner.cancel(job_id)

    @mcp.tool()
    async def verify_provenance(job_id: str) -> dict:
        """Re-read provenance.json and verify archived inputs and artifact hashes."""
        return runner.verify(job_id)

    @mcp.tool()
    async def validate_edm4hep_file(job_id: str) -> dict:
        """Return the full-read podio validation report for a completed job, after
        checking its provenance and artifact hashes. Validation runs automatically
        before generation can succeed; this tool does not accept arbitrary paths.
        """
        verification = runner.verify(job_id)
        if not verification["valid"]:
            return verification
        return {
            "valid": True,
            "validation": runner.status(job_id)["validation"],
            "provenance": verification,
        }

    return mcp


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Operator-owned JSON config with repo, output root and pinned releases",
    )
    args = parser.parse_args()
    runner = Runner(json.loads(args.config.read_text()))
    create_server(runner).run(transport="stdio")


if __name__ == "__main__":
    main()
