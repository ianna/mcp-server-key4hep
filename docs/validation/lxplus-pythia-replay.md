> Historical 0.1 evidence: this run used the custom example removed in 0.2.
> It does not validate the new FCC-config demonstration or v2 content hashes.

# LXPLUS Pythia event-content replay

The operator reported a successful two-run comparison on CERN LXPLUS using
Key4hep release `2026-04-08`. The smoke client submitted the same request twice:
Pythia dimuon generation at 91.2 GeV, seed 42, and 10 events per run.

## Reproduction command

With the release profile configured and the example steering/cards committed:

```sh
.venv/bin/python -I -m mcp_server_key4hep.smoke \
  --config server-config.local.json \
  --release 2026-04-08 \
  --seed 42 \
  --events 10 \
  --replay
```

This creates fresh run and comparison directories. Their identifiers and ROOT
file hashes need not match those of the reported run.

## Reported result

The following is an excerpt of the operator-provided MCP comparison output.
Personal filesystem paths are omitted. The original ROOT files, comparison
report, and provenance manifests remain in the operator's run storage and are
not included in this repository. This is a record of that reported execution,
not an independently rerun test or a substitute for those artifacts.

```json
{
  "comparison_id": "dbf195b9ce2e42159f6efbf5c4e894ae",
  "format": "key4hep-event-content-v1",
  "status": "MATCH",
  "valid": true,
  "identical": true,
  "events": {
    "left": 10,
    "right": 10
  },
  "different_events": 0,
  "first_difference": null,
  "content_sha256": {
    "left": "3ef7397054e5d1ec45c72e1ebb7f038c84f7674104ff3b3c72ae406436e1a741",
    "right": "3ef7397054e5d1ec45c72e1ebb7f038c84f7674104ff3b3c72ae406436e1a741"
  }
}
```

The comparison tool requires two distinct successful jobs, verifies both input
provenance records, and checks matching configurations and recorded software
environments before comparing event content.

## Interpretation and limits

The two samples matched exactly within `key4hep-event-content-v1`:

- MCParticle fields and parent/daughter relations; EventHeader content when present.
- Event, particle, header, and relation ordering are significant.
- Finite floating-point values are compared exactly using `float.hex()`;
  signed zero is significant.
- Numeric collection IDs are normalized to collection names and object indices.

ROOT storage metadata, file-level metadata, and frame parameters are excluded.
The equal hashes are canonical event-content hashes, not whole-file hashes.
This result does not establish precision physics accuracy, statistical
agreement, cross-platform replay, WHIZARD replay, or reproducibility beyond
this tested pair. The complete field list and rejection rules are documented
in the [README](../../README.md#compare-event-content-across-two-identical-runs).
