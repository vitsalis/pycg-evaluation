# The evaluation of PyCG

This repository holds the evaluation of [PyCG](https://github.com/vitsalis/PyCG),
a call graph generator for Python that is fully static: the benchmarks with
their ground truths, the scripts of each research question, and the tables.
PyCG itself is the submodule `PyCG`, at the commit under test. PyCG was
first published at ICSE 2021 ([the paper](https://arxiv.org/pdf/2103.00587.pdf));
the artifact of that paper is the tag `evaluation-2021` of this repository
(Zenodo DOI [10.5281/zenodo.4457106](https://doi.org/10.5281/zenodo.4457106)).

## The research questions and their results

The tables are files of the repository, in `evaluation/results/`: read
them without running anything (see "Reading the results without running
anything"). Every number below is read from those files. All rows are of
one run, on the machine of "Reproducing the results": PyCG at commit
`c5c6a89` (the submodule `PyCG`), the PyCG of 2021 (PyCG commit `046479e`,
the 2021 analysis) and five prior tools, each pinned by its commit. The
ground truths are those after the audit of 2026-11-16. P is precision, R
is recall.

### RQ1: how accurate is PyCG?

Precision and recall of the call edges against three references: the
micro-benchmark (one small program per language feature, in
`PyCG/micro-benchmark`), the macro-benchmark (30 real packages with
hand-written, audited ground truths) and, for recall only, the execution
traces of 48 larger projects (DyPyBench).

**The micro-benchmark and the 30 packages** ([full table](evaluation/results/rq1-accuracy.md)).
Micro: the 622 cases that every tool can parse (up to Python 3.12),
edges inside the case. The 30 packages: all projects together; *inside*
counts the edges whose callee is in the analysed package (the headline),
*outside* the edges to builtins, the standard library and other packages.
A failed run counts every edge of its project as a false negative.

| Tool | Micro, P / R | 30 packages, inside, P / R | 30 packages, outside, P / R | Runs |
|---|---|---|---|---|
| PyCG | 0.925 / 0.936 | 0.945 / 0.878 | 0.941 / 0.890 | - |
| PyCG of 2021 | 0.868 / 0.605 | 0.913 / 0.397 | 0.670 / 0.445 | 3 failed runs |
| pyan3 | 0.642 / 0.575 | 0.662 / 0.492 | names no outside callee | - |
| JARVIS | 0.573 / 0.618 | 0.645 / 0.432 | 0.867 / 0.399 | - |
| JARVIS, class edges dropped | 0.794 / 0.618 | 0.776 / 0.432 | 0.867 / 0.399 | - |
| JARVIS --precision | 0.873 / 0.606 | 0.928 / 0.416 | 0.869 / 0.340 | - |
| CPyGraph | 0.876 / 0.753 | 0.620 / 0.116 | 0.884 / 0.020 | - |
| HeaderGen (hybrid: imports the analysed modules) | 0.884 / 0.571 | 0.901 / 0.351 | 0.793 / 0.509 | 22 of 30 projects measured, 2 failed runs |
| PyAnalyzer | 0.846 / 0.619 | 0.935 / 0.224 | names no outside callee | 1 failed run |
| PyAnalyzer (call relations of the dependency report) | 0.823 / 0.591 | 0.560 / 0.374 | names no outside callee | 1 failed run |

JARVIS has three rows: the tool as published, the same graph without its
edges to classes, and its `--precision` mode. PyAnalyzer has two: its call
graph, and the call relations of its dependency report. HeaderGen imports
the modules that it analyses, so a package needs its dependencies in
HeaderGen's image: 22 of the 30 have them (`prior-tools.md` gives the
reason for each of the other 8). pyan3 and PyAnalyzer name no
callee outside the package, so their outside recall is 0.

**Recall against execution traces** ([full table](evaluation/results/rq1-trace-recall.md)).
The traced calls of DyPyBench's projects (48 with a trace). A run that
did not finish finds none of its project's edges. *Inside*: the traced
edges whose callee is a definition of the project; *all*: every traced
edge that maps to a name.

| Tool | Projects finished | Recall, inside | Recall, all edges |
|---|---|---|---|
| PyCG | 48 of 48 | 90.4% | 84.3% |
| PyCG of 2021 | 38 of 48 | 48.9% | 39.2% |
| pyan3 | 46 of 48 | 74.3% | 26.4% |
| JARVIS | 48 of 48 | 68.6% | 52.7% |
| JARVIS, class edges dropped | 48 of 48 | 68.6% | 52.7% |
| JARVIS --precision | 48 of 48 | 66.4% | 47.6% |
| CPyGraph | 48 of 48 | 28.9% | 13.7% |
| HeaderGen (hybrid: imports the analysed modules) | not run: it imports the analysed code | | |
| PyAnalyzer | 48 of 48 | 49.0% | 17.4% |
| PyAnalyzer (call relations of the dependency report) | 48 of 48 | 60.6% | 21.6% |

**Each batch at its first measurement** (PyCG, inside the package; the
last section of [the table of RQ1](evaluation/results/rq1-accuracy.md)).
The macro-benchmark grew in batches of five packages (see "How the
benchmark grew"). Each batch was measured once by a frozen engine before
anyone read its false positives and false negatives. This is a recorded
history: the ground truths changed afterwards, so these numbers do not
compare with the table above.

| Batch | Frozen engine | Batches known then, P / R | The batch, P / R | Gap P | Gap R |
|---|---|---|---|---|---|
| 2 | `2bcc0b1d9a` | 0.922 / 0.888 | 0.933 / 0.749 | +0.011 | -0.138 |
| 3 | `343541c8ae` (`freeze-round-1`) | 0.945 / 0.898 | 0.862 / 0.921 | -0.082 | +0.023 |
| 4 | `b8469bd1b5` (`freeze-round-2`) | 0.950 / 0.921 | 0.864 / 0.912 | -0.087 | -0.008 |
| 5 | `8ef722cadd` (`freeze-round-3`) | 0.953 / 0.919 | 0.902 / 0.817 | -0.051 | -0.102 |

### RQ2: why is each false positive and each false negative there?

Every false positive and false negative of PyCG on the 30 packages has one
label: the mechanism that explains it ([full table](evaluation/results/rq2-causes.md)).
There are 3,056 of them, inside and outside the package.

| Group | False positives and false negatives | Share |
|---|---|---|
| Under a stated limit of the design | 2,834 | 92.7% |
| Scorer artefacts (two names of one function, a naming convention) | 140 | 4.6% |
| Ground-truth doubt | 1 | 0.0% |
| A rule that was measured and is not built | 45 | 1.5% |
| A gap: a mechanism that the design does not state as a limit | 36 | 1.2% |

A stated limit is a kind of edge that the design leaves out on purpose
(`PyCG/docs/design.md`). The full table lists the 44 stated limits, each
with its count.

### RQ3: does PyCG scale, and does it finish?

Each tool runs once on each package of a corpus in three parts: the top
100 PyPI packages by downloads, the 50 projects of DyPyBench (49 with
source to analyse) and ten large projects (the projects of the NameRTS
dataset). That is 159 packages with a target. Each run has 30 minutes and
16 GiB ([full table](evaluation/results/rq3-scale.md)). *Finished*: the
run ended within both limits. Time and memory: median and maximum over the
finished runs.

| Tool | Finished | PyPI top 100 | DyPyBench | Large projects | Did not finish | Time, median / max | Peak memory, median / max | Recall of RQ1 (30 packages, inside) |
|---|---|---|---|---|---|---|---|---|
| PyCG | 155 of 159 | 98 / 100 | 49 / 49 | 8 / 10 | memory 3, timeout 1 | 0.82 s / 599 s | 49.3 / 14,504 MiB | 0.878 |
| PyCG of 2021 | 117 of 159 | 77 / 100 | 39 / 49 | 1 / 10 | crash 26, memory 2, timeout 14 | 1.28 s / 946 s | 48.8 / 362 MiB | 0.397 |
| pyan3 | 152 of 159 | 96 / 100 | 47 / 49 | 9 / 10 | crash 7 | 0.56 s / 145 s | 41.6 / 1,368 MiB | 0.492 |
| JARVIS | 150 of 159 | 96 / 100 | 49 / 49 | 5 / 10 | crash 3, memory 6 | 0.64 s / 68.5 s | 45.5 / 4,840 MiB | 0.432 |
| CPyGraph | 158 of 159 | 100 / 100 | 49 / 49 | 9 / 10 | timeout 1 | 1.69 s / 458 s | 33.4 / 1,778 MiB | 0.116 |
| HeaderGen (hybrid: imports the analysed modules) | not run: it imports the analysed code | | | | | | | |
| PyAnalyzer | 153 of 159 | 96 / 100 | 49 / 49 | 8 / 10 | crash 5, memory 1 | 1.04 s / 720 s | 45.9 / 10,041 MiB | 0.224 |

Finishing is not accuracy: a tool that models less can finish more
often. CPyGraph finishes the most packages (158 of 159) and PyCG 155, but
on the 30 packages of RQ1 CPyGraph finds 0.116 of the edges inside the
package and PyCG 0.878 (the last column). The four runs of PyCG that did
not finish, with their time and peak memory:

- numpy (timeout, 1,800 s, 7,073 MiB): at the time limit;
- sqlalchemy (memory, 458 s, 16,393 MiB), scikit-learn (memory, 678 s, 16,385 MiB) and sympy (memory, 428 s, 16,510 MiB): over the memory limit.

The PyCG of 2021 crashes on 26 packages. The time of a run depends on the
machine (see "Reproducing the results").

The accuracy on packages that the analysis was not developed on is a
section of RQ1 (the first measurements above, and "How the benchmark
grew"), not a question of its own.

## Reading the results without running anything

Everything that a table reads or writes is in the repository. Nothing
must run to read the results.

- **`evaluation/results/<table>.md`** is the table for a reader. Its first
  lines say which script wrote it and the section of
  `evaluation/README.md` that explains it. **`<table>.json`** beside it
  holds the same data unrounded, with what the run was: the versions of the
  tools, the Python, the platform, the limits, and the digests of the
  inputs. The tables: `rq1-accuracy` (RQ1), `prior-tools` (the prior
  tools' runs scored, which RQ1 reads), `rq1-trace-recall` (RQ1, traces),
  `rq2-causes` (RQ2), `rq3-scale` (RQ3), and `robustness-<part>` (the run
  of PyCG and the PyCG of 2021 on each part of RQ3's corpus, package by
  package).
- **`evaluation/results/tools/<row>/<question>.json`** is the stored run
  of a prior tool: per job its status, its time, its peak memory and the
  SHA-256 of its input and of its graph. `rq1.json` also holds the graphs
  of the micro cases.
- **`evaluation/results/graphs/<row>/<project>.json`** is the call graph of
  each prior tool on each of the 30 packages, in the benchmark's names. A
  reader can score a tool again from these files alone.
- **`macro-benchmark/ground-truth/<name>.json`** is the ground truth of a
  package. **`evaluation/rq2-causes/labels.json`** holds every false
  positive and false negative of PyCG with its label and a note.

The pilot of RQ3 (`rq3-pilot`, ten packages) checks that the question runs
before the full run. It writes `results/rq3-pilot.*` and
`results/tools/<row>/rq3-pilot.json`. These files are not results, and the
repository does not keep them.

## Reproducing the results

**The machine of the committed run.** A Hetzner Cloud CCX43: 16 dedicated
vCPUs, 64 GB of RAM, Ubuntu 26.04.1 LTS (linux x86_64), Docker Engine
29.10.0 from Docker's apt repository. RQ1 and the trace recall
ran at commit `f53515b` of this repository, RQ3 at commit `7d1e46a`. The
Python of the runner is 3.14.10.

**The limits.** Every run of every tool has 30 minutes and 16 GiB of
resident memory. RQ1 and the trace recall ran the prior tools three jobs
at a time (`--jobs 3`): their results are edges, not times. RQ3 ran one
job at a time (K = 1), one service at a time, with nothing else on the
machine: its results are times and memory.

You need Docker with the Compose plugin and the network (the hosts that
the builds reach are listed in `evaluation/README.md`, "The hosts of the
builds"). Nothing else runs on your machine: no Python, no make, no git
command on a repository of someone else. The images take about 25 GB of
disk with Docker's build cache (measured before the ten large projects
joined the corpus, which add to it).

```sh
git clone --recurse-submodules <this repository>
cd pycg-evaluation
C="docker compose -f evaluation/compose.yaml"
export EVAL_UID=$(id -u) EVAL_GID=$(id -g)   # so that a service can write the results
```

The commands, in this order:

```sh
# 1. The images (20 to 30 minutes). Only the inputs image downloads
#    third-party code; every other image copies its inputs from it.
$C build inputs
$C build rq1 pyan jarvis cpygraph pyanalyzer headergen
for name in aiocache bottle configargparse itables markdown2 marshmallow tenacity; do
    NAME=$name $C build headergen-deps
done

# 2. RQ1 and RQ2 (about 3 hours on the machine above, with the prior tools).
for tool in pyan jarvis cpygraph pyanalyzer headergen; do
    EVAL_MEMORY=50g $C run --rm $tool rq1 --jobs 3
done
for name in aiocache bottle configargparse itables markdown2 marshmallow tenacity; do
    NAME=$name $C run --rm headergen-deps rq1
done
for tool in pyan jarvis cpygraph pyanalyzer; do
    EVAL_MEMORY=50g $C run --rm $tool rq1-trace --jobs 3
done
$C run --rm rq1                               # results/rq1-accuracy.*, prior-tools.*
$C run --rm rq2                               # results/rq2-causes.*
EVAL_MEMORY=50g $C run --rm rq1-trace --jobs 3   # results/rq1-trace-recall.*

# 3. RQ3 (13 hours 20 minutes on the machine above), one service at a time,
#    nothing else on the machine.
for tool in pyan jarvis cpygraph pyanalyzer; do
    $C run --rm $tool rq3
done
$C run --rm rq3                               # results/rq3-scale.*, robustness-*
```

`$C run --rm rq3-pilot` runs the pilot of RQ3 first, if you want to check
the question on ten packages. A run in the background needs `-T`
(`nohup $C run --rm -T rq3 > rq3.log 2>&1 &`). `evaluation/README.md`,
section "The full run", explains each step.

**What must come out equal on any machine.** The counts of RQ1 and RQ2:
every TP, FP and FN, and so every precision and recall. PyCG's analysis is
deterministic: the same input gives the same graph, and the run on
linux/aarch64 gave the same counts for PyCG as the committed run on
x86_64. RQ2 counts committed labels. One prior tool can vary: PyAnalyzer
hashes its heap objects by `id()`, and one of its rules depends on that
order (`prior-tools.md` says where). The build of the inputs image checks each input against its pin,
and the JSON of a table records the digest of the benchmark that it read
(`inputs` in `prior-tools.json`).

**What depends on the machine.** The times and the peak memory of RQ3,
and with the times which runs end at the time limit: a run near 30
minutes can finish on a faster machine. The counts of RQ3 (finished,
failures) can therefore differ on another machine, and its times do.

**"with changes" in a table.** A table names the commit of this
repository that it read the benchmark from, and adds "with changes" when
the checkout had uncommitted files. During a run, the result files of the
run itself are not committed yet, so the committed `prior-tools.md` says
"`macro-benchmark` at `f53515b300`, with changes". The digest of the
inputs, not the commit, identifies the benchmark.

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
