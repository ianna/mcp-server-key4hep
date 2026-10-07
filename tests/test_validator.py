"""Exercise validator decisions with a minimal podio API double (not ROOT I/O)."""

import sys
from types import SimpleNamespace

import pytest

from mcp_server_key4hep.validator import validate


class Particle:
    def __init__(self, index=0, finite=True, relations=None):
        self.index = index
        self.finite = finite
        self.relations = relations or []

    def getObjectID(self):
        return SimpleNamespace(collectionID=0, index=self.index)

    def getMomentum(self):
        return SimpleNamespace(x=0, y=0, z=1 if self.finite else float("nan"))

    def getMass(self):
        return 0.1

    def getCharge(self):
        return -1

    def getParents(self):
        return self.relations

    def getDaughters(self):
        return []

    def isAvailable(self):
        return True


class Collection(list):
    def getTypeName(self):
        # Model a C++ string_view proxy whose Python str() is not its contents.
        return SimpleNamespace(data=lambda: "edm4hep::MCParticleCollection")


class OtherCollection(list):
    __cpp_name__ = "edm4hep::ReconstructedParticleCollection"

    def getTypeName(self):
        # A matching display string is not proof of the correct C++ class.
        return "edm4hep::MCParticleCollection"


class Frame:
    def __init__(self, particles):
        self.particles = Collection(particles)

    def getAvailableCollections(self):
        return ["MCParticles"]

    def get(self, name):
        assert name == "MCParticles"
        return self.particles


def install_reader(monkeypatch, frames, categories=("events",)):
    reader = SimpleNamespace(categories=categories, get=lambda _: frames)
    monkeypatch.setitem(sys.modules, "edm4hep", SimpleNamespace())
    monkeypatch.setitem(
        sys.modules,
        "ROOT",
        SimpleNamespace(edm4hep=SimpleNamespace(MCParticleCollection=Collection)),
    )
    monkeypatch.setitem(sys.modules, "podio", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "podio.reading", SimpleNamespace(get_reader=lambda _: reader))


@pytest.fixture
def output(tmp_path):
    path = tmp_path / "events.e4h.root"
    path.write_bytes(b"reader test fixture")
    return path


def test_reads_each_frame_and_relation(output, monkeypatch):
    a, b = Particle(0), Particle(1)
    b.relations = [a]
    install_reader(monkeypatch, [Frame([a, b]), Frame([Particle()])])
    result = validate(output, 2)
    assert result["valid"] and result["particles"] == 3


@pytest.mark.parametrize("case", ["count", "empty", "nonfinite", "relation", "category", "type"])
def test_rejects_bad_content(output, monkeypatch, case):
    frame = Frame([Particle()])
    if case == "empty":
        frame = Frame([])
    elif case == "nonfinite":
        frame = Frame([Particle(finite=False)])
    elif case == "relation":
        frame = Frame([Particle(relations=[Particle(99)])])
    elif case == "type":
        frame.particles = OtherCollection(frame.particles)
    install_reader(monkeypatch, [frame], categories=() if case == "category" else ("events",))
    with pytest.raises(ValueError):
        validate(output, 2 if case == "count" else 1)


def test_wrong_type_reports_actual_cpp_class(output, monkeypatch):
    frame = Frame([Particle()])
    frame.particles = OtherCollection(frame.particles)
    install_reader(monkeypatch, [frame])
    with pytest.raises(ValueError, match="got edm4hep::ReconstructedParticleCollection"):
        validate(output, 1)
