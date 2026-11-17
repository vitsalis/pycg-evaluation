#!/usr/bin/env python3
"""Run the script of a research question inside its Compose service.

    docker compose -f evaluation/compose.yaml run --rm QUESTION [--jobs K]

The service (evaluation/compose.yaml) runs, without network, with a
read-only root, no capability, a non-root user and a memory limit:

    python3 -I -B /repo/evaluation/container/question_entry.py QUESTION [--jobs K]

/repo is this repository, read-only; /inputs the inputs that the image
took from the inputs image at its build; /results (evaluation/results/)
the only writable host mount; /tmp and /pycg-evaluation are tmpfs. This entry:

1. for a question that runs PyCG, checks that the PyCG repository under test
   (PYCG, a path in /repo; default /repo) is under git and that its `pycg`
   package has no uncommitted change, and exports the `pycg` package of its
   HEAD (common/engine.py) to
   /tmp/engine/pycg; the 2021 engine is the package at ORIGINAL (default
   ORIGINAL_REVISION, the 2021 analysis: commit 046479e, by its full hash) of
   the same repository, which must be in the history of HEAD, exported to
   /tmp/engine/original; ORIGINAL empty: no original PyCG;
2. writes the versions of the exports and of /repo to
   /pycg-evaluation/versions.json, which run.git_version reads in place of
   git (an export is no checkout);
3. runs the question's script (SCRIPTS) with the exports, its inputs and
   `--results /tmp/results`; for rq1, first common/prior_tools.py, which
   scores the stored runs of the prior tools (FIRST);
4. copies the files that the question writes (OUTPUTS) to /results, only if
   the script ended with status 0 and only regular files: a link at any
   level, a missing file, a directory or a file over MAX_FILE_BYTES
   (MAX_TOTAL_BYTES in all) copies nothing; a file of /results is replaced,
   never followed if it is a link.
"""

import argparse
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVALUATION = HERE.parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


engine = _load("evaluation_engine", EVALUATION / "common" / "engine.py")
Refused = engine.Refused

# The script of each question under evaluation/, and its first arguments.
SCRIPTS = {
    "rq1": ("rq1-accuracy/table.py",),
    "rq1-trace": ("rq1-accuracy/trace_recall.py", "run"),
    "rq2": ("rq2-causes/table.py",),
    "rq3": ("rq3-scale/table.py",),
    "rq3-pilot": ("rq3-scale/table.py", "--pilot"),
    "rq4": ("rq4-test-selection/table.py",),
    "rq4-pilot": ("rq4-test-selection/table.py", "--pilot"),
}
# The files that each question writes to /results, and nothing else.
OUTPUTS = {
    "rq1": (
        "prior-tools.json",
        "prior-tools.md",
        "rq1-accuracy.json",
        "rq1-accuracy.md",
    ),
    "rq1-trace": ("rq1-trace-recall.json", "rq1-trace-recall.md"),
    "rq2": ("rq2-causes.json", "rq2-causes.md"),
    "rq3": (
        "rq3-scale.json",
        "rq3-scale.md",
        "robustness-pypi-top-100.json",
        "robustness-pypi-top-100.md",
        "robustness-dypybench.json",
        "robustness-dypybench.md",
    ),
    "rq3-pilot": ("rq3-pilot.json", "rq3-pilot.md"),
    "rq4": ("rq4-test-selection.json", "rq4-test-selection.md"),
    "rq4-pilot": ("rq4-pilot.json", "rq4-pilot.md"),
}
# The script that runs first, with `--out /tmp/results`: the scores of the
# prior tools' stored runs (results/tools/ and results/graphs/), which the
# table of RQ1 reads.
FIRST = {"rq1": "common/prior_tools.py"}
NEEDS_PYCG = ("rq1", "rq1-trace", "rq3", "rq3-pilot", "rq4", "rq4-pilot")
# The input of a question (in the image, from the inputs image) and the
# option that gives it to the script.
INPUTS = {
    "rq1-trace": ("--work", "/inputs/corpus"),
    "rq3": ("--work", "/inputs/corpus"),
    "rq3-pilot": ("--work", "/inputs/corpus"),
    "rq4": ("--export", "/inputs/rts/export"),
    "rq4-pilot": ("--export", "/inputs/rts/export"),
}
# The questions whose script takes --jobs K: K analyses at a time, each with
# its own limits (the service's memory must hold K of them: compose.yaml,
# EVAL_MEMORY).
JOBS = ("rq1-trace", "rq3", "rq3-pilot", "rq4", "rq4-pilot")
# The memory of one analysis (GiB): under the service's mem_limit, so that
# the watchdog of robustness.py records a run as over its limit before the
# kernel kills the container.
ANALYSIS_MEMORY_GIB = 16
# The original PyCG: the 2021 analysis, the commit 046479e of PyCG's history,
# pinned by its hash as every other input (no tag).
ORIGINAL_REVISION = "046479ebcbc27a3ff7a7fa45cc08336726fb0bc9"
REPO = Path("/repo")
ENGINES = Path("/tmp/engine")
VERSIONS_FILE = Path("/pycg-evaluation/versions.json")  # run.VERSIONS_FILE
SCRATCH_RESULTS = Path("/tmp/results")
RESULTS = Path("/results")
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
# The outputs are tables: a larger file is refused, read with this bound.
MAX_FILE_BYTES = 64 << 20
MAX_TOTAL_BYTES = 256 << 20


def arguments(question, engines=ENGINES, original=True, jobs=1):
    """The arguments of the question's script after its first ones."""
    args = []
    if question in NEEDS_PYCG:
        args += ["--checkout", str(engines / "pycg")]
        if original:
            args += ["--original", str(engines / "original")]
    if question in INPUTS:
        args += list(INPUTS[question])
    if question in ("rq1-trace", "rq3", "rq3-pilot"):
        args += ["--memory-limit", str(ANALYSIS_MEMORY_GIB)]
    if question == "rq1-trace":
        args += ["--graphs", "/tmp/graphs"]
    elif question in ("rq1", "rq4", "rq4-pilot"):
        args += ["--work", "/tmp/work"]
    if question in FIRST:
        args += ["--prior-tools", str(SCRATCH_RESULTS / "prior-tools.json")]
    if question in JOBS:
        args += ["--jobs", str(jobs)]
    return args


def engines(pycg, revision, destination=ENGINES):
    """Export the `pycg` package of HEAD of the PyCG repository `pycg`, and
    of `revision` (none if empty) of the same repository; return
    {export path: version}. A repository whose `pycg` package has changes is
    refused: the exports are of commits; so is a revision that is not in the
    history of HEAD."""
    found = {}
    head = engine.package_version(pycg)
    destination = Path(destination)
    destination.mkdir(mode=0o755, parents=True, exist_ok=True)
    found[str(engine.export(pycg, head["commit"], destination / "pycg"))] = head
    if revision:
        commit = engine.revision(pycg, revision)
        if not engine.in_history(pycg, commit, head["commit"]):
            raise Refused(f"{revision} is not in the history of HEAD {head['commit']}")
        exported = engine.export(pycg, commit, destination / "original")
        label = "the 2021 analysis" if commit == ORIGINAL_REVISION else revision
        found[str(exported)] = {"commit": commit, "describe": label, "dirty": False}
    return found


def read_outputs(results, names):
    """{name: bytes} of the files `names` in the directory `results`. Raises
    Refused if the directory or a file is a symbolic link, if a file is not
    a regular file, or if one is missing."""
    fds = []
    try:
        try:
            fds.append(os.open(results, DIRECTORY))
        except OSError as error:
            raise Refused(f"{results} is not a plain directory ({error})")
        found, total = {}, 0
        for name in names:
            try:
                fd = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fds[0]
                )
            except FileNotFoundError:
                raise Refused(f"the script wrote no {name}")
            except OSError as error:
                raise Refused(f"{name} is not a regular file ({error})")
            fds.append(fd)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise Refused(f"{name} is not a regular file")
            data = os.read(fd, MAX_FILE_BYTES + 1)
            while len(data) <= MAX_FILE_BYTES and (chunk := os.read(fd, 1 << 20)):
                data += chunk
            total += len(data)
            if len(data) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise Refused(f"{name} is over the limit of the outputs' size")
            found[name] = data
        return found
    finally:
        for fd in fds:
            os.close(fd)


def write_outputs(results, found):
    """Write each file into `results`, replacing a file there without
    following it if it is a link; return the paths."""
    results = Path(results)
    try:
        fd = os.open(results, DIRECTORY)
    except OSError as error:
        raise Refused(f"{results} is not a plain directory ({error})")
    written = []
    try:
        for name, data in found.items():
            temporary = f".{name}.{os.getpid()}.tmp"
            out = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o644,
                dir_fd=fd,
            )
            with os.fdopen(out, "wb") as file:
                file.write(data)
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
            written.append(results / name)
    finally:
        os.close(fd)
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("question", choices=sorted(SCRIPTS))
    parser.add_argument("--jobs", type=int, default=1, help=f"for {', '.join(JOBS)}")
    args = parser.parse_args(argv)
    question = args.question
    if args.jobs < 1 or (args.jobs != 1 and question not in JOBS):
        parser.error(f"--jobs K (at least 1) is for {', '.join(JOBS)} only")
    revision = os.environ.get("ORIGINAL", ORIGINAL_REVISION)
    versions = {str(REPO): engine.git_version(REPO)}
    if question in NEEDS_PYCG:
        try:
            versions |= engines(Path(os.environ.get("PYCG") or REPO), revision)
        except (Refused, subprocess.CalledProcessError) as error:
            parser.error(f"the PyCG repository under test: {error}")
    VERSIONS_FILE.write_text(json.dumps(versions, sort_keys=True), encoding="utf-8")
    SCRATCH_RESULTS.mkdir(parents=True, exist_ok=True)
    script, *first = SCRIPTS[question]
    command = [sys.executable, "-I", "-B", str(EVALUATION / script), *first]
    command += arguments(question, original=bool(revision), jobs=args.jobs)
    command += ["--results", str(SCRATCH_RESULTS)]
    commands = [command]
    if question in FIRST:
        script = str(EVALUATION / FIRST[question])
        python = [sys.executable, "-I", "-B", script]
        commands.insert(0, [*python, "--out", str(SCRATCH_RESULTS)])
    for command in commands:
        done = subprocess.run(command)
        if done.returncode != 0:
            break
    if done.returncode != 0:
        print(
            f"question_entry.py: {question}: the script failed "
            f"(exit status {done.returncode}); nothing is copied",
            file=sys.stderr,
        )
        return 1
    try:
        found = read_outputs(SCRATCH_RESULTS, OUTPUTS[question])
        for path in write_outputs(RESULTS, found):
            print(path)
    except Refused as error:
        print(
            f"question_entry.py: {question}: {error}; nothing is copied",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
