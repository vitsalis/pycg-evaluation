# Evaluation

Scripts that run versions of PyCG and prior tools on the benchmarks and
score their call graphs. Standard library only. The analysed sources are
data: the tools read them, and no script imports or runs them.

The evaluation answers four research questions. Each question has one
directory, one command and one table in `results/` (Markdown, with a JSON
file beside it). How the accuracy holds on projects that the analysis was
not developed on is a section of the table of RQ1 (see "How the benchmark
grew"), not a question of its own.

| Question | Directory | Command | Table |
|---|---|---|---|
| RQ1 accuracy | `rq1-accuracy/` | `docker compose -f evaluation/compose.yaml run --rm rq1` and `rq1-trace` | `results/rq1-accuracy.md`, `results/rq1-trace-recall.md` |
| RQ2 causes | `rq2-causes/` | `docker compose -f evaluation/compose.yaml run --rm rq2` | `results/rq2-causes.md` |
| RQ3 scale and robustness | `rq3-scale/` | `docker compose -f evaluation/compose.yaml run --rm rq3` (first `rq3-pilot`) | `results/rq3-scale.md` |
| RQ4 regression test selection | `rq4-test-selection/` | `docker compose -f evaluation/compose.yaml run --rm rq4` (first `rq4-pilot`) | `results/rq4-test-selection.md` |

Each command is a service of `evaluation/compose.yaml` (see "Docker
Compose"): Docker is the only program that the evaluation needs on the
host. Each service runs one entry script, `rqN-<name>/table.py`, which
calls the code described below.

The tables of RQ1, RQ3 and RQ4 have a row for each tool: PyCG (the
checkout under test), the original PyCG, pyan3, JARVIS, CPyGraph, HeaderGen
and PyAnalyzer. RQ2 is about PyCG only: its rows are the causes. A tool that does not
run has a row with the reason. A benchmark or an input that a command does
not have gives a cell with the reason, or an error that names the input. No
tool and no benchmark is left out without a reason.

## Layout

| Path | Content |
|---|---|
| `common/run.py` | Runs a PyCG checkout on the micro- and macro-benchmark, one child process per job |
| `common/analyse.py` | The child process: one adapter per API flavour (`original`, `new`) |
| `common/score.py` | Scores call graphs against the ground truths |
| `common/loop.py` | One iteration of an optimisation loop: run and score a checkout with a terminal summary; compare two results |
| `common/prior_tools.py` | Scores the stored runs of the prior tools on the micro-benchmark and the macro-benchmark; writes the comparison table that RQ1 reads (see "Prior tools") |
| `common/tables.py` | What the tables of the questions share: the benchmarks and the batches, the tools, the cells with a reason, the rows of the prior tools, the writing of a table |
| `common/same_function.json` | The table of names of one function of the standard library that the scorer uses (see "How names are matched") |
| `common/lookup.py` | The static lookup of standard-library names, for the generator of the table (never for scoring) |
| `tools/same_function.py` | Proposes groups for `same_function.json` from the graphs of any tool; a person reviews them |
| `rq1-accuracy/table.py` | The table of RQ1 |
| `rq1-accuracy/first-measurements.json` | The first measurement of each batch of the macro-benchmark, recorded then (see "How the benchmark grew") |
| `rq1-accuracy/trace_recall.py` | RQ1's recall against DyPyBench's traces of real executions: fetches the pinned traces zip, runs the tools on DyPyBench's projects, maps the traces' names and writes its table (see "Trace recall") |
| `rq2-causes/table.py` | The table of RQ2 |
| `rq2-causes/labels.json` | Every false positive and false negative of the final engine on the macro-benchmark, per batch, each with its label and a note |
| `rq2-causes/limits.json` | The group of each label; for a stated limit, its name and family |
| `rq3-scale/table.py` | The table of RQ3 |
| `rq3-scale/corpus.py` | A pinned corpus of PyPI packages: the manifest, the download and the unpacking as data, the analysis target of a package |
| `rq3-scale/robustness.py` | Does a version finish on the corpus: one run per package under a time and a memory limit, and its report |
| `rq3-scale/corpus/` | The manifests of the corpus (`pypi-top-<N>.json`, `dypybench.json`) |
| `rq4-test-selection/table.py` | The table of RQ4 |
| `rq4-test-selection/rts.py` | Regression test selection, the client task: selects test files with a call graph on NameRTS's dataset and scores the selection; exports what it reads from git as plain files |
| `rq4-test-selection/corpus/rts.json` | The manifest of `rts.py`: the NameRTS commit, file hashes, projects and target commits |
| `tools/` | One adapter per prior tool, the images of the tools and their build and pins (see "Prior tools") |
| `compose.yaml`, `container/question_entry.py`, `container/Dockerfile.runner` | The services of the questions and their entry (see "Docker Compose") |
| `container/trace_entry.py` | The entry of the `trace` service: re-traces one macro project (see "Docker Compose") |
| `container/tool_job.py` | The entry of a prior tool's service: runs the jobs of one question, each a child process, and writes one result file per row and, for RQ1, the graph of each macro project (see "Prior tools") |
| `container/Dockerfile.inputs`, `container/fetch.py` | The inputs image: every pinned input, fetched when the image is built; `fetch.py` fetches the projects of the macro-benchmark and the sources of the prior tools (see "The inputs image") |
| `container/` (the rest) | Records the trace of a macro-benchmark project in a Docker container (`trace.sh`; see "Recording traces in a container" in `macro-benchmark/README.md`) |
| `results/` | The table of each question, and the results that they read (see "The results that the tables read") |

Each question directory has a short `README.md`: what it measures, its
commands, inputs and outputs, how to read its table, and its limits.

The scripts load each other by path: `evaluation/` is not a Python
package. Every path derives from the directory `evaluation/` and its
parent, the root that holds `macro-benchmark/` and the submodule `PyCG/`.
The micro-benchmark is `PyCG/micro-benchmark`: it is versioned with the
tool (`macro-benchmark/projects.py`, `MICRO_BENCHMARK`, is the one place
that names it). The PyCG checkout under test is a parameter (`--checkout`).

## Commands

Each prior tool and each question is a service of `compose.yaml`; the
inputs are an image, `inputs`, which every other image takes its inputs
from when it is built (see "The inputs image", "Docker Compose" and "The
full run"). The variables:

| Variable | Meaning |
|---|---|
| `PYCG` | For the Compose services: the PyCG repository under test, a path of this repository (default `PyCG`, the submodule); its HEAD is measured |
| `ORIGINAL` | For the Compose services: the revision of the original PyCG in that repository (default the full hash of `046479e`, the 2021 analysis, which must be in the history of HEAD); empty: its rows say so |
| `EVAL_UID`, `EVAL_GID` | For the Compose services: the user that a service runs as (default `1000:1000`); set them to your user on a Linux host where it differs |
| `NAME` | For the services `trace` and `headergen-deps`: a project of the macro-benchmark (default `unset`, which the entry refuses) |

## The inputs image

**The rule** (the maintainer, 2026-11-14): git and every download run only
inside a container. The threat is a vulnerability of git that a clone could
trigger on the machine that runs it. The maintainer's decision of
2026-11-15: the inputs are fetched inside the containers, when their images
are built, with no separate fetch step. So every command that downloads,
or runs git on third-party content, is a step of the build of one image,
`container/Dockerfile.inputs` (the service `inputs` of `compose.yaml`), and
nothing on the host downloads or runs git on third-party content. The
repository holds no submodule but `PyCG`, our own tool;
`macro-benchmark/projects.toml` and the pins of the tools are the only
lock of every other source.

**What it holds.** Each input is a stage of the build, on the Python image
pinned by digest in `compose.yaml` with git and the CA certificates, as a
user without rights; the image itself is the Python image and the inputs,
without git:

| Path in the image | What | Fetched by (hosts) | Checked by |
|---|---|---|---|
| `/inputs/macro-benchmark/projects/<name>` | The 30 projects of the macro-benchmark | `container/fetch.py macro` (github.com; files.pythonhosted.org for the two sdists) | The commit of `projects.toml` (git), or the SHA-256 of the sdist |
| `/inputs/tools/<name>` | The trees of the prior tools' images | `container/fetch.py tools` (github.com) | The commit of the adapter (git); the image of the tool checks the tree's SHA-256 of `tools/pins.json` |
| `/inputs/corpus/cache`, `work`, `dypybench` | The corpus of RQ3: the archives, and their unpacked trees (`CORPUS_WORK` below) | `rq3-scale/corpus.py fetch` (pypi.org, files.pythonhosted.org, codeload.github.com) | The SHA-256 and size of each file of the manifests |
| `/inputs/corpus/dypybench-traces` | DyPyBench's traces | `rq1-accuracy/trace_recall.py fetch` (raw.githubusercontent.com) | The SHA-256 and size of the zip |
| `/inputs/rts/export` | The commits of RQ4's dataset as plain files | `rq4-test-selection/rts.py fetch` and `export` (github.com) | The commits and file lists of `corpus/rts.json` |

A step fails, and so the build, if a source is not its pin. `fetch.py`
fetches a git source at its pinned commit (a bare repository, `git fetch
--depth 1`, https only, no hooks, no submodules, no configuration of the
user or the system, every object checked by git) and writes the plain
files of the commit's tree, without `.git`; no checkout runs, so no
attribute or filter of the fetched repository applies. An sdist is
downloaded, checked against the SHA-256 of `projects.toml` and unpacked
with tarfile's `data` filter; the archive is not kept. The clones of RQ4,
with their `.git`, stay in their stage: only their export reaches the
image. Nothing that the build fetches is installed, imported or run.
`container/Dockerfile.inputs.dockerignore` lets in only the files that the
steps read, so a change of another file fetches nothing again; a change of
one input fetches only that input again.

**The hosts of the builds** (a firewall of the build host must let them
through; no service reaches any host when it runs):

| Build | Hosts |
|---|---|
| Every image: its base image | Docker Hub: registry-1.docker.io, auth.docker.io and its blob host, production.cloudflare.docker.com |
| The inputs image and the runner: `apt-get` of git and the CA certificates | deb.debian.org (the Debian of the official Python images) |
| The inputs image: the inputs (the table above) | github.com, codeload.github.com, raw.githubusercontent.com, pypi.org, files.pythonhosted.org |
| The images of pyan3, JARVIS, CPyGraph, HeaderGen and headergen-deps, and the trace image of a project: their wheels, by hash | pypi.org, files.pythonhosted.org (PyAnalyzer: none) |
| The target `maintain` (not part of a rerun): uv | pypi.org, files.pythonhosted.org |

**What takes the inputs.** Each image copies from the inputs image, when
it is built, only what its services read (`COPY --from=inputs`; the
additional context `inputs: service:inputs` of `compose.yaml`, so Compose
builds the inputs image first). The paths stay those of the inputs image.

| Image | What it copies | Read by |
|---|---|---|
| The runner (every question) | The sources of the macro-benchmark; the whole corpus (`cache`, `work`, `dypybench`, `dypybench-traces`); the whole export of RQ4 | RQ1 the sources and the traces; RQ3 and rq1-trace the corpus (`robustness.plan` checks each archive against its unpacked tree); RQ4 the file lists, blobs and NameRTS's files of the export |
| pyan3, JARVIS, CPyGraph, PyAnalyzer | Its own trees; the sources of the macro-benchmark; the corpus without DyPyBench's traces; the file lists and blobs of the export (`trees`, `blobs`) | rq1; rq3, rq3-pilot and rq1-trace; rq4 and rq4-pilot (`container/tool_job.py`). One image serves every question of the tool, so it holds the inputs of all of them |
| HeaderGen (and `headergen-deps`, on it) | Its own trees; the sources of the macro-benchmark | rq1 only |
| The trace image of a project | That project's source | The trace |

A run has no network and mounts no input from the host.
`macro-benchmark/projects.py` reads the sources at
`/inputs/macro-benchmark/projects` when the image has them, else in
`macro-benchmark/projects/` (`projects.sources`).

**Size** (as built, 2026-11-15): the inputs image 4.35 GB; the runner
2.85 GB; pyan3, JARVIS and PyAnalyzer about 2.7 GB each and CPyGraph
4.26 GB (the full Python image and its build), most of each the corpus
of RQ3; HeaderGen 0.70 GB. About 20 GB in all, and Docker's build cache
keeps the stages of the inputs image besides.

```sh
C="docker compose -f evaluation/compose.yaml"
$C build inputs                                   # every input, by its pin
# A copy of the sources of the macro-benchmark, for reading only (git
# ignores it, no service reads it):
$C run --rm -v "$PWD/macro-benchmark/projects:/repo/macro-benchmark/projects" \
    inputs cp -RP /inputs/macro-benchmark/projects/. macro-benchmark/projects/
```

**The scope.** The build of the inputs image is trusted to write what it
fetched. A defence against a compromised build is not a goal.

**Maintenance.** The commands that write a pin or a manifest need the
network: `tools/pin.py requirements` and `deps` (uv, pypi.org),
`macro-benchmark/draw.py` (its clones and its requests to PyPI and
GitHub), `rq3-scale/corpus.py manifest` and `rq4-test-selection/rts.py
manifest`. They run in the target `maintain` of `Dockerfile.inputs` (git,
the CA certificates and uv), with the repository read-only and the one
directory that the command writes mounted writable. `pin.py trees` reads
the trees of the inputs image and runs in the `inputs` service;
`pin.py images` asks the registry through docker on the host; Compose
builds the images; neither runs git.

```sh
docker build -f evaluation/container/Dockerfile.inputs --target maintain \
    -t pycg-evaluation-maintain .
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD:/repo:ro" -w /repo \
    -v "$PWD/evaluation/tools:/repo/evaluation/tools" pycg-evaluation-maintain \
    python3 -I evaluation/tools/pin.py requirements headergen   # writes the pins
$C run --rm -v "$PWD/evaluation/tools:/repo/evaluation/tools" inputs \
    python3 -I evaluation/tools/pin.py trees                    # tree hashes, from /inputs/tools
```

## Containers

Third-party code never runs on the host. Our own code (PyCG, the runner,
the scorer) runs in a container too, so that every question runs the same
way on every machine. The trace runner (`container/trace.sh`) set the
pattern: an image pinned by digest, no network at run time, a non-root
user, read-only inputs, and a `--dry-run` that prints the docker command.

**Images.**

| Image | What it holds | Used by | State |
|---|---|---|---|
| The inputs image (`container/Dockerfile.inputs`: the Python image pinned in `compose.yaml`) | Every pinned input (see "The inputs image") | The build of every other image | the service `inputs` |
| The runner (`container/Dockerfile.runner`: the Python image pinned in `compose.yaml`, and git) | CPython, git and the inputs of the questions; PyCG, the runner and the scorer come from read-only mounts | Every question: the PyCG rows and every table | the services of `compose.yaml` |
| The trace image of a project (`Dockerfile.trace` on that Python image, with the hash-pinned test requirements of one project) | The test environment of one project, and its source from the inputs image | The traces of the macro-benchmark (RQ1) | the `trace` service; `container/trace.sh` |
| One image per prior tool: pyan3, JARVIS, CPyGraph, HeaderGen (and one per macro project with runtime dependencies), PyAnalyzer | The tool's environment, and the inputs of the questions from the inputs image | The prior-tool rows of RQ1, RQ3 and RQ4 (the tool's service, its jobs one after another: `container/tool_job.py`) | the services of `compose.yaml`; see "Prior tools" for the pins |

HeaderGen executes code of the analysed project (it imports the modules
that the code imports): its jobs run in its service without network,
each a child process on an export of the analysed source. It never runs on a corpus
that no security scan covered: the RQ3 corpus and the RQ4 dataset give it
a stated row instead.

## Docker Compose

The maintainer's requirement: the evaluation is reproducible without
running anything on the executor's machine but Docker. One Compose file,
`evaluation/compose.yaml`, has one service per question, and one command
per table:

```sh
docker compose -f evaluation/compose.yaml run --rm rq2        # the committed label data; no analysis
docker compose -f evaluation/compose.yaml run --rm rq1        # PyCG on the micro-benchmark and the macro-benchmark
docker compose -f evaluation/compose.yaml run --rm rq1-trace  # recall against DyPyBench's traces (/inputs/corpus)
docker compose -f evaluation/compose.yaml run --rm rq3-pilot  # ten packages of the corpus, before rq3
docker compose -f evaluation/compose.yaml run --rm rq3        # the whole corpus (/inputs/corpus)
docker compose -f evaluation/compose.yaml run --rm rq4-pilot  # one target per project, before rq4
docker compose -f evaluation/compose.yaml run --rm rq4        # the export of the NameRTS clones (/inputs/rts/export)
```

**What a service is.** The runner image (`container/Dockerfile.runner`):
the Python image, pinned by digest once in `compose.yaml`, and git. Every
service has no network, a read-only root, no capability, no new
privileges, the caller's user (`EVAL_UID`, `EVAL_GID`; default
`1000:1000`), 512 processes, 4 CPUs, a memory limit without swap
(6 GiB; 18 GiB for rq1-trace, rq3, rq3-pilot, rq4 and rq4-pilot, whose limit
per analysis is 16 GiB, the maintainer's number for every tool), tmpfs at `/tmp` and
`/pycg-evaluation`, and `PYTHONHASHSEED=0`. Its mounts: this repository
read-only at `/repo`, and `evaluation/results` at `/results`, the only
host path that it writes. Its inputs are in its image, from the inputs
image: the sources of the macro-benchmark at
`/inputs/macro-benchmark/projects`, the corpus at `/inputs/corpus` (for
rq1-trace, rq3 and rq3-pilot), the export of RQ4 at `/inputs/rts/export`
(for rq4 and rq4-pilot). No service holds the Docker socket. On a Linux host
where your user is not 1000:1000, `export EVAL_UID=$(id -u)
EVAL_GID=$(id -g)` first, so that a service can write
`evaluation/results` and the files are yours.

**What the entry does** (`container/question_entry.py`). For a question
that runs PyCG it checks that the PyCG repository under test is under git
and that its `pycg` package has no uncommitted change (`PYCG`, a path of
this repository; default `PyCG`, the submodule), and exports the `pycg` package of its HEAD
(`common/engine.py`). A change outside the package does not stop a
question: the export does not hold it, and a table that one question
wrote must not stop the next. The original PyCG is the
package of the same repository at `ORIGINAL` (default: the 2021
analysis, commit `046479e`, by its full hash; it must be in the history of
HEAD); `ORIGINAL=` (empty) runs
without it. The export writes the blobs of the tree as git stores them
(`git ls-tree` and `git cat-file`, without the user's or the system's git
configuration), so no attribute changes them (`export-ignore`,
`export-subst`, a filter); a gitlink or a link in the tree is refused, and
each file is checked against its blob id. git reads the repository, mounted
read-only and owned by another user, as a safe directory and without
optional locks. The entry writes the versions of the exports and of
`/repo` to `/pycg-evaluation/versions.json` (a tmpfs), which
`run.git_version` reads in place of git: an export is no checkout. Then it
runs the question's script with `--results /tmp/results`, and copies to
`/results` only the files that the question writes (`OUTPUTS` in
`question_entry.py`), only if the script ended with status 0, and only
regular files: a link at any level, a missing file, a directory or a file
over 64 MiB (256 MiB for all of them) copies nothing, and a file of
`results/` is replaced, never followed if it is a link.

**Traces.** The `trace` service records the trace of one project of the
macro-benchmark again:

```sh
NAME=jmespath docker compose -f evaluation/compose.yaml run --rm trace [--compare]
```

It builds the project's image from `container/Dockerfile.trace`,
`macro-benchmark/requirements/NAME.txt` (hash-pinned wheels, the same
Python image) and the project's source from the inputs image, and runs
`container/trace_entry.py` without network: it copies the source, runs
`macro-benchmark/trace.py`, checks the trace (`trace_helper.check_trace`)
and writes it, with the distributions of the container, to
`results/trace/NAME.json` and `NAME.installed.txt`; `--compare` prints the
differences from the committed `macro-benchmark/dynamic/NAME.json`
(`trace_helper.compare`) and exits with 0 only if the edges are equal. The
committed trace is never changed (see "Re-recording a trace" in
`macro-benchmark/README.md`). `container/trace.sh` stays for the
maintainers' own use: it runs Docker from the host, takes the source from
the inputs image too, and writes the new trace into
`macro-benchmark/dynamic/`.

**To try it.**

```sh
docker compose -f evaluation/compose.yaml config --quiet
docker compose -f evaluation/compose.yaml build rq2       # the inputs image first: it downloads every input
docker compose -f evaluation/compose.yaml run --rm rq2        # results/rq2-causes.*: equal to the committed files
```

## The full run

The commands of the final run, in order, on a Linux host with Docker and
the Compose plugin, from the root of a clean checkout. Nothing else runs on
the host. `C` stands for `docker compose -f evaluation/compose.yaml`. A
`run --rm` without a terminal (in the background, under `nohup`, from a
script) needs `-T`, which gives the service no pseudo-terminal: for example
`nohup $C run --rm -T rq3 --jobs 3 > rq3.log 2>&1 &`.

```sh
export EVAL_UID=$(id -u) EVAL_GID=$(id -g)
C="docker compose -f evaluation/compose.yaml"

# 1. The inputs: every pinned input, the only step that downloads
#    third-party code.
$C build inputs

# 2. The images: the questions' runner and one image per prior tool, each
#    with its inputs from the inputs image.
$C build rq1 pyan jarvis cpygraph pyanalyzer headergen
# HeaderGen's image of each macro project with runtime dependencies
# (tools/headergen-deps/NAME.txt: aiocache, bottle, configargparse, itables,
# markdown2, marshmallow, tenacity), after the image of headergen:
for name in aiocache bottle configargparse itables markdown2 marshmallow tenacity; do
    NAME=$name $C build headergen-deps
done

# 3. The prior tools, one service run at a time (rq1: the micro-benchmark
#    and the macro-benchmark; rq3-pilot, then rq3: the corpus; rq1-trace:
#    DyPyBench's traced projects; rq4-pilot, then rq4: the parent commits
#    of RQ4's targets). rq1 and rq3 write
#    results/tools/<row>/<question>.json, and rq1 the graph of each macro
#    project to results/graphs/<row>/<project>.json; rq1-trace and rq4
#    write their graphs to the volume tool-graphs.
for tool in pyan jarvis cpygraph pyanalyzer; do
    $C run --rm $tool rq1
    $C run --rm $tool rq3-pilot
    $C run --rm $tool rq3
    $C run --rm $tool rq1-trace
    $C run --rm $tool rq4-pilot
    $C run --rm $tool rq4
done
$C run --rm headergen rq1                                     # rq1 only
for name in aiocache bottle configargparse itables markdown2 marshmallow tenacity; do
    NAME=$name $C run --rm headergen-deps rq1
done

# 4. The tables (results/), each after the runs that it reads.
$C run --rm rq1        # first scores the prior tools' runs: results/prior-tools.*
$C run --rm rq2
$C run --rm rq1-trace  # maps the traces, scores the tools' rq1-trace graphs
$C run --rm rq3-pilot
$C run --rm rq3        # reads results/tools/*/rq3.json
$C run --rm rq4-pilot  # selects with the tools' rq4-pilot graphs
$C run --rm rq4        # selects with the tools' rq4 graphs
```

`$C run --rm jarvis rq1 --variant 2` runs one row of a tool (JARVIS has
three rows, PyAnalyzer two). rq1 and rq1-trace run every row, as accuracy
is what the variants change; rq3, rq3-pilot, rq4 and rq4-pilot run the
headline row of each tool only (the first: JARVIS, PyAnalyzer), as the
other variants would run the same tool again for the same time and memory
or the same client, so their tables have one row per tool. A row without its result file is a row "not
run" in the tables, with the command that runs it; a result whose inputs
changed since it ran is "needs rerun". The graph of each macro project
of rq1 is a committed file, `results/graphs/<row>/<project>.json`, so that
a reader scores a tool again without running it (`$C run --rm rq1`). The
graphs of rq1-trace, rq4 and
rq4-pilot are intermediate data, not results, and they are large: they stay in the volume
`tool-graphs`, which the tools' services write and rq1-trace, rq4 and
rq4-pilot read (`docker volume rm pycg-evaluation_tool-graphs`).

**Measurements and `--jobs K`.** The time and the memory of a run depend
on the load of the machine. rq1-trace, rq3, rq3-pilot, rq4, rq4-pilot and
every prior tool's service take `--jobs K` (default 1): K analyses at a
time, each with its own time and memory limit, each prior tool's job in a
directory of its own (`/tmp/run`, `/tmp/run1`...). The records do not
depend on K; the first error stops every analysis and starts no other
(`run.run_tasks`). No per-analysis limit changes. The maintainer's rule
(2026-11-15):
- rq3 and rq3-pilot measure time and memory: run them for PyCG and for
  every prior tool (`$C run --rm TOOL rq3`) at one K, so that the timings
  compare, one service at a time, with nothing else on the machine. The
  table writes the platform and K in its header, and a tool's run at
  another K than PyCG's is "needs rerun".
- rq4, rq4-pilot and rq1-trace may run at a higher K: their results are
  selections and recall, not times. The table of RQ4 writes the platform
  and K in its header; the times of its pilot are measured beside K - 1
  others.
- The other tables count edges: they do not depend on the load, and the
  prior tools' rq1 runs may run beside each other.

The memory and the CPUs of rq1, rq1-trace, the rq3 and rq4 services and
the tools' services are `EVAL_MEMORY` (default `18g`: one analysis of 16 GiB
and a margin) and `EVAL_CPUS` (default `4`). For K analyses at a time:
- `EVAL_MEMORY` = K x 16 GiB + 2 GiB (`50g` for K = 3, `130g` for K = 8);
- `EVAL_CPUS` = K, and at least 4: an analysis of PyCG is one process
  with one thread, and the runner's threads only sample the memory every
  0.2 s, so K + 1 gains nothing. A tool whose analysis starts threads of
  its own would want more. Docker refuses more CPUs than the host has.

```sh
EVAL_MEMORY=50g $C run --rm rq3 --jobs 3                  # 4 CPUs, the default
EVAL_MEMORY=130g EVAL_CPUS=8 $C run --rm rq4 --jobs 8
EVAL_MEMORY=130g EVAL_CPUS=8 $C run --rm pyan rq4 --jobs 8
```

## RQ1: accuracy

**What is measured.** Precision and recall of each tool, with TP, FP and
FN, on the micro-benchmark (`PyCG/micro-benchmark/snippets`) and on the
macro-benchmark (`macro-benchmark/`, 30 projects). The table has a column
for the micro-benchmark, one per batch of the macro-benchmark (five
projects each: `batch 1` to `batch 5` and `ICSE 2021 set`, the `batch` of
`projects.toml`) and one for all 30 projects. The edges inside the
analysed package are the headline; the edges outside it are reported
apart. The last section of the table is the first measurement of each
batch (see "How the benchmark grew"). The recall against traces of real
executions has its own table (see "Trace recall").

**Command.** `docker compose -f evaluation/compose.yaml run --rm rq1`. `rq1-accuracy/table.py` runs each PyCG checkout on the micro-benchmark and
on the 30 projects with `run.py` and scores it with `score.py`. The
graphs go to the work directory (`--work`). It writes
`results/rq1-accuracy.md` and `.json`.

**Inputs and their pins.** The checkout under test, clean, at a commit
(recorded). The original PyCG at the 2021 analysis, commit `046479e`. The
macro-benchmark: each project is the files of the pin of
`projects.toml`, from the inputs image; a project that is not there makes the cells of the
macro-benchmark "not measured", with its name. The rows of the prior tools come from
`results/prior-tools.json` (`--prior-tools`), the result of
`prior_tools.py`, which the rq1 service runs first: it scores the runs of
each tool's service (`results/tools/` and `results/graphs/`, see "Prior tools"), with the pins of
`tools/` (pyan3 v2.9.0, JARVIS `8871b86`, CPyGraph v0.1.0, HeaderGen
`1ea5226`, PyAnalyzer `ea143ef`). A prior-tool cell belongs to its
inputs, not to the state of the repository (the services write
`results/` themselves): each job records the SHA-256 of its exported input
and of its graph, and each benchmark the SHA-256 of its files
(`tables.inputs_digest`); the commit and the dirty flag are recorded as
facts. A cell whose inputs are not this run's is "needs rerun (its inputs
changed since it was scored)"; so is a finished job without its graph, a
run of other jobs (a micro case or a project added since), and a result of
another pin, row or limits than the adapter's today. None is ever mixed
with this run's; a measurement without an image (on the host) needs a
rerun too. After a run whose processes could not be killed, no later job
of the service starts: each is "not run" with the reason.
Nor is a measurement scored with another table of names of one function
than this run's, or with none recorded: a cell "scored with another table (<its table>; here <this
run's>)". The command has no `--allow-dirty`.

**One micro cohort.** Every row of the column "micro" scores the same
cases: those whose `requires-python` is at most the cohort, the oldest
Python of the tools of the table (PyCG's interpreter, and the bound of the
micro cases of the prior-tool result, Python 3.12 with HeaderGen). The cases
that only newer interpreters parse are the column "micro, newer Python",
for the tools that run them; a prior-tool row there says that its cases
end at its bound. A prior-tool micro cell of another cohort needs a rerun.
`prior_tools.py` records the commit and the dirty flag of the checkout
that holds its micro cases: the submodule `PyCG`, not this repository.

**State today.** `results/rq1-accuracy.md` holds every row. It was
converted on 2026-11-15 from the table of the six former benchmark
directories (one column each), when they became one: each count is a
recorded count or a sum of recorded counts, and the file says so. The next
`rq1` run writes it again from the stored runs and graphs.

### How the benchmark grew

The macro-benchmark was built in batches of five projects. Batch 1 is the
five projects that the analysis was first developed on. Each later batch
(2 to 5) was measured **once, by a frozen engine, before anyone read its
false positives and false negatives**: the first measurement. After that
its false positives and false negatives were read and labelled, the
analysis was improved on them, and the next batch was built. So every
batch was used for development in the end; what each batch gave once is a
measurement on projects that the engine had never been developed on. The
five projects of the ICSE 2021 paper were added between batches 3 and 4,
as a batch that the engine was developed on.

The first measurements are data, `rq1-accuracy/first-measurements.json`:
per batch the commit and the tag of the frozen engine, the batches known
then, and TP, FP and FN inside and outside the package, for PyCG and for
the original PyCG of 2021 (and, for batch 5, for the prior tools). They
were recorded when they were measured and are not measured again: the
engine and some ground truths changed afterwards. `rq1-accuracy/table.py`
shows them as the section "Accuracy on each batch at its first
measurement", with the gap of each batch to the batches known then. One
run on batch 5 failed in the scorer before it showed any number; the
measurement was repeated once, and that one is recorded.

**When the batches stopped.** The maintainer's rule: stop when every rule
that no decision blocks, measured together on the newest batch, gains less
than one point of precision and of recall. On batch 5 they gain 0.76 and
0.07 points (precision 0.902 to 0.910, recall 0.817 to 0.818, inside the
package), so batch 5 is the last. The draw of batches 3 to 5 is
`macro-benchmark/draw.py` (see "Choosing a new batch" in
`macro-benchmark/README.md`).

### How the scores are computed

Every benchmark is run with the tool's single default configuration: the
harness gives it the entry points and the package root, and nothing else.

**The headline is the edges whose callee is inside the analysed package**
(the maintainer's decision). Edges to callees outside the package
(builtins, the standard library, third-party code) are reported apart and
never mixed into the headline: their names depend on how each tool models
code that it does not analyse (a method on the result of `re.compile` is
`re.Pattern.match` for one tool and `re.compile.match` for another) and on
the internals of the libraries (which class defines an inherited method).
The figures on all edges are kept as a secondary column, for comparison
with the 2021 paper, whose metric counts all edges. Every table is ordered
inside, outside, all.

**Macro-benchmark** (`macro-benchmark/`, 30 projects). Per project and
summed over the projects of a batch and over all 30: the number of tool edges, ground-truth edges, true
positives, false positives and false negatives, precision, recall and F1.
Three edge sets: edges whose callee is in the analysed package (`inside`,
by its name); edges whose callee is outside it (`outside`); all edges. The
sums add the counts of the projects, then compute precision and recall
(they are not means of the per-project values). Every tool edge is scored,
also an edge whose caller is not a node of the ground truth (a class, an
invented name). A node view lists the modules and functions of the package that the
tool does not have as nodes, and the tool's nodes in the package that are
not modules or functions of it. The result files also list every false
positive and false negative, as written in each graph.

**Micro-benchmark** (`PyCG/micro-benchmark/snippets`). Per case, as in
the PyCG paper: *complete* if every edge that the tool reports is in the
ground truth (no false positive), *sound* if the tool reports every edge of
the ground truth (no false negative). *Exact* if the graph equals the
ground truth, nodes included, as PyCG's `tests/test_micro_benchmark.py` compares
them; these three count all edges, as in the paper. Per category: the
counts of these cases and the edge-level TP, FP and FN, inside (the callee
is a module or function of the case) and outside. A case whose
`requires-python` is newer than the interpreter is
skipped and counted. A case that crashed or timed out is none of the three,
and its ground-truth edges are false negatives.

**Runs.** For each job: the status (ok; crash, with the error and the
innermost frame in the tool; timeout), the time of construction, analysis
and output in the child process, and the peak resident set size of the
child. On the macro-benchmark the analysis root is the project's `root`,
which holds the package and none of its dependencies.

### Trace recall

**What is measured.** The share of the calls that real executions made
that a tool's call graph has: recall only, against DyPyBench's traces (the
maintainer's decision, 2026-11-14: we run no large test suite ourselves).
Precision cannot be measured against a trace, since an edge that the
tests did not run may exist. Headline: the traced edges whose callee is a
definition of the project; second column: every mapped traced edge. Sums
over the projects whose run finished, and over all projects with a trace,
where a run that did not finish finds none of its edges. The result holds
counts only, no time or memory, so it is the same on every run of the same
inputs; it names its source trees (the corpus manifest by its SHA-256, and
each traced project's commit and archive SHA-256). After a run whose
processes could not be killed, no run starts: each later run is "not run"
with the reason, finds none of its edges, and the table says that it
stopped.

**Command.** `docker compose -f evaluation/compose.yaml run --rm
rq1-trace` runs `rq1-accuracy/trace_recall.py run` on `/inputs/corpus`
(in the image, from the inputs image). It
writes `results/rq1-trace-recall.md` and `.json`, with the scorer and the
SHA-256 of its table. A corpus or a traces zip that is not there is an
error that says to build the inputs image. The command has no `--allow-dirty`.

**The reference.** DyPyBench (Bouzenia, Krishan and Pradel, FSE 2024;
github.com/sola-st/DyPyBench, MIT) ran its projects' test suites under
DynaPyt and published the dynamic call graphs:
`experiments/DynaPyt_callgraphs.zip` at `bccfda7` (91,547 bytes, SHA-256
`1b0c7d5551464ccd5bbd54864c7b45b89b1cd775cef374fd5330be5fa84b0c44`).
`trace_recall.py fetch` downloads it as a raw file from
raw.githubusercontent.com and checks it; the table reads it in memory,
without unpacking it, and only its members `DynaPyt_output/dynapyt_<N>.json`
(`dynapyt_.json` is a copy of trace 13). N is the line of the project in
DyPyBench's older list of 58 (`text/dynapyt_instrument_src.txt`); the
corpus manifest records it as each project's `trace` (assigned on branch
`eval-dypybench` to the project whose files hold the trace's callers). The
traces are small: 9,602 distinct edges over 50 traces, from 3 (pypdf) to
1,438 (PythonRobotics). The DynaPyt runs look cut short, so they are a
small and biased sample of what the tests execute; the table says so.
48 projects have a trace; akshare (its commit cannot be recovered) and
python-diskcache (no trace published) are stated rows.

**The projects.** The trees of RQ3's DyPyBench half, at the same commits
(`CORPUS_WORK/dypybench`), and the same targets: the root and the modules
of each row. No git, and nothing of a project runs. One run per project
and tool, in a child process of `run.py`, 30 minutes and 16 GiB by
default.

**Mapping names** (the rules of branch `eval-dypybench`, ported). A caller
is DynaPyt's `.DyPyBench.temp.project<N>.` and the file's path, then, for
a call in a function, the function's name after the name of its nearest
enclosing class (`file.C.f`, also for a function nested in a method). A
callee is the runtime `module.__qualname__`. The caller's file is the
longest prefix of the name that is a file of the tree (`__init__` kept as
DynaPyt writes it); its function is the one of that name and class. A
callee inside the project is found as Python imports its module (a package
before a module file), under the root, else from the tree's root; then:
a function by its qualname without `<locals>` and comprehensions;
`<lambda>` as `<lambda1>` if it is the only lambda of its scope, where a
lambda counts only if code creates it, as the compiler of the traced
runtime (Python 3.10) does: no annotation under `from __future__ import
annotations`, a variable annotation only at the level of a module or a
class (never inside a function), a function's annotations where it is
defined, defaults, targets and values always; a name that a module imports
from another module of the project, followed to its definition (a relative
import from the module's name as Python imports it, a directory without
`__init__.py` being a namespace package); a class to an `__init__` only
with the proof of the plain case below, our rule for instantiation.

**The proof of a constructor** (`trace_recall.CONSTRUCTOR_PROOF`). A
traced call of a project class maps to the first `__init__` of its method
resolution order only if the class and every class of its hierarchy (its
bases, theirs, ...) meet every condition below. The reason why an edge
is counted apart is, in this order: the first condition, in the order of
the list, that the class fails by itself; if it fails none, the reason of
its first base that fails, base by base in the order of its `class`
statement (each base the same way); if all bases pass, the rebinding part
of condition 2 (an attribute written in a module of the hierarchy), which
can be checked only once the bases are known. So a class with a rebound
base and an unsupported decorator is counted apart as "decorator". Then
`type.__call__` makes the object with the `__new__` of a known base and
runs that `__init__`.

**The closed-class assumption.** The proof reads the syntax of the modules
of the class's hierarchy. It assumes that no other code changes a class
after its `class` statement: a helper function or another module that
receives the class object and sets its attributes, `setattr` with a
computed name, a class decorator applied by hand elsewhere, the
`__set_name__` of a library's descriptor, another module that rebinds a
name of a module of the hierarchy, code that reads or writes another
frame's namespace (`sys._getframe`, `inspect`). A project that breaks
the assumption gets a wrong mapping for the classes concerned, for every
tool alike. The conditions below check only what the syntax of those
modules shows, conservatively; they do not try to prove more.

**Two stated limits of the conditions.** They have the same consequence:
a wrong mapping for the classes concerned, for every tool alike.
(a) Condition 10 sees the names `locals`, `vars`, `globals`, `exec` and
`eval` as they are written; a namespace function reached through an
import alias (`from builtins import locals as namespace`, an imported
alias of `exec`) is not seen. (b) The rebinding part of condition 2 sees
the last name that a class statement writes; a rebinding behind an import
alias (`from m import Base as Imported` while the write names `Base`) or
of a prefix of a dotted name (`base.inner = other` before
`class Child(base.inner.Base)`) is not seen. Both are limits of the
proof: such a class stays mapped.

1. *At the top level.* The class is defined by a `class` statement at the
   top level of its module (a class inside a class or a function is not
   proved; nor a base read as `Outer.Inner`).
2. *Bound once.* Every name that the proof reads is resolved where the
   class statement reads it, at the top level of its module: the class's
   own name and the names that its bases read (a metaclass or a decorator
   is resolved by the same rule; one that is not proved fails 4 or 3).
   Such a name must be bound there exactly once, by one `class` statement
   or one import that is a statement of the module itself (not inside
   `if`, `try`, `for`, `with` or a function), with no other binding of any
   form: assignment, augmented or annotated assignment, `del`, a `for`,
   `with`, `except` or `match` target, walrus, a `def`, a second import or
   class, a `global` declaration in any function, and no `*` import that
   may bind it (a `*` import of a project module may not when its literal
   `__all__`, assigned once and named nowhere else, leaves the name out,
   or when it has no `__all__` and binds no such public name nor has such a
   submodule; an outside module may bind anything). A comprehension's
   target counts too, an over-approximation. A name that nothing binds is
   the builtin. An imported name is resolved by the same rule in the
   module that it comes from; `M.C` reads `C` in the project module `M`
   (a name that is also a submodule is not proved). A name that a base,
   a metaclass or a decorator reads, qualified or not (`Base`,
   `base.Base`, `abc.ABCMeta`), is not bound once either when any module
   of the hierarchy writes an attribute of its last name, on any object:
   an assignment of any form with a value, `del`, a `for` or `with`
   target, or `setattr`/`delattr` with that literal name
   (`base.Base = Right` before `class Child(base.Base)`; `import pkg.m as
   me; me.Base = Right` before `class Child(Base)`).
3. *Decorator.* No decorator, except `functools.total_ordering`, which
   returns the class and only adds the comparisons that it lacks.
4. *Keyword.* No class keyword other than one `metaclass=` that names a
   plain metaclass, and no `**`. Plain: `type`, `abc.ABCMeta` (its
   `__new__` only records the abstract methods), or a project class that
   meets conditions 1 and 2, has no decorator and no keyword, whose bases
   are all plain metaclasses and that binds none of `mro`, `__call__`,
   `__new__`, `__init__`, `__prepare__`, `__init_subclass__` (by any
   binding), has no `global` or `nonlocal` statement in its body, and
   whose module sets none of them nor the last name of one of its bases,
   and uses no namespace name (conditions 2, 9 and 10). Not
   `enum.EnumMeta`: its `__call__` looks a member up instead of making an
   object.
5. *Base.* Every base is a project class (proved by the same conditions)
   or a known base: `object`, `abc.ABC`, and the builtins that the traced
   projects subclass with their bases (`BaseException`, `Exception`,
   `ValueError`, `RuntimeError`, `OSError` and its alias `IOError`,
   `dict`, `list`, `tuple`). Their metaclass is `type` (`abc.ABC`:
   `ABCMeta`), their `__new__` makes an object of exactly the class called
   and they define no `__init_subclass__`; their method resolution orders
   are written in the table and a test checks them against Python.
6. *`__new__`.* No class of the hierarchy binds `__new__` or
   `__init_subclass__`.
7. *`__init__`.* A class of the hierarchy binds `__init__` at most once,
   by an undecorated `def` that is a statement of its body.
8. *Scope declaration.* The body of a class of the hierarchy has no
   `global` or `nonlocal` statement (with `global __init__`, the `def`
   binds a global, not the class's `__init__`). A method's own scope does
   not count.
9. *Attribute set.* The module of a class of the hierarchy sets or deletes
   none of `__init__`, `__new__`, `__call__`, `__class__`, `__bases__`,
   `__mro__`, `__init_subclass__` on any object (assignment of any form,
   `del`; an alias of the class is any object), and calls `setattr` or
   `delattr` with none of them as a literal name, by any name that the
   module binds to them (`setattr`, `builtins.setattr`,
   `object.__setattr__`, an import of them under another name, and a
   chain of assignments of them, `a = setattr; b = a`).
10. *Namespace access.* That module does not use the names `locals`,
   `vars`, `globals`, `exec` or `eval` at all, as a name or as an
   attribute (`builtins.locals`, `model.eval`), read or call: no list of
   the ways to change a namespace is complete (`locals().pop`, `exec` in
   a class body), and their use is rare in a module that defines a
   mapped class.
11. *`__set_name__`.* No file of the project defines or sets
   `__set_name__` (class creation calls it on the values of a class body,
   and it may change the class). A narrower rule (no class body of the
   hierarchy binds the result of a call) is not sound: a value named or
   made by a decorator reaches `__set_name__` too.

Then the order is C3 over the hierarchy, the known bases with their own
orders; bases that C3 cannot order, or a class that inherits from itself,
are counted apart. The first class of the order with an `__init__` gives
it: a project class (its own `__init__`, or an inherited one); `abc.ABC`
is passed over (it has none); `object` means that the class has no
`__init__` of its own (our graphs have no edge); any other known base
first is a constructor that may be the builtin's, counted apart.

A callee outside the
project: `builtins.f` becomes `<builtin>.f`, `T.m` of a builtin type
becomes `<builtin>.T.m`, any other name stays as written. Then the callees
of both graphs go through `score.Names` for the project's packages, so the
table of names of one function applies as in every result. An edge whose
names cannot be mapped is counted apart, by reason (the caller's file is
not in the tree, no or several functions match, the file does not parse,
a callee that is not a name or has no module, a name that the module does
not define, one of several lambdas, a class whose constructor is not
proved, a file outside the root), never as missed.

**What the table measures.** Recall over the traced edges that are
mapped: the share of them that a graph has, given the mapping. It does
not measure the edges counted apart (13.2 % of the traced edges of the 48
projects). That share does not bound the errors among the mapped edges:
a mapping can be wrong under the closed-class assumption or the two
limits above, and such an error is not counted apart. The mapping treats
every tool alike, but equal treatment does not prove equal bias: a wrong
mapping can hurt one tool more than another.

**Not modelled.** DynaPyt attributes a call inside a lambda to the
function around it, and our graphs to the lambda; calls in default values
belong to the function for DynaPyt and to the enclosing scope for us. Such
edges count as missed.

**Rows.** PyCG (the checkout under test), the original PyCG and a row
per row of pyan3, JARVIS, CPyGraph and PyAnalyzer: each tool runs in its
service on the same targets (`docker compose run --rm TOOL rq1-trace`:
`container/tool_job.py`, with RQ3's limits), its graphs go to the volume
`tool-graphs`, and the table maps each trace once and scores those graphs
as PyCG's (the same constructor mapping and the same edges counted apart).
A row without a run is "not run" with its command; a project whose input
changed since the run makes the row "needs rerun". HeaderGen is a stated
cell: it imports the analysed code, and the corpus is not scanned.

**State today.** Tested on fixture projects and on two real ones
(delegator.py and schedule, under a second each); the table is not run
on the 48 projects here.

## RQ2: causes

**What is measured.** Every false negative (a missed edge) and every false
positive (a wrong edge) of PyCG `c5c6a89`, on the ground truths after the
audit of 2026-11-16, on the 30 packages of the macro-benchmark has one
label: the mechanism that
explains it. The table gives the share of them under stated limits (the
kinds of edge that the design leaves out on purpose, PyCG's `docs/design.md`), one
row per stated limit, the counts of the other groups, and the counts per
batch.

**Command.** `docker compose -f evaluation/compose.yaml run --rm rq2`.
`rq2-causes/table.py` reads `rq2-causes/labels.json` and
`rq2-causes/limits.json` and writes `results/rq2-causes.md` and `.json`. It
runs no analysis. A label without a group, an edge that is labelled twice
and the labels of a batch that `projects.toml` does not have stop it, with
the reason.

**The label data** (`rq2-causes/labels.json`). One row per edge, per batch,
sorted: the package, the caller, the callee, `fn` or `fp`, `inside` or
`outside` (whether the callee is in the analysed package), the label and a
note (the evidence: the site, the line of the ledger, the measurement). The
labels come from the final pass over the frozen engine (2026-11-14):
analysts labelled every false positive and false negative. They checked
the large groups with counterfactual runs (a run-time patch of the engine,
scored edge by edge against the frozen run) and read the rest from the
source and the ledgers. A label names the first mechanism that explains
the edge. The labels `L-other` (another stated
limit) and `GAP` (a missing rule) of the final pass are split by their
notes into the limits and rules that the final pass names: `L-test`,
`L-raise`, `L-getattr` and `L-other`; `C38` to `C41`.

**The groups** (`rq2-causes/limits.json` gives each label one group):

- *Stated limit.* 44 limits. 42 are those of the final pass. The other two
  are defects (`D-a`, a coroutine names any attribute; `D-b`, an operator
  on a string constant gives no receiver) whose fix was measured and that
  the maintainer left out on 2026-11-15; `docs/design.md` states both under
  "Open problems", so they count as stated limits. An edge that the
  measured fix of a defect removes has the label of the defect: for `D-b`
  that is 8 false negatives, 2 of which the analysts had labelled `L-any`
  and `C12` (the note of each says so).
- *Scorer artefact.* A naming or matching artefact of the scorer: two
  names of one function, or a convention of the ground truth.
- *Ground-truth doubt.* The label doubts the ground truth, not PyCG.
- *Measured rule, not built.* The final pass measured two rules that the
  engine does not have: `callable(x)` and `inspect.iscoroutine(x)` narrow
  `x` as a class test (`C33`, `C35`), and a call that cannot bind its
  arguments ends the path (`C38`). An edge that such a rule removes has the
  label of the rule (one such edge had the analyst's label `C32`; its note
  says so).
- *Gap.* A mechanism that the design does not state as a limit: PyCG
  lacks a rule, or has a wrong one. Each mechanism is a label of its own
  and a row of the table: `GAP-getattr` (a property or descriptor getter
  raises `AttributeError`, so Python calls `__getattr__`) and `GAP-assert`
  (narrowing after an `assert`, which `python -O` does not run). A gap
  never counts under the stated limits.

**State today.** The table is committed (`results/rq2-causes.md` gives the
count of each group and the share under stated limits).
`tests/test_evaluation_questions.py` checks that the label data has, per
batch and inside and outside, as many false positives and false negatives as
PyCG has in `results/rq1-accuracy.json`, and that every label has exactly
one group. The labels are of PyCG `c5c6a89`: after a change of the
engine or of a ground truth that changes these counts, the test fails
until the changed edges are labelled.

## RQ3: scale and robustness

**What is measured.** Does each tool finish on real packages, and in what
time and memory: the share of the packages of a PyPI corpus that it
analyses within the limits, and the median, 90th percentile and maximum of
the wall time and the peak memory.

**The corpus and the limits** (the maintainer's decision, 2026-11-14): the
top 100 packages of PyPI, for every tool, plus the 50 projects of
DyPyBench; 30 minutes and 16 GB per run. The container of rq3 has 18 GB, so
that the watchdog's limit of 16 GB per analysis acts before the kernel's.

**Command.** `docker compose -f evaluation/compose.yaml run --rm rq3` (the corpus:
`/inputs/corpus`, in the image, from the inputs image).
`rq3-scale/table.py` runs `robustness.py` on each half of the corpus (the
PyPI top 100 in `CORPUS_WORK/work`, DyPyBench's projects in
`CORPUS_WORK/dypybench`) and writes `results/rq3-scale.md` and `.json`,
and the report of `robustness.py` of each half beside it. The table has,
per tool, a row for the whole corpus ("both") and one per half (the
column "Corpus"), and lists every package without a run with its reason
(akshare among them). A half that is not fetched is an error that names it
and the packages that are missing; a row that `corpus.py fetch` would
refuse (its URL, SHA-256, commit or version do not agree) is an error even
if its archive was fetched under another row. A run whose processes could
not be killed stops the whole command: no run starts in either half after
it, each is "not run" with the reason, and the table says that it stopped.

**The pilot** (`docker compose -f evaluation/compose.yaml run --rm
rq3-pilot`, `table.py --pilot`) checks that the question runs before the
full run on a Linux host. It is the same run on ten packages, five of each
half, drawn with `random.Random(PILOT_SEED)` (a constant of `table.py`)
from the sorted names of the packages with a file; in that order a package
is taken if it has an analysis target, until the half has five, and one
without is skipped and recorded. Each sampled package is checked as in the
full run (its row, archive and unpacked tree); one that is not fetched is
an error that names it. The limits per run are those of RQ3 (30 minutes,
16 GiB) unless given. It writes `results/rq3-pilot.md` and `.json` (the seed,
the sample, and per package and tool the status, the time and the peak
memory; then the summary columns of the table), never `rq3-scale.*`.

**Inputs and their pins.** `rq3-scale/corpus/pypi-top-100.json` pins one
file of each package by SHA-256, from a rank list that is pinned by its
SHA-256 (see "Robustness on a corpus"). `rq3-scale/corpus/dypybench.json`
pins DyPyBench's 50 projects by their commits and the SHA-256 of GitHub's
archive of each commit (see "The DyPyBench projects"). The build of the
inputs image fetches both: the top 100 into `CORPUS_WORK/work`, DyPyBench
into `CORPUS_WORK/dypybench`.

**State today.** No run is committed.
Both manifests exist; 49 of DyPyBench's 50 projects have a pinned archive
(akshare cannot be recovered: a stated row). The table reads both halves
as one corpus. The prior tools' rows come from their services' runs
(`results/tools/<row>/rq3.json`, `rq3-pilot.json`; see "Prior tools"): a
row without a run says "not run" with its command, HeaderGen's row is
stated (it imports the analysed code). HeaderGen imports the modules it analyses, so it does not run on a
corpus that no security scan covered: its row says "not run: the tool
imports the analysed code, and the corpus is not scanned" (the
maintainer's rule, 2026-11-14, unless the maintainer decides otherwise).
The final
time and memory figures need a Linux host (the maintainer's decision) and
are the last step.

### Robustness on a corpus

Does a version of PyCG finish on real packages, and in what time and
memory? The corpus is the top N projects of a download-rank list of PyPI
(`top.json`: `last_update` and `rows` of `{project, download_count}`), in
the order of the list, with no draw. `corpus.py manifest` pins one file of
each project in `rq3-scale/corpus/pypi-top-<N>.json`, which is committed:

- the release: the highest version by PEP 440 that is final (neither a
  pre-release nor a development release) and has a file that is not yanked
  and was uploaded on or before the date of the list (the whole day, UTC).
  Files uploaded later are left out; yanking is as PyPI reports it today.
  A version with a local label (`1.0+abc`), which PyPI refuses, is not a
  public release and is left out, as is one that PEP 440 cannot parse;
- the file, of the wheels with a Python 3 tag: a pure wheel (ABI `none`,
  platform `any`); else the platform wheel first by interpreter
  (`cp312-cp312`, then `cp3N-abi3` with N <= 12, newest first, then `py3`
  with ABI `none`, then any other), then by platform (manylinux x86_64,
  musllinux x86_64, macOS x86_64 or universal2, macOS arm64, Windows amd64,
  any other), then by file name; else the sdist (`.tar.gz` first);
- a row has the name, rank, version, kind, file, URL, SHA-256, size and
  upload time; a project without such a release or file has the reason.
  The manifest records the rank list by its SHA-256 and its path in the
  repository: `macro-benchmark/selection/top-pypi-packages-2026-11-01.json`
  (SHA-256 `3e958a64ebe1de0b6a4ab4c6b465d481f350fd1dfa926c639add36eb34dabc1e`,
  15,000 rows, `last_update` 2026-11-01), the default of `--rank-list`.

`corpus.py fetch` downloads each file from files.pythonhosted.org (only;
a DyPyBench archive from codeload.github.com, see below), checks its size
and SHA-256, and unpacks it as data into `WORK/<name>/`. Every request and
every redirect goes to https on pypi.org, files.pythonhosted.org,
codeload.github.com or raw.githubusercontent.com (the traces of RQ1); another
is refused. A transfer cut short goes on
with a `Range` request (`corpus.ATTEMPTS` requests in all). A file is
stopped when its server announces or sends another length than the
manifest's, a project's JSON past `METADATA_MAX_BYTES` (256 MiB), and one
body has `DOWNLOAD_DEADLINE` (30 min; JSON `METADATA_DEADLINE`, 5 min) in
all. The deadline is checked after each read of the body (`read1`) and at
its end. The headers, the redirects and the framing of a chunked body
(chunk sizes, trailers) are bounded only by the socket timeout of each
read (300 s for a file, 60 s for JSON): a server that sends them one byte
at a time, within that timeout, can hold a transfer past its deadline. A
length field (`Content-Length`, `Content-Range`) that is not 1 to 18
digits is a refused transfer. A network
failure is a failed fetch of that package (status, exit
status 1), never a result. Unpacking:
regular files and directories only, without execute permission. A member
with an absolute path, a `..` component or a backslash, a link and a
special file are not written, and `WORK/fetch.json` lists them. No file is
written over another. An archive is not unpacked at all, and the package
has no run (the reason is in `WORK/fetch.json` and in the report), when it
is over a bound (`corpus.MAX_MEMBERS` 200,000 members,
`MAX_MEMBER_BYTES` 1 GiB for one member, `MAX_TOTAL_BYTES` 4 GiB in all;
checked on the declared sizes before anything is written, and on the bytes
written; a refused member counts too, as the reader goes through it),
or when two of its members have ambiguous names: paths that are
equal after Unicode normalisation and case folding (as on a file system
that ignores case), their directories included, or an exact duplicate. So
the corpus is the same on every file system. Nothing of the corpus is
installed, built, imported or run. The bounds cover the members and the
bytes written, not the metadata of an archive: the standard library reads
the PAX and GNU headers of a tar and the central directory of a zip before
any bound applies, so a crafted archive can still take memory and time.
The unpacking is not a defence against a crafted archive; the corpus is
pinned by SHA-256 from PyPI, and a corpus from an untrusted source belongs
in a container.

The analysis target. A wheel: the analysis root is the unpack directory,
and the target is each entry at its top that is a `.py` file or a
directory that holds one (a namespace package counts), but `*.dist-info`
and `*.data`. An sdist: the base is the single directory at the top of the
archive; the root is `base/src` if a package directory is directly under
it, else the base; the target is each directory with `__init__.py`
directly under the root. A package that the rule does not cover is
reported with the reason, not guessed. The entry points are all `.py`
files of the target; the dependencies are outside the root.

**The DyPyBench projects** (`rq3-scale/corpus/dypybench.json`). DyPyBench
(Bouzenia, Krishan and Pradel, FSE 2024; github.com/sola-st/DyPyBench at
`bccfda7`, MIT) lists its projects in `text/github-url.txt` and pins each
with `git log -n 1 --until=2023-01-18` on the default branch
(`scripts/install-all-projects.sh`). Git reads a date without a time of day
as that day at the moment the command runs, so for the four projects with
a commit on 2023-01-18 (Pillow, black, marshmallow, pypdf) the manifest
takes the end of the day (UTC) and says so in the row's note. cerberus is
pinned on its branch `main`: its default branch today (`1.3.x`) lacks files
that DyPyBench's trace calls from. akshare has no row with a file: its
repository has no commit before 2023-01-18 (its history starts on
2025-03-31) and PyPI has no release of it before 2025-04-05. The commits
were found with git on clones (branch `eval-dypybench`) and again, for all
49, through GitHub's API (the last commit of the row's branch at or before
the end of 2023-01-18, UTC); both agree.

A row is of kind `git archive`: `repository`, `commit`, its committer date
(`committed`, UTC), and the archive that GitHub makes of the commit
(`https://codeload.github.com/<owner>/<name>/tar.gz/<commit>`) with its
SHA-256 and size. `corpus.py fetch` downloads it like any file, only from
that URL (the row's own repository and commit, a full commit id) and only
over https from codeload.github.com, checks the size and the SHA-256, and
unpacks it with the same rules. The whole row is checked before anything
is downloaded or taken from the cache (`corpus.row_problem`: the URL is
that of the row's file or of its repository and commit, the SHA-256 has 64
hex digits, the size is a positive integer, a git archive's version is
its commit), and again when a fetched corpus is planned: a corpus fetched
under one row is never analysed under another. The unpacked tree is not
trusted either: before any plan or run, and when `fetch` would reuse it,
it is checked against the archive in `CORPUS_WORK/cache`, whose size and
SHA-256 the manifest pins: every file of the tree must be the archive's
member, byte for byte, with no file missing and none added, and its
directories must be those that unpack writes (those that the archive
names and those that hold its members; an entry `.` or `./` names the
root itself), with none missing and none added (`corpus.verify_unpacked`). So the target of a package, which is read from
the tree's directories, is the archive's. A difference is refused with its
path, never repaired; `corpus.py fetch` then fails. `fetch.json` only says which archive a tree came from; it is not
trusted for content. GitHub does not promise that the bytes of
an archive never change; two downloads a few minutes apart gave the same
bytes, and a change would be a hash mismatch, never another tree.

The analysis target of a row is stated in it, from the list of paths that
DyPyBench instruments (`text/dynapyt_instrument_src.txt`, an older list of
58 projects): the root (`""` or `src`) and the modules under it, each a
directory that holds `.py` files or a `.py` file. A package's own tests
that sit inside it (`flask_api/tests`, say) are analysed, though DyPyBench
leaves them out of its instrumentation; tests outside the package are not.
PythonRobotics has no package: its ten directories of scripts are its
modules. python-future's root is `src`, with its Python 2 shims as modules.
A module or root that is not in the archive is a reason, not a guess. The
49 targets hold 2,207 files and 381,817 code lines.

`robustness.py` runs each tool once on each package, in a child process of
`run.py`, under two limits:

- **time**: the wall time of the child; at the limit it is killed (status
  `timeout`);
- **memory**: the child starts a session of its own, so it leads a process
  group that holds every process it starts (a tool may analyse each file
  in a new process). A watchdog in the parent samples the sum of the
  resident set sizes of the group every 0.2 s (`run.WATCH_INTERVAL`;
  Linux: the processes whose `/proc/<pid>/stat` names the group, and their
  `/proc/<pid>/statm`; macOS: `proc_listpgrppids` and `proc_pid_rusage`,
  as `RLIMIT_AS` is not enforced there) and kills the group at the first
  sample over the limit (status `memory`). A process that has gone, or
  that has ended and is not yet reaped (a zombie), is left out of a sample:
  its memory is freed (macOS still reports a zombie's last resident size,
  so a process with an exit time in `proc_pid_rusage` is left out; on
  Linux a zombie's `statm` is all zeros); a group or a process that is there and cannot be
  read is an error, which stops the run (status `unmeasured`, with the
  error): an unread memory is not an empty one. The peak memory of a run
  that finished is the larger of the child's own `ru_maxrss` (exact, for
  the child alone) and the largest sample of the group; the peak of a
  killed run is the largest sample. The tests run the macOS path only;
  the Linux path is not exercised here.

  What the watchdog does not see: what a run allocates and frees within
  one interval (a run can pass the limit by what it allocates in one
  interval); memory that macOS compresses, which is not resident; a
  descendant that leaves the group, by `setsid` (a session of its own) or
  `setpgid` (another group in the same session), which is neither counted
  nor killed. Memory that two processes of the group share after a fork
  counts in each, so the sum can be larger than the memory used. Only the
  child is reaped by the runner; its descendants, once killed, are reaped
  by their new parent (`init` or `launchd`), and until then they are
  zombies.

The runner looks for the end of the child every `run.POLL_INTERVAL`
(10 ms), between its memory samples. The recorded wall time runs from the
start of the child process to the moment the runner sees that the child
has ended, before the group is killed and the child reaped: it includes
the start of the process (the interpreter, the imports of the tool) and
the delay until the end is seen, to which a memory sample or a busy
machine can add. It has no upper bound on that delay. The child also
records its own time of analysis (`seconds`).

At its end, at a limit, and on any exception or interrupt of the runner
(Ctrl-C, or an error of the runner in one run, stops the analyses that
run, at once, and starts no other), the
whole group is killed and then the child is reaped (`run.py` does so
for every run, with or without a memory limit). The runner sees that the
child has ended without reaping it (`waitid` with `WNOWAIT`), so the
group's id, which is the child's pid, cannot have gone to another process
when the group is killed.

A run that finished is `ok`, with the counts of the tool's output: for the
new engine, the modules by status, the definitions, the call sites, the
edges of the classic call graph and the unresolved sites by cause (see
`docs/format.md`); for the original analysis, the nodes and edges of its
graph. A run that raised is a `crash`, with the exception type and the
innermost frame inside the tool; a child that died is a `crash` with its
exit status; a run whose memory could not be read is `unmeasured`; a run
whose process group could not be killed at its end is `cleanup failed`
(the live processes of the group are then listed and sent SIGKILL one
by one, three times, and listed once more; the detail counts the signals
sent and denied and the processes gone and still alive; if the group
cannot be listed, the child alone is killed). A signal sent to a single
process id can reach another process if the id was reused between the
listing and the signal; this path runs only after the system denied the
signal to the whole group, and a run that takes it is recorded as
`cleanup failed`. After a `cleanup failed`, the corpus
run starts no other run: processes that it may have left would disturb
the measurements of later runs. A run not started is `not run`, listed
with the packages without a run, and not counted in the rates. A run
is not repeated. `--jobs K` runs K children at a time.
K changes neither the order of the result file nor which result belongs to
which package and tool; the measured times and memory, and the details of
an error that change from run to run (a pid), are kept apart
(`measured`). Runs share the machine, so the times of a run with
`--jobs 4` are longer than those of a run alone, and an outcome that
depends on the resources can change with K or with the load: a run that
ends just under the time limit alone can time out beside three others.

The report (`results/robustness-<manifest>.md`) gives, per tool, the
success rate at each published threshold at or under the limits of the
run, from the recorded wall time and peak memory (time 30 min, 1 h, 2 h,
6 h; memory 8, 16, 60 GiB): a run succeeds at a threshold if it is `ok`
within both. Then the failures by status and cause; the median, 90th
percentile (nearest rank) and maximum of the time and memory of the runs
that finished, by the size of the target in code lines (< 1k, 1k-10k,
10k-100k, >= 100k); the sums of the counts; and the packages without a
run.

The scripts' own commands (`manifest` and `fetch` need the network: they
run in the maintenance image and in the build of the inputs image, never
on the host):

```sh
python -I evaluation/rq3-scale/corpus.py manifest --top 100
python -I evaluation/rq3-scale/corpus.py fetch --manifest evaluation/rq3-scale/corpus/pypi-top-100.json \
    --cache "$TMPDIR/corpus/cache" --work "$TMPDIR/corpus/work"
python -I evaluation/rq3-scale/robustness.py --manifest evaluation/rq3-scale/corpus/pypi-top-100.json \
    --work "$TMPDIR/corpus/work" --tool new-engine:new:"$TMPDIR/tools/new-engine" \
    --timeout 1800 --memory-limit 8 --jobs 4
```

`robustness.py` options: `--timeout` (seconds, default 1800),
`--memory-limit` (GiB, default 8), `--jobs` (default 1), `--python`,
`--hash-seed` (default 0), `--results`, `--allow-dirty`. It refuses a
checkout with changes, as `run.py` does, and a manifest row that is not
unpacked from its file. A memory limit needs Linux or macOS.

## RQ4: regression test selection

**What is measured.** A client of the call graph: which test files must
run again after a commit. NameRTS's metrics per tool: safe rate (by
analysis and by fallback), test reduction and precision, the mean over the
ten projects, with the rows of NameRTS's and BabelRTS's published
selections.

**Command.** `docker compose -f evaluation/compose.yaml run --rm rq4-pilot` to measure,
then `docker compose -f evaluation/compose.yaml run --rm rq4` (the export:
`/inputs/rts/export`, in the image; the build of the inputs image clones
and then exports). `rq4-test-selection/table.py` runs
`rts.py select` for each PyCG checkout and project, then `rts.py score`,
and writes `results/rq4-test-selection.md` and `.json`. In the container
it reads the export of the clones (`--export`), never the clones: the
image has no git. Without `--export` (on the host) it reads the clones,
and a clone that is not there is an error that names it; with it, a
project that the export lacks is an error that names it. `--pilot`
(the service `rq4-pilot`) selects the first target of each project only, prints
the time and the peak memory of each analysis and writes
`results/rq4-pilot.md` and `.json` instead of the table. On the host a
full run with the same work directory reuses the pilot's graphs; the
container's work directory is temporary and only the tables come out, so
after `rq4-pilot` the full run analyses again.

**The export** (`rts.py export`, run in the build of the inputs image,
where git is; `select` and `score` read the clones with git unless given
`--export`, so on the host they take `--export`). For each project, the file list of each target's parent and
commit (`trees/<project>/<commit>.json`: path, mode and blob id), the
blobs of their regular Python files (mode 100644 or 100755) under the
analysis root (`blobs/<id[:2]>/<id>`, checked against their git blob id
when they are read: a changed byte is an error) and NameRTS's files
(`namerts/<path>`, checked against the manifest's SHA-256). A link is
listed, never read or asked of the remote; a submodule (a gitlink) is not
in the file lists at all, since `ls_tree` keeps blobs only; `select` treats a
changed Python path that is not a regular file as a non-Python file, from
the clone as from the export. The clones are partial: the export fetches
the blobs they lack as git's own lazy fetch does, without negotiation (with
it, GitHub refused a blob of dask: "bad revision"), in one request, else
one blob at a time. A blob that the remote still refuses is not fatal: it
goes to `unavailable.json` (project, target, commit, path, blob id), the
export goes on, and `select` records that target as "not exported" with
the reason. `score` leaves such a target out of every row of its project
(the tools', the published selections' and the ground truth's), and the
table lists it, with the reason, under "Targets left out of every row".
`select` and `score` read exactly what they read from git, so a selection
from the export equals one from the clone.

**The trust anchor is the committed manifest, not the place where the
export was made.** `rts.json` pins, per project and target commit, the
digest of its file list (`trees`: the SHA-256 of the canonical JSON of
`{path: [mode, blob id]}`, keys sorted, no space, ASCII; `rts.tree_digest`).
`select` uses a file list only if its digest is the pinned one, and refuses
a list with an entry substituted, left out, added or renamed, and a commit
that the manifest does not pin; each blob is then checked against its id.
The pins were computed from the export of 2026-11-14 (made on the host from
the clones). The build of the inputs image derives each file list from the
commit objects it fetches (the tree of the commit, blobs only, as
`ls_tree`) and must reproduce these digests; a list that does not is
refused here.
Each row keeps the provenance of its tool from the report of `rts.py score`
(its commit and the hash of its code). The means are kept unrounded in the
JSON, each with the number of projects where its metric is defined; the
Markdown rounds them and says "over n of N" when a metric is not defined on
every project. The command has no `--allow-dirty`.

**Inputs and their pins.** `rq4-test-selection/corpus/rts.json` pins the
NameRTS commit, the SHA-256 of each file that is read from it, and the
projects and target commits.

**The prior tools.** Each row of pyan3, JARVIS, CPyGraph and PyAnalyzer
runs in its service on the parent commit of each target (`docker compose
run --rm TOOL rq4`, or `rq4-pilot` for the first target of each project,
as PyCG's pilot), from the export, with rts.py's limits; it stores per
parent commit its record, the key of its input (`rts.analysis_key`) and
its graph in the volume `tool-graphs`. The rq4 services select with each
stored graph (`rts.select` with `stored`: no analysis runs) and score it as
PyCG's; the time of an analysis is the tool's. A row without its graphs is
"not run" with the command; a graph of another input is "needs rerun".
HeaderGen's row says "not run: the tool imports the analysed code, and the
corpus is not scanned".

**State today.** No selection is committed.

### Regression test selection

The client task (`rts.py`): a real job that takes a call graph as input.
After a commit changes code, which test files must run again? The client
selects the test files from which a changed definition is reachable in the
call graph of the commit's parent. It runs once per version of PyCG, on the
same commits, with the same rules.

**Dataset.** NameRTS (Wang, Pradel, Liu, ISSTA 2026;
github.com/ZJU-CTAG/NameRTS, Apache-2.0): 10 projects, 50 target commits
each. A target is an entry `[direct_parent, true_parent, current, 1]` of
`dataset/commit_pairs_<project>.json`. Its change is `true_parent..current`:
NameRTS's `evaluate.py` runs the tests at `true_parent` and selects for
`current`. On 221 of the 500 targets `true_parent` is older than the git
parent, because the commits between them are not in the dataset. The ground
truth (`ground truth/gt_<project>.json`) gives, per commit, `tests_to_run`,
the test files that ran a changed function, and `total_tests`, the number
of test files. NameRTS found the affected files by instrumenting the
changed functions and running each test file; it traced changed global
variables to their functions with a language server, and removed by hand
the test files that ran a change only while importing. 51 targets have an
empty `tests_to_run`: every selection is safe on them.

`rq4-test-selection/corpus/rts.json` pins the NameRTS commit, the SHA-256 of each file that
is read from it, the repository, analysis root, package and targets of
each project, and the checks (`notes`). The checks compare the dataset with
the ground truth (entries, parents, target flags), look up every target
and parent in the clone, check that the parent is an ancestor, count the
test files of each target against `total_tests`, and check that each file
of `tests_to_run` is a test file of the commit. All of them pass.

**The analysis.** For each target, the Python files under the analysis
root of the parent commit are read from git's object store (no checkout)
and written to a scratch directory. The analysis root is the directory from
which the package is imported: the repository, or `lib` for matplotlib.
The entry points are the files of the package (`packages` in the
manifest), every test file and every `conftest.py` under the root; other
Python files (test data, examples) are analysed if an entry point imports
them. A test file is recognised by NameRTS's own rule
(`is_test_file_pytest`): a `.py` file whose name starts with `test_` or
`unittest_`, in a directory below one named `tests` or `testing`. With
this rule our count equals `total_tests` on all 500 targets. Every command
after `manifest` reads the root and the package from the manifest. The
analysis runs in a child process through `run.py`'s runner (the same
isolation, time limit, memory limit and hash seed).

The tool runs from a snapshot: a copy of the source files of its `pycg/`
(no bytecode) in `WORK/tools/<their SHA-256>/`. The child writes no
bytecode (`-B`), so the hash names the code that runs, whatever bytecode
the checkout holds. The interpreter is resolved once to an absolute path
(on `PATH` if it is a bare name), and that path is both probed for its
version and launched.

A graph, or a failure, is kept in `WORK/graphs/<tool>/<project>/` under a
key of everything that the analysis depends on: the tool's code (the
snapshot's hash), its API, the interpreter and its version, the hash seed,
the time and memory limits, the code of the runner and of the preparation
of its input, and the input itself (the path and the SHA-256 of the bytes
of each file given to the analysis, the entry points, the root). A target
reuses the graph of its parent only under the same key. A run that says
nothing about the tool (its memory could not be measured, its process group
could not be killed) is not kept.

**Changed definitions.** The mapping compares the two versions of each
changed Python file under the root as text: decoded with their declared
encoding (a byte-order mark is the codec's; a second one is text, and
invalid), every line ending (`\r\n`, `\r`, `\n`) as one, line by line with
`difflib`. Git's diff options and settings play no part. It reads both
versions with `ast` and `tokenize` of the interpreter that runs `rts.py`
(recorded; `score` requires one for every selection) and names definitions
as PyCG names them (`pkg.mod.Class.method`, `pkg.mod.f.inner`). The
mapping is the same for every tool, so the tools differ only in their
edges; the 2021 PyCG writes no definitions or lines, so the `definitions`
of `docs/format.md` cannot serve both.

- A removed line belongs to the innermost function or class of the parent
  that holds it, decorators included. An added line belongs to the
  innermost one of the commit that holds it and that the parent has too,
  so a new function belongs to the function, class or module around it. A
  line in no such scope belongs to the module.
- A function's body belongs to the function. The lines of its decorators
  and of its signature (defaults, annotations) belong to the function and
  to the scope around it, which runs the `def` statement.
- A line of a class body outside its methods (an attribute, a base, a
  decorator, a new or deleted method) belongs to the class and to the
  function or module that runs the class body. The new engine has no class
  node: it gives the calls of a class body to the scope around the class.
  The original engine has one (in module `m`, the caller `m.C` makes the
  calls of the body of `class C`), so the class's name stays among the
  changed names.
- A line that holds no token of code (blank, or only a comment) changes
  nothing; a line inside a string is code. A new declared encoding changes
  the lines whose text it changes. If a version cannot be decoded (invalid
  text), the module changes when the file's bytes do. If it decodes but
  does not parse (a syntax error, a NUL byte), a changed line of code in it
  changes the module, and a changed comment changes nothing; when the text
  does not tokenize either, every line counts as code.

**Selection.** A test file is selected if its module, or a node of its
module (a node belongs to the longest module that prefixes its name),
reaches a changed definition or module in the call graph; a path of
length zero counts, so a change inside a test file selects it. A failed
analysis (crash, timeout, memory limit) selects every test file of its
commit. Three client rules follow from Python's and pytest's semantics;
each is measured separately (the variants `no r1`, `no r2`, `no r3` drop
one rule, `no rule` all three):

- **r1**: the `conftest.py` files of a test file's directory and of every
  directory above it are part of the test file, and a new `conftest.py`
  selects every test file below it. Pytest loads them and passes their
  fixtures by parameter name, which no call edge shows. NameRTS's client
  does the same (`conftests_for_test_file`).
- **r2**: each node has an edge to its module, and each module an edge to
  the nearest module above it (across a namespace directory without
  `__init__.py`). A function runs only after the code of its module ran,
  and importing a module runs its packages' `__init__.py` first. So a
  change of module-level code selects every test file that reaches a
  definition of the module, and the calls of a module's code are reached
  by every test file that reaches a definition of the module. Neither
  engine writes an edge for an import (a module node is only a caller), so
  a rule that counts only edges to the module node would select nothing.
- **r3**: a test file that the commit adds or changes is selected. A new
  test file has no node in the parent's graph. NameRTS's client selects
  changed test files too.

A deleted file changes each definition that held one of its lines. A
deleted test file is not selected: the commit lacks it.

**What this client cannot see.** These are limits, not rules: where one
applies, the client can select too little; such a commit is listed among
the unsafe commits and lowers the safe rate. The two safety figures of the
table are measured against NameRTS's ground truth, not guaranteed. This
README is where each limit is stated, in the list below; the table counts
what they cost as unsafe commits.

- A change of a class body (an attribute, a base, a new or deleted method)
  that changes what an inherited or an inheriting method does: the graph
  reaches the methods, not the class.
- A new or deleted module that changes what an import finds: an optional
  import that now succeeds, a new or deleted `__init__.py`, a module that
  shadows another, an import by a computed name. A file that the parent
  lacks has no node in the parent's graph, and adding one changes no line
  of the parent.
- What collecting a new test module or a new `conftest.py` does beyond the
  file itself and the test files below the conftest: code that pytest runs
  while it imports them (a function of the package replaced, for example).
- Fixtures that a test takes by name from a plugin or anywhere but a
  conftest, parametrisation through such fixtures, and plugins loaded by
  entry point.
- Scripts that a test runs in a subprocess (loguru's
  `tests/exceptions/source/`).
- Files that are not Python (data, stubs, C, settings) and Python files
  outside the analysis root: they map to no changed definition (a changed
  test file outside the root still selects itself, by r3). The report
  counts the commits that change a file of each kind.
- Calls for which the graph has no edge: implicit calls (`format(x)` calls
  `__format__`; so does `str.format` with a format string held in a
  variable), calls made by extension modules or by libraries outside the
  root, attributes with computed names.
- Code that an import runs, in a module none of whose definitions a test
  reaches (a module that only holds constants that its code computes).

An analysis that finishes need not have parsed every module: the new
engine keeps a module that does not parse as `failed`.

**Metrics**, as NameRTS defines them (its section 4.1), per project over
its targets. *Safe rate*: the share of commits whose every file of
`tests_to_run` is selected, split into *safe by fallback* (the analysis
failed, so every test file was selected) and *safe by analysis* (the
others). *Test reduction* (skipped): (Tall - Tsel) / Tall, with Tall the
sum of `total_tests` and Tsel the number of selected test files, both
summed over the commits. *Precision*: Taff / Tsel, Taff the selected files
that are in `tests_to_run`. The mean over projects is the mean of the
projects' values, as in NameRTS's Table 2; where a value is undefined for
a project (a precision without any selected file), the report says over
how many projects the mean is. The report adds rows computed by the same
code from the published selections of NameRTS and BabelRTS
(`results/rq1/`); with every target kept they reproduce Table 2 to the
last digit (a target left out of every row of a project, as an unavailable
file makes it, changes these rows too). Not measured:
NameRTS's time reduction, which needs the test suites to run.

`score` scores one experiment or nothing. It needs the selection of every
tool on every project that it is given (`--project` names a subset; the
report then covers that subset, in every row). It refuses a selection made
by another version of the client (`rts.py`, `run.py`, `analyse.py`), on
another manifest or project layout, with a target missing, twice or with
another parent than the manifest's, or with a record of another analysis
than the file's; the selections of one tool that were analysed with other
settings on two projects; and selections whose lines were mapped by more
than one Python.

**Commands.** WORK is outside the repository; the ten clones take about
260 MB, since no blob is fetched before it is read. `fetch` and `manifest`
need the network and git: they run in the build of the inputs image and
in the maintenance image (see "The inputs image"), never on the host.

```sh
python -I evaluation/rq4-test-selection/rts.py fetch --work "$WORK"     # or --project NAME
python -I evaluation/rq4-test-selection/rts.py manifest --work "$WORK"  # rewrites corpus/rts.json
python -I evaluation/rq4-test-selection/rts.py select --work "$WORK" --project loguru \
    --tool new-engine:new:"$TMPDIR/tools/new-engine" --timeout 1800 --memory-limit 16384
python -I evaluation/rq4-test-selection/rts.py select --work "$WORK" --project loguru \
    --tool original-engine:original:"$TMPDIR/tools/original-engine" --timeout 300
python -I evaluation/rq4-test-selection/rts.py score --work "$WORK" --tool new-engine --tool original-engine
```

`select` writes `WORK/selections/<tool>/<project>.json`: the experiment
(the tool's commit and code, the settings, the client, the dataset, the
layout, the Python of the mapping) and, per target, the changed
definitions, the selection of each variant, and the run.
It refuses a checkout with changes, as `run.py` does (`--allow-dirty`);
`--limit N` selects for the first N targets only, to try a project.
`--memory-limit` is 16384 MiB unless given. `score` writes
`WORK/report/rts.json` and `rts.md` (`--out DIR`). A project name and a
tool label must each be one path component (a letter or digit, then
letters, digits, `.`, `_`, `-`), and a target a 40-digit commit id. The
clones (`WORK/NameRTS`, `WORK/repos/`), the tool snapshots, the graphs, the
selections and the default report are written only where they stay inside
WORK, links resolved. Two destinations are the user's to choose and are not
checked: the report of `score --out DIR`, and the manifest that `manifest`
writes (`--manifest`, by default `evaluation/rq4-test-selection/corpus/rts.json`).

`fetch` clones from github.com over https only: `git clone --no-checkout
--no-recurse-submodules --template= --filter=blob:none`. Every git command
runs in the environment of `macro-benchmark/draw.py`'s `git_environment`:
no `GIT_*` variable of the caller, of the caller's settings only `http.*`
(a proxy's), neither the user's nor the system's configuration, no prompt,
no LFS download; and `GIT_ALLOW_PROTOCOL=https`, `GIT_NO_REPLACE_OBJECTS=1`.
A clone in WORK is used, by every command that reads it (also `score`'s
reads of NameRTS), only if it is as `fetch` makes it: its own `.git`
directory; no hook, replacement ref, grafts, alternates, `commondir` or
`shallow` file; and only the local settings of a clone, each once and with
its value, the remote that of the manifest. Nothing is checked out, so no
hook or filter runs. Files are read with `git ls-tree` and `git cat-file`;
the blobs that one commit needs are fetched in one request. `fetch` and
`score` check the SHA-256 of each NameRTS file against the manifest.

## What the questions share

### How names are matched

Edges are sets of (caller, callee) pairs. Two modes are reported, always
both.

**Raw.** The names as the tool writes them, with one rule for callees
outside the package: the trace of the project (`dynamic/<name>.json`) gives,
for each outside function that the test suite called, its canonical name
and its other public names (`aliases`). A callee name that is one of these
names stands for that function, by the same lookup that
`macro-benchmark/check.py` uses for the ground truth (`check.callee_names`).
So `os.path.join` and `posixpath.join` are one function, and a caller that
reaches it under both names has one edge. Not matched: a name that the
trace does not know (a function that no test called, or a name through a
private module or a class that no traced object has), and a name that the
trace gives to two or more callees (classes that a library creates at run
time can share one qualified name, and then the `__call__` of that name is
a name of each of their functions); the trace maps such names to nothing,
and they are compared as written (the table of names of one function below
still applies to them).
Callers are package functions and are matched
as written: the aliases apply to callees only. The micro-benchmark and the
2021 benchmark have no trace: no aliases apply there, only the table of
names of one function below. On the two versions in `results/`, the aliases
change no count.

**Two names of one function of the standard library** (the maintainer's
decision, 2026-11-14) are one callee, in both modes and for every tool. The
names come from a committed table, `common/same_function.json`: groups of
names, each with its representative and a one-line source (the CPython
file, and what makes the names one object, for example "`os._Environ`
inherits `get` from `collections.abc.Mapping`"). A callee outside the
analysed package, after the aliases of the trace, that is a name of a group
stands for the group's representative, in the tool's graph and in the
ground truth alike. A name inside the analysed package is never mapped,
and nor is a name whose representative would be inside it, so no name
moves between the inside and the outside edges and the inside counts are
the same as without the table. Nothing is imported and no object is looked
up while scoring: the scores depend on the graphs, the ground truths, the
traces and the table only, not on the scorer's interpreter, on import order
or on warning filters. Every result records, as `scorer.table`, the
SHA-256 of the bytes of the table that scored it (and, only for the
record, the scorer's interpreter). A stored
measurement scored with another table, or with none recorded, is never
mixed with this run's: its cell says "scored with another table".

A group is in the table only if its names are one object on every
supported CPython (3.12 to 3.15); `tests/test_same_function_table.py`
checks, on the interpreter that runs it, that each group's names resolve
statically to one object, and that the table is sorted, has no group of
one name and no name in two groups. The groups come from a procedure: the
generator `tools/same_function.py candidates TOOL_DIR...`
lists, for the graphs of any tool alike, the pairs of names at one caller
(in the tool's graph or the ground truth, after the trace's aliases) that
resolve to one object (`common/lookup.py`, static inspection) and are not
yet one group; a person checks each on CPython 3.12 to 3.15 and adds it
with its source. The table holds five groups today, found for the PyCG
rows (see PyCG's `docs/paper-notes.md`). The first measurements of batches
2 to 4 were scored before the table existed (names by string equality and
the trace's aliases); that of batch 5 with it.

A scored edge is one matched (caller, callee) pair, however many spellings
of it a graph has. The result files list each false positive and false
negative once, by its matched names, and keep the names as written in the
graph under `spellings` when they differ.

**Normalised.** The raw matching after the renamings of `NORMALISATIONS` in
`score.py`, applied to both graphs. The table, with the reason why each
renaming keeps the function that an edge refers to, is `NORMALISATIONS`
itself: a class-body caller becomes the scope that runs the
class body (the original analysis attributes the decorators of methods to
the class); the 2021 names of builtin types (`<**PyStr**>.join`) become
today's (`<builtin>.str.join`); `<builtin>.generator` becomes
`types.GeneratorType`. A name that a tool invents (`re.compile.match` for
`re.Pattern.match`) is not renamed: that would decide which function the
tool meant.

### Isolation and determinism

Each analysis runs in `python -S -s -P -B evaluation/common/analyse.py`: no
`site` module, so no `.pth` file and no `sitecustomize` runs (an installed
package that is the analysed target could run code that way); no user
site-packages; neither the script directory nor the working directory on
`sys.path`; no bytecode. `analyse.py` puts the checkout first on
`sys.path`; the working directory is a new temporary directory, and the
environment has only `PYTHONHASHSEED` (`-E` would drop it). The version
probe runs with `-I -S`. Output paths are made absolute before the child
starts. The original
analysis iterates over sets, so its result depends on the hash seed: with
hash seeds 0 to 9 it gave 9 different graphs of cachetools, and 2 of h11.
The seed is 0 unless `--hash-seed` says otherwise, and `runs.json` records
it. The scorer's output is a function of its inputs (graphs, ground
truths, traces, sources, the table of names of one function), written in a
fixed order.

The generator of the table looks up whether an outside
name exists (`common/lookup.py`; the scoring never does). The lookup imports
only the modules of its fixed list `ALLOWED_MODULES`: standard-library modules and
submodules that do nothing but define names when imported (no `__main__`
entry point, no `antigravity` or `this`, no GUI toolkit, no third-party
module, no module of the analysed sources). It imports the longest prefix
of the name that is on the list, with warnings ignored, then looks up the
rest by static inspection only: the `__dict__` of a module (never a
submodule from it), of a class and its MRO, of an instance and its type;
no property, descriptor or `__getattr__` of the objects runs. A name with
no prefix on the list is "not looked up". An allowed module still runs its
own code when it is imported, and may import others, so what a name finds
can depend on what was imported before; this is why the scoring uses the
table. Any exception in the import or the lookup,
also `SystemExit`, makes the name unknown. The lookup uses the interpreter
that runs it, so a name of another Python version can differ.

### Prior tools

`prior_tools.py` compares PyCG with five prior tools on the
micro-benchmark and on the macro-benchmark:
[pyan3](https://github.com/Technologicat/pyan) v2.9.0, JARVIS
([`pythonJaRvis.github.io`](https://github.com/pythonJaRvis/pythonJaRvis.github.io)
at `8871b86`), [CPyGraph](https://github.com/awen-li/CPyGraph) v0.1.0,
[HeaderGen](https://github.com/secure-software-engineering/HeaderGen) at
`1ea5226` and [PyAnalyzer](https://github.com/2024icse/PyAnalyzer) at
`ea143ef`. Every tool runs in its own Compose service, its jobs one
after another or `--jobs K` at a time, each a child process in a
directory of its own (`container/tool_job.py`); no tool runs
on the host. The tables measure PyCG themselves. JARVIS has three rows: **JARVIS** (its CLI defaults,
edges as written: the headline row of the tool), **JARVIS, class edges
dropped** and **JARVIS --precision**. PyAnalyzer has two rows:
**PyAnalyzer** (its call-graph file) and **PyAnalyzer (call relations of
the dependency report)**. A run writes `results/tools/<row>/rq1.json`
and the graph of each macro project, `results/graphs/<row>/<project>.json`
(RQ1), `rq3-pilot.json` and `rq3.json` (RQ3); the rq1 service
scores the rq1 runs first (`prior_tools.py`): `results/prior-tools.md` and
`results/prior-tools.json`, which the table of RQ1 reads.

| File in `tools/` | Content |
|---|---|
| `build.py` | The pins of the images, the tree hash and HeaderGen's dependencies of a project (Compose builds the images: see "The images") |
| `pin.py` | Resolves, once, the pins that need the network: base image digests, tree hashes, requirements files |
| `pins.json` | The base image of each tool by digest and the SHA-256 of each exported tree |
| `docker/<tool>.Dockerfile` | The image of a tool; `headergen-deps.Dockerfile` adds a project's runtime dependencies |
| `<tool>-requirements.txt` | The PyPI wheels of an image, with the hash of every file (`.in`: the input of `pin.py`) |
| `image_checks.py` | Checks at the build of an image (our code): the hash of each tree; CPyGraph's interpreter and CMake cache |
| `headergen-shim/` | Our `framework_models` for HeaderGen (see "HeaderGen") |
| `pyan.py`, `jarvis.py`, `cpygraph.py`, `headergen.py`, `pyanalyzer.py` | The adapters: the image of the tool (`IMAGE`), the driver that runs inside the container, and the conversion of its output |
| `headergen-deps/<project>.txt` | The runtime dependencies of a macro project for HeaderGen, by hash for CPython 3.12 (from `pin.py deps`), or why they cannot be installed |

#### The images

Compose builds each image from pinned inputs only (`docker compose
build TOOL`): the base image by digest (one anchor per base in
`compose.yaml`, equal to `pins.json`: the Python that its requirements were
resolved for), the tool's source as the plain tree of its pinned commit
from the inputs image (`/inputs/tools/<name>`, the additional context
`inputs` of the build; the image checks its SHA-256 against `pins.json`
with `image_checks.py tree`)
or as a PyPI wheel, and its wheels by hash (`pip install --require-hashes
--only-binary :all: --no-deps`: no resolution at build time). The build
context is `evaluation/`, of which only `tools/` goes in
(`.dockerignore`). The build is the only time with network. pip is removed
after the install. Each image has a user 10001 (a service runs as the
caller's user: the tool's files are readable by all), the name
`pycg-tool-<tool>`, and `/opt/tool/image.txt`, its pin and its trees: the
entry refuses to run an image of another pin or other trees ("build it
again"). `tests/test_compose.py` checks the build arguments against
`pins.json` and the adapters.

| Tool (rows) | Base image | Python | Source pin | Build fetches (hosts) | Shim, stated | Image tag |
|---|---|---|---|---|---|---|
| pyan3 | `python:3.15-slim` by digest | 3.15 | PyPI wheel `pyan3==2.9.0` by hash | 3 wheels (`pyan-requirements.txt`): pypi.org, files.pythonhosted.org | Node hash of namespace and name (see below) | `pycg-tool-pyan` |
| JARVIS (3 rows) | `python:3.13-slim` by digest | 3.13 (it uses `ast.Num`, gone in 3.14) | `8871b86494a1aec0095686ffa543994a7361af88`, tree `Jarvis/tool/Jarvis`, SHA-256 in `pins.json` | setuptools 80.9.0 (`jarvis-requirements.txt`): pypi.org, files.pythonhosted.org | `importlib.metadata` imported first (see below) | `pycg-tool-jarvis` |
| CPyGraph | `python:3.14` (the full image: C++ compiler, make, CPython headers and library) by digest | 3.14 | `984d34ed2f6b81eecb60b975723ad20394ec4a69`, whole tree, SHA-256 in `pins.json`; built with `CPYGRAPH_ABLATION=none` | CMake wheel (`cpygraph-requirements.txt`, from `pin.py`): pypi.org, files.pythonhosted.org | none | `pycg-tool-cpygraph` |
| HeaderGen (hybrid: imports the analysed modules) | `python:3.12-slim` by digest | 3.12 | `1ea52265ca4e76bb202a2deb26f3b9394d3caa95` (`pycg_extended`, `beniget`, `typestub-database`, `framework_models/aliases`) and typeshed `be0ef211679fabf020dcc79cd4b5c43af0fa1839` (`stdlib`, `stubs`), SHA-256s in `pins.json` | about 35 wheels (`headergen-requirements.txt`, from `pin.py`, cutoff 2024-10-19): pypi.org, files.pythonhosted.org; per project with dependencies, `tools/headergen-deps/<project>.txt` | `framework_models` replaced (see "HeaderGen") | `pycg-tool-headergen`; `pycg-tool-headergen-deps-<project>` (`NAME`) |
| PyAnalyzer (2 rows) | `python:3.13-slim` by digest | 3.13 | `ea143ef8bdec2b5f8841bb600b348f0975b90349`, directory `SourceCode/pyanalyzer` only, SHA-256 in `pins.json`; no licence file (ENRE-py tag `zdh-stable`, `eb6d0f6`, MIT, is the same code but 15 lines) | nothing but the base image | `Path.iterdir` sorted (see "PyAnalyzer") | `pycg-tool-pyanalyzer` |

Every build also pulls its base image from Docker Hub (registry-1.docker.io,
auth.docker.io and its blob host production.cloudflare.docker.com; see "The
hosts of the builds"). What of a tool runs at build time runs
in the build container: the probes, which import the tool (pyan, JARVIS,
PyAnalyzer), CPyGraph's CMake files and its compilation, and HeaderGen's
first import, which writes its stub cache.

**What each tool runs on.** Every row runs on the micro-benchmark and on
the 30 projects of the macro-benchmark; if the sources are not there,
the cell says so. The
micro cohort is the cases up to the oldest Python of the rows that run:
3.12 with HeaderGen.

| Benchmark | pyan3, JARVIS, CPyGraph, PyAnalyzer | HeaderGen: runtime dependencies in the image of the job |
|---|---|---|
| micro-benchmark | the job's modules | none (the cases import only each other and the standard library) |
| batch 1 | the package's modules | tenacity (tornado, trio, sniffio); the others list none |
| batch 2 | the package's modules | configargparse (yaml, toml), marshmallow (simplejson); the others list none |
| batch 3 | the package's modules | aiocache (redis, async_timeout, aiomcache, msgpack), markdown2 (pygments, latex2mathml); the others list none |
| batch 4 | the package's modules | bottle and itables have a pinned file; the file of itables says why its dependencies cannot be installed |
| batch 5 | the package's modules | not pinned: HeaderGen's cells of the projects with dependencies are not measured |
| ICSE 2021 set | the package's modules | fabric, face_classification and Sublist3r list dependencies, but have no requirements file (they have no test run): their HeaderGen cells are not measured, with that reason; asciinema and autojump list none |

A project "lists" dependencies in the `dependencies` of its
`projects.toml` (top-level modules). `pin.py deps` pins them for HeaderGen:
each module's distribution at the version of the project's trace
requirements (`macro-benchmark/requirements/<project>.txt`, the test environment of
its trace), with its closure resolved under HeaderGen's own pins and those
trace versions as constraints, for CPython 3.12 on Linux aarch64 and x86_64
(the two hosts of the evaluation), with the hashes of the files of both, in
`tools/headergen-deps/<project>.txt`. The rule: the tool's own
environment wins, as it is the tool as pinned and it must work. A
distribution that HeaderGen's image has is left out of the project's file
(a test refuses a committed file that holds one), and one that it has at another
version than the trace's is recorded there and in the result, per project
(the distribution, the tool's version, the trace's version); a project
whose dependencies cannot be resolved against HeaderGen's pins is
"unavailable" with that reason. A limit of this rule: it compares
distribution names only. Two distributions with different names that ship
one import name, or one portion of a namespace package, are not seen as
one; the later install then overwrites files of the other. No committed
file has such a pair (checked by the third review), but nothing refuses
one. The trace requirements cannot
serve as they are: they were pinned for CPython 3.14, and their compiled
wheels have no 3.12 file (the pilot's build of aiocache's image refused
msgpack 1.1.2 so). A module that the trace environment does not hold
(win32api) is not installed, and the file says so. If no resolution exists
for 3.12 on a platform, the file says why and the project gets no image.
A project with dependencies runs in its own image only, the service
`headergen-deps` (`NAME=<project>`, built after `headergen`; its
entry refuses an image whose `/opt/deps/requirements.txt` is not the
committed file), which writes `results/tools/<HeaderGen's row>/rq1-<project>.json`
and the project's graph;
the scorer lays it over the run of `headergen`, which states the project
"unavailable" with that command. A project whose dependencies are not
available (no requirements file, not pinned, not installable, or not run in
its image) is never run in HeaderGen's own image and its edges are never
scored as false negatives: its job is "unavailable" with the reason, its project is left out of
the totals, and the cell says "n of m projects measured".

#### Build the images

A rerun builds the images from the committed pins:

```sh
C="docker compose -f evaluation/compose.yaml"
$C build inputs                                      # /inputs/tools/<name>, plain trees
$C build pyan jarvis cpygraph pyanalyzer headergen
NAME=tenacity $C build headergen-deps                # one per project with dependencies
```

Maintenance only, not part of a rerun: `pin.py` writes the pins again
(after a new version of a tool or of a base image). `pin.py images` asks
the registry through docker on the host (`docker buildx imagetools
inspect`) and runs no git; the others run in a container:

```sh
python3 -I evaluation/tools/pin.py images            # base image digests (registry)
$C run --rm -v "$PWD/evaluation/tools:/repo/evaluation/tools" inputs \
    python3 -I evaluation/tools/pin.py trees         # tree hashes, from /inputs/tools
# uv, pypi.org: in the maintenance image (see "The inputs image")
pin="docker run --rm -u $(id -u):$(id -g) -e HOME=/tmp -v $PWD:/repo:ro -w /repo \
    -v $PWD/evaluation/tools:/repo/evaluation/tools pycg-evaluation-maintain \
    python3 -I evaluation/tools/pin.py"
$pin requirements headergen
$pin requirements cpygraph
$pin deps                                            # HeaderGen, per project (uv)
```

After `pin.py images` or `pin.py trees`, copy the new digests and hashes
into the build arguments of `compose.yaml` (the test says which differ).
The trees are data: the build copies them from the plain trees of the
pinned commits (no git on the host or in the build), checks their SHA-256,
and nothing of them runs on the host (the HeaderGen tree needs no
submodule: the typeshed tree comes from its own clone). `pin.py` writes
`pins.json` and the two requirements files, which are committed. `pin.py
requirements` runs `uv pip compile` for the tool's Python and Linux,
wheels only, with an upload cutoff, `--generate-hashes` and no
configuration; uv reads wheel metadata only. It refuses a result with a
line that is not `name==version` with hashes, and, for HeaderGen, one that
holds numpy, scipy, scikit-learn or nltk (the shim makes them unneeded).

#### Run a tool on a question

```sh
docker compose -f evaluation/compose.yaml run --rm pyan rq1
docker compose -f evaluation/compose.yaml run --rm jarvis rq1 --variant 2
NAME=tenacity docker compose -f evaluation/compose.yaml run --rm headergen-deps rq1
docker compose -f evaluation/compose.yaml run --rm rq1       # scores them, then the table
```

The entry (`container/tool_job.py TOOL QUESTION [--variant N]`) runs the
jobs of the question for each row of the tool (rq3 and rq4: its headline
row only), one after another: rq1, the
micro cases up to the oldest Python of the tools' bases (3.12, HeaderGen)
and the 30 projects of the macro-benchmark (not run if a project has no
source); rq3-pilot, the sample of the PyCG
pilot (`rq3-scale/table.py pilot_plan`); rq3, both halves of the corpus
(`robustness.plan`). HeaderGen runs rq1 only: it imports the analysed code,
and the corpus is not scanned. Each job has the one limit of every run,
1800 s and 16384 MiB, as for PyCG; the service has 18 GiB (`EVAL_MEMORY`). A row's result file holds the tool's facts (version, pin,
Python, image, limits) and per benchmark (rq1) or half of the corpus (rq3) the
record of each job: status, time, peak memory, the SHA-256 of its exported
input and of its graph. For rq1 the graphs, in the benchmark's names, are
stored too: those of the micro cases in the result file, that of each
macro project in `results/graphs/<row>/<project>.json`. Both directories
are committed, so a reader scores a tool again without running it.

`prior_tools.py` (in the rq1 service, Python 3.14 for the scorer) scores
each row's rq1 file. It exports every job's input again and compares its
SHA-256 with the run's: a benchmark whose inputs changed is "needs rerun"; a row
without a file is "not run" with the command; a micro run of another
cohort is "needs rerun". The benchmark commit of a cell is that of the
scoring, so the hashes, not the commit, prove that the graphs belong to
these inputs. A graph is used only with the record of its run (its
SHA-256): a finished job whose graph file is missing, or is another graph,
is "needs rerun", never a score of zero.

#### What `container/tool_job.py` does

For each job it exports the job's files to `/tmp/run/job` (a tmpfs of the
service), with the stub (`.pyi`) beside each module, which PyCG reads: each
must be a regular file below the job's root, reached through no symbolic
link; it is copied with mode 0644 (directories 0755). The tool sees that
export, never the repository's tree, so no ground truth and no other
project is read through it. It writes the driver's request and copies the driver
(the adapter's own file); the image leads `/job`, `/out`, `/driver` and
`/request.json` to `/tmp/run`. Then it starts the adapter's command as a
child in a new process group, with `PATH`, `HOME=/tmp`, `PYTHONHASHSEED=0`
and `PYTHONDONTWRITEBYTECODE=1` only, and watches it as `run.py` watches
PyCG (`run._watch`): past the time limit the group is killed (status
timeout); a sample of the resident memory of the group over the limit
kills it too (status memory; `peak_rss_mib` is the largest sample); a group
that cannot be killed is "cleanup failed". The next job starts on an empty
work directory. Only after status 0 does the entry read the declared
output of `out/`, opened without following a link, a regular file of at
most 64 MiB; anything else is refused (a crash with the reason). The
outputs are data: the adapters parse JSON only (no pickle, no eval) and
check its shape; a value of another type is a crash of the job. The
service has no network, a read-only root, no capability and the caller's
user, as every service; HeaderGen's jobs share its container, which holds
nothing but the tool, the read-only repository and the inputs.

#### How each tool runs and what its adapter converts

Each adapter's docstring has the full list. Every edge that an adapter
drops, and every edge with a name that it cannot map, is counted by reason
in the table ("Edges dropped or kept by the adapters"). No conversion uses
the ground truth.

**pyan3.** The driver calls `CallGraphVisitor(files, root=root)`, the
analyser of the CLI with its defaults, on every module of the job (pyan
does not follow imports, so a micro case gives all its files), and writes
every "uses" edge with the flavor of each end and whether it is defined in
the files. An edge is kept when the CLI draws it (both ends defined in the
files) and its target is a function, method, staticmethod or classmethod.
Names are pyan's full names, which are those of the benchmarks. Lost:
every outside edge (pyan defines no node outside the files); lambdas and
comprehensions (folded into the enclosing function); the edge to an
instantiated class (pyan adds one to `C.__init__`). A "uses" edge is not
always a call: a function passed as a value is an edge. Dropped:
wildcard targets (`*.name`), names, attributes, imported items, modules
and classes. A shim, stated here as for JARVIS: before the analysis the
driver gives pyan's `Node` a hash of its namespace and name (pyan sets both
once, in the constructor); equality stays identity. Without it a Node
hashes by its address, so sets of Nodes iterate in an order that changes
from process to process, and `resolve_imports()` takes the first same-name
candidate of such a set: an ambiguous import could resolve differently on
two runs. The shim makes pyan's traversal deterministic without changing
its inference rules.

**JARVIS.** The driver runs the tool's own CLI, `jarvis_cli.main()`, with
`<entry points> --package <root> -o <file>`: the CLI defaults, without
`--precision` and never with `--decy`; the row **JARVIS --precision**
adds `--precision`. A shim, stated in the table too: the driver imports
`importlib.metadata` before the tool starts. JARVIS puts an import hook
with empty module sources first, then calls
`importlib.invalidate_caches()`, which on Python 3.10 and later imports
`importlib.metadata` through that hook; without the shim every run fails.
Names are those of the PyCG of 2021; `<str>.m`, `<list>.m`, `<int>.m` and
`<map>.m` become `<builtin>.str.m`, `.list.m`, `.int.m` and
`<builtin>.dict.m`. Edges to classes: JARVIS links the scope that defines
a class to the class; the rows **JARVIS** and **JARVIS --precision** keep
them as written (false positives); the row **JARVIS, class edges
dropped** drops every edge whose callee is a class defined in the job's
files and counts them. Names with a tool marker that cannot be mapped are
kept and counted. Lost: every outside callee that is not a builtin.

**CPyGraph.** The runner exports the job's modules below the root without
an empty root `__init__.py` (one with code is refused; a job with a
top-level `src` directory of modules is refused, as pygCG would analyse
`src` instead). The driver, run by the interpreter that pygCG embeds
(`/usr/local/bin/python3.14 -I -S -B`), copies the export to `/tmp/run/stage`
and runs `pygCG /tmp/run/stage --no-profile` with an environment of
`PYTHONHASHSEED=0`, `PYTHONNOUSERSITE=1`, `PYTHONSAFEPATH=1`,
`PYTHONDONTWRITEBYTECODE=1` and `TMPDIR` only, in a new empty working
directory. pygCG starts that interpreter twice without `-I` or `-S`, so
the image build refuses an interpreter whose site-packages or standard
library holds startup code (`image_checks.py site`), and checks in the
CMake cache that CMake chose it and no ablation. The driver also compiles
each staged module (`compile()` only) and names every code object twice,
as pygCG names it (`<lambdaN>` numbered per enclosing code object) and as
the benchmark names it (by the line and column of the `lambda` keyword in
the scope that evaluates it); the adapter renames by this map. A helper
frame (generic parameters, annotation scope, generator expression) is its
owner as a caller; as a callee its edge is dropped and counted. The three
builtin-type methods of its fixed list are renamed to `<builtin>` names.
Lost: every outside callee but the 12 names of its list; every function
that no module body reaches (pygCG has no entry points).

**HeaderGen (hybrid: imports the analysed modules).** HeaderGen is not
static: its import manager puts the package root first on `sys.path` and
imports every module that the analysed code imports, with their imports;
its type lookup imports the libraries that the code names. So it runs
only in its image, a new container per job, and its result depends on what
is installed: a module whose import fails is outside code for it. Each
macro project with runtime dependencies runs in an image that has them
(table above). The driver calls the tool's API,
`CallGraphGenerator(entry_points, root, 500, "call-site")`, `analyze()` and
`formats.CallSites(cg).get_cg()` (500 is the iteration limit of its own
product path; its `call-graph` operation and its CLI fail on any edge).
The stub cache that HeaderGen writes at its first import is built in the
image and is read-only at run time. Never its server, its `setup.sh` or
its Dockerfile. A shim, stated in the table: the analysis uses one
function of the tool's `framework_models` package, `check_alias`; the real
package loads scikit-learn, scipy, numpy, nltk and eight pickled
classifiers, and downloads nltk data, at import. Our `framework_models`
(`tools/headergen-shim/`, 15 lines), first on `sys.path`, has the same
`check_alias` over the pinned `aliases/*.json`; the image holds no other
`framework_models`, and the driver checks which one it imported.
Conversion: every dotted part loses a trailing `:<line>` and `<n>`; nodes
that become equal are merged and counted; `<dict>.m` and `<list>.m`
become `<builtin>.dict.m` and `<builtin>.list.m`; names with other markers
(`<RETURN>`, `<**INVALID**>`, `<external>`) are kept and counted. Its
outside callees are named by their definition (`posixpath.join`), the
benchmarks by access path: kept as written. `get_cg` itself starts a new
set for each raw caller that strips to an earlier name, so of two
definitions of one name only the later one's callees reach the output: the
tool as released. Its unit is the call site of one line: a caller-to-callee
graph cannot show its flow-sensitivity.

**PyAnalyzer.** Static: it reads the files as text and parses them; it
imports and runs nothing of them. The driver copies the export to
`/tmp/root/stage` (an absolute path whose parent holds nothing else, as its
import resolution looks one level up; the parent is made new for each job),
works in `/tmp/run/scratch`, sorts
`Path.iterdir` (the shim, stated in the table: the order of the files then
does not depend on the file system), and runs its command line
`pyanalyzer /tmp/root/stage --cfg --cg` without a builtins stub (the paper's
is not shipped), standard output discarded. The row **PyAnalyzer** reads
its call-graph file; the row **PyAnalyzer (call relations of the
dependency report)** reads each Call relation of its report with its
`resolved` targets, as its authors' own evaluation did, and keeps an edge
whose caller is a module, function, lambda or class and whose target a
function or lambda (the rest dropped and counted). Names: the root prefix
`stage.` is stripped, `pkg.__init__` becomes `pkg`, and a lambda
`<scope>.(<line>)` becomes `<scope>.<lambdaN>` by the source: the lambdas
of a scope are numbered by line and column, those of decorators, defaults,
annotations, bases and type-parameter bounds counting in the scope that
evaluates them. As in the benchmark's names, which come from the code that
Python 3.14 and later compile, an annotation that runs nothing does not
count: a postponed one (`from __future__ import annotations`), that of a
local variable, and that of a target that is not a plain name (`box[0]: T`,
`obj.x: T`, `(x): T`); the target and the value of an annotated assignment
always count. The report gives each entity its line and column, so its
row names every lambda; the call-graph file gives the name only, so two
lambdas of one scope on one line share a name there (kept and counted),
and so do the lambdas inside them. Entities are named before edges are
joined. It names no
callee outside the package: its outside columns say so, and its rows are
scored on the edges inside the package. An exception of the tool ends the
job without a graph: a failed run with the last line of its error. A
second source of run-to-run difference stays: its heap objects hash by
`id()`, and one rule returns after the first pair of a product of such
sets. Lost, by reading: `self` has values only where the class is
instantiated in the analysed code (the report keeps those calls), `from .
import x`, package objects, calls of instances, `super()`, inherited
`__init__`.

The pilot of PyAnalyzer, before its full run (the lead's), ran in its
image with `tools/pyanalyzer_pilot.py` (removed with the host runner, at
the step to the Compose services): `stored` compared the converted graph
of each of the 119 stored PyCG-2021 cases with the converted stored graph,
without the edges of the authors' stub; `repeat 20` counted the distinct
graphs of 20 new containers.

The pilot's results (2026-11-14, on Apple silicon, image `python:3.13-slim`):
`repeat 20` gave one graph of 20 on `classes/call`, `decorators/assigned`
and h11. `stored`: of the 119 cases, 118 equal and one differs,
`dicts/update`, where the stored graph has `main -> main.func2` and ours
has not. The cause is the missing builtins stub, not our conversion or the
shim. The case is `d = {"a": func1}; d.update({"a": func2}); d["a"]()`.
PyAnalyzer models `d.update(e)` only when the callee is a function object
named `update` (`is_dict_update`, `cfg/HeapObject.py:279`, applied in
`Resolver.handle_indexable_object_modify`, `Resolver.py:391-399`); that
function object exists only in a builtins stub. The stored graph has the
callee `builtins.dict.update` beside `func2`, which shows that the
authors' stub gave it; without a stub `d.update` resolves to nothing, so
`func2` never enters `d`. The shim changes only the order of files, and
the case has one file; our conversion maps `update.main.func2` as it maps
`func1`.

**PyCG.** The checkout's `pycg` tree, exported, runs through `analyse.py`
in the plain image; its names are the benchmark's, so nothing is
converted.

### Another tool

One more prior tool is one image, one adapter and one table entry: an
adapter `tools/<tool>.py` (its `PIN`, its `IMAGE`, `command`, `convert` and
the driver that runs in the container), its Dockerfile in `tools/docker/`,
its base image and trees in `tools/pins.json`, its service in
`compose.yaml` (the build arguments from `pins.json`), its name in
`build.TOOLS` and the prefix of its rows in `tables.PRIOR_TOOLS`.
`prior_tools.py` and `tool_job.py` need no change. Graphs of a tool run elsewhere can be scored
too: write them as `{caller: [callees]}` JSON in the layout of `run.py`
(`<dir>/macro/<project>.json`, `<dir>/micro/<category>/<case>.json`), named
by the conventions of `PyCG/micro-benchmark/README.md`, and give `<dir>` to
`score.tool_results` (as `loop.py compare` does). Without `runs.json`, a
missing graph counts as a failed run.

### Reproducing the results

The results come from the Compose services, in the order of "The full
run": nothing runs on the host but Docker, and no command clones a
checkout or a submodule. The services measure the PyCG of this repository
(`PYCG`, clean at a commit) and the original PyCG at `ORIGINAL`; every
input is fetched by the build of the inputs image and checked against its
pin there, before any image that analyses or traces it is built (see "The
inputs image").

**One limit for every run** (the maintainer, 2026-11-15): every run of
every question, of PyCG, of the PyCG of 2021 and of each prior tool, has
30 minutes and 16 GiB of resident memory for its process group
(`tool_job.LIMITS`, which `rq1-accuracy/table.py` uses for PyCG; the same
numbers in `robustness.py`, `trace_recall.py` and `rts.py`). RQ1 had
600 s and 2 GiB until then: CPyGraph on pypinyin finishes in 725 s alone,
so its committed "ok" was a run just under 600 s, and other runs of the
2021 PyCG and of PyAnalyzer ended at that limit. The prior tools' stored
runs of rq1 are of the old limits: their cells say "needs rerun: it ran
with other limits" until they run again.

**The 2021 evaluation.** The evaluation of the ICSE 2021 paper, with its
benchmark, its ground truths and its metric, is the tag `evaluation-2021`
of this repository (Zenodo DOI 10.5281/zenodo.4457106). Its five projects
are the batch `icse-2021` of the macro-benchmark here, at the same
commits, with new ground truths under the current conventions.

### The optimisation loop

`loop.py` is a tool for work on the engine, not a command of a question:
it runs on the host, on a copy of the sources out of the inputs image
(see "The inputs image"). The scorer and everything that
uses it (`score.py`, `loop.py`) need Python 3.14 or later: they
reuse `macro-benchmark/check.py`, which loads the tracer of
`PyCG/micro-benchmark/validate.py`. `run.py` and `analyse.py` run on 3.12 and
later, both benchmarks included: macro discovery reads
`macro-benchmark/projects.py` only, not the tracer. So do
`corpus.py` and `robustness.py`, which score nothing. A tool's analyses can
use any interpreter (`--python`).

`loop.py run` runs a clean checkout on both benchmarks, scores it and prints
a summary (the micro-benchmark: cases, exact cases, edge precision and
recall; the macro-benchmark inside and outside the package: precision,
recall, F1 and TP/FP/FN per project and summed over the 30 projects; the
failed runs). The graphs and `result.json` go to `$TMPDIR/pycg-evaluation/NAME`,
so name each run by its commit:

```sh
python -I evaluation/common/loop.py run --flavour new --checkout CHECKOUT --name engine-abc1234
```

`loop.py compare` lists what changed between two such directories: per
project, the edges that became true positives (`+TP`), stopped being true
positives (`-TP`), became false positives (`+FP`) and stopped being false
positives (`-FP`), each with `[in]` when the callee is in the package; and
the micro-benchmark cases that became or stopped being exact (`--json` for
the data). A project or case whose run failed, or has no graph, on either
side is "not measured", with both statuses: its edges are not counted as
lost or new. It never reads the `result.json` of either directory: it
scores both graph directories again, now, with today's table of names of
one function, so the two sides are always scored alike. Its first line
names that table (the first ten characters of its SHA-256), and `--json`
records the scorer under `scorer`.

```sh
python -I evaluation/common/loop.py compare "$TMPDIR/pycg-evaluation/engine-abc1234" \
    "$TMPDIR/pycg-evaluation/engine-def5678"
```

`score.tool_results` refuses a name whose micro and macro runs record
different tools (another commit, or changes), and a run that `runs.json`
records as ok but whose graph is gone counts as failed (`missing graph`):
in the counts per benchmark, in the list of failed runs of the result
(`failed`) and in the summary of `loop.py run`.

### The results that the tables read

Everything in `results/` is written by a command of a question, or read
by one:

| Path | Written by | Read by |
|---|---|---|
| `rq1-accuracy.md`, `.json` | `rq1` | `tests/` (the labels of RQ2 against it) |
| `prior-tools.md`, `.json` | `rq1` (`prior_tools.py`, first) | `rq1` (the rows of the prior tools) |
| `rq1-trace-recall.md`, `.json` | `rq1-trace` | - |
| `rq2-causes.md`, `.json` | `rq2` | - |
| `rq3-pilot.md`, `.json` | `rq3-pilot` | - |
| `rq3-scale.md`, `.json`, `robustness-*.md`, `.json` | `rq3` (not run yet: the files are not there) | - |
| `rq4-pilot.md`, `.json` | `rq4-pilot` | - |
| `rq4-test-selection.md`, `.json` | `rq4` (not run yet: the files are not there) | - |
| `tools/<row>/rq1.json`, `rq1-<project>.json` | `TOOL rq1`, `headergen-deps rq1`: the record of each job and the graphs of the micro cases | `rq1` |
| `graphs/<row>/<project>.json` | `TOOL rq1`: the graph of each macro project | `rq1` |
| `tools/<row>/rq3-pilot.json`, `rq3.json` | `TOOL rq3-pilot`, `TOOL rq3` | `rq3-pilot`, `rq3` |
| `trace/` (not in git) | `trace` | - |

The findings are in PyCG's `docs/paper-notes.md`.
