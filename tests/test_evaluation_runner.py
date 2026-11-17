"""Behaviour tests for evaluation/common/run.py and evaluation/common/analyse.py.

They run fake PyCG checkouts, written to tmp_path, in child processes. They
need neither the network nor the benchmark's submodules, and run on every
supported Python version.
"""

import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "evaluation_fakes", Path(__file__).resolve().parent / "evaluation_fakes.py"
)
fakes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fakes)


gone = fakes.gone
run, project, tool, fake_checkout = (
    fakes.run,
    fakes.project,
    fakes.tool,
    fakes.fake_checkout,
)


def test_a_crash_is_recorded_with_the_error_and_where_it_happened(tmp_path):
    job = project(tmp_path, "crash")
    graph_path = tmp_path / "graph.json"
    graph_path.write_text("{}")  # a graph of an earlier run
    record = run.run_job(tool(tmp_path), job, graph_path, timeout=60)
    assert record["status"] == "crash"
    assert record["error"].startswith("JSONDecodeError: Expecting property name")
    assert record["where"].startswith("pycg/pycg.py:")
    assert record["where"].endswith(" in analyze")
    assert not graph_path.exists()


def test_micro_cases_that_need_a_newer_python_are_skipped(tmp_path):
    for case, version in (
        ("a/old", None),
        ("a/new", "3.99"),
        ("b/same", "3.12"),
        ("b/patch", "3.12.0"),
        ("b/later", "3.12.1"),
    ):
        directory = tmp_path / case
        directory.mkdir(parents=True)
        (directory / "main.py").write_text("")
        if version:
            (directory / "requires-python").write_text(version + "\n")
    jobs, skipped = run.micro_jobs(tmp_path, version=(3, 12, 0))
    assert [job.key for job in jobs] == ["micro/a/old", "micro/b/patch", "micro/b/same"]
    assert skipped == ["micro/a/new", "micro/b/later"]
    assert jobs[0].entry_points == (tmp_path / "a" / "old" / "main.py",)
    assert jobs[0].package == tmp_path / "a" / "old"


def test_the_runner_runs_the_projects_of_the_macro_benchmark(tmp_path, monkeypatch):
    config = tmp_path / "bench" / "projects.toml"
    monkeypatch.setattr(run.tables.projects, "CONFIG_PATH", config)
    project = tmp_path / "bench" / "projects" / "demo"
    (project / "pkg").mkdir(parents=True)
    (project / "pkg" / "__init__.py").write_text("")
    (project / "BEHAVIOUR").write_text("ok")
    (tmp_path / "bench" / "projects.toml").write_text(
        '[demo]\nroot = "."\npackages = ["pkg"]\nexclude = []\n'
        "dependencies = []\npytest_args = []\ntest_requirements = []\n"
    )
    checkout = fake_checkout(tmp_path)
    out = tmp_path / "out"
    argv = ["--flavour", "new", "--checkout", str(checkout), "--name", "t"]
    argv += ["--benchmark", "macro", "--allow-dirty", "--out", str(out)]
    assert run.main(argv) == 0
    runs = json.loads((out / "t" / "macro" / "runs.json").read_text())
    assert runs["runs"]["macro/demo"]["status"] == "ok"


def test_the_original_pycg_is_pinned_by_a_commit_of_the_history_of_head(tmp_path):
    entry = fakes.load("container/question_entry")
    compose = (fakes.EVALUATION / "compose.yaml").read_text(encoding="utf-8")
    pinned = entry.ORIGINAL_REVISION  # a full hash, never a tag
    assert len(pinned) == 40 and int(pinned, 16) >= 0
    assert f"ORIGINAL: ${{ORIGINAL-{pinned}}}" in compose
    repository = fake_checkout(tmp_path)
    fakes.git(repository, "init", "-q", "-b", "main")
    fakes.git(repository, "add", "-A")
    fakes.git(repository, "commit", "-q", "-m", "2021")
    fakes.git(repository, "checkout", "-q", "-b", "side")
    fakes.git(repository, "commit", "-q", "--allow-empty", "-m", "side")
    fakes.git(repository, "checkout", "-q", "main")
    fakes.git(repository, "commit", "-q", "--allow-empty", "-m", "head")
    engine = entry.engine
    head = engine.revision(repository, "main")
    assert engine.in_history(repository, engine.revision(repository, "main~1"), head)
    side = engine.revision(repository, "side")
    assert not engine.in_history(repository, side, head)
    found = entry.engines(
        repository, engine.revision(repository, "main~1"), tmp_path / "e"
    )
    assert [v["describe"] for v in found.values()][1] == engine.revision(
        repository, "main~1"
    )
    with pytest.raises(entry.Refused, match="is not in the history of HEAD"):
        entry.engines(repository, side, tmp_path / "f")
