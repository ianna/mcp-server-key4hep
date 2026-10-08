"""Exercise the real setup boundary without CVMFS or event generation."""

import os
import shlex
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("status", [0, 7])
def test_setup_uses_normal_shell_and_restores_strict_mode(tmp_path, status):
    source = Path(__file__).resolve().parents[1] / "examples/fcc-config/demonstrate.sh"
    script = source.read_text().split("export PYTHONPATH=", 1)[0]
    setup = tmp_path / "setup.sh"
    setup.write_text(
        '[[ "$1" == -r && "$2" == 2026-04-08 ]] || return 9\n'
        # The failure observed on LXPLUS: an unset variable in vendor setup.
        'value="$compiler"\n'
        "false\n"
        "false | true\n"
        f"return {status}\n"
    )
    script = script.replace("/cvmfs/sw.hsf.org/key4hep/setup.sh", shlex.quote(str(setup)))
    script += '\n[[ "$-" == *e* && "$-" == *u* ]]\n'
    script += 'set -o | awk \'$1 == "pipefail" {exit $2 != "on"}\'\n'
    path = tmp_path / "examples/fcc-config/demonstrate.sh"
    path.parent.mkdir(parents=True)
    path.write_text(script)
    work = tmp_path / "run"
    proc = subprocess.run(
        ["/bin/bash", str(path), str(work)],
        env={
            "PATH": os.environ["PATH"],
            "KEY4HEP_RELEASE": "2026-04-08",
            "FCC_PYTHIA_SEED": "42",
            "FCC_GAUDI_SEED": "42",
        },
        capture_output=True,
        text=True,
    )
    assert proc.returncode == status, proc.stderr
    assert work.exists() == (status == 0)
