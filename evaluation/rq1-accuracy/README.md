# RQ1: accuracy

**What it measures.** Precision and recall of the call edges of each tool
(PyCG, the PyCG of 2021, pyan3, JARVIS, CPyGraph, HeaderGen, PyAnalyzer)
against three references:

- the micro-benchmark (`PyCG/micro-benchmark/snippets`): one small program
  per language feature, each with its exact call graph;
- the macro-benchmark (`macro-benchmark/`): 30 real packages, in six
  batches of five, each with a hand-written, audited ground truth;
- for recall only, the execution traces of DyPyBench's projects.

**Commands** (`C="docker compose -f evaluation/compose.yaml"`):

```sh
$C run --rm rq1         # the micro-benchmark and the macro-benchmark
$C run --rm rq1-trace   # recall against execution traces
$C run --rm TOOL rq1         # a prior tool: its runs and graphs, read by rq1
$C run --rm TOOL rq1-trace   # a prior tool: its graphs, read by rq1-trace
```

**Inputs.** The submodule `PyCG` at a clean commit, and in its history
the 2021 analysis, commit `046479e` (the PyCG of 2021). The sources of the 30
projects (in the image: `/inputs/macro-benchmark/projects/`, from the
inputs image), their ground truths and traces. The stored runs and graphs
of the prior tools (`../results/tools/`, `../results/graphs/`).
`first-measurements.json`: recorded data, never measured again. For the
traces: DyPyBench's projects and its traces (in the image:
`/inputs/corpus/`).

**Outputs** (in `../results/`): `rq1-accuracy.md` and `.json`;
`prior-tools.md` and `.json` (the prior tools scored, written first);
`rq1-trace-recall.md` and `.json`.

**How to read the table.** A cell is `P 0.945 / R 0.878 (7531/441/1046)`:
precision, recall, then TP/FP/FN. One row per tool. Columns: the micro
cases that every tool can parse; the micro cases that need a newer
Python; one column per batch; all 30 projects. The first table counts the
edges whose callee is inside the analysed package (the headline), the
second the edges to code outside it. "n failed runs": a run that failed
counts every edge of its project as a false negative. "n of m projects
measured": the tool could not run on the others, and they are left out. A
cell without a measurement says why. The last section, "Accuracy on each
batch at its first measurement", shows what a frozen engine gave on each
batch before anyone read its false positives and false negatives, and the
gap to the batches known then.

**Known limits.** A ground truth is hand-written: it can be wrong
(`../rq2-causes/` counts one doubt). Names of callees outside the package
depend on how a tool models code that it does not analyse, so the outside
columns compare less well. The first measurements of batches 2 to 4 were
scored before the table of names of one function existed. The traces of
DyPyBench are a small and biased sample of the calls of each project, and
a trace gives recall only. HeaderGen does not run on the traces (it
imports the analysed code, and DyPyBench's projects are not scanned).

Reference: [`../README.md`](../README.md), sections "RQ1: accuracy", "How
the benchmark grew", "How the scores are computed", "Trace recall" and
"How names are matched".
