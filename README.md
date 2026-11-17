# The evaluation of PyCG

This repository holds the evaluation of [PyCG](https://github.com/vitsalis/PyCG),
a call graph generator for Python that is fully static: the benchmarks with
their ground truths, the scripts of each research question, and the tables.
PyCG itself is the submodule `PyCG`, at the commit under test. PyCG was
first published at ICSE 2021 ([the paper](https://arxiv.org/pdf/2103.00587.pdf));
the artifact of that paper is the tag `evaluation-2021` of this repository
(Zenodo DOI [10.5281/zenodo.4457106](https://doi.org/10.5281/zenodo.4457106)).

## The research questions

**RQ1: how accurate is PyCG?** Precision and recall of the call edges, for
PyCG, the PyCG of 2021 and five prior tools (pyan3, JARVIS, CPyGraph,
HeaderGen, PyAnalyzer). Three references: the micro-benchmark (one small
program per language feature, in `PyCG/micro-benchmark`), the
macro-benchmark (30 real packages with hand-written, audited ground
truths) and, for recall only, the execution traces of 48 larger projects
(DyPyBench). The table of the macro-benchmark also shows the accuracy on
each batch of five packages at its first measurement (see "How the
benchmark grew").

**RQ2: why is each false positive and each false negative there?** Every
false positive and false negative of PyCG on the 30 packages has one
label, the mechanism that explains it. The table gives the share that
falls under limits that the design states on purpose.

**RQ3: does PyCG scale, and does it finish?** Each tool runs on a corpus
in three parts (the top 100 PyPI packages by downloads, the DyPyBench
projects, and ten large projects) under a time and a memory limit. The
table gives the share of packages that finish, and the time and the
memory.

The accuracy on packages that the analysis was not developed on is a
section of the table of RQ1 (see "How the benchmark grew"), not a question
of its own.

## The results

The tables are in `evaluation/results/`. The numbers below are those of
the tables as committed. RQ1 and RQ2: PyCG at commit `c5c6a89` (the
published main), on the ground truths after the audit of 2026-11-16. The
trace recall of RQ1 and the pilot of RQ3 are still of the older
PyCG commit `88ff2263ad`; their tables say so. The PyCG of 2021 is the 2021
analysis, PyCG commit `046479e`, pinned by its full hash (`ORIGINAL`); the
older tables name `8555f0590f`, an earlier commit with the same `pycg`
tree (`7a59545`), so the same engine. P is precision, R is recall.

**RQ1, edges inside the analysed package** ([full table](evaluation/results/rq1-accuracy.md)):

| Tool | Micro-benchmark, P / R | Macro-benchmark, 30 packages, P / R |
|---|---|---|
| PyCG | 0.925 / 0.933 | 0.945 / 0.878 |
| PyCG of 2021 | 0.869 / 0.605 | 0.913 / 0.397 (3 failed runs) |

The prior tools have no row here yet. Their stored runs of RQ1 were made at
the former limit of RQ1 (600 s and 2 GiB) and on the ground truths before
the audit, so the table gives each of them "needs rerun"; their numbers
come with the rerun at the one limit of every run (30 minutes and
16 GiB).

**RQ1, each batch at its first measurement** (PyCG, inside the package;
the section at the end of the same table). A historical record: these
numbers were measured on the ground truths before the audit of 2026-11-16,
which changed them afterwards (18,119 edges now), so they do not compare
with the table above.

| Batch | Frozen engine | Batches known then, P / R | The batch, P / R | Gap P | Gap R |
|---|---|---|---|---|---|
| 2 | `2bcc0b1d9a` | 0.922 / 0.888 | 0.933 / 0.749 | +0.011 | -0.138 |
| 3 | `343541c8ae` (`freeze-round-1`) | 0.945 / 0.898 | 0.862 / 0.921 | -0.082 | +0.023 |
| 4 | `b8469bd1b5` (`freeze-round-2`) | 0.950 / 0.921 | 0.864 / 0.912 | -0.087 | -0.008 |
| 5 | `8ef722cadd` (`freeze-round-3`) | 0.953 / 0.919 | 0.902 / 0.817 | -0.051 | -0.102 |

**RQ1, recall against execution traces** ([full table](evaluation/results/rq1-trace-recall.md)),
PyCG at `88ff2263ad`.
The rows of JARVIS, CPyGraph and PyAnalyzer are not run yet; HeaderGen
does not run on this corpus (it imports the analysed code):

| Tool | Projects finished | Recall inside the project, all 48 projects |
|---|---|---|
| PyCG | 47 of 48 | 89.8% |
| PyCG of 2021 | 38 of 48 | 48.9% |
| pyan3 | 46 of 48 | 74.3% |

**RQ2** ([full table](evaluation/results/rq2-causes.md)), PyCG at
`c5c6a89`, on the ground truths after the audit: 3,056 false positives and
false negatives on the 30 packages.

| Group | Edges | Share |
|---|---|---|
| Under a stated limit of the design | 2,834 | 92.7% |
| Scorer artefacts (two names of one function) | 140 | 4.6% |
| A gap: a mechanism that the design does not state as a limit | 36 | 1.2% |
| A rule that was measured and is not built | 45 | 1.5% |
| Ground-truth doubt | 1 | 0.0% |

**RQ3: a pilot of ten packages only** ([pilot table](evaluation/results/rq3-pilot.md)).
The run on the whole corpus is not done. The rows of JARVIS, CPyGraph and
PyAnalyzer are not run yet:

| Tool | Finished | Time, median / max | Peak memory, max |
|---|---|---|---|
| PyCG | 10 of 10 | 0.6 s / 132 s | 2,404 MiB |
| PyCG of 2021 | 7 of 10 (3 crashes) | 1.3 s / 84 s | 286 MiB |
| pyan3 | 10 of 10 | 0.3 s / 2.3 s | 113 MiB |

## The structure

```
README.md                 this file
PyCG/                     the tool (a submodule); PyCG/micro-benchmark is the micro-benchmark
macro-benchmark/
  README.md               how the benchmark was built, the conventions, how to re-record a trace
  projects.toml           the 30 projects: pin, scope, test command, batch
  projects/               empty; a copy of the sources, for reading only, goes here (not in git)
  requirements/           the pinned test environment of each project (to re-record its trace)
  dynamic/                the execution trace of each project
  ground-truth/           per project: <name>.json (the call graph, what scoring reads) and its
                          provenance (<name>.ledger, .md, .audit.md, .added.json, .rejected.json)
  plugins/                pytest plugins that three traces need
  selection/              how batches 3 to 5 were drawn
  check.py trace.py projects.py draw.py sites.py ledger.py
evaluation/
  README.md               the reference
  compose.yaml            every command
  common/                 the runner of PyCG, the scorer, what the tables share
  container/              the images of the inputs, the questions and the traces, and the
                          entries of the services
  tools/                  the adapters, images and pins of the prior tools
  results/                the tables, and the stored runs and graphs of the prior tools
  rq1-accuracy/ rq2-causes/ rq3-scale/   each with a short README.md
tests/                    the tests of the scripts
pyproject.toml uv.lock .github/
```

## How to run it again

You need Docker with the Compose plugin and the network (the hosts that
the builds reach are listed in `evaluation/README.md`, "The hosts of the
builds"). Nothing else runs
on your machine: no Python, no make, no git command on a repository of
someone else.

```sh
git clone --recurse-submodules <this repository>
cd pycg-evaluation
C="docker compose -f evaluation/compose.yaml"
```

On a Linux host where your user is not `1000:1000`, also
`export EVAL_UID=$(id -u) EVAL_GID=$(id -g)`, so that a service can write
the results and the files are yours.

```sh
$C build inputs         # every pinned input; the only step that downloads third-party code
$C build rq1            # the image of the questions, with the inputs in it
$C run --rm rq1         # RQ1: the micro-benchmark and the macro-benchmark
$C run --rm rq1-trace   # RQ1: recall against execution traces
$C run --rm rq2         # RQ2: the causes
$C run --rm rq3-pilot   # RQ3 on ten packages, before the whole corpus
$C run --rm rq3         # RQ3: scale and robustness
```

**The inputs come with the images.** The build of the inputs image
(`evaluation/container/Dockerfile.inputs`) fetches every input by its pin:
each project of the macro-benchmark by its commit (or the SHA-256 of its
sdist), the sources of the prior tools by their commits, the corpus of RQ3
and DyPyBench's traces by SHA-256 and size. A source that is not its pin fails the build. Every other image
copies what it needs from the inputs image when it is built, so a run has
no network and fetches nothing. Each image copies only the inputs that its
services read. As built, the inputs image is 4.35 GB, the image of the
questions 2.85 GB, the images of pyan3, JARVIS and PyAnalyzer about 2.7 GB
each, CPyGraph's 4.26 GB and HeaderGen's 0.70 GB, most of each the corpus
of RQ3 (measured before its ten large projects were added): plan for about 25 GB of disk with Docker's build cache.

Each command writes its table to `evaluation/results/`. These commands
measure PyCG and the PyCG of 2021. The rows of the prior tools come from
the tools' own services, `$C run --rm TOOL QUESTION` (`TOOL`: `pyan`,
`jarvis`, `cpygraph`, `pyanalyzer`, `headergen`). For RQ1 their runs are
stored in the repository (`evaluation/results/tools/` and
`evaluation/results/graphs/`), so `rq1` scores them without running them.
A tool that did not run has a row that says so, with the command that runs
it. The ordered list of every command of a full run, and which
measurements must run alone, is in [`evaluation/README.md`](evaluation/README.md),
section "The full run".

## The artifacts, and what you need them for

- **To score a tool** you need `macro-benchmark/projects.toml` (the
  analysed packages of each project), `macro-benchmark/ground-truth/<name>.json`
  (the call graph), `macro-benchmark/dynamic/<name>.json` (the trace: its
  aliases match names) and the tool's graph. The graphs of the prior tools
  are in `evaluation/results/graphs/<tool>/<project>.json`.
- **To run a tool again** you also need the sources: the images hold
  them (at `/inputs/macro-benchmark/projects/`), each checked against its
  pin when the inputs image was built. To read them on your machine, copy
  them out of that image into `macro-benchmark/projects/` (git ignores
  them; no service reads that copy):
  `$C run --rm -v "$PWD/macro-benchmark/projects:/repo/macro-benchmark/projects" inputs cp -RP /inputs/macro-benchmark/projects/. macro-benchmark/projects/`
- **To record a trace again** you need `macro-benchmark/requirements/<name>.txt`
  and the `trace` service: `NAME=<name> $C run --rm trace --compare` (see
  "Re-recording a trace" in [`macro-benchmark/README.md`](macro-benchmark/README.md)).
- **A ground truth is not regenerated by a command.** It is the trace of
  the project's test suite, minus the edges that a reviewer rejected
  (`.rejected.json`), plus the edges that a reviewer added by reading the
  source (`.added.json`), and then audited blind (`.audit.md`). The ledger
  (`.ledger`) holds every judgement, one entry per call site.

Everything is pinned: PyCG by the commit of the submodule; every benchmark
project and every prior tool by a commit or the SHA-256 of an sdist; every
Python image by a digest; every Python dependency by a hash.

## What scoring is

A call graph is a set of edges, each a pair (caller, callee) of names. An
edge is *inside* if its callee is in the analysed package, else *outside*
(builtins, the standard library, other packages). Inside is the headline;
outside is reported apart. A tool's edge that the ground truth has is a
true positive (TP); one that it does not have is a false positive (FP); a
ground-truth edge that the tool does not report is a false negative (FN).
Precision is TP / (TP + FP), recall is TP / (TP + FN). The counts of the
projects are added first; precision and recall are computed from the sums.
Names are compared as written, with two exceptions for outside callees: a
name that the project's trace knows as another name of a traced function
counts as that function (the trace's aliases), and a committed, reviewed
table joins the names of one function of the standard library
(`evaluation/common/same_function.json`). A run that fails (a crash, a
timeout) counts every edge of its project as a false negative.

## How the benchmark grew

The macro-benchmark was built in batches of five packages (`batch` in
`projects.toml`). Batch 1 is the five packages that the analysis was first
developed on. Each later batch was measured once, by a frozen engine,
before anyone read its false positives and false negatives: the table
above. Then they were read, the analysis was improved on them, and the
next batch was built. So every batch was used for development in the end.
The five packages of the ICSE 2021 paper were added between batches 3 and
4. The maintainer's rule to stop: when every rule that no decision blocks,
measured together on the newest batch, gains less than one point of
precision and of recall. Batch 5 met it (0.76 and 0.07 points), so it is
the last.

**The audit.** When the five batches were built, the 30 ground truths were
audited in four rounds (2026-11-15 and 2026-11-16), all 30 each time,
under one set of global rules for what is an edge and how it is named.
The ground truths went from 17,618 to 18,121 edges; a correction after the
audit removed two edges of fabric (18,119 now). The tables of RQ1 and
RQ2 are of the audited ground truths. The audit records (reports,
registers, patches, evidence) are kept outside the repository; see
[`macro-benchmark/README.md`](macro-benchmark/README.md).

## How to run the tests

The tests of the evaluation's scripts are in `tests/`. A run of the
evaluation does not need them: they are for work on the scripts. Install
[uv](https://docs.astral.sh/uv/). Then, from the root directory:

```sh
uv run pytest                                                # all tests
uv run ruff check evaluation tests macro-benchmark           # lint
uv run ruff format --check evaluation tests macro-benchmark  # format check
```

The tests import `pycg` from the submodule `PyCG`, so the checkout needs
it. They run on Python 3.12 to 3.15. The tests of the scorer and of the
tracer need Python 3.14 or later and skip themselves on an older Python.
No test needs Docker, the network or the sources of the projects.
