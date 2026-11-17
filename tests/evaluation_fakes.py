"""Fake PyCG checkouts and analysed projects for the evaluation tests.

Not a test module: tests/test_evaluation*.py load it by path.
"""

import gzip
import hashlib
import importlib.util
import io
import os
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

EVALUATION = Path(__file__).resolve().parents[1] / "evaluation"


def load(name):
    """Load evaluation/<name>.py by path (`name`: "common/run", say):
    evaluation/ is not a package."""
    spec = importlib.util.spec_from_file_location(
        f"evaluation_{Path(name).name}", EVALUATION / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run = load("common/run")

FAKE_TOOL = """
import json, os, sys, time

class CallGraphGenerator:
    def __init__(self, entry_points, package, *rest):
        self.entry_points, self.package, self.rest = entry_points, package, rest

    def analyze(self):
        behaviour = open(os.path.join(self.package, "BEHAVIOUR")).read().strip()
        if behaviour == "crash":
            json.loads("{")  # raises in the standard library
        if behaviour == "named":  # a crash that names the package and tool
            marker = os.path.join(os.path.dirname(__file__), "MARKER")
            tool = open(marker).read() if os.path.exists(marker) else "-"
            raise RuntimeError(f"{os.path.basename(self.package)} {tool}")
        if behaviour == "hang":
            self.mark("child.pid")
            time.sleep(60)
        if behaviour in ("fork-eat", "fork-leave", "fork-hang"):
            # A worker process, as a tool that analyses each file in a new
            # process would start.
            if os.fork() == 0:
                self.mark("worker.pid")  # before it can be killed
                if behaviour == "fork-eat":
                    memory = b"x" * (400 << 20)  # noqa: F841
                time.sleep(60)
                os._exit(0)
            while not os.path.exists(os.path.join(self.package, "worker.pid")):
                time.sleep(0.05)
            if behaviour == "fork-eat":
                time.sleep(60)
            if behaviour == "fork-hang":
                self.mark("child.pid")
                time.sleep(60)
        if behaviour == "sequential":
            # Two workers, one after the other, each left unreaped (a
            # zombie) when it ends: their memory never overlaps.
            for name in ("first", "second"):
                readable, writable = os.pipe()
                if os.fork() == 0:
                    os.close(readable)
                    memory = b"x" * (150 << 20)  # noqa: F841
                    time.sleep(0.5)  # for a sample to see it
                    os._exit(0)
                os.close(writable)
                os.read(readable, 1)  # the end of the pipe: the worker ended
                os.close(readable)
            time.sleep(1.0)  # for samples to see both zombies
        if behaviour == "stop":
            import signal

            os.kill(os.getpid(), signal.SIGSTOP)
        if behaviour == "die":
            os._exit(3)
        if behaviour == "spike":
            memory = b"x" * (300 << 20)
            time.sleep(1)  # long enough for a sample
            del memory  # freed: the resident set size falls again
            time.sleep(60)
        if behaviour == "eat":
            self.memory = b"x" * (400 << 20)  # written, so resident
            time.sleep(60)

    def mark(self, name):
        # Write the id of this process to a file of the package, for the test.
        path = os.path.join(self.package, name)
        with open(path + ".part", "w") as file:
            file.write(str(os.getpid()))
        os.rename(path + ".part", path)

    def output(self):
        return {
            "main": {"main.b", "main.a"},
            "rest": set(map(str, self.rest)),
            "path": set(sys.path),
            "cwd": {os.getcwd()},
            "hash": {str(hash("seed"))},
            "environment": set(os.environ),
            "found": {str(os.path.isfile(p)) for p in self.entry_points},
        }

    def document(self):
        return {
            "modules": {
                "m": {"kind": "source", "status": "analysed"},
                "n": {"kind": "source", "status": "analysed"},
                "b": {"kind": "source", "status": "failed", "cause": "SyntaxError"},
                "e": {"kind": "extension"},
            },
            "definitions": {"m": {}, "m.f": {}, "n": {}, "b": {}},
            "calls": {
                "m": [
                    {"callees": ["m.f", "x.y"]},
                    {"callees": ["m.f"], "implicit": "iteration"},
                    {"callees": [], "unresolved": "no value"},
                ],
                "m.f": [
                    {"callees": [], "unresolved": "computed name"},
                    {"callees": [], "unresolved": "no value"},
                ],
            },
        }
"""


def fake_checkout(tmp_path, original=False):
    checkout = tmp_path / "fake-pycg"
    (checkout / "pycg").mkdir(parents=True)
    (checkout / "pycg" / "__init__.py").write_text("")
    (checkout / "pycg" / "pycg.py").write_text(FAKE_TOOL)
    if original:
        (checkout / "pycg" / "utils").mkdir()
        (checkout / "pycg" / "utils" / "__init__.py").write_text(
            "from pycg.utils import constants\n"
        )
        (checkout / "pycg" / "utils" / "constants.py").write_text(
            'CALL_GRAPH_OP = "call-graph"\n'
        )
    return checkout


def project(tmp_path, behaviour):
    directory = tmp_path / f"project-{behaviour}"
    directory.mkdir()
    (directory / "BEHAVIOUR").write_text(behaviour)
    # Analysed source: running or importing it would leave a file behind.
    (directory / "main.py").write_text(
        "open(__file__ + '.ran', 'w').close()\nimport os\n"
    )
    return run.Job(f"micro/demo/{behaviour}", (directory / "main.py",), directory)


def tool(tmp_path, flavour="new"):
    checkout = fake_checkout(tmp_path, original=flavour == "original")
    return run.Tool("fake", flavour, checkout, sys.executable)


def gone(pid):
    """Whether the process `pid` has ended, within a few seconds."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        # A killed process that its new parent has not yet reaped.
        if run.resident_mib(pid) in (None, 0.0):
            return True
        time.sleep(0.05)
    return False


def git(directory, *args):
    subprocess.run(
        [
            "git",
            "-C",
            str(directory),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "-c",
            "commit.gpgsign=false",
        ]
        + list(args),
        check=True,
        capture_output=True,
    )


def pinned_archive(cache, work, name, files, archive_name):
    """An archive of `files` ({path: text}) in CACHE and its tree unpacked
    into WORK/<name> by corpus.py's own unpack, as `corpus.py fetch` leaves
    them: a zip for a `.whl` or `.zip` name, else a tar.gz. A path that
    ends with `/` is a directory member. Returns the fields of its manifest
    row (file, sha256, size) and its fetch.json record."""
    corpus = load("rq3-scale/corpus")
    buffer = io.BytesIO()  # the same bytes on every run: fixed times
    if archive_name.endswith((".whl", ".zip")):
        with zipfile.ZipFile(buffer, "w") as archive:
            for path, text in files.items():
                archive.writestr(zipfile.ZipInfo(path, (1980, 1, 1, 0, 0, 0)), text)
        data = buffer.getvalue()
    else:
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            for path, text in files.items():
                content = text.encode()
                info = tarfile.TarInfo(path.rstrip("/"))
                if path.endswith("/"):
                    info.type = tarfile.DIRTYPE
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
        data = gzip.compress(buffer.getvalue(), mtime=0)
    Path(cache).mkdir(parents=True, exist_ok=True)
    (Path(cache) / archive_name).write_bytes(data)
    refused = corpus.unpack(Path(cache) / archive_name, Path(work) / name)
    sha256 = hashlib.sha256(data).hexdigest()
    fields = {"file": archive_name, "sha256": sha256, "size": len(data)}
    return fields, {"file": archive_name, "sha256": sha256, "refused": refused}
