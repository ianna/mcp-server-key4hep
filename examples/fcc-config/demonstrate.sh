#!/usr/bin/env bash
# Run in a fresh shell on LXPLUS. This is the ordinary k4run workflow plus checks.
set -euo pipefail
: "${FCC_PYTHIA_SEED:?Export an explicit Pythia seed}"
: "${FCC_GAUDI_SEED:?Export an explicit Gaudi seed}"
: "${KEY4HEP_RELEASE:?Export an explicit dated release}"
[[ "$KEY4HEP_RELEASE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || exit 2
[[ -z "${ROOTSYS:-}" ]] || { echo "Start a fresh shell without a configured ROOT stack" >&2; exit 2; }
project=$(cd "$(dirname "$0")/../.." && pwd)
work=${1:?Supply a NEW absolute work directory}
[[ "$work" == /* ]] || { echo 'Use an absolute work directory' >&2; exit 2; }
[[ ! -e "$work" && ! -L "$work" ]] || { echo "Work directory already exists: $work" >&2; exit 2; }
export project work
# Checked-out source is supplied to the stack Python without requiring MCP/uv.
# CVMFS setup is written for an ordinary interactive shell, not strict mode.
# Capture its status explicitly and restore our checks before doing any work.
set +eu
set +o pipefail
source /cvmfs/sw.hsf.org/key4hep/setup.sh -r "$KEY4HEP_RELEASE"
setup_status=$?
set -euo pipefail
if (( setup_status != 0 )); then
  echo "Key4hep setup failed (status $setup_status)" >&2
  exit "$setup_status"
fi
mkdir "$work"
export PYTHONPATH="$project/src${PYTHONPATH:+:$PYTHONPATH}"
python3 - <<'PY'
import json, os, pathlib, subprocess
project, work = map(pathlib.Path, (os.environ['project'], os.environ['work']))
lock = json.loads((project / 'examples/fcc-config/upstream.json').read_text())
for name, item in lock.items():
    repo = work / name
    subprocess.run(['git', 'clone', '--no-checkout', item['url'], str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'checkout', '--detach', item['commit']], check=True)
PY
for side in left right; do
  run="$work/$side"
  mkdir "$run"
  cd "$run"
  # Keep upstream card energy (91.188 GeV), process settings and vertex smearing.
  command=(k4run "$work/k4Gen/k4Gen/options/pythia.py"
    "$project/examples/fcc-config/seeds.py" -n 10
    --IOSvc.Output events.e4h.root
    --Pythia8.PythiaInterface.pythiacard
    "$work/FCC-config/FCCee/Generator/Pythia8/p8_ee_Zmumu_ecm91.cmd")
  python3 - "${command[@]}" <<'PY'
import hashlib, json, os, pathlib, sys
project, work = map(pathlib.Path, (os.environ['project'], os.environ['work']))
lock = json.loads((project / 'examples/fcc-config/upstream.json').read_text())
inputs = [dict(repository=str(work / name), path=item.get('card', item.get('steering')))
          for name, item in lock.items()]
inputs += [dict(repository=str(project), path='examples/fcc-config/' + name)
           for name in ('seeds.py', 'demonstrate.sh', 'upstream.json')]
spec = dict(release=os.environ['KEY4HEP_RELEASE'],
            seeds=dict(pythia=int(os.environ['FCC_PYTHIA_SEED']),
                       gaudi=int(os.environ['FCC_GAUDI_SEED'])),
            command=sys.argv[1:], inputs=inputs, cwd=str(pathlib.Path.cwd()),
            setup=dict(path='/cvmfs/sw.hsf.org/key4hep/setup.sh',
                       sha256=hashlib.sha256(pathlib.Path('/cvmfs/sw.hsf.org/key4hep/setup.sh').read_bytes()).hexdigest()),
            source='FCC tutorial Pythia8 dimuon example; upstream defaults preserved')
pathlib.Path('spec.json').write_text(json.dumps(spec, indent=2))
PY
  python3 -m mcp_server_key4hep.cli prepare . --spec spec.json > preparation.json
  python3 -m mcp_server_key4hep.probe
  # Commit hashes and exact argv are in preparation.json before execution.
  "${command[@]}" > generation.stdout.log 2> generation.stderr.log
  python3 -m mcp_server_key4hep.cli record . --output events.e4h.root \
    --events 10 --exit-code 0 --artifact environment.json \
    --artifact generation.stdout.log --artifact generation.stderr.log
  python3 -m mcp_server_key4hep.cli verify provenance.json
 done
python3 -m mcp_server_key4hep.cli compare "$work/left/events.e4h.root" \
  "$work/right/events.e4h.root" --report "$work/comparison.json"
