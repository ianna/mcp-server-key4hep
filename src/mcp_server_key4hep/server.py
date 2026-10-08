"""Optional read-only stdio MCP adapter. No generation or shell execution tools."""

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from .provenance import verify


def create_server(root):
    from mcp.server.fastmcp import FastMCP

    root = Path(root).resolve(strict=True)
    server = FastMCP(
        "key4hep-validation",
        instructions=(
            "Inspect existing files only. Content identity is scoped, not physics validation. "
            "Provenance verification checks hashes, not execution attestation."
        ),
    )
    lock = asyncio.Lock()

    def resolve(path):
        target = (root / path).resolve(strict=True)
        target.relative_to(root)
        if not target.is_file():
            raise ValueError("Expected a file inside the configured root")
        return target

    async def inspect(operation, *args):
        # ROOT can write native diagnostics to stdout. Isolate it from MCP framing.
        async with lock:
            with tempfile.TemporaryDirectory(prefix="key4hep-check-") as directory:
                report = Path(directory) / "report.json"
                command = [
                    sys.executable,
                    "-m",
                    "mcp_server_key4hep.cli",
                    operation,
                    *map(str, args),
                    "--report",
                    str(report),
                ]
                proc = await asyncio.to_thread(
                    subprocess.run, command, capture_output=True, text=True, timeout=300
                )
                if report.exists():
                    return json.loads(report.read_text())
                raise ValueError(f"Inspection failed: {proc.stderr[-2000:]} {proc.stdout[-2000:]}")

    @server.tool()
    async def verify_provenance(manifest: str) -> dict:
        """Verify a portable provenance.json and its archived artifacts."""
        return verify(resolve(manifest))

    @server.tool()
    async def validate_edm4hep_file(path: str, expected_events: int) -> dict:
        """Read an existing EDM4hep file and check MCParticle structure and relations."""
        return await inspect("validate", resolve(path), "--events", expected_events)

    @server.tool()
    async def compare_event_content(left: str, right: str) -> dict:
        """Compare exact ordered supported event content, independently of run manifests.
        A match does not establish that configurations match. Inspect valid and identical.
        """
        return await inspect("compare", resolve(left), resolve(right))

    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, required=True, help="Only files under this directory may be inspected"
    )
    args = parser.parse_args()
    create_server(args.root).run(transport="stdio")


if __name__ == "__main__":
    main()
