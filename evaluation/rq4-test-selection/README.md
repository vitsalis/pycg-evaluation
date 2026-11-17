# RQ4: regression test selection

**What it measures.** Whether the call graph is useful to a client. The
client is regression test selection: after a commit, select the test
files that must run again. The dataset is NameRTS's (ten projects, each
with target commits and the tests that each commit really affects). Each
tool's call graph of the parent commit selects the tests. The metrics are
those of NameRTS: the safe rate (no affected test is left out), the test
reduction and the precision, as the mean over the ten projects, beside the
published selections of NameRTS and BabelRTS.

**Commands** (`C="docker compose -f evaluation/compose.yaml"`; the
selections do not depend on the load, so a higher `--jobs K` is fine; the
times of the pilot are measured beside K - 1 others):

```sh
$C run --rm rq4-pilot        # the first target of each project: checks that the question runs
$C run --rm rq4              # every target
$C run --rm TOOL rq4-pilot   # a prior tool: its graphs, read by rq4-pilot
$C run --rm TOOL rq4         # a prior tool: its graphs, read by rq4
```

`--jobs K` runs K selections at a time (one per tool and project; a prior
tool: K of its analyses), each with its own limits; the
records do not depend on K, but the times are measured beside the other
K - 1 analyses, so the table writes K beside the platform. The service
needs K analyses of 16 GiB and 2 GiB more, and K CPUs (at least 4):
`EVAL_MEMORY` (default 18g) and `EVAL_CPUS` (default 4) set them; see
"Measurements and `--jobs K`" in `../README.md`.

```sh
EVAL_MEMORY=50g $C run --rm rq4 --jobs 3                      # PyCG, three analyses at a time
EVAL_MEMORY=130g EVAL_CPUS=8 $C run --rm pyan rq4 --jobs 8    # a prior tool, eight jobs at a time
```

**Inputs.** `corpus/rts.json`: the manifest, which pins the NameRTS
commit, each file read from it, the projects, the target commits and the
digest of the file list of each commit. The export of the projects'
commits as plain files (in the image: `/inputs/rts/export/`, from the
inputs image; the clones never leave its build). The submodule `PyCG` at a clean
commit, and in its history the 2021 analysis (commit `046479e`). The graphs of the prior
tools (the Docker volume `tool-graphs`).

**Outputs** (in `../results/`): `rq4-pilot.md` and `.json` (the pilot);
`rq4-test-selection.md` and `.json`.

**How to read the table.** The pilot table has one row per tool and
project: the status of the analysis of the first target (ok, crash,
timeout, memory), its time and its peak memory. The full table has one row
per tool with the mean of each metric; "over n of N" says on how many
projects a metric is defined. A target whose files could not be exported
is left out of every row and listed with the reason.

**Known limits.** Only the pilot is committed: one target per project, the
analysis only, with the limit of 6 GiB that it had then (the full run has
16 GiB). No selection is scored yet. The rows of JARVIS, CPyGraph and
PyAnalyzer are not run yet. HeaderGen does not run on this dataset: it
imports the code that it analyses, and no security scan covers the
dataset.

Reference: [`../README.md`](../README.md), sections "RQ4: regression test
selection" and "Regression test selection".
