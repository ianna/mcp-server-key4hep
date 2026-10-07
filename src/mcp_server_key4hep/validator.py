"""Read every event with podio's backend-independent reader.

This is structural validation, not a physics-reference comparison. Importing
edm4hep loads dictionaries required by podio. Run in the selected stack.
"""

import json
import math
import sys
from pathlib import Path


def validate(path, expected):
    import edm4hep  # noqa: F401
    from podio.reading import get_reader

    if expected <= 0 or not Path(path).is_file() or Path(path).stat().st_size == 0:
        raise ValueError("Expected a nonempty output and a positive event count")
    reader = get_reader(str(path))
    if "events" not in reader.categories:
        raise ValueError("No events category")
    frames = reader.get("events")
    if len(frames) != expected:
        raise ValueError(f"Event count mismatch: {len(frames)} != {expected}")
    particles_total = 0
    schema = None
    for frame in frames:
        names = set(frame.getAvailableCollections())
        if "MCParticles" not in names:
            raise ValueError("Missing MCParticles collection")
        if schema is None:
            schema = sorted(names)
        elif sorted(names) != schema:
            raise ValueError("Collection names change between events")
        # Force deserialization of every collection, not just the tree header.
        for name in names:
            collection = frame.get(name)
            for _ in collection:
                pass
        particles = frame.get("MCParticles")
        if str(particles.getTypeName()) != "edm4hep::MCParticleCollection":
            raise ValueError("MCParticles has the wrong EDM4hep collection type")
        identifiers = set()
        for particle in particles:
            oid = particle.getObjectID()
            identifiers.add((oid.collectionID, oid.index))
            momentum = particle.getMomentum()
            if not all(
                math.isfinite(v)
                for v in (
                    momentum.x,
                    momentum.y,
                    momentum.z,
                    particle.getMass(),
                    particle.getCharge(),
                )
            ):
                raise ValueError("Non-finite MCParticle values")
        for particle in particles:
            for related in [*particle.getParents(), *particle.getDaughters()]:
                if not related.isAvailable():
                    raise ValueError("Unresolved MCParticle relation")
                oid = related.getObjectID()
                if (oid.collectionID, oid.index) not in identifiers:
                    raise ValueError("MCParticle relation leaves the required collection")
        particles_total += len(particles)
    if particles_total == 0:
        raise ValueError("No MCParticles in generated sample")
    return {
        "valid": True,
        "entries": expected,
        "particles": particles_total,
        "collections": schema,
        "scope": "EDM4hep structure and MCParticle relations",
    }


def main():
    path, count, report = sys.argv[1:]
    try:
        result = validate(path, int(count))
    except Exception as exc:
        result = {"valid": False, "error": f"{type(exc).__name__}: {exc}"}
    Path(report).write_text(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
