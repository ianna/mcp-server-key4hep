"""Content-level tests independent of ROOT, including equal-count counterexamples."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from mcp_server_key4hep.compare import canonical_event, compare_frames


class Particle:
    def __init__(self, index, collection_id=1):
        self.index = index
        self.collection_id = collection_id
        self.parents = []
        self.daughters = []
        self.values = {
            "PDG": 13,
            "GeneratorStatus": 1,
            "SimulatorStatus": 0,
            "Charge": -1.0,
            "Time": 0.0,
            "Mass": 0.105,
            "Vertex": SimpleNamespace(x=0.0, y=0.0, z=0.0),
            "Endpoint": SimpleNamespace(x=0.0, y=0.0, z=0.0),
            "Momentum": SimpleNamespace(x=1.0, y=2.0, z=3.0),
            "MomentumAtEndpoint": SimpleNamespace(x=0.0, y=0.0, z=0.0),
            "Helicity": 9,
        }

    def __getattr__(self, name):
        values = self.__dict__.get("values", {})
        if name.startswith("get") and name[3:] in values:
            return lambda: values[name[3:]]
        raise AttributeError(name)

    def getObjectID(self):
        return SimpleNamespace(collectionID=self.collection_id, index=self.index)

    def getParents(self):
        return self.parents

    def getDaughters(self):
        return self.daughters

    def isAvailable(self):
        return True


class Particles(list):
    def isSubsetCollection(self):
        return False


class Headers(Particles):
    pass


class Frame:
    def __init__(self, collection_id=1):
        a, b = Particle(0, collection_id), Particle(1, collection_id)
        b.values["PDG"] = -13
        a.daughters = [b]
        b.parents = [a]
        self.collections = {"MCParticles": Particles([a, b])}

    def getAvailableCollections(self):
        return list(self.collections)

    def get(self, name):
        return self.collections[name]


TYPES = {"MCParticles": Particles, "EventHeader": Headers}


def test_equal_events_ignore_storage_ids_and_unscoped_metadata():
    left, right = Frame(10), Frame(900)
    left.metadata, right.metadata = {"timestamp": 100}, {"timestamp": 200}
    result = compare_frames([left], [right], TYPES)
    assert result["valid"] and result["identical"]
    assert result["content_sha256"]["left"] == result["content_sha256"]["right"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("PDG", 11),
        ("GeneratorStatus", 2),
        ("SimulatorStatus", 8),
        ("Charge", 1.0),
        ("Time", 1.0),
        ("Mass", 0.106),
        ("Helicity", 1),
    ],
)
def test_equal_counts_but_different_scalar_fields_fail(field, value):
    left, right = Frame(), Frame()
    right.get("MCParticles")[1].values[field] = value
    result = compare_frames([left], [right], TYPES)
    assert result["events"] == {"left": 1, "right": 1}
    assert result["valid"] and not result["identical"]
    assert result["different_events"] == 1
    assert result["first_difference"]["event_index"] == 0
    assert result["content_sha256"]["left"] != result["content_sha256"]["right"]


@pytest.mark.parametrize("field", ["Vertex", "Endpoint", "Momentum", "MomentumAtEndpoint"])
def test_vector_components_compared_exactly(field):
    left, right = Frame(), Frame()
    right.get("MCParticles")[0].values[field].x += 1e-14
    result = compare_frames([left], [right], TYPES)
    assert not result["identical"]
    assert result["first_difference"]["path"].endswith(".x")


def test_signed_zero_is_explicitly_significant():
    left, right = Frame(), Frame()
    right.get("MCParticles")[0].values["Time"] = -0.0
    assert not compare_frames([left], [right], TYPES)["identical"]


def test_relations_and_order_are_compared():
    left, right = Frame(), Frame()
    right.get("MCParticles")[1].parents = [right.get("MCParticles")[1]]
    diff = compare_frames([left], [right], TYPES)["first_difference"]
    assert diff["path"] == "events[0].MCParticles[1].parents[0].index"
    a, b = Frame(), Frame()
    b.get("MCParticles")[0].values["PDG"] = 22
    assert not compare_frames([a, b], [b, a], TYPES)["identical"]


def test_event_and_particle_counts_are_compared():
    result = compare_frames([Frame()], [Frame(), Frame()], TYPES)
    assert not result["identical"] and result["first_difference"]["event_index"] == 1
    left, right = Frame(), Frame()
    right.get("MCParticles").append(Particle(2))
    assert compare_frames([left], [right], TYPES)["first_difference"]["path"].endswith(".length")


@pytest.mark.parametrize(
    "problem", ["nonfinite", "external_relation", "extra_collection", "subset", "wrong_type"]
)
def test_unsupported_or_invalid_data_fail_closed(problem):
    frame = Frame()
    if problem == "nonfinite":
        frame.get("MCParticles")[0].values["Mass"] = float("nan")
    elif problem == "external_relation":
        frame.get("MCParticles")[0].parents = [Particle(0, 999)]
    elif problem == "extra_collection":
        frame.collections["Tracks"] = []
    elif problem == "subset":
        frame.get("MCParticles").isSubsetCollection = lambda: True
    else:
        frame.collections["MCParticles"] = list(frame.get("MCParticles"))
    with pytest.raises(ValueError):
        compare_frames([frame], [Frame()], TYPES)


def test_event_header_and_optional_legacy_fields():
    left, right = Frame(), Frame()
    header = SimpleNamespace(
        getEventNumber=lambda: 1,
        getRunNumber=lambda: 2,
        getTimeStamp=lambda: 3,
        getWeight=lambda: 1.0,
        getWeights=lambda: [1.0, 2.0],
    )
    left.collections["EventHeader"] = Headers([header])
    right.collections["EventHeader"] = Headers([deepcopy(header)])
    for frame in (left, right):
        frame.get("MCParticles")[0].values.update(
            Spin=SimpleNamespace(x=1.0, y=0.0, z=0.0), ColorFlow=[1, 2]
        )
    assert compare_frames([left], [right], TYPES)["identical"]
    right.collections["EventHeader"][0].getWeights = lambda: [1.0, 3.0]
    assert (
        compare_frames([left], [right], TYPES)["first_difference"]["path"]
        == "events[0].EventHeader[0].weights[1]"
    )
    record = canonical_event(left, TYPES)["MCParticles"][0]
    assert "spin" in record and record["colorFlow"] == [1, 2]


def test_empty_samples_not_replay_evidence():
    with pytest.raises(ValueError, match="empty"):
        compare_frames([], [], TYPES)


def test_upstream_stable_particle_clones_and_cross_collection_relations():
    left, right = Frame(10), Frame(20)
    for frame, identity in ((left, 11), (right, 21)):
        clone = Particle(0, identity)
        clone.parents = [frame.get("MCParticles")[0]]
        frame.collections["MCParticlesStable"] = Particles([clone])
    assert compare_frames([left], [right], TYPES)["identical"]
    right.get("MCParticlesStable")[0].values["Mass"] += 0.1
    result = compare_frames([left], [right], TYPES)
    assert not result["identical"]
    assert "MCParticlesStable" in result["first_difference"]["path"]


def test_stable_subset_membership_order_and_external_reference():
    left, right = Frame(10), Frame(20)
    for frame in (left, right):
        subset = Particles(frame.get("MCParticles"))
        subset.isSubsetCollection = lambda: True
        frame.collections["MCParticlesStable"] = subset
    assert compare_frames([left], [right], TYPES)["identical"]
    right.get("MCParticlesStable").reverse()
    assert not compare_frames([left], [right], TYPES)["identical"]
    right.get("MCParticlesStable").append(Particle(0, 999))
    with pytest.raises(ValueError, match="relation"):
        compare_frames([left], [right], TYPES)
