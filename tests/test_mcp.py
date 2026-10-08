"""The optional adapter exposes inspection only and confines file access."""

import json

import pytest

pytest.importorskip("mcp")
from mcp_server_key4hep.server import create_server  # noqa: E402


async def test_tools_are_inspection_only(tmp_path):
    server = create_server(tmp_path)
    names = {tool.name for tool in await server.list_tools()}
    assert names == {"verify_provenance", "validate_edm4hep_file", "compare_event_content"}


async def test_outside_root_and_symlinks_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    (root / "link.json").symlink_to(outside)
    server = create_server(root)
    for path in ("../outside.json", "link.json", str(outside)):
        with pytest.raises(Exception, match="(subpath|relative|root)"):
            await server.call_tool("verify_provenance", {"manifest": path})


async def test_verify_works_without_root_modules(tmp_path):
    (tmp_path / "provenance.json").write_text("{}")
    server = create_server(tmp_path)
    result = await server.call_tool("verify_provenance", {"manifest": "provenance.json"})
    # FastMCP returns content plus structured output for a dictionary result.
    content = result[0] if isinstance(result, tuple) else result
    assert json.loads(content[0].text)["valid"] is False


async def test_real_stdio_handshake(tmp_path):
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-I", "-m", "mcp_server_key4hep.server", "--root", str(tmp_path)],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert len(tools.tools) == 3
            (tmp_path / "manifest.json").write_text("{}")
            result = await session.call_tool("verify_provenance", {"manifest": "manifest.json"})
            assert not result.isError
            assert json.loads(result.content[0].text)["valid"] is False
