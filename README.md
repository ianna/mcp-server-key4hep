# Key4hep provenance and validation

Reusable tools around the existing FCC/Key4hep workflow. Generate events with
`k4run` and the FCC-config cards as usual; use this package to archive committed
inputs, record an output manifest, validate EDM4hep structure, and compare event
content. It does not provide a generator launcher or a process-card catalogue.
MCP is an optional, read-only interface to the same inspection tools.

## Install

Python 3.11+ is required. The core has no pip dependencies:

```bash
uv sync --frozen
.venv/bin/python -I -m mcp_server_key4hep.cli --help
.venv/bin/python -I -m pytest -q
```

`prepare` and `verify` need only Python and Git (Git is needed for preparation).
`record`, `validate` and `compare` need ROOT, podio and EDM4hep from your selected
Key4hep environment. On LXPLUS, use a **fresh shell** and an explicit release:

```bash
source /cvmfs/sw.hsf.org/key4hep/setup.sh -r 2026-04-08
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
python3 -m mcp_server_key4hep.cli --help
```

Use stack Python for ROOT operations. `-I` deliberately ignores `PYTHONPATH` and
is appropriate for isolated local tests, not this source-tree/stack invocation.
Do not layer a second release over an already configured shell.

## Inspect files produced by your existing workflow

No server, job ID or provenance manifest is required:

```bash
python3 -m mcp_server_key4hep.cli validate events.e4h.root --events 10 --report validation.json
python3 -m mcp_server_key4hep.cli compare left.e4h.root right.e4h.root --report comparison.json
```

Reports are created exclusively; existing files are never overwritten. CLI exit
codes are 0 for success/match, 1 for an error or failed validation, and 2 for a
completed comparison finding different content.

Validation reads every collection, checks event counts, and checks MCParticles
finite momentum/mass/charge and parent/daughter references. It is structural
validation, not a physics-quality assessment or comprehensive schema validation.

Comparison v2 covers ordered `MCParticles`, optional `MCParticlesStable` (cloned
particles or subset membership), and optional `EventHeader`. Particle fields and
relations are compared exactly; raw collection IDs are normalized to collection
names and object indices. Finite floats use `float.hex`; signed zero and ordering
are significant. Unknown collections fail rather than being silently excluded.
ROOT storage metadata, file-level metadata and frame parameters are excluded.
A match does not establish matching input configurations or physics correctness.
The v2 hashes are intentionally distinct from historical v1 hashes.

## Portable provenance for external runs

1. Write a JSON specification with `release`, explicit named `seeds`, the exact
   `command` argument list, and `inputs` containing `repository` and relative
   `path`. Inputs may come from multiple Git repositories.
2. Run `prepare` **before** executing the normal generator command. It checks
   committed input bytes, records commits and hashes, and archives copies.
3. Execute your usual command yourself; preserve stdout, stderr and environment
   information. The package does not execute the specification.
4. Run `record` to validate the output and write `provenance.json` beside it.
5. Run `verify` to check archived input and artifact hashes. Verification works
   after moving the whole run directory and without the original repositories.

```bash
python3 -m mcp_server_key4hep.cli prepare /absolute/run --spec spec.json
# Run your normal, explicitly seeded command in /absolute/run here.
python3 -m mcp_server_key4hep.cli record /absolute/run \
  --output events.e4h.root --events 10 --exit-code 0 \
  --artifact generation.stdout.log --artifact generation.stderr.log
python3 -m mcp_server_key4hep.cli verify /absolute/run/provenance.json
```

`record` rejects changed inputs or snapshots, failed declared exit codes, and
outputs that change during validation. A failed external command leaves the
pending manifest and logs for diagnosis. Use a fresh directory for another run.

The command, seeds, release and exit code are **operator declarations**. These
utilities cannot prove that an external command actually used them. Hashes detect
changes relative to the manifest; an unsigned manifest is not tamper-proof or an
execution attestation. The snapshots cover only listed inputs, so list all files
that can influence the run. Review actual logs and effective settings as well.

## Demonstration using FCC-config

See [the FCC tutorial demonstration](examples/fcc-config/README.md). It uses
pinned FCC-config and k4Gen commits, the existing dimuon card, and normal `k4run`.
Only explicit Pythia and Gaudi seeds are added through a small options file.
The upstream energy, decay settings, vertex smearing and collections are retained.
No MCP server is involved.

**Status:** unit tests cover the portable tooling and canonicalization. This
upstream-based demonstration still needs execution on LXPLUS with the selected
release. The [historical replay](docs/validation/lxplus-pythia-replay.md) used the
removed custom example and is not evidence that this demonstration passes.

## Optional MCP interface

Install the extra only when an MCP client needs it:

```bash
uv sync --frozen --extra mcp
```

In a pinned stack environment, run the installed package using a Python that can
import both the optional MCP dependency and the stack modules:

```bash
python3 -m mcp_server_key4hep.server --root /absolute/path/to/run-archive
```

The stdio server exposes only `verify_provenance`, `validate_edm4hep_file` and
`compare_event_content`. Paths are confined to `--root`; ROOT inspection runs in
a subprocess so native stdout cannot corrupt MCP framing. There are no tools for
generation, shell execution, job scheduling, cancellation or writing manifests.
Preparation and recording remain explicit CLI/library operations.

For a stack-derived venv on LXPLUS, install with `uv sync --frozen --extra mcp
--python "$(command -v python3)"`, then launch `.venv/bin/python -m
mcp_server_key4hep.server ...` in that same pinned environment. Unlike isolated
tests, stack inspection needs its configured module search paths.

## Migration from 0.1

Version 0.2 removes `Runner`, the smoke launcher, custom generator examples,
operator release/job configuration, and generation MCP tools. Existing ROOT files
can be inspected directly. Old job manifests remain historical records; the new
portable verifier accepts `key4hep-external-run-v1`, not the old runner schema.
Use the old release to verify old manifests; do not rewrite them as new evidence.
The package/repository name is retained for continuity; MCP is no longer required.
