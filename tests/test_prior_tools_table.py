"""Behaviour tests for evaluation/common/prior_tools.py: the scores of the
prior tools' stored runs, the input of the rows of the prior tools in RQ1.

The stored runs are written here as container/tool_job.py writes them
(results/tools/<row>/rq1.json, the graph of each macro project in
results/graphs/<row>/<project>.json); the benchmarks are small directories
written to tmp_path. No tool runs.
The scorer needs Python 3.14 or later.
"""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from textwrap import dedent

import pytest

if sys.version_info < (3, 14):
    pytest.skip(
        "the scorer loads macro-benchmark/check.py, which needs Python 3.14",
        allow_module_level=True,
    )

_spec = importlib.util.spec_from_file_location(
    "evaluation_fakes", Path(__file__).resolve().parent / "evaluation_fakes.py"
)
fakes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fakes)


prior = fakes.load("common/prior_tools")
tool_job = prior.tool_job
tables = prior.tables

PROJECT = dedent("""
    [{name}]
    commit = "{commit}"
    root = "."
    packages = ["{package}"]
    exclude = []
    dependencies = []
    pytest_args = []
    test_requirements = []
""")


def benchmark(directory, projects):
    """A macro-benchmark: {project: package}; each package calls its f."""
    (directory / "ground-truth").mkdir(parents=True)
    toml = ""
    for name, package in projects.items():
        toml += PROJECT.format(name=name, package=package, commit="a" * 40)
        source = directory / "projects" / name / package
        source.mkdir(parents=True)
        (source / "__init__.py").write_text("def f():\n    pass\n\nf()\n")
        truth = {package: [f"{package}.f"], f"{package}.f": []}
        (directory / "ground-truth" / f"{name}.json").write_text(json.dumps(truth))
    (directory / "projects.toml").write_text(toml)
    return directory


def snippets(directory, cases):
    """A micro-benchmark: {category/case: requires-python or None}."""
    for key, required in cases.items():
        case = directory / key
        case.mkdir(parents=True)
        (case / "main.py").write_text("def a():\n    pass\n\na()\n")
        truth = {"main": ["main.a"], "main.a": []}
        (case / "callgraph.json").write_text(json.dumps(truth))
        if required:
            (case / "requires-python").write_text(required)
    return directory


@pytest.fixture
def sets(tmp_path):
    root = tmp_path / "root"
    projects = {"good": "good", "other": "other", "broken": "brokenpkg"}
    benchmark(root / "macro-benchmark", projects)
    micro = snippets(tmp_path / "snippets", {"a/one": None, "a/mid": "3.13"})
    return root, micro


def digest(adapter, job):
    """The input_sha256 that tool_job.run records for a job."""
    files = adapter.files(job) if hasattr(adapter, "files") else job.files
    with tempfile.TemporaryDirectory() as work:
        return tool_job.export(job.root, tool_job.with_stubs(files), Path(work) / "job")


def right(job):
    """The graph of a job that the benchmark expects."""
    if job.key.startswith("micro/"):
        return {"main": ["main.a"], "main.a": []}
    package = Path(job.entry_points[0]).parent.name
    return {package: [f"{package}.f"], f"{package}.f": []}


def ok(adapter, job):
    """The record of a finished job, as tool_job.analyse gives it."""
    return {
        "status": "ok",
        "input_sha256": digest(adapter, job),
        "graph_sha256": tool_job.graph_digest(right(job)),
    }


def store(tmp_path, root, micro, tool="pyan", row="pyan3", broken=()):
    """Write the result of `row` as tool_job.rq1 does: every job ok with the
    expected graph, but the projects of `broken`, which crashed. Return the
    directories of the results and of the graphs."""
    adapter = tool_job._build().load_adapter(tool)
    jobs, skipped = tool_job.micro_jobs(tool_job.cohort(), micro)
    runs = {key: {"status": "skipped"} for key in skipped}
    runs |= {j.key: ok(adapter, j) for j in jobs}
    data = {
        "question": "rq1",
        "row": row,
        "settings": dict(
            tool_job.rows(tool)[[r for r, _ in tool_job.rows(tool)].index(row)][1]
        ),
        "tool": tool_job.facts(tool, adapter, tool_job.LIMITS["rq1"]),
        "cohort": "3.12",
        "absent": {},
        "sets": {"micro": {"runs": runs, "graphs": {j.key: right(j) for j in jobs}}},
    }
    results, graphs = tmp_path / "results" / "tools", tmp_path / "results" / "graphs"
    jobs, _ = tool_job.macro_jobs(root / "macro-benchmark")
    runs = {}
    for job in jobs:
        name = job.key.removeprefix("macro/")
        if name in broken:
            runs[job.key] = {"status": "crash", "error": "the detail"}
            continue
        runs[job.key] = ok(adapter, job)
        tool_job.write(tool_job.graph_path(graphs, row, name), right(job))
    data["sets"]["macro"] = {"runs": runs}
    tool_job.write(tool_job.result_path(results, row, "rq1"), data)
    return results, graphs


REAL_COMPARE = prior.compare
HEADERGEN = tool_job.rows("headergen")[0][0]


def compare(sets, results, graphs):
    root, micro = sets
    return REAL_COMPARE(results, graphs, root=root, snippets=micro)


def test_a_row_without_its_result_file_is_not_run_with_its_command(tmp_path, sets):
    data = compare(sets, tmp_path / "none", tmp_path / "graphs")
    assert set(data["tools"]) == {
        row for tool in prior.PRIOR_TOOLS for row, _ in tool_job.rows(tool)
    }
    pyan = data["tools"]["pyan3"]["not run"]
    assert pyan.startswith("no results/tools/pyan3/rq1.json")
    assert "docker compose -f evaluation/compose.yaml run --rm pyan rq1" in pyan
    assert data["results"] == {}
    cells = tables.prior_tool_cells(write_data(tmp_path, data), {}, None)
    assert cells["pyan3"][None]["reason"].startswith("not run: no results/tools/")


def write_data(tmp_path, data):
    path = tmp_path / "prior-tools.json"
    path.write_text(prior.score.dumps(data))
    return path


def test_the_stored_graphs_are_scored_on_both_benchmarks(tmp_path, sets):
    root, micro = sets
    results, graphs = store(tmp_path, root, micro, broken=("broken",))
    data = compare(sets, results, graphs)
    found = data["results"]["pyan3"]
    assert data["sets"]["micro"]["cases"] == 1  # a/mid needs Python 3.13
    assert found["micro"]["runs"] == 1
    assert found["micro"]["raw"]["inside"]["tp"] == 1
    assert found["macro"]["raw"]["inside"]["tp"] == 2
    assert found["macro"]["raw"]["inside"]["fn"] == 1  # the failed run's edge
    assert found["macro"]["failed"] == [
        {"run": "macro/broken", "status": "crash", "error": "the detail"}
    ]
    assert {n: p["status"] for n, p in found["macro"]["projects"].items()} == {
        "good": "ok",
        "other": "ok",
        "broken": "crash",
    }
    assert data["tools"]["pyan3"]["image"] == {"reference": "pycg-tool-pyan"}
    # The rows that the tables read: measured where the inputs are these,
    # whatever the state of the repository (the services write results/).
    current = tables.current_inputs(micro, root / "macro-benchmark")
    assert data["sets"]["macro"]["inputs"] == current["macro"]
    assert data["sets"]["macro"]["directory"] == "macro-benchmark"
    data["sets"]["macro"]["dirty"] = True
    path = write_data(tmp_path, data)
    cells = tables.prior_tool_cells(path, current, data["scorer"]["table"])
    assert cells["pyan3"]["macro"]["status"] == "measured"
    assert cells["pyan3"]["macro"]["inside"]["tp"] == 2
    assert cells["pyan3"]["macro"]["projects"]["good"]["raw"]["inside"]["tp"] == 1
    assert cells["JARVIS"][None]["reason"].startswith("not run: no results/tools/")


def test_a_stored_graph_in_results_graphs_is_what_the_scorer_reads(tmp_path, sets):
    """A reader scores a tool again from the committed files: the record of
    the run and the graph file of the project, which belong together."""
    root, micro = sets
    results, graphs = store(tmp_path, root, micro)
    before = compare(sets, results, graphs)["results"]["pyan3"]["macro"]
    assert before["projects"]["good"]["raw"]["inside"] | {"f1": 1.0} == {
        "tool": 1,
        "truth": 1,
        "tp": 1,
        "fp": 0,
        "fn": 0,
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
    }
    # Another graph for the project, with the record of that graph.
    other = {"good": ["good.f", "good.g"], "good.f": []}
    tool_job.write(tool_job.graph_path(graphs, "pyan3", "good"), other)
    path = tool_job.result_path(results, "pyan3", "rq1")
    data = json.loads(path.read_text())
    assert "graphs" not in data["sets"]["macro"]  # only the records are there
    record = data["sets"]["macro"]["runs"]["macro/good"]
    record["graph_sha256"] = tool_job.graph_digest(other)
    path.write_text(json.dumps(data))
    after = compare(sets, results, graphs)["results"]["pyan3"]["macro"]
    counts = after["projects"]["good"]["raw"]["inside"]
    assert (counts["tp"], counts["fp"], counts["fn"]) == (1, 1, 0)
    assert after["raw"]["inside"]["fp"] == 1


def test_a_benchmark_whose_inputs_changed_since_the_run_needs_a_rerun(tmp_path, sets):
    root, micro = sets
    results, graphs = store(tmp_path, root, micro)
    source = root / "macro-benchmark" / "projects" / "good" / "good" / "__init__.py"
    source.write_text("def f():\n    pass\n\nf()\nf()\n")
    data = compare(sets, results, graphs)
    assert data["results"]["pyan3"]["macro"] == {"not run": prior.STALE}
    assert "raw" in data["results"]["pyan3"]["micro"]
    path = write_data(tmp_path, data)
    cells = tables.prior_tool_cells(path, {}, None)
    assert cells["pyan3"]["macro"]["reason"] == prior.STALE
    assert f"pyan3, `macro`: {prior.STALE}." in prior.render(data)


def test_a_benchmark_whose_sources_are_not_fetched_is_not_measured(tmp_path, sets):
    root, micro = sets
    results, graphs = store(tmp_path, root, micro)
    source = root / "macro-benchmark" / "projects" / "good" / "good" / "__init__.py"
    source.unlink()
    found = compare(sets, results, graphs)["results"]["pyan3"]
    assert found["macro"] == {"not run": "not measured: 1 projects have no source"}
    assert "raw" in found["micro"]


def test_micro_runs_of_another_cohort_need_a_rerun(tmp_path, sets):
    root, micro = sets
    results, graphs = store(tmp_path, root, micro)
    path = tool_job.result_path(results, "pyan3", "rq1")
    data = json.loads(path.read_text())
    data["cohort"] = "3.13"
    path.write_text(json.dumps(data))
    found = compare(sets, results, graphs)["results"]["pyan3"]
    assert found["micro"] == {"not run": "needs rerun: cohort 3.13"}


def test_the_runs_of_headergen_in_the_images_of_dependencies_are_laid_over(
    tmp_path, sets
):
    root, micro = sets
    results, graphs = store(
        tmp_path, root, micro, tool="headergen", row=HEADERGEN, broken=("good",)
    )
    path = tool_job.result_path(results, HEADERGEN, "rq1")
    data = json.loads(path.read_text())
    adapter = tool_job._build().load_adapter("headergen")
    jobs, _ = tool_job.macro_jobs(root / "macro-benchmark")
    [job] = [j for j in jobs if j.key == "macro/good"]
    data["sets"]["macro"]["runs"]["macro/good"] = {
        "status": "unavailable",
        "error": "runs in the image of its dependencies",
    }
    path.write_text(json.dumps(data))
    before = compare(sets, results, graphs)["results"][HEADERGEN]["macro"]
    assert before["projects measured"] == [2, 3]
    assert before["projects"]["good"] == {"status": "unavailable"}
    extra = {
        "row": data["row"],
        "settings": data["settings"],
        "tool": data["tool"],
        "only": "good",
        "sets": {
            "macro": {
                "runs": {
                    "macro/good": ok(adapter, job)
                    | {"dependencies": "installed: tools/headergen-deps/x.txt"}
                },
            }
        },
    }
    path.with_name("rq1-good.json").write_text(json.dumps(extra))
    tool_job.write(tool_job.graph_path(graphs, HEADERGEN, "good"), right(job))
    after = compare(sets, results, graphs)["results"][HEADERGEN]["macro"]
    assert "projects measured" not in after
    assert after["raw"]["inside"]["tp"] == 3
    assert after["dependencies"] == {"installed: tools/headergen-deps/x.txt": 1}


def test_the_command_writes_both_files(tmp_path, sets, monkeypatch):
    root, micro = sets
    results, graphs = store(tmp_path, root, micro)
    monkeypatch.setattr(prior, "REPO", root)
    monkeypatch.setattr(prior.score, "SNIPPETS", micro)
    monkeypatch.setattr(
        prior, "compare", lambda tools, graphs: compare(sets, tools, graphs)
    )
    out = tmp_path / "out"
    argv = ["--tools", str(results), "--graphs", str(graphs), "--out", str(out)]
    assert prior.main(argv) == 0
    data = json.loads((out / "prior-tools.json").read_text())
    assert data["results"]["pyan3"]["macro"]["raw"]["inside"]["tp"] == 3
    text = (out / "prior-tools.md").read_text()
    assert "| pyan3 | macro | 3 |" in text
    assert "`macro-benchmark` at `" in text and ": broken, good, other." in text


def test_a_finished_job_without_its_graph_needs_a_rerun_never_scores_zero(
    tmp_path, sets
):
    root, micro = sets
    results, graphs = store(tmp_path, root, micro)
    graph = tool_job.graph_path(graphs, "pyan3", "good")
    tool_job.write(graph, {"good": []})  # not the graph of its run
    found = compare(sets, results, graphs)["results"]["pyan3"]
    reason = "needs rerun: a graph is missing or is not that of its run"
    assert found["macro"] == {"not run": reason}
    assert "raw" in found["micro"]
    graph.unlink()  # the graph is gone
    assert compare(sets, results, graphs)["results"]["pyan3"]["macro"] == {
        "not run": reason
    }


def test_a_micro_case_or_project_added_since_the_run_needs_a_rerun(tmp_path, sets):
    root, micro = sets
    results, graphs = store(tmp_path, root, micro)
    snippets(micro, {"a/new": None})  # in the cohort: the run did not see it
    benchmark_dir = root / "macro-benchmark"
    toml = (benchmark_dir / "projects.toml").read_text()
    source = benchmark_dir / "projects" / "late" / "late"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text("def f():\n    pass\n")
    truth = {"late": ["late.f"], "late.f": []}
    (benchmark_dir / "ground-truth" / "late.json").write_text(json.dumps(truth))
    (benchmark_dir / "projects.toml").write_text(
        toml + PROJECT.format(name="late", package="late", commit="a" * 40)
    )
    found = compare(sets, results, graphs)["results"]["pyan3"]
    assert found["micro"] == {
        "not run": "needs rerun: the jobs of the benchmark changed since the tool ran"
    }
    assert found["macro"] == found["micro"]


@pytest.mark.parametrize(
    "change, reason",
    [
        (
            lambda d: d["tool"]["pin"].update(commit="0" * 40),
            "needs rerun: it ran another version of the tool",
        ),
        (
            lambda d: d.update(row="JARVIS"),
            "needs rerun: it is the result of another row or variant",
        ),
        (
            lambda d: d["tool"].update(
                limits={"seconds": 600, "memory_mib": 2048}, memory_mib=2048
            ),
            "needs rerun: it ran with other limits (600 s, 2048 MiB; "
            "now 1800 s, 16384 MiB)",
        ),
    ],
)
def test_a_stored_result_of_another_pin_row_or_limits_needs_a_rerun(
    tmp_path, sets, change, reason
):
    root, micro = sets
    results, graphs = store(tmp_path, root, micro)
    path = tool_job.result_path(results, "pyan3", "rq1")
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))
    data = compare(sets, results, graphs)
    assert data["tools"]["pyan3"]["not run"] == reason
    assert "pyan3" not in data["results"]


def test_the_committed_graphs_are_those_of_the_committed_records():
    """results/tools and results/graphs are committed so that a reader
    scores a tool again without running it: every finished run has its
    graph file, which is the graph of its record, and no other graph is
    there."""
    seen = set()
    for row in sorted(prior.TOOLS_RESULTS.iterdir()):
        runs = {}  # the run of the tool, then those of headergen-deps over it
        for path in [row / "rq1.json", *sorted(row.glob("rq1-*.json"))]:
            macro = json.loads(path.read_text())["sets"].get("macro", {})
            assert "graphs" not in macro, path
            runs |= macro.get("runs", {})
        for key, record in runs.items():
            graph = tool_job.graph_path(prior.GRAPHS, row.name, key.split("/")[1])
            assert graph.is_file() is (record["status"] == "ok"), graph
            if graph.is_file():
                found = json.loads(graph.read_text())
                assert tool_job.graph_digest(found) == record["graph_sha256"], graph
                seen.add(graph)
    assert seen and seen == set(prior.GRAPHS.glob("*/*.json"))
