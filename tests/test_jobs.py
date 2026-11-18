"""--jobs K of the prior tools' runner (evaluation/container/tool_job.py):
K jobs at a time, each in a directory of its own with its own limits, give
the records of one job at a time; the first error stops them all. The tool
is a fake driver run in real child processes; no Docker."""

import sys
import time
import types

import pytest
from evaluation_fakes import gone, load

tool_job = load("container/tool_job")

DRIVER = """
import json, os, sys, time

request = json.load(open(sys.argv[1]))
root = request["root"]
names = sorted(
    os.path.relpath(os.path.join(top, name), root)
    for top, _, files in os.walk(root)
    for name in files
)
if "hang.py" in names:
    with open(request["marker"] + ".part", "w") as file:
        file.write(str(os.getpid()))
    os.rename(request["marker"] + ".part", request["marker"])
    time.sleep(60)
time.sleep(0.05 * (len(names) % 3))  # the jobs end in another order
with open(request["output"], "w") as file:
    json.dump({"names": names}, file)
"""


def adapter(tmp_path, refuse=None):
    """A fake adapter: its driver writes the names of the job's files.
    `refuse(job)` may raise before the job runs (an error of the runner)."""
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER)

    def command(tool, job, work):
        if refuse:
            refuse(job)
        request = {
            "root": str(job.root),
            "output": str(work / "out" / "native.json"),
            "marker": str(tmp_path / "hang.pid"),
        }
        argv = [sys.executable, "-I", str(work / "driver" / driver.name)]
        return {
            "argv": argv + [str(work / "request.json")],
            "request": request,
            "output": "native.json",
        }

    def convert(native, job, tool):
        return {name: [] for name in native["names"]}, {}

    return types.SimpleNamespace(__file__=str(driver), command=command, convert=convert)


def jobs(tmp_path, names):
    """One job per name: a package with 1 to 4 modules."""
    found = []
    for number, name in enumerate(names):
        root = tmp_path / "projects" / name
        files = [root / f"{name}.py"] + [root / f"m{i}.py" for i in range(number % 4)]
        for path in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x = 1\n")
        found.append(tool_job.Job(f"macro/{name}", root, tuple(files), tuple(files)))
    return found


@pytest.fixture(autouse=True)
def work(tmp_path, monkeypatch):
    """The jobs' directories in tmp_path: run, run1, run2..."""
    monkeypatch.setattr(tool_job, "WORK", tmp_path / "run")
    monkeypatch.setattr(tool_job, "STOPPED", None)


def without_measurements(records):
    measured = ("wall_seconds", "peak_rss_mib")
    return {
        k: {f: v for f, v in r.items() if f not in measured} for k, r in records.items()
    }


def test_k_jobs_at_a_time_give_the_records_of_one_at_a_time(tmp_path):
    fake = adapter(tmp_path)
    found = jobs(tmp_path, [f"p{i}" for i in range(7)])
    one = tool_job.run_jobs(fake, {}, found, ["macro/skipped"], (30, 1024), k=1)
    three = tool_job.run_jobs(fake, {}, found, ["macro/skipped"], (30, 1024), k=3)
    assert without_measurements(one[0]) == without_measurements(three[0])
    assert one[1] == three[1]
    assert list(three[0]) == ["macro/skipped", *(job.key for job in found)]
    assert {r["status"] for k, r in one[0].items() if k != "macro/skipped"} == {"ok"}
    assert one[1]["macro/p3"] == {"m0.py": [], "m1.py": [], "m2.py": [], "p3.py": []}
    # Three directories at most, one per job that runs at a time.
    assert sorted(p.name for p in tmp_path.glob("run*")) == ["run", "run1", "run2"]


def test_an_error_of_one_job_stops_the_others_now(tmp_path):
    marker = tmp_path / "hang.pid"

    def refuse(job):
        if job.key == "macro/b":
            while not marker.exists():  # once the analysis of hang runs
                time.sleep(0.02)
            raise OSError("the runner failed on b")

    found = jobs(tmp_path, ["hang", "b", "c", "d"])
    start = time.monotonic()
    with pytest.raises(OSError, match="the runner failed on b"):
        tool_job.run_jobs(adapter(tmp_path, refuse), {}, found, [], (60, 1024), k=2)
    assert time.monotonic() - start < 15  # not the 60 s of hang
    assert gone(int(marker.read_text()))  # its child was killed
