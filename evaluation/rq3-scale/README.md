# RQ3: scale and robustness

**What it measures.** Does each tool finish on real packages, and in what
time and memory. The corpus is the top 100 packages of PyPI by downloads
and the 50 projects of DyPyBench. Each analysis has 30 minutes and
16 GiB.

**Commands** (`C="docker compose -f evaluation/compose.yaml"`; run them
one service at a time, with nothing else on the machine, at one `--jobs K`
for PyCG and every tool: they measure time and memory, and a tool's run at
another K than PyCG's is "needs rerun"):

```sh
$C run --rm rq3-pilot        # ten packages, five of each half: checks that the question runs
$C run --rm rq3              # the whole corpus
$C run --rm TOOL rq3-pilot   # a prior tool on the ten packages
$C run --rm TOOL rq3         # a prior tool on the whole corpus
```

`--jobs K` runs K analyses at a time, each with its own limits; the
records do not depend on K, but the times and the memory are measured
beside the other K - 1 analyses, so the table writes K beside the
platform. The service
needs K analyses of 16 GiB and 2 GiB more, and K CPUs (at least 4):
`EVAL_MEMORY` (default 18g) and `EVAL_CPUS` (default 4) set them; see
"Measurements and `--jobs K`" in `../README.md`.

```sh
EVAL_MEMORY=50g $C run --rm rq3 --jobs 3                      # PyCG, three analyses at a time
EVAL_MEMORY=130g EVAL_CPUS=8 $C run --rm pyan rq3 --jobs 8    # a prior tool, eight jobs at a time
```

**Inputs.** `corpus/pypi-top-100.json` and `corpus/dypybench.json`: the
manifests, which pin each package by URL and SHA-256 (or commit). The
fetched and unpacked packages (in the image: `/inputs/corpus/`, from the
inputs image). The submodule `PyCG`
at a clean commit, and in its history the 2021 analysis (commit `046479e`). The runs of the
prior tools (`../results/tools/<row>/rq3-pilot.json`, `rq3.json`).

**Outputs** (in `../results/`): `rq3-pilot.md` and `.json` (the pilot);
`rq3-scale.md` and `.json`, with the report of each half
(`robustness-pypi-top-100.*`, `robustness-dypybench.*`).

**How to read the table.** Per tool, a row for the whole corpus ("both")
and one per half. "With a target": the packages that have Python source to
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
