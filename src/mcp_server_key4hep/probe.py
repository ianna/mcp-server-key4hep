"""Executed with the pinned stack's Python; output goes to a file, not stdout."""

import importlib
import importlib.metadata
import json
import os
import platform
import shutil
import sys
from pathlib import Path

import ROOT


def main():
    modules = {}
    for name in ("podio", "edm4hep", "Gaudi", "k4FWCore"):
        module = importlib.import_module(name)
        version = getattr(module, "__version__", None)
        if version is None:
            try:
                version = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                pass
        modules[name] = {"file": getattr(module, "__file__", None), "version": version}
    executables = {}
    for name in ("python3", "k4run", "whizard"):
        executable = shutil.which(name)
        executables[name] = str(Path(executable).resolve()) if executable else None
    data = {
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "root_version": ROOT.gROOT.GetVersion(),
        "modules": modules,
        "executables": executables,
        "stack_paths": {
            key: value
            for key, value in os.environ.items()
            if key
            in {
                "PATH",
                "PYTHONPATH",
                "LD_LIBRARY_PATH",
                "ROOTSYS",
                "CMAKE_PREFIX_PATH",
                "PYTHIA8DATA",
                "PYTHIA8",
            }
        },
    }
    Path("environment.json").write_text(json.dumps(data, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
