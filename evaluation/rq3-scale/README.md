# RQ3: scale and robustness

**What it measures.** Does each tool finish on real packages, and in what
time and memory. The corpus has three parts: the top 100 packages of PyPI
by downloads, the 50 projects of DyPyBench, and ten large projects (the
projects of NameRTS's dataset, each at one commit). Each analysis has
30 minutes and 16 GiB.

**Commands** (`C="docker compose -f evaluation/compose.yaml"`; run them
one service at a time, with nothing else on the machine, at K = 1 (the
default) for PyCG and every tool: they measure time and memory, and a
tool's run at another K than PyCG's is "needs rerun"):

```sh
$C run --rm rq3-pilot        # ten packages, five of each of the first two parts: checks that the question runs
$C run --rm rq3              # the whole corpus
$C run --rm TOOL rq3-pilot   # a prior tool on the ten packages
$C run --rm TOOL rq3         # a prior tool on the whole corpus
```

The maintainer runs RQ3 at K = 1: its results are the time and the
memory of each analysis alone. The services take `--jobs K` (K analyses
at a time, each with its own limits); the records do not depend on K, but
the times and the memory are measured beside the other K - 1 analyses, so
the table writes K beside the platform. See "Measurements and `--jobs K`"
in `../README.md`.

**Inputs.** `corpus/pypi-top-100.json`, `corpus/dypybench.json` and
`corpus/large-projects.json`: the manifests, which pin each package by URL
and SHA-256 (or commit). The source block of `large-projects.json` says
where its list comes from: the ten projects of NameRTS (Wang, Pradel, Liu,
ISSTA 2026), each at the parent commit of the first target of its dataset. The
fetched and unpacked packages (in the image: `/inputs/corpus/`, from the
inputs image). The submodule `PyCG`
at a clean commit, and in its history the 2021 analysis (commit `046479e`). The runs of the
prior tools (`../results/tools/<row>/rq3-pilot.json`, `rq3.json`).

**Outputs** (in `../results/`): `rq3-pilot.md` and `.json` (the pilot);
`rq3-scale.md` and `.json`, with the report of each part
(`robustness-pypi-top-100.*`, `robustness-dypybench.*`,
`robustness-large-projects.*`).

**How to read the table.** Per tool, a row for the whole corpus ("all")
and one per part. "With a target": the packages that have Python source to
analyse. "Finished" and "Share": the packages whose analysis ended within
the limits. "Failures": the others by status (crash, timeout, memory).
Time and memory: median, 90th percentile and maximum over the finished
runs. The pilot table also lists each package and tool.

**Known limits.** Only the pilot is committed: ten packages, with the
limit of 8 GiB that it had then. The run on the whole corpus is not done,
and its time and memory figures need a quiet Linux host. The rows of
JARVIS, CPyGraph and PyAnalyzer are not run yet. HeaderGen does not run on
this corpus: it imports the code that it analyses, and no security scan
covers the corpus. A package that the target rule does not cover is
listed with the reason, not guessed.

Reference: [`../README.md`](../README.md), sections "RQ3: scale and
robustness" and "Robustness on a corpus".
