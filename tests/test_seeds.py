"""Seed overlay must configure the tool instance the generator actually runs."""

import copy
import runpy
import sys
import types
from pathlib import Path

import pytest

SEEDS = Path(__file__).resolve().parents[1] / "examples/fcc-config/seeds.py"


class Configurable:
    """Minimal model of old-style Gaudi configurables: cached by name, and a
    private tool assigned to a parent is copied (as GaudiKernel does)."""

    registry = {}

    def __new__(cls, name=None):
        name = name or cls.__name__
        key = (cls.__name__, name)
        if key not in cls.registry:
            obj = super().__new__(cls)
            obj.__dict__["name"] = name
            cls.registry[key] = obj
        return cls.registry[key]

    def __init__(self, name=None):
        pass

    def getType(self):
        return type(self).TYPE

    def __setattr__(self, key, value):
        if isinstance(value, Configurable) and key == "SignalProvider":
            child = object.__new__(type(value))
            child.__dict__.update(copy.deepcopy(value.__dict__))
            child.__dict__["name"] = f"{self.name}.{value.name}"
            value = child
        self.__dict__[key] = value


def configurables():
    module = types.ModuleType("Configurables")
    for name, type_name in (
        ("GenAlg", "GenAlg"),
        ("PythiaInterface", "PythiaInterface"),
        ("RndmGenSvc", "RndmGenSvc"),
        ("HepRndm__Engine_CLHEP__RanluxEngine_", "HepRndm::Engine<CLHEP::RanluxEngine>"),
    ):
        setattr(module, name, type(name, (Configurable,), {"TYPE": type_name}))
    return module


@pytest.fixture
def upstream(monkeypatch):
    Configurable.registry = {}
    module = configurables()
    monkeypatch.setitem(sys.modules, "Configurables", module)
    monkeypatch.setenv("FCC_PYTHIA_SEED", "42")
    monkeypatch.setenv("FCC_GAUDI_SEED", "7")
    # What the pinned k4Gen options/pythia.py does before our overlay is loaded.
    tool = module.PythiaInterface()
    tool.pythiaExtraSettings = [""]
    module.GenAlg("Pythia8").SignalProvider = tool
    return module


def test_seed_reaches_the_copy_used_by_the_generator(upstream):
    runpy.run_path(str(SEEDS))
    used = upstream.GenAlg("Pythia8").SignalProvider
    assert used.name == "Pythia8.PythiaInterface"
    assert used.pythiaExtraSettings == ["Random:setSeed = on", "Random:seed = 42"]
    # The detached original is irrelevant to the run and must not be relied upon.
    assert upstream.PythiaInterface().pythiaExtraSettings == [""]
    engine = upstream.HepRndm__Engine_CLHEP__RanluxEngine_("RndmGenSvc.Engine")
    assert engine.Seeds == [7, 0]


def test_upstream_extras_are_kept_before_seed(upstream):
    upstream.GenAlg("Pythia8").SignalProvider.pythiaExtraSettings = ["", "Next:numberCount = 0"]
    runpy.run_path(str(SEEDS))
    assert upstream.GenAlg("Pythia8").SignalProvider.pythiaExtraSettings == [
        "Next:numberCount = 0",
        "Random:setSeed = on",
        "Random:seed = 42",
    ]


@pytest.mark.parametrize("seed", ["0", "900000001"])
def test_out_of_range_seed_rejected(upstream, monkeypatch, seed):
    monkeypatch.setenv("FCC_PYTHIA_SEED", seed)
    with pytest.raises(ValueError):
        runpy.run_path(str(SEEDS))
