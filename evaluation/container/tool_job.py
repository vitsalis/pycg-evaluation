#!/usr/bin/env python3
"""Run a prior tool on the jobs of a question, inside the tool's service.

    docker compose -f evaluation/compose.yaml run --rm TOOL QUESTION \\
        [--variant N] [--jobs K]

TOOL is a service of a prior tool in evaluation/compose.yaml (pyan, jarvis,
cpygraph, headergen, headergen-deps, pyanalyzer): the tool's image, built by
Compose from evaluation/tools/docker/ with the inputs of the questions
from the inputs image, without network, with the repository read-only and
evaluation/results/ the only writable host path. QUESTION is rq1 (the
micro-benchmark and the macro-benchmark), rq3 (the corpus of RQ3),
rq3-pilot (its ten packages, the sample of the PyCG pilot), rq1-trace
(DyPyBench's traced projects), rq4 or rq4-pilot (the parent commits of
RQ4's targets; the pilot: PyCG's pilot's). rq1-trace and rq4 write their
graphs to the volume tool-graphs (GRAPHS), which their questions read. The entry runs
in the tool's Python:

    python3 -I -B /repo/evaluation/container/tool_job.py TOOL QUESTION

For rq1 and rq1-trace, for each row of the tool (the variants of its
adapter, or --variant N); for rq3, rq3-pilot, rq4 and rq4-pilot, for its
headline row only (the first of its variants: HEADLINE_ONLY), it runs the
question's jobs, --jobs K at a time (default 1; run.run_tasks: the first
error stops them all, and the records are in the order of the jobs,
whatever K), each in a child process (`run`) in a directory of its own
(WORK, the job's directory: /tmp/run, or /tmp/runN for the Nth of K at a
time):
1. exports the job's files to WORK/job: each must be a regular file
   below the root, reached through no symbolic link (no ground truth, no
   link out of it);
2. writes the request of the driver (WORK/request.json) and copies the
   driver, our own script (the adapter's file), to WORK/driver; the
   adapter's command names these paths;
3. runs the adapter's command in a new process group, with the time limit
   of the question and a limit on the resident memory of the group
   (run._watch, as for PyCG; above it the job is "memory"), and kills the
   group at the end;
4. reads back the declared outputs only: each a regular file of at most
   MAX_OUTPUT_BYTES (MAX_TOTAL_BYTES in all), opened without following a
   link; the adapter converts the parsed JSON (never pickle, never eval).
It writes one result file per row and question, evaluation/results/tools/
<row>/<question>.json: the tool's facts, the limits, and per benchmark
(rq1) or part of the corpus (rq3) the record of each job. For rq1 the graph
of each job, in the benchmark's names, is stored too: those of the micro
cases in the result file, that of each macro project in
evaluation/results/graphs/<row>/<project>.json, so that a reader scores a
tool again without running it. common/prior_tools.py scores them for rq1;
rq3-scale/table.py reads the records.

HeaderGen imports the modules that it analyses: it runs on rq1 only (the
corpus is not scanned). A macro project with runtime dependencies runs in
the image of those dependencies, the service headergen-deps with NAME (the
entry gets `--project NAME`; it writes
results/tools/<HeaderGen's row>/rq1-NAME.json and the project's graph); the
headergen service states such a project "unavailable" and names that
command.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import queue
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import NamedTuple

HERE = Path(__file__).resolve().parent
EVALUATION = HERE.parent
REPOSITORY = EVALUATION.parent

# The directory of a job: its export (job/), the request of its driver
# (request.json), the driver (driver/) and its outputs (out/). The first of
# --jobs K jobs at a time works in WORK, the others in /tmp/run1, /tmp/run2...
# (work_of), so that a job's paths, depth included, are those of a job alone.
WORK = Path("/tmp/run")


def work_of(slot):
    """The directory of the job of slot `slot` (0 to K - 1)."""
    return WORK if slot == 0 else WORK.with_name(f"{WORK.name}{slot}")


RESULTS = Path("/results") / "tools"
# The graphs of the macro projects of rq1: results/graphs/<row>/<project>.json.
RQ1_GRAPHS = Path("/results") / "graphs"
# What the image holds: its pin and trees (tools/build.py image_text), and
# in the image of headergen-deps the pinned dependencies of its project.
# The graphs of rq1-trace, rq4 and rq4-pilot: a Docker volume, read by the
# services of those questions (rq1-accuracy/trace_recall.py,
# rq4-test-selection/table.py); intermediate data, not results.
GRAPHS = Path("/tool-graphs")
EXPORT = Path("/inputs/rts/export")
IMAGE_FILE = Path("/opt/tool/image.txt")
DEPS_FILE = Path("/opt/deps/requirements.txt")
CORPUS = Path("/inputs/corpus")
# The limits of a job: (seconds, MiB of the resident memory of its process
# group). One limit for every run of every question (the maintainer,
# 2026-11-15): 30 minutes and 16 GiB.
LIMITS = {"rq1": (1800, 16384), "rq3": (1800, 16384), "rq3-pilot": (1800, 16384)}
LIMITS["rq1-trace"] = LIMITS["rq3"]  # the targets of the DyPyBench part of rq3
LIMITS["rq4"] = LIMITS["rq4-pilot"] = (1800, 16384)  # rts.py's of PyCG
TIMEOUT, MEMORY_MIB = LIMITS["rq1"]
MAX_OUTPUT_BYTES = 64 << 20
MAX_TOTAL_BYTES = 256 << 20
NO_FOLLOW = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
ENVIRONMENT = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/tmp",
    "PYTHONHASHSEED": "0",
    "PYTHONDONTWRITEBYTECODE": "1",
}
UNAVAILABLE = "unavailable"  # a job whose environment is not there: no score
UNSET = "unset"  # NAME of compose.yaml when it is not given


class Refused(Exception):
    """A job or an output that is not used, with the reason."""


class Job(NamedTuple):
    """One analysis: a micro-benchmark case, a macro-benchmark project or a
    package of a corpus."""

    key: str  # "micro/<category>/<case>", "macro/<project>", "corpus/<name>"
    root: Path  # the package root: module names are relative to it
    entry_points: tuple  # micro: main.py; macro, corpus: every module
    files: tuple  # every module that the tool reads


class Spec(NamedTuple):
    """One run of a driver."""

    argv: tuple  # the command, its paths those of the job's directory
    request: dict  # written to request.json, read by the driver
    driver: Path  # our script, copied to driver/<its name>
    root: Path  # the job's root
    files: tuple  # the files to export below `root`
    outputs: tuple  # the names in out/ that are read back
    memory_mib: int = MEMORY_MIB
    timeout: float = TIMEOUT


def relative(path, root):
    """The path of `path` below `root`, as a PurePosixPath; Refused if it
    is not below it."""
    try:
        found = Path(os.path.abspath(path)).relative_to(os.path.abspath(root))
    except ValueError:
        raise Refused(f"{path} is not below the root {root}") from None
    if ".." in found.parts or not found.parts:
        raise Refused(f"{path} is not below the root {root}")
    return PurePosixPath(found.as_posix())


def inside(job, work=WORK):
    """The job as the container sees it: its paths below WORK/job."""
    root = PurePosixPath(work) / "job"

    def moved(paths):
        return tuple(root / relative(p, job.root) for p in paths)

    return Job(job.key, root, moved(job.entry_points), moved(job.files))


def export(root, files, destination):
    """Copy `files` (below `root`) to the new directory `destination`;
    return the SHA-256 of the export (its paths and bytes). Raises Refused
    for a file that is not regular, or that is reached through a link."""
    destination = Path(destination)
    destination.mkdir(mode=0o755)
    os.chmod(destination, 0o755)
    digest = hashlib.sha256()
    for path in sorted(set(files), key=lambda p: relative(p, root)):
        name = relative(path, root)
        directory = Path(os.path.abspath(root))
        for part in name.parts[:-1]:
            directory = directory / part
            if not stat.S_ISDIR(os.lstat(directory).st_mode):
                raise Refused(f"{name}: {part} is not a plain directory")
        try:
            fd = os.open(directory / name.name, NO_FOLLOW)
        except OSError as error:
            raise Refused(f"{name} is not a regular file ({error})") from None
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise Refused(f"{name} is not a regular file")
        with os.fdopen(fd, "rb") as source:
            data = source.read()
        target = destination.joinpath(*name.parts)
        for parent in reversed(target.relative_to(destination).parents[:-1]):
            (destination / parent).mkdir(mode=0o755, exist_ok=True)
            os.chmod(destination / parent, 0o755)
        target.write_bytes(data)
        os.chmod(target, 0o644)
        digest.update(str(name).encode() + b"\0" + hashlib.sha256(data).digest())
    return digest.hexdigest()


def with_stubs(files):
    """`files` and, beside each module, its stub (a .pyi of the same name),
    which PyCG reads: supporting inputs of the export."""
    found = set(files)
    for path in files:
        stub = Path(path).with_suffix(".pyi")
        if stub.suffix != Path(path).suffix and os.path.lexists(stub):
            found.add(stub)
    return sorted(found, key=str)


def prepare(spec, work):
    """Write the export, the request, the driver and the output directory
    into `work`; return the SHA-256 of the export."""
    work = Path(work)
    os.chmod(work, 0o755)
    exported = export(spec.root, with_stubs(spec.files), work / "job")
    (work / "request.json").write_text(json.dumps(spec.request, sort_keys=True))
    os.chmod(work / "request.json", 0o644)
    (work / "driver").mkdir(mode=0o755)
    os.chmod(work / "driver", 0o755)
    driver = work / "driver" / Path(spec.driver).name
    driver.write_bytes(Path(spec.driver).read_bytes())
    os.chmod(driver, 0o644)
    (work / "out").mkdir()
    os.chmod(work / "out", 0o777)  # the container's user writes here
    return exported


def read_outputs(out, names):
    """{name: bytes} of the files `names` of the directory `out` that are
    there. Raises Refused if `out` or a file is a link, if a file is not
    regular, or over the limits."""
    try:
        directory = os.open(out, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as error:
        raise Refused(f"{out} is not a plain directory ({error})") from None
    found, total = {}, 0
    try:
        for name in names:
            try:
                fd = os.open(name, NO_FOLLOW, dir_fd=directory)
            except FileNotFoundError:
                continue
            except OSError:
                raise Refused(f"{name} is not a regular file") from None
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                os.close(fd)
                raise Refused(f"{name} is not a regular file")
            with os.fdopen(fd, "rb") as file:
                data = file.read(MAX_OUTPUT_BYTES + 1)
            total += len(data)
            if len(data) > MAX_OUTPUT_BYTES or total > MAX_TOTAL_BYTES:
                raise Refused(f"{name} is over the limit of an output")
            found[name] = data
    finally:
        os.close(directory)
    return found


def _parse(data):
    """The JSON of an output, or the reason why it is not JSON."""
    try:
        return json.loads(data.decode("utf-8")), None
    except (ValueError, UnicodeDecodeError, RecursionError) as error:
        return None, f"{type(error).__name__}: {error}"


# Set by the first job whose processes could not be killed: no later job of
# this service run starts (they would disturb its measurements and escape
# their own memory sample); each is "not run" with this reason.
STOPPED = None
NOT_STARTED = (
    "not started after a cleanup failure: the processes left by that run "
    "would disturb the measurements of later runs"
)


def analyse(adapter, tool, job, limits, runner=None, work=WORK):
    """Run `adapter`'s tool on `job` in this container, in the directory
    `work`; return (record, graph or None). `limits` is (seconds, MiB).
    The adapter says which files to
    export (`files(job)`, default all of the job's), how to run the job as
    the container sees it (`command(tool, job, work)`: argv, request,
    output) and how to convert the
    parsed output (`convert(native, job, tool)`: graph and notes). A job that
    the adapter refuses (ValueError) is a crash without a run; a missing,
    unreadable or unconvertible output is a crash. After a cleanup failure
    (STOPPED) no job runs: it is "not run"."""
    global STOPPED
    if STOPPED is not None:
        return {"status": "not run", "error": NOT_STARTED}, None
    try:
        files = adapter.files(job) if hasattr(adapter, "files") else job.files
        given = adapter.command(tool, inside(job, work), PurePosixPath(work))
    except (ValueError, Refused) as error:
        problem = f"the adapter cannot run the job: {error}"
        return {"status": "crash", "error": problem[:300]}, None
    spec = Spec(
        tuple(given["argv"]),
        given["request"],
        Path(adapter.__file__),
        job.root,
        tuple(files),
        (given["output"],),
        memory_mib=limits[1],
        timeout=limits[0],
    )
    record, outputs = runner(spec) if runner else run(spec, work)
    if record["status"] == "cleanup failed":
        STOPPED = job.key
    if record["status"] != "ok":
        return record, None
    if given["output"] not in outputs:
        return record | {"status": "crash", "error": "no native output"}, None
    native, problem = _parse(outputs[given["output"]])
    if problem:
        problem = f"unreadable native output: {problem}"
        return record | {"status": "crash", "error": problem[:300]}, None
    try:
        graph, notes = adapter.convert(native, job, tool)
    except Exception as error:  # a format that the adapter does not expect
        problem = f"the conversion failed: {type(error).__name__}: {error}"
        return record | {"status": "crash", "error": problem[:300]}, None
    return record | {"notes": notes, "graph_sha256": graph_digest(graph)}, graph


def graph_digest(graph):
    """The SHA-256 of a graph, which its record keeps: a reader uses a graph
    only with the record that it belongs to."""
    text = json.dumps(graph, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def graph_problem(record, graph):
    """Why a stored job cannot be scored, or None: a finished run whose
    graph is missing, or is not the graph of its record."""
    if record.get("status") != "ok":
        return None
    if graph is None or graph_digest(graph) != record.get("graph_sha256"):
        return "needs rerun: a graph is missing or is not that of its run"
    return None


def stored_problem(tool, row, data, question):
    """Why a stored result is not one of `row` of `tool` as it is pinned and
    configured now, or None: its row, its settings (the variant), the
    tool's pin and its limits."""
    adapter = _build().load_adapter(tool)
    settings = dict(rows(tool))[row]
    settings = json.loads(json.dumps(settings))  # as stored: lists, not tuples
    wanted = facts(tool, adapter, LIMITS[question])
    found = data.get("tool", {})
    if data.get("row") != row or data.get("settings") != settings:
        return "needs rerun: it is the result of another row or variant"
    if found.get("pin") != wanted["pin"]:
        return "needs rerun: it ran another version of the tool"
    if (found.get("limits"), found.get("memory_mib")) != (
        wanted["limits"],
        wanted["memory_mib"],
    ):
        ran, now = found.get("limits") or {}, wanted["limits"]
        return (
            f"needs rerun: it ran with other limits ({ran.get('seconds')} s, "
            f"{found.get('memory_mib')} MiB; now {now['seconds']} s, "
            f"{now['memory_mib']} MiB)"
        )
    return None


def input_changed(adapter, job, record):
    """Whether the input of a job that ran is not what it is now: the
    SHA-256 of its export (tool_job.prepare) against the record's. A record
    without one (a job that did not run) has not changed."""
    recorded = record.get("input_sha256")
    if recorded is None:
        return False
    files = adapter.files(job) if hasattr(adapter, "files") else job.files
    with tempfile.TemporaryDirectory(prefix="tool-job-inputs-") as work:
        try:
            found = export(job.root, with_stubs(files), Path(work) / "job")
        except (Refused, OSError, ValueError):
            return True
    return found != recorded


def is_graph(data):
    """Whether `data` is {caller: [callee, ...]} with strings only."""
    return isinstance(data, dict) and all(
        isinstance(callees, list) and all(isinstance(c, str) for c in callees)
        for callees in data.values()
    )


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(spec, work=WORK):
    """Run one Spec in a child process of this container; return (record,
    {output: bytes}). The record has `status` (ok, crash, timeout, memory,
    cleanup failed), `wall_seconds`, `peak_rss_mib` (the largest sample of
    the group), `input_sha256` (the export) and, for a crash, `error`."""
    work = Path(work)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    try:
        exported = prepare(spec, work)
    except (Refused, OSError) as error:
        problem = f"the job cannot be exported: {error}"
        return {"status": "crash", "error": problem[:300]}, {}
    record = _child(spec, work) | {"input_sha256": exported}
    if record["status"] != "ok":
        return record, {}
    try:
        return record, read_outputs(work / "out", spec.outputs)
    except Refused as error:
        problem = f"the output was refused: {error}"
        return record | {"status": "crash", "error": problem[:300]}, {}


def _child(spec, work):
    runner = _runner()
    start = time.perf_counter()
    with tempfile.TemporaryFile() as stderr:
        child = subprocess.Popen(
            list(spec.argv),
            cwd=work,
            env=ENVIRONMENT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            with runner.active(child):  # an error of another job kills it
                killed, largest = runner._watch(child, spec.timeout, spec.memory_mib)
        except runner.MemoryUnmeasured as error:
            killed, largest = "unmeasured", None
            unmeasured = error
        finally:
            ended = time.perf_counter()
            cleanup = runner._kill_group(child)
        stderr.seek(max(0, stderr.seek(0, os.SEEK_END) - (1 << 16)))
        lines = stderr.read().decode("utf-8", "replace").strip().splitlines()
    wall = round(ended - start, 3)
    record = {"wall_seconds": wall}
    if largest is not None:
        record["peak_rss_mib"] = largest
    if cleanup:
        return record | {"status": "cleanup failed", "error": cleanup[0]}
    if killed == "timeout":
        return record | {"status": "timeout", "wall_seconds": spec.timeout}
    if killed == "memory":
        return record | {"status": "memory"}
    if killed == "unmeasured":
        return record | {"status": "unmeasured", "error": unmeasured.cause}
    if child.returncode != 0:
        tail = lines[-1] if lines else ""
        error = f"exit status {child.returncode}: {tail}"[:300]
        return record | {"status": "crash", "error": error}
    return record | {"status": "ok"}


_RUNNER = None


def _runner():
    """common/run.py: its watchdog of a process group (loaded once)."""
    global _RUNNER
    if _RUNNER is None:
        _RUNNER = _load("evaluation_run", EVALUATION / "common" / "run.py")
    return _RUNNER


_BUILD = None


def _build():
    global _BUILD
    if _BUILD is None:
        _BUILD = _load("tools_build", EVALUATION / "tools" / "build.py")
    return _BUILD


# --------------------------------------------------------------------------
# The jobs of a question
# --------------------------------------------------------------------------


def cohort(pins=None):
    """The newest Python of the micro cases that every prior tool runs: the
    oldest Python of their base images (pins.json)."""
    pins = pins or _build().read_pins()
    versions = []
    for image in pins["base_images"].values():
        found = re.match(r"python:(\d+)\.(\d+)", image)
        versions.append((int(found[1]), int(found[2])))
    return min(versions)


def micro_jobs(limit, snippets=None):
    """The micro-benchmark jobs whose requires-python is at most `limit`,
    and the keys of the others."""
    runner = _runner()
    jobs, skipped = runner.micro_jobs(snippets or runner.SNIPPETS, limit)
    found = []
    for job in jobs:
        files = tuple(sorted(job.package.rglob("*.py")))
        found.append(Job(job.key, job.package, job.entry_points, files))
    return found, skipped


def macro_jobs(benchmark_dir):
    """The jobs of the macro-benchmark: every module of each package is an
    entry point."""
    jobs, skipped = _runner().macro_jobs(Path(benchmark_dir) / "projects.toml")
    return [Job(j.key, j.package, j.entry_points, j.entry_points) for j in jobs], (
        skipped
    )


def graph_path(graphs, row, project):
    """The stored graph of a macro project of a row of rq1."""
    return Path(graphs) / slug(row) / f"{project}.json"


# --------------------------------------------------------------------------
# The rows of a tool and their results
# --------------------------------------------------------------------------


def rows(tool, variant=None):
    """[(row name, the record that the adapter's command gets)] of a tool:
    one per variant of its adapter (VARIANTS), or the one of --variant."""
    adapter = _build().load_adapter(tool)
    variants = getattr(adapter, "VARIANTS", ((adapter.PIN["name"], {}),))
    base = dict(getattr(adapter, "TOOL", {}))
    found = [(name, base | settings) for name, settings in variants]
    return found if variant is None else [found[variant]]


# The questions that run the headline row of a tool only (the first of its
# VARIANTS): RQ3 measures the time and the memory of the tool, and RQ4 a
# client of its call graph; another variant would run the same tool again.
# RQ1 and rq1-trace measure accuracy, which the variants change: every row.
HEADLINE_ONLY = ("rq3", "rq3-pilot", "rq4", "rq4-pilot")


def headline(tool):
    """[(row name, record)] of the headline row of a tool."""
    return rows(tool)[:1]


def slug(row):
    """The directory of a row under results/tools/."""
    return re.sub(r"[^a-z0-9]+", "-", row.lower()).strip("-")


def result_path(results, row, question):
    return Path(results) / slug(row) / f"{question}.json"


def facts(tool, adapter, limits):
    pin = adapter.PIN
    found = {
        "tool": tool,
        "version": f"{pin['version']} ({pin['commit'][:10]})",
        "pin": pin,
        "python": platform.python_version(),
        "note": getattr(adapter, "NOTE", None),
        "image": f"pycg-tool-{tool}",
        "limits": {"seconds": limits[0], "memory_mib": limits[1]},
        "memory_mib": limits[1],
    }
    if getattr(adapter, "NO_OUTSIDE", None):
        found["outside"] = adapter.NO_OUTSIDE
    return found


def parallel(items, function, jobs):
    """[function(item, work) for each of `items`], `jobs` at a time
    (run.run_tasks: in the order of `items`, and the first error stops
    them all); each runs in a directory of its own (work_of), with its own
    limits."""
    slots = queue.SimpleQueue()
    for slot in range(jobs):
        slots.put(work_of(slot))

    def task(item):
        work = slots.get()
        try:
            return function(item, work)
        finally:
            slots.put(work)

    return _runner().run_tasks(list(items), task, jobs)


def run_jobs(
    adapter, record, jobs, skipped, limits, environment=None, progress=None, k=1
):
    """({key: record}, {key: graph}) of the jobs, `k` at a time.
    `environment(job)` gives (why the job cannot run here, or None; the
    statement of its dependencies, or None)."""

    def one(job, work):
        problem, statement = environment(job) if environment else (None, None)
        if problem:
            return {"status": UNAVAILABLE, "error": problem}, None
        found, graph = analyse(adapter, record, job, limits, work=work)
        if statement:
            found["dependencies"] = statement
        if progress:
            progress(f"{job.key}: {found['status']}")  # no error text
        return found, graph

    records = {key: {"status": "skipped"} for key in skipped}
    graphs = {}
    for job, (found, graph) in zip(jobs, parallel(jobs, one, k)):
        records[job.key] = found
        if graph is not None:
            graphs[job.key] = graph
    return records, graphs


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=0, sort_keys=True) + "\n", encoding="utf-8")


def headergen_environment(only, benchmark_dir):
    """environment() of HeaderGen's jobs (see run_jobs): a project with
    runtime dependencies runs in their image only (headergen-deps), and one
    whose dependencies cannot be installed nowhere."""
    build = _build()

    def environment(job):
        if not job.key.startswith("macro/"):
            return None, "none: not a macro project"
        project = job.key.removeprefix("macro/")
        runnable, statement = build.dependencies("headergen", benchmark_dir, project)
        if not runnable:
            return statement, statement
        installed = statement.startswith("installed")
        if installed and only is None:
            command = (
                f"NAME={project} docker compose -f "
                "evaluation/compose.yaml run --rm headergen-deps rq1"
            )
            return f"runs in the image of its dependencies: {command}", statement
        return None, statement

    return environment


def rq1(
    tool,
    variant=None,
    only=None,
    results=RESULTS,
    root=REPOSITORY,
    graphs=RQ1_GRAPHS,
    jobs=1,
):
    """Run the rows of `tool` on the micro-benchmark and the
    macro-benchmark; `only`: that one macro project (headergen-deps).
    Return the paths written. The macro-benchmark is not run if a project
    has no source (its edges would be false negatives)."""
    adapter = _build().load_adapter(tool)
    limits = LIMITS["rq1"]
    benchmark_dir = Path(root) / "macro-benchmark"
    macro, absent = [], {}
    if not (benchmark_dir / "projects.toml").is_file():
        absent["macro"] = "not measured: no macro-benchmark/projects.toml"
    else:
        macro, skipped = macro_jobs(benchmark_dir)
        if skipped:
            absent["macro"] = f"not run: {len(skipped)} projects have no source"
    limit = cohort()
    micro, excluded = micro_jobs(limit) if only is None else ([], [])
    environment = None
    if getattr(adapter, "PROJECT_DEPENDENCIES", False):
        environment = headergen_environment(only, benchmark_dir)
    written = []
    for row, record in rows(tool, variant):
        data = {
            "question": "rq1",
            "row": row,
            "settings": record,
            "tool": facts(tool, adapter, limits),
            "cohort": ".".join(map(str, limit)),
            "absent": absent,
            "sets": {},
        }
        if only is not None:
            data["only"] = only
        else:
            runs, found = run_jobs(
                adapter, record, micro, excluded, limits, environment, print, jobs
            )
            data["sets"]["micro"] = {"runs": runs, "graphs": found}
        if "macro" not in absent:
            chosen = macro
            if only is not None:
                chosen = [j for j in chosen if j.key == f"macro/{only}"]
            runs, found = run_jobs(
                adapter, record, chosen, [], limits, environment, print, jobs
            )
            data["sets"]["macro"] = {"runs": runs}
            for key, graph in found.items():
                path = graph_path(graphs, row, key.removeprefix("macro/"))
                write(path, graph)
                written.append(path)
        question = "rq1" if only is None else f"rq1-{only}"
        path = result_path(results, row, question)
        write(path, data)
        written.append(path)
    return written


def rq3(tool, pilot=False, results=RESULTS, work=CORPUS, jobs=1):
    """Run the headline row of `tool` on the corpus of RQ3 (its pilot's
    sample with `pilot`): per package the status, the time and the peak
    memory."""
    adapter = _build().load_adapter(tool)
    question = "rq3-pilot" if pilot else "rq3"
    limits = LIMITS[question]
    table = _load("rq3_table", EVALUATION / "rq3-scale" / "table.py")
    planned = {}  # {label: (SHA-256 of the manifest, entries)}
    if pilot:
        for label, (_, text, _, _, _, entries) in table.pilot_plan(work).items():
            planned[label] = (hashlib.sha256(text).hexdigest(), entries)
    else:
        for label, path, directory in table.PARTS:
            text = Path(path).read_bytes()
            entries = table.robustness.plan(json.loads(text), work / directory)
            planned[label] = (hashlib.sha256(text).hexdigest(), entries)
    written = []
    for row, record in headline(tool):

        def one(item, slot):
            _, name, job = item
            files = tuple(job.entry_points)
            found, graph = analyse(
                adapter,
                record,
                Job(job.key, job.package, files, files),
                limits,
                work=slot,
            )
            result = {"status": found["status"]}
            if "error" in found:
                result["error"] = found["error"]
            result["measured"] = {
                k: found[k] for k in ("wall_seconds", "peak_rss_mib") if k in found
            }
            if graph is not None:
                result["counts"] = {
                    "edges": sum(len(callees) for callees in graph.values())
                }
            print(f"{row} {job.key}: {found['status']}", flush=True)
            return result

        tasks = [
            (label, name, job)
            for label, (_, entries) in planned.items()
            for name, (_, job) in entries.items()
            if job is not None
        ]
        keys = [(label, name) for label, name, _ in tasks]
        outcomes = dict(zip(keys, parallel(tasks, one, jobs)))
        parts = {}
        for label, (digest, entries) in planned.items():
            packages = {}
            for name, (entry, job) in entries.items():
                packages[name] = dict(entry)
                if job is not None:
                    packages[name]["runs"] = {row: outcomes[label, name]}
            parts[label] = {"sha256": digest, "packages": packages}
        data = {
            "question": question,
            "row": row,
            "settings": record,
            "tool": facts(tool, adapter, limits),
            "jobs": jobs,  # the times were measured beside jobs - 1 others
            "halves": parts,  # the key of the stored runs
        }
        path = result_path(results, row, question)
        write(path, data)
        written.append(path)
    return written


def analysed(adapter, record, job, limits, label, work=WORK):
    """(the record of a job, kept for a reader: status, error, time, peak
    memory, input hash; its graph or None)."""
    found, graph = analyse(adapter, record, job, limits, work=work)
    kept = ("status", "error", "wall_seconds", "peak_rss_mib", "input_sha256")
    kept += ("graph_sha256",)
    print(f"{label}: {found['status']}", flush=True)
    return {k: found[k] for k in kept if k in found}, graph


def rq1_trace(tool, variant=None, graphs=GRAPHS, work=CORPUS, jobs=1):
    """Run the rows of `tool` on DyPyBench's traced projects: the targets of
    the DyPyBench part of rq3 (robustness.plan) whose row names a trace.
    Writes GRAPHS/<row>/rq1-trace.json; trace_recall.py maps the traces and
    scores the graphs."""
    adapter = _build().load_adapter(tool)
    limits = LIMITS["rq1-trace"]
    table = _load("rq3_table", EVALUATION / "rq3-scale" / "table.py")
    [(path, directory)] = [(p, d) for _, p, d in table.PARTS if d == "dypybench"]
    text = Path(path).read_bytes()
    manifest = json.loads(text)
    traced = {row["name"] for row in manifest["rows"] if "trace" in row}
    entries = table.robustness.plan(manifest, work / directory)
    written = []
    tasks = [
        (name, job)
        for name, (_, job) in entries.items()
        if job is not None and name in traced
    ]
    for row, record in rows(tool, variant):

        def one(item, slot):
            name, job = item
            files = tuple(job.entry_points)
            return analysed(
                adapter,
                record,
                Job(job.key, job.package, files, files),
                limits,
                f"{row} {name}",
                slot,
            )

        runs, found = {}, {}
        for (name, _), (kept, graph) in zip(tasks, parallel(tasks, one, jobs)):
            runs[name] = kept
            if graph is not None:
                found[name] = graph
        data = {
            "question": "rq1-trace",
            "row": row,
            "settings": record,
            "tool": facts(tool, adapter, limits),
            "sha256": hashlib.sha256(text).hexdigest(),
            "runs": runs,
            "graphs": found,
        }
        target = result_path(graphs, row, "rq1-trace")
        write(target, data)
        written.append(target)
    return written


def rq4(tool, pilot=False, graphs=GRAPHS, export=EXPORT, manifest=None, jobs=1):
    """Run the headline row of `tool` on the parent commit of each target of RQ4's
    dataset (the pilot: the first target of each project, as PyCG's pilot),
    from the export of the clones. Writes
    GRAPHS/<row>/<rq4|rq4-pilot>/<project>.json: per parent commit the
    record, the key of its input (rts.analysis_key) and the graph; the rq4
    services select and score with it (rts.py select with a stored graph)."""
    adapter = _build().load_adapter(tool)
    question = "rq4-pilot" if pilot else "rq4"
    limits = LIMITS[question]
    rts = _load("evaluation_rts", EVALUATION / "rq4-test-selection" / "rts.py")
    if manifest is None:
        manifest = json.loads(Path(rts.MANIFEST).read_text(encoding="utf-8"))
    written = []
    for row, record in headline(tool):
        # Each parent commit once, in the order of the targets: read first,
        # then analysed `jobs` at a time.
        analyses, tasks = {}, []
        for name, entry in manifest["projects"].items():
            project = rts.project_config(manifest, name)
            repo = rts.Export(export, name, entry["trees"])
            analyses[name] = {}
            for target in entry["targets"][: 1 if pilot else None]:
                parent = rts.commit_id(target["parent"])
                if parent in analyses[name]:
                    continue
                try:
                    sources = rts.parent_sources(repo, parent, project)
                except rts.NotExported as error:
                    analyses[name][parent] = {"record": {"status": "not exported"}}
                    print(f"{row} {name} {parent[:10]}: {error}", flush=True)
                    continue
                analyses[name][parent] = None  # its place in the order
                tasks.append((name, parent, project, sources))

        def one(item, slot):
            name, parent, project, sources = item
            key = rts.analysis_key(project, sources)
            with tempfile.TemporaryDirectory(prefix="rts-tree-") as scratch:
                rts.write_tree(sources, scratch)
                tree = Path(scratch).resolve()
                package = tree.joinpath(*PurePosixPath(project.root).parts)
                files = tuple(tree / p for p in sorted(sources))
                entry_points = tuple(
                    tree / p for p in sorted(sources) if rts.is_entry_point(p, project)
                )
                found, graph = analysed(
                    adapter,
                    record,
                    Job(f"rts/{name}/{parent}", package, entry_points, files),
                    limits,
                    f"{row} {name} {parent[:10]}",
                    slot,
                )
            return {"record": found, "key": key, "graph": graph}

        for (name, parent, _, _), analysis in zip(tasks, parallel(tasks, one, jobs)):
            analyses[name][parent] = analysis
        for name in manifest["projects"]:
            data = {
                "question": question,
                "row": row,
                "settings": record,
                "tool": facts(tool, adapter, limits),
                "dataset": rts.dataset_fingerprint(manifest, name),
                "jobs": jobs,  # the times were measured beside jobs - 1 others
                "analyses": analyses[name],
            }
            target_path = Path(graphs) / slug(row) / question / f"{name}.json"
            write(target_path, data)
            written.append(target_path)
    return written


def main(argv=None, image_file=IMAGE_FILE, deps_file=DEPS_FILE):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("tool", choices=_build().TOOLS)
    parser.add_argument("question", choices=sorted(LIMITS))
    parser.add_argument("--variant", type=int, help="one row of the tool only")
    parser.add_argument(
        "--jobs", type=int, default=1, help="K jobs at a time, each with its limits"
    )
    parser.add_argument(
        "--project", metavar="NAME", help="headergen-deps: its one macro project"
    )
    args = parser.parse_args(argv)
    build = _build()
    adapter = build.load_adapter(args.tool)
    if args.variant is not None and not 0 <= args.variant < len(rows(args.tool)):
        parser.error(f"{args.tool} has rows 0 to {len(rows(args.tool)) - 1}")
    if args.jobs < 1:
        parser.error("--jobs is at least 1")
    if args.variant is not None and args.question in HEADLINE_ONLY:
        parser.error(f"{args.question} runs the headline row of a tool only")
    if args.question != "rq1" and getattr(adapter, "PROJECT_DEPENDENCIES", False):
        parser.error(
            f"{args.tool} imports the analysed code: rq1 only (the other "
            "inputs are not scanned)"
        )
    only = None
    if args.project is not None:  # the service headergen-deps
        only = args.project
        if only == UNSET or not re.fullmatch(r"[A-Za-z0-9_.-]+", only):
            parser.error("set NAME to a project of the macro-benchmark")
        pinned = build.deps_file(REPOSITORY / "macro-benchmark", only)
        found = deps_file.read_bytes() if deps_file.is_file() else None
        if not pinned.is_file() or found != pinned.read_bytes():
            parser.error(
                f"the image does not hold {pinned.name}: build it again "
                "(NAME=... docker compose -f evaluation/compose.yaml build "
                "headergen-deps)"
            )
    wanted = build.image_text(args.tool)
    held = image_file.read_text(encoding="utf-8") if image_file.is_file() else None
    if held != wanted:
        parser.error(
            f"the image is not of the pin of {args.tool}: build it again "
            f"(docker compose -f evaluation/compose.yaml build {args.tool})"
        )
    k = args.jobs
    if args.question == "rq1":
        written = rq1(args.tool, args.variant, only, jobs=k)
    elif args.question == "rq1-trace":
        written = rq1_trace(args.tool, args.variant, jobs=k)
    elif args.question in ("rq4", "rq4-pilot"):
        written = rq4(args.tool, pilot=args.question == "rq4-pilot", jobs=k)
    else:
        written = rq3(args.tool, pilot=args.question == "rq3-pilot", jobs=k)
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
