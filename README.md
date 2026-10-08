# mcp-server-key4hep

A standalone stdio MCP server for Pythia8 generation, basic WHIZARD generation
with HepMC3 conversion, and EDM4hep validation. It uses the official Python MCP
SDK's `mcp.server.fastmcp`, not the separately distributed `fastmcp` package.

The runner enforces an explicit seed, an operator-configured pinned CVMFS setup,
committed physics inputs, and a verified `provenance.json` next to
`events.e4h.root`. Success requires a full podio read of the expected events.

**Status: early prototype.** A real Pythia smoke run passed on CERN LXPLUS with
Key4hep release `2026-04-08` on 7 October 2026. The run generated 10 dimuon events
at 91.2 GeV with seed 42, containing 131 MCParticles. The full-read EDM4hep
structural validation passed, and provenance verification reported no errors.

An independent two-run Pythia replay on the same release also passed: both
10-event samples produced the same canonical event-content SHA-256, with zero
differing events. See the [LXPLUS replay validation record](docs/validation/lxplus-pythia-replay.md)
for the reported result, comparison scope, and reproduction command.

WHIZARD generation/conversion has **not yet been tested on LXPLUS**. Its tests
use controlled substitutes for the physics stages. The automated suite covers
the runner, MCP interface, event comparison, and validation decisions; these
tests are separate from the real Pythia smoke and replay runs.

The smoke and replay results establish structurally valid output and exact
event-content agreement for this tested pair within the comparator's scope.
Reproducibility for other samples or environments, physics accuracy, and
statistical agreement have **not** been established.
The included adapters target modern k4Gen with `pythiaExtraSettings` and
k4FWCore `IOSvc`; qualify your chosen release before production use.

## Install and configure

Use Python 3.11+ for the server. The selected Key4hep stack supplies the Python
used inside generation/validation subprocesses. Run on a Linux host with CVMFS
and Git; the server does not provision CVMFS, SSH workers or containers.

```sh
git clone https://github.com/ianna/mcp-server-key4hep.git
cd mcp-server-key4hep
uv sync --frozen
cp examples/server-config.json /your/config/key4hep-server.json
```

Edit the copied configuration:

- `input_repository`: absolute Git **root**, containing all steering/cards.
- `output_root`: writable run storage, preferably outside the source repository.
- `releases`: explicit tag → setup script, optional `setup_args`, and script SHA-256.
- `stage_timeout_seconds`, `max_events`, `max_parallel_jobs`: execution limits.

For the standard Key4hep selector, use the following release profile (with the
actual SHA-256 computed on your host):

```json
"2026-04-08": {
  "setup_script": "/cvmfs/sw.hsf.org/key4hep/setup.sh",
  "setup_args": ["-r", "2026-04-08"],
  "setup_sha256": "REPLACE_WITH_SHA256_OF_SETUP_SCRIPT"
}
```

```sh
sha256sum /cvmfs/sw.hsf.org/key4hep/setup.sh
```

The only accepted nonempty `setup_args` are exactly `["-r", declared_release]`.
Alternatively, omit `setup_args` when using a release-specific setup path that
contains the declared tag as a path component. Floating tags and paths, setup
paths outside CVMFS, missing scripts, and checksum changes are rejected. The
selector must successfully source the requested release before any stage runs;
there is no fallback to latest. If the shared selector script changes, inspect
it before updating the configured digest.

The configuration file is controlled by the operator, not an MCP tool. A setup
script hash does not preserve an entire dependency stack: retain the CVMFS
release and all referenced package content for long-term replay.

Check paths, release-selection policy and hashes without sourcing the stack or
running generation:

```sh
.venv/bin/python -I -m mcp_server_key4hep.server --config /your/config/key4hep-server.json --check-config
```

This check does not confirm that the selected stack can load or run the physics
adapters; stage execution performs setup and records its logs.

Start the server:

```sh
.venv/bin/python -I -m mcp_server_key4hep.server --config /your/config/key4hep-server.json
```

Use Python's `-I` isolated mode for the server and tests, especially in a shell
where Key4hep has already been sourced. A virtual environment alone does not
ignore `PYTHONPATH`: stack packages can override the versions installed in
`.venv` (for example, pytest 9.0.0 overriding the locked pytest 9.1.1).
`-I` ignores Python environment variables and user site packages while retaining
the virtual environment's installed packages. Generator/validator subprocesses
still load their explicitly selected Key4hep stack normally.

For an MCP client supporting `mcpServers` JSON:

```json
{
  "mcpServers": {
    "key4hep": {
      "command": "/absolute/path/to/mcp-server-key4hep/.venv/bin/python",
      "args": ["-I", "-m", "mcp_server_key4hep.server", "--config", "/your/config/key4hep-server.json"]
    }
  }
}
```

Use absolute paths. MCP stdout carries only protocol messages; subprocess output
goes to per-stage log files. Keep the server connected while jobs run. Graceful
shutdown cancels jobs; after an abrupt crash, persisted active jobs are reported
as `INTERRUPTED` and never automatically rerun. Inspect old worker processes
before manually resubmitting.

## Pythia workflow

### First end-to-end check on a configured Linux host

This command **generates physics events**, unlike `--check-config` or pytest.
It starts a real MCP stdio connection, checks the committed inputs, submits ten
electron-positron dimuon events at 91.2 GeV, waits, and verifies output provenance:

```sh
.venv/bin/python -I -m mcp_server_key4hep.smoke \
  --config server-config.local.json \
  --release 2026-04-08 --seed 42 --events 10
```

The configured repository must contain the committed `examples/pythia.py` and
`examples/ee_mumu.cmd`. Do not start a separate server: the smoke client manages
its lifetime. It prints a unique run directory immediately after submission,
then state changes and the validation result. A failure exits nonzero and leaves
the manifest and stage logs in that directory. The default five-minute timeout
can be changed with `--timeout`; timeout or interruption cancels active work.

This is a functional smoke test, not a statistical physics validation. Once it
passes, connect the server to your agent using the MCP configuration above.

### Compare event content across two identical runs

```sh
.venv/bin/python -I -m mcp_server_key4hep.smoke \
  --config server-config.local.json \
  --release 2026-04-08 --seed 42 --events 10 --replay
```

This generates **two** independent samples in fresh directories through MCP,
validates each, then calls `compare_event_content(left_job_id, right_job_id)`.
The seed and full request are identical for both runs. The command exits zero
only if both runs succeed, both provenance checks pass, and the compared event
content matches exactly. The timeout applies to each run and the comparison.

Existing successful jobs can be compared with the same MCP tool without running
generation again. The tool requires distinct job IDs and matching configuration
digests (including committed input bytes and Git commit), effective settings,
recorded software environment, and runner source hash. Different configurations,
failed runs, or tampered artifacts are rejected. Both inputs are verified before
and after comparison. A changed configured setup profile is also rejected.

The comparator reads both files with podio and walks events in order, retaining
one event from each stream at a time. Its versioned representation covers:

- `MCParticles`: PDG, generator/simulator status, charge, time, mass, vertex,
  endpoint, momentum, endpoint momentum, and ordered parent/daughter links.
  Helicity, legacy spin and color-flow values are included when those accessors
  are available in the selected EDM4hep version.
- Optional `EventHeader`: event/run numbers, event timestamp, weight, and
  additional weights when available. The event timestamp is event content and
  is compared, unlike a ROOT file creation timestamp.

Particle order, event order and relation order are significant. Numeric podio
collection IDs are normalized to the collection name and object index. Finite
floating-point values use `float.hex()` without rounding or tolerance; signed
zero is significant. Unknown collection names/types, subsets, invalid relations,
non-finite floats and zero-event samples fail instead of being declared equal.
New EDM4hep fields outside this documented list require a comparator update.

ROOT storage timestamps, UUIDs and compression details are excluded. File-level
metadata and frame parameters are also explicitly outside this first version's
scope. It does not compare cross-sections or establish physics accuracy.

Each comparison writes a new directory under
`output_root/comparisons/<comparison_id>/`, containing `comparison.json`, a
separate `provenance.json`, the exact comparator source, and stage logs. Neither
original run is changed. The comparison provenance links both parent manifests
and output-file hashes, records the pinned setup and comparator hash, and hashes
the resulting report and logs.

The report includes `valid`, `identical`, canonical content SHA-256 values,
event counts, number of differing events, and the first differing event/field
with both values. Indices are zero-based and floating-point diagnostics use hex
strings. Status is `MATCH`, `DIFFERENT`, or `FAILED`; only `MATCH` establishes
equality **within the documented scope for this pair of runs**. A matching
10-event test is not proof of reproducibility for all processes or environments.
The first real LXPLUS replay check passed; its [validation record](docs/validation/lxplus-pythia-replay.md)
preserves the reported content digest and limitations.

### Submitting through an agent

1. Review `examples/pythia.py` and `examples/ee_mumu.cmd` against the chosen stack.
2. Commit the steering, card, and any supporting files to Git. The server refuses
   untracked files and changes relative to HEAD, including staged changes. It
   does not automatically commit files or change repository history.
3. Call `validate_steering_config` for a preflight without executing generation.
4. Submit `run_pythia8_generation` with these arguments (paths are relative to
   the configured Git root):

```json
{
  "process_name": "ee_mumu",
  "nevents": 100,
  "random_seed": 42,
  "ecm_gev": 91.2,
  "cvmfs_release": "YOUR_VERIFIED_RELEASE_TAG",
  "steering_path": "examples/pythia.py",
  "cmd_card_path": "examples/ee_mumu.cmd"
}
```

For preflight use `generator: "pythia8"` and rename `cmd_card_path` to `card_path`.
The returned `job_id` identifies the execution, even if another request has the
same configuration. Poll `get_job_status`; call `verify_provenance` before using
the artifacts. `cancel_job` also cancels queued work and kills the active process
group. At most 32 jobs may be queued/running per server.

The example applies the requested energy and Pythia seed **after** the card is
read. It fixes `Beams:frameType=1`, disables EvtGen and uses zero vertex smearing.
It is a minimal electron-positron example, not a precision production tune. Its
configuration receipt records these choices. New stochastic components require
explicit seed handling in a reviewed, committed adapter.

## WHIZARD workflow

Commit `examples/ee_mumu.sin` and `examples/hepmc_to_edm4hep.py`, then call:

```json
{
  "process_name": "ee_mumu",
  "nevents": 100,
  "random_seed": 42,
  "ecm_gev": 91.2,
  "cvmfs_release": "YOUR_VERIFIED_RELEASE_TAG",
  "sindarin_file_path": "examples/ee_mumu.sin",
  "converter_path": "examples/hepmc_to_edm4hep.py"
}
```

`run_whizard_generation` checks that the committed script's literal seed,
`n_events`, and `sqrts` match the request. It never injects assignments or rewrites
the script. Supported syntax is deliberately limited to the example's basic SM
two-to-two workflow: comments, `model = SM`, one quoted-particle process,
integer `seed`/`n_events`, `sqrts` in GeV, numeric integration iterations,
`integrate`, `$sample = "events"`, `sample_format = hepmc`, and `simulate`.
Includes, control flow, repeated seed settings, local blocks, alternate output
formats, and general-purpose Sindarin are rejected. Extend and test the adapter
explicitly for more advanced workflows.

Both native `events.hepmc` and converted `events.e4h.root` are retained. A
successful WHIZARD subprocess alone is not success: conversion and validation
must also pass. This adapter does not add showering or decays.

## Steering contract and provenance

Every steering/conversion script runs through `k4run` in a fresh run directory.
It reads the JSON path in `KEY4HEP_RUN_CONFIG` and must:

- Apply `random_seed`, `nevents`, and `ecm_gev` as appropriate.
- Use absolute `card_file`, `hepmc_file`, and `output_file` from that request.
- Write `effective_settings_file` as JSON with `generator`, `random_seed`,
  `nevents`, and `ecm_gev` matching the request, plus useful adapter settings.

This receipt is the trusted steering adapter's assertion of configured values;
it is not an independent measurement of the physics output. Git provenance is
not code isolation. Only run reviewed steering in a trusted local deployment.
No arbitrary shell-command tool is exposed.

Pass repository-relative `extra_inputs` for supporting files. Their directory
layout is preserved under `inputs/`; custom scripts should resolve them relative
to their own snapshot location. All non-stack dependencies must be declared;
the runner cannot discover arbitrary Python imports or absolute file reads.
Symlink inputs are rejected. Unrelated uncommitted repository files do not block
execution. Execution uses committed file bytes, never a live working-tree card.

Each unique run directory contains:

- `inputs/`: exact committed steering, card and supporting-file snapshots.
- `request.json` and `effective-settings.json`.
- `environment.json`: executable/module locations, available versions, platform
  and selected stack paths. Unavailable module versions are recorded as null.
- Stage stdout/stderr logs and command arguments in the manifest.
- `probe.py` and `validator.py`: archived helper sources.
- Output artifacts and `validation.json`.
- `provenance.json`: Git commit, input hashes, resolved setup digest, configuration
  digest, status, timestamps, commands, validation result and artifact hashes.

Failed and cancelled runs retain provenance, logs and available partial output.
Successful manifests are re-read and verified. `verify_provenance` repeats these
checks later; it detects accidental changes, not malicious rewriting of both a
manifest and its artifacts. It is not a signed attestation.

## Validation scope

Validation uses `podio.reading.get_reader`, allowing the stack's supported ROOT
backends. It reads every event and collection, checks the event count and stable
collection names, requires a correctly typed `MCParticles` collection, checks
finite momentum/mass/charge and resolvable parent/daughter references within that
collection, and rejects a sample containing no MCParticles.

`validate_edm4hep_file(job_id)` returns this report only after artifact integrity
checks pass. Validation occurs automatically during execution; this tool does
not open arbitrary user-supplied paths. Other collection relations, all metadata
semantics, cross-sections and physics distributions are not validated.

SHA-256 identifies stored bytes. Identical ROOT file hashes are not promised
across reruns; timestamps and UUIDs may differ. The replay comparison above uses
canonical event-content digests instead. Statistical physics validation remains
separate work. Release pinning and seeds alone do not establish cross-platform
bitwise reproducibility.

## Development and release qualification

```sh
uv sync --frozen
.venv/bin/python -I -m pytest -q
.venv/bin/ruff check src examples tests
.venv/bin/ruff format --check src examples tests
```

Tests cover real stdio MCP initialization/tool calls, Git input checks, request
validation, restricted Sindarin settings, subprocess argument handling,
timeouts/cancellation, status persistence, and artifact tampering. Runner
success-path tests use explicit test doubles for the physics stages; they do
not claim to validate a real ROOT file.

Before adopting a release, run small Pythia and WHIZARD samples on that Linux
stack, confirm expected energy/seed/count in generator logs, inspect the EDM4hep
content, and replay in fresh directories. Exercise a failing generator and
cancellation. Record the qualified release and configuration in your project.

API references: [MCP Python SDK v1](https://py.sdk.modelcontextprotocol.io/v1/),
[k4Gen Pythia example](https://github.com/key4hep/k4Gen/blob/main/k4Gen/options/pythia.py),
[FCC event generation tutorial](https://hep-fcc.github.io/fcc-tutorials/main/2-gen-and-fastsim/2-1-event-generation/README.html).
