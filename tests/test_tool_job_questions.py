"""The questions that run for hours, end to end at K = 1 and K = 3, up to
the files that they write: rq1, rq1-trace, rq3-pilot and rq3 of the prior
tools' runner (evaluation/container/tool_job.py, a fake driver in real
child processes), and the PyCG side of rq3 (rq3-scale/table.py, a fake
PyCG checkout), on tiny fake benchmarks and corpora. No Docker."""

import json
import sys
import types

import pytest
from evaluation_fakes import fake_checkout, git, load, pinned_archive
from test_jobs import adapter

if sys.version_info < (3, 14):
    pytest.skip(
        "the table of RQ3 loads the scorer, which needs Python 3.14",
        allow_module_level=True,
    )

tool_job = load("container/tool_job")
table = load("rq3-scale/table")
PIN = {"name": "fake-tool", "version": "1.0", "commit": "f" * 40}
MEASURED = ("wall_seconds", "peak_rss_mib")


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """The tool `fake`: one row, the fake driver; the jobs' directories in
    tmp_path; the RQ3 table that tool_job loads is `table`, whose parts the
    tests set."""
    tool = adapter(tmp_path)
    tool.PIN = PIN
    build = types.SimpleNamespace(
        load_adapter=lambda name: tool,
        read_pins=lambda: {"base_images": {"fake": "python:3.12-slim"}},
        TOOLS=("fake",),
    )
    monkeypatch.setattr(tool_job, "_BUILD", build)
    monkeypatch.setattr(tool_job, "WORK", tmp_path / "run")
    monkeypatch.setattr(tool_job, "STOPPED", None)
    loader = tool_job._load
    monkeypatch.setattr(
        tool_job,
        "_load",
        lambda name, path: table if name == "rq3_table" else loader(name, path),
    )
    return tool


def part(corpus, directory, names, trace=False):
    """A part of pure wheels in CORPUS/<directory>, each one module; its
    manifest beside the corpus."""
    work, cache = corpus / directory, corpus / "cache"
    rows, recorded = [], {}
    for rank, name in enumerate(names, start=1):
        file = f"{name}-1.0-py3-none-any.whl"
        files = {f"{name}/__init__.py": "x = 1\n", f"{name}/core.py": "y = 2\n"}
        files["BEHAVIOUR"] = "ok"  # for the fake PyCG
        fields, recorded[name] = pinned_archive(cache, work, name, files, file)
        row = {
            "name": name,
            "rank": rank,
            "version": "1.0",
            "kind": "pure wheel",
            "url": "https://files.pythonhosted.org/packages/" + file,
            **fields,
        }
        if trace:
            row["trace"] = f"project{rank}"
        rows.append(row)
    rows.append({"name": f"{directory}-gone", "rank": len(rows) + 1, "reason": "gone"})
    (work / "fetch.json").write_text(json.dumps(recorded))
    manifest = corpus / f"{directory}.json"
    data = {
        "rows": rows,
        "source": {"repository": "r", "commit": "c" * 40},
    }  # as dypybench.json
    if directory == "work":  # as pypi-top-100.json
        data = {
            "top": len(rows),
            "rank_list": {"file": "t", "last_update": "x"},
            "rows": rows,
        }
    manifest.write_text(json.dumps(data))
    return manifest


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    root = tmp_path / "corpus"
    parts = (
        ("PyPI top 100", part(root, "work", [f"t{i}" for i in range(6)]), "work"),
        (
            "DyPyBench",
            part(root, "dypybench", [f"d{i}" for i in range(6)], trace=True),
            "dypybench",
        ),
        ("Large projects", part(root, "large", ["big0", "big1"]), "large"),
    )
    monkeypatch.setattr(table, "PARTS", parts)
    monkeypatch.setattr(table, "PILOT_PARTS", parts[:2])
    return root


def unmeasured(data):
    """`data` without what the load of the machine changes."""
    if isinstance(data, dict):
        return {
            k: unmeasured(v) for k, v in data.items() if k not in (*MEASURED, "jobs")
        }
    return data


@pytest.mark.parametrize("pilot", [False, True])
def test_rq3_writes_its_result_with_every_part_at_any_k(tmp_path, fake, corpus, pilot):
    question = "rq3-pilot" if pilot else "rq3"
    found = {}
    for k in (1, 3):
        results = tmp_path / f"results{k}" / "tools"
        written = tool_job.rq3(
            "fake", pilot=pilot, results=results, work=corpus, jobs=k
        )
        path = results / "fake-tool" / f"{question}.json"
        assert written == [path]
        found[k] = json.loads(path.read_text())
    data = found[1]
    assert (data["question"], data["row"], data["jobs"]) == (question, "fake-tool", 1)
    assert found[3]["jobs"] == 3
    assert unmeasured(found[3]) == unmeasured(data)
    labels = [label for label, _, _ in table.PARTS]
    assert sorted(data["halves"]) == sorted(labels[:2] if pilot else labels)
    for label, stored in data["halves"].items():
        runs = {n: p["runs"] for n, p in stored["packages"].items() if "runs" in p}
        assert len(runs) == (5 if pilot else {"Large projects": 2}.get(label, 6))
        for name, run in runs.items():
            assert run["fake-tool"]["status"] == "ok", name
            assert run["fake-tool"]["counts"] == {"edges": 0}
            assert set(run["fake-tool"]["measured"]) == set(MEASURED)
    if not pilot:
        assert data["halves"]["PyPI top 100"]["packages"]["work-gone"] == {
            "rank": 7,
            "reason": "gone",
        }
    # The stored run is one that the table of RQ3 reads as current.
    assert tool_job.stored_problem("fake", "fake-tool", data, question) is None


def test_rq1_trace_writes_the_graphs_of_the_traced_projects_at_any_k(
    tmp_path, fake, corpus
):
    found = {}
    for k in (1, 3):
        graphs = tmp_path / f"graphs{k}"
        written = tool_job.rq1_trace("fake", graphs=graphs, work=corpus, jobs=k)
        path = graphs / "fake-tool" / "rq1-trace.json"
        assert written == [path]
        found[k] = json.loads(path.read_text())
    assert unmeasured(found[3]) == unmeasured(found[1])
    data = found[1]
    assert sorted(data["runs"]) == [f"d{i}" for i in range(6)]
    assert {r["status"] for r in data["runs"].values()} == {"ok"}
    assert data["graphs"]["d0"] == {"d0/__init__.py": [], "d0/core.py": []}


def micro_and_macro(root):
    """A micro-benchmark of two cases and a macro-benchmark of two projects."""
    snippets = root / "snippets"
    for key in ("a/one", "b/two"):
        (snippets / key).mkdir(parents=True)
        (snippets / key / "main.py").write_text("def a():\n    pass\n")
    benchmark = root / "macro-benchmark"
    toml = ""
    for name in ("p", "q"):
        toml += (
            f'[{name}]\ncommit = "{"a" * 40}"\nroot = "."\npackages = ["{name}"]\n'
            "exclude = []\ndependencies = []\npytest_args = []\n"
            "test_requirements = []\n"
        )
        source = benchmark / "projects" / name / name
        source.mkdir(parents=True)
        (source / "__init__.py").write_text("x = 1\n")
    (benchmark / "projects.toml").write_text(toml)
    return snippets


def test_rq1_writes_its_result_and_the_macro_graphs_at_any_k(
    tmp_path, fake, monkeypatch
):
    snippets = micro_and_macro(tmp_path / "root")
    monkeypatch.setattr(tool_job._runner(), "SNIPPETS", snippets)
    found = {}
    for k in (1, 3):
        results, graphs = tmp_path / f"r{k}" / "tools", tmp_path / f"r{k}" / "graphs"
        written = tool_job.rq1(
            "fake", results=results, root=tmp_path / "root", graphs=graphs, jobs=k
        )
        path = results / "fake-tool" / "rq1.json"
        assert sorted(written) == sorted(
            [path, graphs / "fake-tool" / "p.json", graphs / "fake-tool" / "q.json"]
        )
        found[k] = json.loads(path.read_text())
    assert unmeasured(found[3]) == unmeasured(found[1])
    data = found[1]
    assert sorted(data["sets"]["micro"]["runs"]) == ["micro/a/one", "micro/b/two"]
    assert sorted(data["sets"]["macro"]["runs"]) == ["macro/p", "macro/q"]
    runs = {**data["sets"]["micro"]["runs"], **data["sets"]["macro"]["runs"]}
    assert {r["status"] for r in runs.values()} == {"ok"}


@pytest.mark.parametrize("pilot", [False, True])
def test_the_table_of_rq3_runs_pycg_on_every_part_at_any_k(tmp_path, corpus, pilot):
    checkout = fake_checkout(tmp_path / "pycg")
    git(checkout, "init", "-q")
    git(checkout, "add", ".")
    git(checkout, "commit", "-q", "-m", "fake")
    stem = table.PILOT_STEM if pilot else table.STEM
    found = {}
    for k in ("1", "3"):
        results = tmp_path / f"results{k}"
        argv = ["--checkout", str(checkout), "--work", str(corpus)]
        argv += ["--results", str(results), "--jobs", k]
        assert table.main(argv + (["--pilot"] if pilot else [])) == 0
        found[k] = json.loads((results / f"{stem}.json").read_text())
        assert (results / f"{stem}.md").is_file()
        if not pilot:
            for _, path, _ in table.PARTS:
                assert (results / f"robustness-{path.stem}.json").is_file()
    assert (found["1"]["jobs"], found["3"]["jobs"]) == (1, 3)
    for data in found.values():
        corpus_cells = data["rows"][0]["corpus"]
        parts = table.PILOT_PARTS if pilot else table.PARTS
        labels = [label for label, _, _ in parts]
        assert list(corpus_cells) == ["all", *labels]
        finished = {
            k: (c["with_target"], c["finished"]) for k, c in corpus_cells.items()
        }
        expected = {"PyPI top 100": (6, 6), "DyPyBench": (6, 6)}
        expected |= {"Large projects": (2, 2), "all": (14, 14)}
        if pilot:
            expected = {"PyPI top 100": (5, 5), "DyPyBench": (5, 5), "all": (10, 10)}
        assert finished == expected
