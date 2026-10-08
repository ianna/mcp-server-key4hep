"""Seed-only options loaded AFTER upstream k4Gen steering by standard k4run.

No beam, process, vertex-smearing or output-collection settings are changed.
Commit this file before using it. Both seeds must be explicitly supplied.
"""

import os

from Configurables import HepRndm__Engine_CLHEP__RanluxEngine_, PythiaInterface, RndmGenSvc

pythia_seed = int(os.environ["FCC_PYTHIA_SEED"])
gaudi_seed = int(os.environ["FCC_GAUDI_SEED"])
if not all(1 <= seed <= 900_000_000 for seed in (pythia_seed, gaudi_seed)):
    raise ValueError("Explicit seeds must be in 1..900000000")
PythiaInterface().pythiaExtraSettings = [
    "Random:setSeed = on",
    f"Random:seed = {pythia_seed}",
]
engine = HepRndm__Engine_CLHEP__RanluxEngine_("RndmGenSvc.Engine")
engine.Seeds = [gaudi_seed, 0]
engine.UseTable = False
RndmGenSvc().Engine = engine.getType()
