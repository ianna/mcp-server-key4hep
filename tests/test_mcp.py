import json
import subprocess
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def test_real_stdio_transport(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    config = tmp_path / "server.json"
    config.write_text(
        json.dumps(
            {"input_repository": str(repo), "output_root": str(tmp_path / "runs"), "releases": {}}
        )
    )
    server = StdioServerParameters(
        command=sys.executable, args=["-m", "mcp_server_key4hep.server", "--config", str(config)]
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            assert len(tools) == 8
            assert {"random_seed", "cvmfs_release", "ecm_gev"} <= set(
                tools["run_pythia8_generation"].inputSchema["required"]
            )
            releases = await session.call_tool("list_releases", {})
            assert not releases.isError
            assert json.loads(releases.content[0].text) == {"releases": []}
            for seed in (0, True, "42"):
                result = await session.call_tool(
                    "run_pythia8_generation",
                    {
                        "process_name": "test",
                        "nevents": 1,
                        "random_seed": seed,
                        "ecm_gev": 91.2,
                        "cvmfs_release": "missing",
                        "steering_path": "x.py",
                        "cmd_card_path": "x.cmd",
                    },
                )
                assert result.isError
                assert "random_seed" in result.content[0].text
            assert list((tmp_path / "runs").iterdir()) == []
