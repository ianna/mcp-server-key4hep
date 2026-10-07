"""Modern k4Gen HepMC3 conversion; commit before executing with k4run."""

import json
import os
from pathlib import Path

from Configurables import (
    EventDataSvc,
    GaussSmearVertex,
    GenAlg,
    HepMCFileReader,
    HepMCToEDMConverter,
)
from k4FWCore import ApplicationMgr, IOSvc

config = json.loads(Path(os.environ["KEY4HEP_RUN_CONFIG"]).read_text())
assert config["generator"] == "whizard"
app = ApplicationMgr()
app.EvtSel = "NONE"
app.EvtMax = config["nevents"]
app.ExtSvc += ["RndmGenSvc", EventDataSvc("EventDataSvc")]
reader = HepMCFileReader()
reader.Filename = config["hepmc_file"]
vertex = GaussSmearVertex()
vertex.xVertexSigma = 0.0
vertex.yVertexSigma = 0.0
vertex.zVertexSigma = 0.0
vertex.tVertexSigma = 0.0
generator = GenAlg("ReadHepMC")
generator.SignalProvider = reader
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
receipt["vertex_sigma"] = [0.0, 0.0, 0.0, 0.0]
Path(config["effective_settings_file"]).write_text(json.dumps(receipt, indent=2))
