"""Exact, ordered EDM4hep content comparison, executed inside the pinned stack.

This deliberately supports MCParticles and optional EventHeader only. It is not
a file-byte comparison, graph-isomorphism check or statistical physics test.
"""

import hashlib
import itertools
import json
import math
import sys
from pathlib import Path

FORMAT = "key4hep-event-content-v1"
SCOPE = {
    "collections": ["MCParticles", "EventHeader (optional)"],
    "ordering": "event, particle, header and relation order are significant",
    "floats": "exact finite values encoded with float.hex; signed zero is significant",
    "excluded": ["ROOT storage metadata", "file-level metadata", "frame parameters"],
}


def real(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Non-finite floating-point content is unsupported")
    return value.hex()


def vector(value):
    return {axis: real(getattr(value, axis)) for axis in ("x", "y", "z")}


def oid(obj):
    identity = obj.getObjectID()
    return int(identity.collectionID), int(identity.index)


def canonical_event(frame, types):
    names = sorted(str(name) for name in frame.getAvailableCollections())
    if "MCParticles" not in names or set(names) - {"MCParticles", "EventHeader"}:
        raise ValueError(
            f"Unsupported collection set: {names}; require MCParticles and optional EventHeader"
        )
    collections = {name: frame.get(name) for name in names}
    for name, collection in collections.items():
        if not isinstance(collection, types[name]):
            actual = getattr(type(collection), "__cpp_name__", type(collection).__name__)
            raise ValueError(f"Wrong C++ type for {name}: {actual}")
        if collection.isSubsetCollection():
            raise ValueError(f"Subset collection is unsupported: {name}")
    particles = collections["MCParticles"]
    identities = {}
    for index, particle in enumerate(particles):
        identity = oid(particle)
        if identity in identities or identity[1] != index:
            raise ValueError("MCParticles must have unique, ordered object indices")
        identities[identity] = index

    def relations(values):
        result = []
        for related in values:
            if not related.isAvailable() or oid(related) not in identities:
                raise ValueError("Unresolved or external MCParticle relation")
            # Raw podio collection IDs are storage identifiers, not physics data.
            result.append({"collection": "MCParticles", "index": identities[oid(related)]})
        return result

    records = []
    for particle in particles:
        record = {
            "PDG": int(particle.getPDG()),
            "generatorStatus": int(particle.getGeneratorStatus()),
            "simulatorStatus": int(particle.getSimulatorStatus()),
            "charge": real(particle.getCharge()),
            "time": real(particle.getTime()),
            "mass": real(particle.getMass()),
            "vertex": vector(particle.getVertex()),
            "endpoint": vector(particle.getEndpoint()),
            "momentum": vector(particle.getMomentum()),
            "momentumAtEndpoint": vector(particle.getMomentumAtEndpoint()),
            "parents": relations(particle.getParents()),
            "daughters": relations(particle.getDaughters()),
        }
        # EDM4hep versions differ in their spin representation. Capture every
        # known available representation, including legacy color-flow fields.
        if hasattr(particle, "getHelicity"):
            record["helicity"] = int(particle.getHelicity())
        if hasattr(particle, "getSpin"):
            record["spin"] = vector(particle.getSpin())
        if hasattr(particle, "getColorFlow"):
            record["colorFlow"] = [int(value) for value in particle.getColorFlow()]
        records.append(record)
    result = {"MCParticles": records}
    if "EventHeader" in collections:
        headers = []
        for header in collections["EventHeader"]:
            record = {
                "eventNumber": int(header.getEventNumber()),
                "runNumber": int(header.getRunNumber()),
                "timeStamp": int(header.getTimeStamp()),
                "weight": real(header.getWeight()),
            }
            if hasattr(header, "getWeights"):
                record["weights"] = [real(weight) for weight in header.getWeights()]
            headers.append(record)
        result["EventHeader"] = headers
    return result


def first_difference(left, right, path="event"):
    if type(left) is not type(right):
        return {"path": path, "left": left, "right": right}
    if isinstance(left, dict):
        if left.keys() != right.keys():
            return {"path": path + ".fields", "left": sorted(left), "right": sorted(right)}
        for key in sorted(left):
            diff = first_difference(left[key], right[key], f"{path}.{key}")
            if diff:
                return diff
    elif isinstance(left, list):
        if len(left) != len(right):
            return {"path": path + ".length", "left": len(left), "right": len(right)}
        for index, (a, b) in enumerate(zip(left, right)):
            diff = first_difference(a, b, f"{path}[{index}]")
            if diff:
                return diff
    elif left != right:
        return {"path": path, "left": left, "right": right}
    return None


def compare_frames(left, right, types):
    hashes = [hashlib.sha256((FORMAT + "\n").encode()) for _ in range(2)]
    counts = [0, 0]
    first = None
    different_events = 0
    missing = object()
    for index, frames in enumerate(itertools.zip_longest(left, right, fillvalue=missing)):
        events = []
        for side, frame in enumerate(frames):
            if frame is missing:
                events.append(None)
                continue
            event = canonical_event(frame, types)
            counts[side] += 1
            hashes[side].update(
                json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
                + b"\n"
            )
            events.append(event)
        diff = first_difference(*events, path=f"events[{index}]")
        if diff:
            different_events += 1
            if first is None:
                first = {"event_index": index, **diff}
    if not all(counts):
        raise ValueError("Cannot establish replay with an empty event sample")
    return {
        "valid": True,
        "identical": first is None,
        "format": FORMAT,
        "scope": SCOPE,
        "events": {"left": counts[0], "right": counts[1]},
        "different_events": different_events,
        "first_difference": first,
        "content_sha256": {"left": hashes[0].hexdigest(), "right": hashes[1].hexdigest()},
    }


def compare_files(left, right):
    import edm4hep  # noqa: F401
    import ROOT
    from podio.reading import get_reader

    if Path(left).samefile(right):
        raise ValueError("Comparison requires two distinct output files")
    readers = [get_reader(str(path)) for path in (left, right)]
    if any("events" not in reader.categories for reader in readers):
        raise ValueError("Both files must contain an events category")
    types = {
        "MCParticles": ROOT.edm4hep.MCParticleCollection,
        "EventHeader": ROOT.edm4hep.EventHeaderCollection,
    }
    return compare_frames(readers[0].get("events"), readers[1].get("events"), types)


def main():
    left, right, report = sys.argv[1:]
    try:
        result = compare_files(left, right)
    except Exception as exc:
        result = {
            "valid": False,
            "identical": False,
            "format": FORMAT,
            "error": f"{type(exc).__name__}: {exc}",
        }
    Path(report).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    # A completed comparison finding different data is not an execution failure.
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
