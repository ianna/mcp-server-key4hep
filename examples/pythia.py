"""Minimal modern k4Gen/IOSvc steering. Commit this file before running.

The card is immutable. Explicit run settings are applied after reading it by
PythiaInterface.pythiaExtraSettings. Vertex smearing is zero in this example.
"""

import json
import os
from pathlib import Path

from Configurables import (
    EventDataSvc,
    GaussSmearVertex,
    GenAlg,
    HepMCToEDMConverter,
    PythiaInterface,
)
from k4FWCore import ApplicationMgr, IOSvc

config = json.loads(Path(os.environ["KEY4HEP_RUN_CONFIG"]).read_text())
assert config["generator"] == "pythia8"

app = ApplicationMgr()
app.EvtSel = "NONE"
app.EvtMax = config["nevents"]
app.ExtSvc += ["RndmGenSvc", EventDataSvc("EventDataSvc")]

provider = PythiaInterface()
provider.pythiacard = config["card_file"]
provider.doEvtGenDecays = False
provider.printPythiaStatistics = True
provider.pythiaExtraSettings = [
    "Random:setSeed = on",
    f"Random:seed = {config['random_seed']}",
    "Beams:frameType = 1",
    f"Beams:eCM = {config['ecm_gev']}",
]
vertex = GaussSmearVertex()
vertex.xVertexSigma = 0.0
vertex.yVertexSigma = 0.0
vertex.zVertexSigma = 0.0
vertex.tVertexSigma = 0.0
generator = GenAlg("Pythia8")
generator.SignalProvider = provider
generator.VertexSmearingTool = vertex
generator.hepmc.Path = "hepmc"
converter = HepMCToEDMConverter()
converter.hepmc.Path = "hepmc"
converter.hepmcStatusList = []
converter.GenParticles.Path = "MCParticles"
app.TopAlg = [generator, converter]
output = IOSvc()
output.Output = config["output_file"]
output.outputCommands = ["keep *"]

receipt = {key: config[key] for key in ("generator", "nevents", "random_seed", "ecm_gev")}
receipt.update(
    pythia_extra_settings=provider.pythiaExtraSettings,
    vertex_sigma=[0.0, 0.0, 0.0, 0.0],
    evtgen=False,
)
Path(config["effective_settings_file"]).write_text(json.dumps(receipt, indent=2))
