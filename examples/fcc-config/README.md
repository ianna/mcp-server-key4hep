# FCC tutorial dimuon demonstration

This follows the [FCC event generation tutorial](https://hep-fcc.github.io/fcc-tutorials/main/2-gen-and-fastsim/2-1-event-generation/README.html)
using the existing [FCC-config Pythia8 cards](https://github.com/HEP-FCC/FCC-config/tree/main/FCCee/Generator/Pythia8).
`upstream.json` pins both source repositories by full commit:

- FCC-config `4a559aec93298d4a0af509a732c73570756f32f4`:
  `FCCee/Generator/Pythia8/p8_ee_Zmumu_ecm91.cmd`.
- k4Gen `bc82cca1a6bf66d5882f30af57d0819161990461`:
  `k4Gen/options/pythia.py`.

The card sets 91.188 GeV. The upstream steering supplies vertex smearing,
EventHeader and MCParticlesStable. We do not replace these settings. `seeds.py`
is a seed-only Gaudi options overlay loaded after upstream steering: it sets
Pythia's seed and explicitly configures the Gaudi Ranlux engine used by vertex
smearing. Both seeds must be provided; the script has no default seed.

The tutorial's older `--out.filename` spelling is replaced here with
`--IOSvc.Output`, matching the pinned steering's IOSvc configuration. Compatibility
of these pinned sources with release 2026-04-08 must still be checked on LXPLUS.
This is an integration demonstration awaiting execution, not a claimed passing run.

## Run on LXPLUS

Commit local edits first (the demonstration checks the overlay, shell script and
lock file against Git HEAD). From a **fresh shell**, at the project root:

```bash
export KEY4HEP_RELEASE=2026-04-08
export FCC_PYTHIA_SEED=42
export FCC_GAUDI_SEED=42
bash examples/fcc-config/demonstrate.sh "$PWD/fcc-demonstration"
```

The destination must not exist. The script checks out the pinned sources, then
executes the same standard `k4run` command twice in separate directories. It
records source commits before execution, captures environment and generation
logs, validates both outputs, writes and verifies both provenance sidecars, and
writes `comparison.json`. It stops on any failed command or content difference.
It then runs two control jobs, changing only the Pythia seed and only the Gaudi
seed, and requires each to *differ* from the left run (`control-*.json`). This
catches a declared seed that the job silently ignores, which would otherwise
still replay identically.
It does not invoke MCP or a custom generation service.

The command in each run is equivalent to:

```bash
k4run /path/to/k4Gen/k4Gen/options/pythia.py \
  /path/to/this/project/examples/fcc-config/seeds.py -n 10 \
  --IOSvc.Output events.e4h.root \
  --Pythia8.PythiaInterface.pythiacard \
  /path/to/FCC-config/FCCee/Generator/Pythia8/p8_ee_Zmumu_ecm91.cmd
```

Inspect `left/preparation.json`, `left/environment.json`, the generation logs,
`left/provenance.json`, the corresponding right-hand files, and `comparison.json`.
Success requires verified provenance on each run, `valid: true` and
`identical: true` in `comparison.json`, and `identical: false` in both controls. This establishes exact replay within the
reported collection scope for this configuration and environment only.

To demonstrate reuse on another FCC card, use its committed path in your normal
command and provenance input list. No new MCP tool or process implementation is
needed. A changed card/configuration is a separate experiment, not an identical
replay. The provenance API never edits or commits cards on your behalf.
