"""Seed-only options loaded AFTER upstream k4Gen steering by standard k4run.

No beam, process, vertex-smearing or output-collection settings are changed.
Commit this file before using it. Both seeds must be explicitly supplied.

Upstream assigns ``PythiaInterface()`` to the *private* ``SignalProvider`` of
``GenAlg("Pythia8")``. Gaudi copies a private tool on assignment (hence the
``--Pythia8.PythiaInterface.*`` command-line spelling), so configuring a fresh
``PythiaInterface()`` here would silently miss the instance that runs. Configure
the copy held by the algorithm instead.
"""

import os

from Configurables import GenAlg, HepRndm__Engine_CLHEP__RanluxEngine_, RndmGenSvc

pythia_seed = int(os.environ["FCC_PYTHIA_SEED"])
gaudi_seed = int(os.environ["FCC_GAUDI_SEED"])
if not all(1 <= seed <= 900_000_000 for seed in (pythia_seed, gaudi_seed)):
    raise ValueError("Explicit seeds must be in 1..900000000")

pythia = GenAlg("Pythia8").SignalProvider
if pythia.getType() != "PythiaInterface":
    raise RuntimeError(
        f"Expected upstream GenAlg('Pythia8') to use PythiaInterface, got {pythia.getType()}"
    )
# Keep any non-empty upstream extras; ours are applied last so they take effect.
pythia.pythiaExtraSettings = [
    *(setting for setting in pythia.pythiaExtraSettings if setting),
    "Random:setSeed = on",
    f"Random:seed = {pythia_seed}",
]

engine = HepRndm__Engine_CLHEP__RanluxEngine_("RndmGenSvc.Engine")
engine.Seeds = [gaudi_seed, 0]
engine.UseTable = False
RndmGenSvc().Engine = engine.getType()
