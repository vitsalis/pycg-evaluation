"""Behaviour tests for the entry scripts of the research questions
(evaluation/rq*/table.py) and what they share (evaluation/common/tables.py,
engine.py): each writes its table, and an input that it cannot have gives a
stated cell or an error that names it, never a silent gap. Fake PyCG
checkouts and small benchmark directories in tmp_path; no network, no
submodule.

The scorer needs Python 3.14 or later (see tests/test_evaluation.py).
"""

import importlib.util
import json
import re
import sys
from pathlib import Path

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


run = fakes.run
tables = fakes.load("common/tables")
rq1 = fakes.load("rq1-accuracy/table")
rq2 = fakes.load("rq2-causes/table")
rq3 = fakes.load("rq3-scale/table")


PROJECTS = """
[demo]
root = "src"
packages = ["demo"]
exclude = []
dependencies = []
pytest_args = []
test_requirements = []
"""
MICRO_TRUTH = {"main": ["main.f", "<builtin>.len"], "main.f": [], "<builtin>.len": []}
COMMIT = "c" * 40
# The inputs of the benchmarks of this run (tables.current_inputs): a
# prior-tool cell is of these inputs if it records them.
INPUTS = "1" * 64
CURRENT = {"micro": INPUTS, "macro": INPUTS}
# The batches of the fake macro-benchmark (tables.batches).
BATCHES = {1: ["demo"]}
TABLE = rq1.score.SAME_FUNCTION_SHA256  # the table of names of one function


def macro_benchmark(root, checked_out=True, truth=True, toml=PROJECTS):
    """root/macro-benchmark with the project demo; its source only if
    `checked_out`, its ground truth only if `truth`."""
    benchmark = root / "macro-benchmark"
    (benchmark / "projects" / "demo").mkdir(parents=True)
    if checked_out:
        source = benchmark / "projects" / "demo" / "src"
        (source / "demo").mkdir(parents=True)
        (source / "demo" / "__init__.py").write_text("def f():\n    pass\n")
        (source / "BEHAVIOUR").write_text("ok")
    (benchmark / "projects.toml").write_text(toml)
    (benchmark / "ground-truth").mkdir()
    if truth:
        graph = {"demo": ["demo.f", "os.getcwd"], "demo.f": []}
        (benchmark / "ground-truth" / "demo.json").write_text(json.dumps(graph))
        (benchmark / "ground-truth" / "demo.ledger").write_text("# no site\n")
    return benchmark


def snippets(root, newer=False):
    """Micro cases: a/ok, and with `newer` b/new, which needs Python 3.99."""
    directory = root / "snippets"
    cases = [("a", "ok", None)] + ([("b", "new", "3.99")] if newer else [])
    for category, case, required in cases:
        path = directory / category / case
        path.mkdir(parents=True)
        (path / "main.py").write_text("")
        (path / "BEHAVIOUR").write_text("ok")
        (path / "callgraph.json").write_text(json.dumps(MICRO_TRUTH))
        if required:
            (path / "requires-python").write_text(required)
    return directory


COUNTS = {"tool": 10, "truth": 10, "tp": 9, "fp": 1, "fn": 1}
COUNTS |= {"precision": 0.9, "recall": 0.9, "f1": 0.9}


IMAGE = {"reference": "pycg-prior-pyan:0123456789ab", "id": "sha256:" + "e" * 64}


def prior_tools_file(
    path,
    sets=("micro", "macro"),
    not_run=(),
    commit=COMMIT,
    image=IMAGE,
    table=TABLE,
):
    """A result file of prior_tools.py with pyan3 and JARVIS (and PyCG,
    whose rows the tables ignore), measured at `commit` in `image` and
    scored with `table`."""
    result = {"runs": 5, "failed": [], "notes": {}}
    result["raw"] = {"inside": COUNTS, "outside": COUNTS, "all": COUNTS}
    project = {"status": "ok", "raw": result["raw"]}
    results = {"micro": result, "macro": result | {"projects": {"demo": project}}}
    names = ["new-engine", "pyan3", "JARVIS", *not_run]
    data = {
        "scorer": {"table": table, "python": "3.14.0"},
        "tools": {
            name: {
                "version": f"{name} 1.0",
                "python": "3.13",
                "not run": "no image" if name in not_run else None,
                "note": None,
                "image": image,
            }
            for name in names
        },
        "sets": {
            "micro": {
                "directory": "micro",
                "cases": 620,
                "excluded": 13,
                "python": "3.13",
                "commit": commit,
                "dirty": False,
                "inputs": INPUTS,
            },
            "macro": {"directory": "macro-benchmark", "commit": commit, "dirty": True}
            | {"inputs": INPUTS},
        },
        "results": {
            name: {s: results[s] for s in sets} for name in names if name not in not_run
        },
    }
    path.write_text(json.dumps(data))
    return path


# --------------------------------------------------------------------------
# What the tables share
# --------------------------------------------------------------------------


def test_the_prior_tool_cells_are_keyed_by_benchmark_and_keep_its_facts(tmp_path):
    path = prior_tools_file(tmp_path / "prior-tools.json")
    cells = tables.prior_tool_cells(path, CURRENT, TABLE)
    assert "new-engine" not in cells  # PyCG is measured by the tables
    assert set(cells["pyan3"]) == {"micro", "macro"}
    assert cells["pyan3"]["micro"]["inside"]["tp"] == 9
    assert cells["pyan3"]["micro"]["source"] == "prior-tools.json"
    # The counts of each project: the table of RQ1 makes its columns of them.
    assert cells["pyan3"]["macro"]["projects"]["demo"]["raw"]["inside"]["tp"] == 9
    benchmark = cells["pyan3"]["macro"]["benchmark"]
    # The commit is a fact of the measurement, not a condition: results/ is
    # written by the services, so the repository has changes when it is read.
    assert benchmark == {
        "directory": "macro-benchmark",
        "commit": COMMIT,
        "dirty": True,
        "inputs": INPUTS,
    }
    assert cells["pyan3"]["micro"]["benchmark"]["python"] == "3.13"
    # A prior tool that has no row in the file is stated, not left out.
    assert cells["CPyGraph"] == {
        None: tables.missing("not yet run: no row in prior-tools.json")
    }


@pytest.mark.parametrize("current", [CURRENT | {"macro": "2" * 64}, {}])
def test_a_prior_tool_cell_of_other_inputs_needs_a_rerun(tmp_path, current):
    path = prior_tools_file(tmp_path / "prior-tools.json")
    cell = tables.prior_tool_cells(path, current, TABLE)["pyan3"]["macro"]
    assert cell["status"] == "missing"
    assert cell["reason"] == "needs rerun (its inputs changed since it was scored)"
    assert cell["benchmark"]["commit"] == COMMIT
    assert "inside" not in cell  # its counts are never shown


def test_older_results_without_their_inputs_need_a_rerun(tmp_path):
    path = prior_tools_file(tmp_path / "prior-tools.json")
    data = json.loads(path.read_text())
    del data["sets"]["micro"]["inputs"]
    path.write_text(json.dumps(data))
    cells = tables.prior_tool_cells(path, CURRENT, TABLE)["pyan3"]
    assert cells["micro"]["reason"] == "needs rerun (the result records no inputs)"
    assert cells["macro"]["status"] == "measured"  # not inferred from it


@pytest.mark.parametrize("table", [None, "e" * 64])
def test_a_prior_tool_cell_scored_with_another_table_is_not_shown(tmp_path, table):
    path = prior_tools_file(tmp_path / "prior-tools.json", table=table)
    cell = tables.prior_tool_cells(path, CURRENT, TABLE)["pyan3"]["macro"]
    which = "eeeeeeeeee" if table else "none recorded"
    assert cell["reason"] == f"scored with another table ({which}; here {TABLE[:10]})"
    assert cell["table"] == table
    assert "inside" not in cell


@pytest.mark.parametrize("table", [None, TABLE])
def test_a_prior_tool_cell_never_matches_a_measurement_with_no_table(tmp_path, table):
    # A prior-tool result records the table that scored it, or nothing,
    # which proves nothing.
    path = prior_tools_file(tmp_path / "prior-tools.json", table=table)
    cell = tables.prior_tool_cells(path, CURRENT, None)["pyan3"]["macro"]
    which = TABLE[:10] if table else "none recorded"
    assert cell["reason"] == f"scored with another table ({which}; here none)"
    assert "inside" not in cell


def test_a_prior_tool_that_did_not_run_keeps_its_reason(tmp_path):
    path = prior_tools_file(tmp_path / "prior.json", not_run=("CPyGraph",))
    cells = tables.prior_tool_cells(path, CURRENT, TABLE)
    assert cells["CPyGraph"] == {None: tables.missing("not run: no image")}


def test_a_prior_tool_result_without_an_image_ran_on_the_host(tmp_path):
    path = prior_tools_file(tmp_path / "prior-tools.json", image=None)
    cells = tables.prior_tool_cells(path, CURRENT, TABLE)["pyan3"]
    assert cells["macro"]["reason"] == tables.ON_THE_HOST
    assert cells["micro"]["reason"] == tables.ON_THE_HOST
    assert "inside" not in cells["macro"]  # never mixed with a run


# --------------------------------------------------------------------------
# RQ1
# --------------------------------------------------------------------------


def test_rq1_measures_pycg_on_micro_and_on_the_macro_benchmark(tmp_path):
    root = tmp_path / "root"
    macro_benchmark(root)
    tool = run.Tool("fake", "new", fakes.fake_checkout(tmp_path), sys.executable)
    cells = rq1.measure_pycg(
        tool, tmp_path / "work", root, snippets(tmp_path), 60, allow_dirty=True
    )
    assert list(cells) == ["micro", rq1.NEWER, "macro"]
    assert cells["micro"]["cases"] == 1
    assert cells[rq1.NEWER]["status"] == "missing"  # no case needs a newer Python
    demo = cells["macro"]
    assert demo["status"] == "measured"
    assert demo["runs"] == 1 and demo["inside"]["truth"] == 1
    assert demo["projects"]["demo"]["raw"]["inside"] == demo["inside"]
    # PyCG's budget per job is that of the prior tools (one place).
    # One limit for every run of every question: 30 minutes and 16 GiB.
    assert {rq1.LIMITS, *rq1.tool_job.LIMITS.values()} == {(1800, 16384)}
    stored = next((tmp_path / "work").rglob("macro/runs.json"))
    assert json.loads(stored.read_text())["memory_limit_mib"] == rq1.LIMITS[1]


def counts(tp, fp, fn):
    """Counts as score.py stores them (Counts.to_json)."""
    return rq1.score.Counts(tp + fp, tp + fn, tp).to_json()


def test_rq1_has_a_column_per_batch_and_one_for_all_projects():
    def project(tp, fp, fn, status="ok"):
        found = counts(tp, fp, fn)
        return {"status": status, "raw": dict.fromkeys(rq1.score.EDGE_SETS, found)}

    projects = {"a": project(8, 2, 0), "b": project(1, 0, 9, "crash")}
    projects |= {"c": project(5, 0, 5), "d": {"status": "unavailable"}}
    summed = dict.fromkeys(rq1.score.EDGE_SETS, counts(14, 2, 14))
    failed = [{"run": "macro/b", "status": "crash"}]
    cell = tables.measured(summed, runs=3, failed=failed, projects=projects)
    cell |= {"version": "1.0", "no outside": "names no outside callee"}
    batches = {1: ["a", "b"], 2: ["c", "d"], "icse-2021": []}
    cells = rq1.by_batch(cell, batches)
    assert list(cells) == ["batch 1", "batch 2", "ICSE 2021 set", rq1.ALL]
    first = cells["batch 1"]
    assert (first["inside"]["tp"], first["inside"]["fp"], first["inside"]["fn"]) == (
        9,
        2,
        9,
    )
    assert first["inside"]["precision"] == 0.8182 and first["inside"]["recall"] == 0.5
    # A failed run is of its batch; its edges count as false negatives there.
    assert first["failed"] == failed and first["runs"] == 2
    assert tables.cell_text(first) == "P 0.818 / R 0.500 (9/2/9), 1 failed runs"
    # A project whose environment was not there is not measured, and said so.
    second = cells["batch 2"]
    assert second["inside"]["tp"] == 5 and second["failed"] == []
    assert second["projects measured"] == [1, 2]
    assert tables.cell_text(second).endswith(", 1 of 2 projects measured")
    # The facts of the tool stay with every column.
    assert second["version"] == "1.0"
    assert tables.cell_text(second, "outside") == "names no outside callee"
    # All projects: the cell of the macro-benchmark, without the projects.
    assert cells[rq1.ALL]["inside"] == summed["inside"]
    assert "projects" not in cells[rq1.ALL] and "projects" not in first
    # A cell without a measurement is the cell of every column.
    missing = tables.missing("not run: no checkout")
    assert rq1.by_batch(missing, batches) == dict.fromkeys(cells, missing)


def test_the_batches_of_the_tables_are_those_of_projects_toml():
    batches = tables.batches(tables.projects.load_projects())
    assert [tables.batch_title(batch) for batch in batches] == [
        "batch 1",
        "batch 2",
        "batch 3",
        "batch 4",
        "batch 5",
        "ICSE 2021 set",
    ]
    assert sum(len(names) for names in batches.values()) == 30
    assert "cachetools" in batches[1] and "whitenoise" in batches[5]


def test_the_first_measurements_render_into_the_table_of_rq1(tmp_path):
    path = tmp_path / "first.json"
    known = {"inside": [90, 10, 10], "outside": [50, 0, 50]}
    rows = [
        {"tool": "PyCG", "batch": {"inside": [80, 20, 20], "outside": [1, 1, 0]}}
        | {"known": known, "runs": "5 ok"},
        {"tool": "original PyCG", "known": None, "runs": "3 ok, 2 crash"}
        | {"batch": {"inside": [40, 10, 60], "outside": [0, 0, 2]}},
        {"tool": "pyan3", "version": "v2.9.0", "known": known}
        | {"batch": {"inside": [10, 10, 10], "outside": None}}
        | {"no outside": "names no outside callee", "projects measured": [1, 5]},
    ]
    data = {
        "original": {"commit": "8" * 40, "tag": "baseline-original-engine"},
        "batches": [
            {"batch": 2, "date": "2026-11-13", "known": [1, "icse-2021"]}
            | {"engine": {"commit": "2bcc0b1" + "0" * 33, "tag": None}}
            | {"names": "string equality", "rows": rows}
            | {"corrected after": [{"date": "D", "project": "p", "what": "W"}]}
        ],
    }
    path.write_text(json.dumps(data))
    found, lines = rq1.first_measurements(path)
    text = "\n".join(lines)
    assert found == data
    assert "## Accuracy on each batch at its first measurement" in text
    # The frozen engine: its commit and tag; the gap to the known batches.
    assert (
        "| batch 2 | 2026-11-13 | PyCG | 2bcc0b1000 (no tag) | batch 1, ICSE 2021 set "
        "| P 0.900 / R 0.900 (90/10/10) | P 0.800 / R 0.800 (80/20/20) | -0.100 "
        "| -0.100 | P 0.500 / R 1.000 (1/1/0) | 5 ok |"
    ) in text
    assert "| original PyCG | 8888888888 (baseline-original-engine) |" in text
    assert "| - | P 0.800 / R 0.400 (40/10/60) | - | - |" in text  # no known numbers
    assert (
        "| pyan3 | v2.9.0 | batch 1, ICSE 2021 set | P 0.900 / R 0.900 (90/10/10) "
        "| P 0.500 / R 0.500 (10/10/10), 1 of 5 projects measured | -0.400 | -0.400 "
        "| names no outside callee | - |"
    ) in text
    assert "- batch 2: names matched by string equality" in text
    assert "- batch 2, p, corrected on D (the first measurement stays" in text
    # The table of RQ1 ends with this section, and its data holds the file.
    row = ("PyCG", "abc", {None: tables.missing("not run")})
    built, markdown = rq1.build([row], (3, 13), BATCHES, path)
    assert built["first measurements"] == data
    assert markdown.endswith(text)


def test_the_committed_first_measurements_are_of_four_batches_and_two_engines():
    data, lines = rq1.first_measurements()
    assert [batch["batch"] for batch in data["batches"]] == [2, 3, 4, 5]
    assert [batch["known"] for batch in data["batches"]] == [
        [1],
        [1, 2],
        [1, 2, 3, "icse-2021"],
        [1, 2, 3, 4, "icse-2021"],
    ]
    for batch in data["batches"]:
        tools = [row["tool"] for row in batch["rows"]]
        assert tools[:2] == ["PyCG", "original PyCG"]
        assert len(batch["engine"]["commit"]) == 40
        for row in batch["rows"]:
            assert len(row["batch"]["inside"]) == 3
    # Only the last batch had the prior tools at its first measurement.
    assert [len(batch["rows"]) for batch in data["batches"]] == [2, 2, 2, 10]
    text = "\n".join(lines)
    assert "| batch 2 | 2026-11-13 | PyCG | 2bcc0b1d9a (no tag) | batch 1 |" in text
    assert "P 0.933 / R 0.749 (1366/98/457) | +0.011 | -0.138 |" in text
    assert "| batch 5 | 2026-11-14 | PyCG | 8ef722cadd (freeze-round-3) |" in text
    assert "P 0.902 / R 0.817 (1198/130/268) | -0.051 | -0.102 |" in text
    assert "held" not in text.lower() and "look" not in text.lower()


def test_rq1_scores_one_micro_cohort_and_the_newer_cases_apart(tmp_path):
    tool = run.Tool("fake", "new", fakes.fake_checkout(tmp_path), sys.executable)
    cases = snippets(tmp_path, newer=True)
    (cases / "b" / "new" / "requires-python").write_text("3.12")
    cells = rq1.measure_pycg(
        tool, tmp_path / "work", tmp_path, cases, 60, allow_dirty=True, bound=(3, 11)
    )
    assert cells["micro"]["cases"] == 1 and cells["micro"]["cohort"] == "3.11"
    assert cells[rq1.NEWER]["cases"] == 1
    assert cells[rq1.NEWER]["inside"]["truth"] == 1


def test_rq1_puts_prior_tools_on_the_cohort_or_states_why_not(tmp_path):
    path = prior_tools_file(tmp_path / "prior-tools.json")
    rows = {name: cells for name, _, cells in rq1.prior_rows(path, CURRENT, (3, 13))}
    assert rows["pyan3"]["micro"]["status"] == "measured"
    assert rows["pyan3"][rq1.NEWER] == tables.missing(
        "not run: its micro cases end at Python 3.13"
    )
    rows = {name: cells for name, _, cells in rq1.prior_rows(path, CURRENT, (3, 12))}
    assert rows["pyan3"]["micro"]["reason"] == (
        "needs rerun: its micro cases end at Python 3.13, the cohort at 3.12"
    )


def test_rq1_takes_the_oldest_python_of_its_tools_as_the_cohort(tmp_path):
    path = prior_tools_file(tmp_path / "prior-tools.json")
    assert rq1.cohort(sys.executable, path) == min(sys.version_info[:2], (3, 13))
    assert rq1.cohort(sys.executable, tmp_path / "absent") == sys.version_info[:2]


def test_rq1_states_a_benchmark_without_source_or_without_projects(tmp_path):
    root = tmp_path / "root"
    macro_benchmark(root, checked_out=False)
    tool = run.Tool("fake", "new", fakes.fake_checkout(tmp_path), sys.executable)
    micro = snippets(tmp_path)
    cells = rq1.measure_pycg(tool, tmp_path / "work", root, micro, 60, allow_dirty=True)
    assert cells["macro"] == tables.missing(
        "not measured: the source of demo is not there "
        "(the images hold it: build the inputs image)"
    )
    (root / "macro-benchmark" / "projects.toml").unlink()
    cells = rq1.measure_pycg(tool, tmp_path / "work", root, micro, 60, allow_dirty=True)
    assert cells["macro"] == tables.missing(
        "not measured: no macro-benchmark/projects.toml"
    )


def test_rq1_has_a_row_for_every_tool_and_a_reason_in_every_empty_cell(tmp_path):
    path = prior_tools_file(tmp_path / "prior-tools.json", sets=("micro",))
    raw = dict.fromkeys(rq1.score.EDGE_SETS, COUNTS)
    project = {"status": "ok", "raw": raw}
    cell = tables.measured(raw, failed=[])
    pycg = dict.fromkeys(("micro", rq1.NEWER), cell)
    pycg["macro"] = cell | {"projects": {"demo": project}}
    rows = [("PyCG", "abc", pycg)]
    rows.append(("original PyCG", "-", {None: tables.missing("not run: no checkout")}))
    rows += rq1.prior_rows(path, CURRENT, (3, 13))
    data, markdown = rq1.build(rows, (3, 13), BATCHES)
    assert data["columns"] == ["micro", rq1.NEWER, "batch 1", rq1.ALL]
    assert data["batches"] == {"batch 1": ["demo"]}
    tools = [row["tool"] for row in data["rows"]]
    assert tools == ["PyCG", "original PyCG", "pyan3", "JARVIS", "CPyGraph"] + [
        "HeaderGen (hybrid: imports the analysed modules)",
        "PyAnalyzer",
    ]
    for row in data["rows"]:
        assert list(row["columns"]) == data["columns"]
        for cell in row["columns"].values():
            assert cell["status"] == "measured" or cell["reason"]
    assert data["rows"][0]["columns"]["batch 1"]["inside"]["tp"] == 9
    pyan = data["rows"][2]["columns"]
    assert pyan["micro"]["status"] == "measured"
    assert (
        pyan["batch 1"]
        == pyan[rq1.ALL]
        == tables.missing("not yet run: prior-tools.json has no run on macro")
    )
    assert (
        "| Tool | Version | micro | micro, newer Python | batch 1 | all projects |"
        in (markdown)
    )
    assert data["cohort"] == "the cases up to Python 3.13"
    # The recall against traces has its own table (trace_recall.py).
    assert "results/rq1-trace-recall.md" in markdown
    assert "not yet run: no row in prior-tools.json" in markdown  # HeaderGen
    assert "P 0.900 / R 0.900 (9/1/1)" in markdown


def test_rq1_without_the_prior_tool_file_names_it_in_each_row(tmp_path):
    rows = rq1.prior_rows(tmp_path / "absent.json", CURRENT, (3, 13))
    assert [name for name, _, _ in rows] == list(tables.PRIOR_TOOLS)
    for _, _, cells in rows:
        assert "absent.json" in cells[None]["reason"]


# --------------------------------------------------------------------------
# RQ2
# --------------------------------------------------------------------------


def rq2_inputs(directory, rows=None, limits=None, name="batch 1"):
    """A label file and a limits file: each group, and one limit left out."""
    if rows is None:
        rows = [
            ["p", "p.a", "p.b", "fn", "inside", "C1", "a note"],
            ["p", "p.a", "p.c", "fn", "inside", "C1", "a note"],
            ["q", "q.a", "q.b", "fp", "inside", "C1", "a note"],
            ["q", "q.a", "os.w", "fn", "outside", "D", "a note"],
            ["p", "p.a", "os.x", "fp", "outside", "S", "a note"],
            ["p", "p.a", "os.y", "fn", "outside", "T", "a note"],
            ["p", "p.a", "os.z", "fp", "outside", "R1", "a note"],
            ["p", "p.b", "os.z", "fp", "outside", "R2", "a note"],
            ["q", "q.a", "os.g", "fn", "outside", "G", "a note"],
        ]
    if limits is None:
        limits = {
            "C1": {"group": "stated limit", "name": "Partial", "family": "F-callback"},
            "D": {
                "group": "stated limit",
                "name": "A defect",
                "family": "defect",
                "design": "Open problems",
                "left_out": "2026-11-15",
            },
            "S": {"group": "scorer artefact", "name": "Two names"},
            "T": {"group": "ground-truth doubt", "name": "A doubt"},
            "R1": {"group": "measured rule, not built", "name": "Rule R"},
            "R2": {"group": "measured rule, not built", "name": "Rule R"},
            "G": {"group": "gap", "name": "A gap"},
        }
    directory.mkdir(exist_ok=True)
    engine = {"commit": "abc1234" + "0" * 33, "ground_truth": "the audited truths"}
    labels = {"engine": engine, "batches": {name: rows}}
    (directory / "labels.json").write_text(json.dumps(labels))
    (directory / "limits.json").write_text(json.dumps(limits))
    return directory / "labels.json", directory / "limits.json"


def test_rq2_counts_each_group_and_the_share_under_stated_limits(tmp_path):
    data, markdown = rq2.build(*rq2_inputs(tmp_path))
    cells = {"inside_fn": 2, "inside_fp": 1, "outside_fn": 3, "outside_fp": 3}
    assert data["edges"] == {**cells, "total": 9}
    assert [(g["group"], g["total"], g["share"]) for g in data["groups"]] == [
        ("stated limit", 4, 0.4444),
        ("scorer artefact", 1, 0.1111),
        ("ground-truth doubt", 1, 0.1111),
        ("measured rule, not built", 2, 0.2222),
        ("gap", 1, 0.1111),  # its own row, never under the stated limits
    ]
    assert data["groups"][0]["outside_fn"] == 1
    # One row per limit, the largest first, with its packages.
    first, second = data["stated_limits"]
    assert first == {
        "label": "C1",
        "name": "Partial",
        "family": "F-callback",
        "inside_fn": 2,
        "inside_fp": 1,
        "outside_fn": 0,
        "outside_fp": 0,
        "total": 3,
        "packages": ["p", "q"],
    }
    assert (second["label"], second["total"]) == ("D", 1)
    assert (second["design"], second["left_out"]) == ("Open problems", "2026-11-15")
    # The other groups; a rule of two labels is one row.
    others = [(o["group"], o["name"], o["labels"], o["total"]) for o in data["others"]]
    assert others == [
        ("scorer artefact", "Two names", ["S"], 1),
        ("ground-truth doubt", "A doubt", ["T"], 1),
        ("measured rule, not built", "Rule R", ["R1", "R2"], 2),
        ("gap", "A gap", ["G"], 1),
    ]
    (per_batch,) = data["batches"]
    assert per_batch == {
        "batch": "batch 1",
        **cells,
        "total": 9,
        "groups": {
            "stated limit": 4,
            "scorer artefact": 1,
            "ground-truth doubt": 1,
            "measured rule, not built": 2,
            "gap": 1,
        },
        "share": 0.4444,
    }
    assert "**Under stated limits: 4 of 9 (44.4%).**" in markdown
    assert "| 1 | Partial | C1 | F-callback | 2 | 1 | 0 | 0 | 3 | 2 |" in markdown
    assert "left out on 2026-11-15" in markdown
    assert "| batch 1 | 2 | 1 | 3 | 3 | 9 | 4 | 44.4% | 1 | 1 | 2 | 1 |" in markdown
    assert "| Gaps | 0 | 0 | 1 | 0 | 1 | 11.1% |" in markdown
    assert "| Gaps | A gap | G | 0 | 0 | 1 | 0 | 1 | 1 |" in markdown
    # The engine by its commit, without a tag, and the state of the truths.
    assert "PyCG at `abc1234`, on the audited truths, has 9 false" in markdown


@pytest.mark.parametrize(
    "limits, problem",
    [
        ({}, "the label C1 has no group"),
        ({"C1": {"group": "a limit", "name": "x"}}, "the label C1 has the group"),
    ],
)
def test_rq2_refuses_a_label_without_one_of_the_groups(tmp_path, limits, problem):
    rows = [["p", "p.a", "p.b", "fn", "inside", "C1", ""]]
    with pytest.raises(ValueError, match=problem):
        rq2.build(*rq2_inputs(tmp_path, rows, limits))


@pytest.mark.parametrize(
    "second, problem",
    [
        (["p", "p.a", "p.b", "fp", "inside", "C1", ""], "is labelled twice"),
        (["p", "p.a", "p.c", "tp", "inside", "C1", ""], "neither a false positive"),
        (["p", "p.a", "p.c", "fn", "beside", "C1", ""], "neither inside nor outside"),
    ],
)
def test_rq2_refuses_a_row_that_is_not_one_labelled_edge(tmp_path, second, problem):
    rows = [["p", "p.a", "p.b", "fn", "inside", "C1", ""], second]
    limits = {"C1": {"group": "stated limit", "name": "x", "family": "y"}}
    with pytest.raises(ValueError, match=problem):
        rq2.build(*rq2_inputs(tmp_path, rows, limits))


def test_rq2_refuses_the_labels_of_a_batch_that_the_benchmark_does_not_have(
    tmp_path,
):
    inputs = rq2_inputs(tmp_path, name="batch 9")
    with pytest.raises(ValueError, match="batch 9 is no batch of the benchmark"):
        rq2.build(*inputs)


def _rq2_json(path):
    """A JSON file of RQ2; a key that an object has twice is an error."""

    def once(pairs):
        keys = [key for key, _ in pairs]
        assert len(keys) == len(set(keys)), f"{path.name}: a key is there twice"
        return dict(pairs)

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=once)


def test_the_labels_of_rq2_agree_with_rq1():
    # Per batch, inside and outside: as many labelled false positives and
    # false negatives as PyCG has in the committed table of RQ1.
    data, _ = rq2.build()
    accuracy = json.loads((tables.RESULTS / "rq1-accuracy.json").read_text())
    (pycg,) = [row for row in accuracy["rows"] if row["tool"] == "PyCG"]
    titles = [tables.batch_title(batch) for batch in tables.projects.BATCHES]
    assert [row["batch"] for row in data["batches"]] == titles
    for row in data["batches"]:
        cell = pycg["columns"][row["batch"]]
        assert cell["status"] == "measured" and not cell["failed"], row["batch"]
        for side in ("inside", "outside"):
            for kind in ("fp", "fn"):
                found, expected = row[f"{side}_{kind}"], cell[side][kind]
                assert found == expected, (row["batch"], side, kind)


def test_every_label_of_rq2_maps_to_exactly_one_group():
    labels, limits = _rq2_json(rq2.LABELS), _rq2_json(rq2.LIMITS)
    used = {row[5] for rows in labels["batches"].values() for row in rows}
    assert used == set(limits)  # no label without a group, no group of no edge
    assert {entry["group"] for entry in limits.values()} == set(rq2.GROUPS)
    batches = tables.batches(tables.projects.load_projects())
    projects = {
        tables.batch_title(batch): set(names) for batch, names in batches.items()
    }
    for name, rows in labels["batches"].items():
        assert {row[0] for row in rows} == projects[name], name
        assert rows == sorted(rows), name  # a stable order


def test_the_published_table_of_rq2_is_that_of_the_committed_labels():
    data, markdown = rq2.build()
    results = tables.RESULTS
    published = json.loads((results / "rq2-causes.json").read_text())
    assert published == json.loads(tables.dumps(data))
    assert (results / "rq2-causes.md").read_text(encoding="utf-8") == markdown
    assert data["edges"]["total"] == 3056
    assert {g["group"]: g["total"] for g in data["groups"]} == {
        "stated limit": 2834,
        "scorer artefact": 140,
        "ground-truth doubt": 1,
        "measured rule, not built": 45,
        "gap": 36,
    }
    assert len(data["stated_limits"]) == 44
    assert sum(1 for limit in data["stated_limits"] if "left_out" in limit) == 2
    # The maintainer's wording: false positives and false negatives, never
    # "errors" (an exception's name, `AttributeError`, is no such word).
    assert not re.search(r"\berrors?\b", markdown.lower())


# --------------------------------------------------------------------------
# RQ3
# --------------------------------------------------------------------------


def robustness_result():
    def ok(seconds, mib):
        return {
            "status": "ok",
            "measured": {"wall_seconds": seconds, "peak_rss_mib": mib},
        }

    return {
        "manifest": {"file": "top.json", "top": 4},
        "python": "3.14.0",
        "platform": "linux x86_64",
        "limits": {"seconds": 60, "memory_mib": 1024},
        "tools": {"PyCG": {"commit": "b" * 40}},
        "packages": {
            "a": {"rank": 1, "runs": {"PyCG": ok(1.0, 100)}},
            "b": {"rank": 2, "runs": {"PyCG": ok(3.0, 300)}},
            "c": {"rank": 3, "runs": {"PyCG": {"status": "timeout", "measured": {}}}},
            "d": {"rank": 4, "reason": "no wheel"},
        },
    }


def dypybench_result():
    result = robustness_result()
    result["manifest"] = {"file": "dypybench.json", "projects": 2}
    result["packages"] = {
        "x": {
            "rank": 1,
            "runs": {"PyCG": robustness_result()["packages"]["b"]["runs"]["PyCG"]},
        },
        "akshare": {"rank": 2, "reason": "not recoverable"},
    }
    return result


def test_rq3_summarises_each_tool_over_the_corpus_and_each_half():
    cell = rq3.summary([robustness_result()], "PyCG")
    assert (cell["packages"], cell["with_target"], cell["finished"]) == (4, 3, 2)
    assert cell["share"] == 0.6667
    assert cell["failures"] == {"timeout": 1}
    assert cell["wall_seconds"] == {"median": 2.0, "p90": 3.0, "max": 3.0}
    parts = {"PyPI top 100": robustness_result(), "DyPyBench": dypybench_result()}
    not_run = "not run: no results/tools/pyan3/rq3.json"
    rows = [("PyCG", "PyCG"), ("pyan3", tables.missing(not_run))]
    data, markdown = rq3.build(parts, rows)
    pycg = data["rows"][0]
    assert pycg["version"] == "b" * 10
    assert list(pycg["corpus"]) == ["all", "PyPI top 100", "DyPyBench"]
    assert pycg["corpus"]["all"]["packages"] == 6
    assert data["rows"][1]["corpus"] == {"all": tables.missing(not_run)}
    assert "| PyCG | bbbbbbbbbb | all | 6 | 4 | 3 | 75.0% | timeout 1 |" in markdown
    assert "| PyCG | bbbbbbbbbb | PyPI top 100 | 4 | 3 | 2 | 66.7% |" in markdown
    assert "| PyCG | bbbbbbbbbb | DyPyBench | 2 | 1 | 1 | 100.0% | - |" in markdown
    assert f"| pyan3 | - | all | {not_run} |" in markdown
    # A package of any part without a run is a stated row with its reason.
    assert data["stated packages"] == [
        {"part": "PyPI top 100", "name": "d", "reason": "no wheel"},
        {"part": "DyPyBench", "name": "akshare", "reason": "not recoverable"},
    ]
    assert "| DyPyBench | akshare | not recoverable |" in markdown
    assert data["corpus"] == {
        "PyPI top 100": {"file": "top.json", "top": 4},
        "DyPyBench": {"file": "dypybench.json", "projects": 2},
    }


def test_rq3_reads_the_three_parts_of_the_corpus_and_pilots_the_first_two():
    assert [(label, path.name, sub) for label, path, sub in rq3.PARTS] == [
        ("PyPI top 100", "pypi-top-100.json", "work"),
        ("DyPyBench", "dypybench.json", "dypybench"),
        ("Large projects", "large-projects.json", "large"),
    ]
    assert rq3.PILOT_PARTS == rq3.PARTS[:2]
    assert all(path.is_file() for _, path, _ in rq3.PARTS)


def test_the_published_table_of_rq3_is_that_of_the_committed_runs():
    """rq3-scale.md and .json are what the committed reports of PyCG's run
    (robustness-*.json) and the committed runs of the prior tools give,
    with every tool's row measured: a stored run of another pin, manifest
    or K would be a "needs rerun" row."""
    results = tables.RESULTS
    parts = {
        label: json.loads((results / f"robustness-{path.stem}.json").read_text())
        for label, path, _ in rq3.PARTS
    }
    rows = [("PyCG", "PyCG"), ("original PyCG", "original-PyCG")]
    rows += rq3.tool_rows(parts, rq3.STEM)
    stopped = any(rq3.cleanup_failed(part) for part in parts.values())
    data, markdown = rq3.build(parts, rows, stopped)
    published = json.loads((results / "rq3-scale.json").read_text())
    assert published == json.loads(tables.dumps(data))
    assert (results / "rq3-scale.md").read_text(encoding="utf-8") == markdown
    measured = {
        row["tool"]: row["corpus"]["all"]["with_target"]
        for row in data["rows"]
        if row["corpus"]["all"]["status"] == "measured"
    }
    assert set(measured) == {
        "PyCG", "original PyCG", "pyan3", "JARVIS", "CPyGraph", "PyAnalyzer"
    }  # fmt: skip
    assert set(measured.values()) == {159}


def test_the_rq3_service_copies_the_report_of_every_part():
    entry = fakes.load("container/question_entry")
    reports = {
        f"robustness-{path.stem}.{suffix}"
        for _, path, _ in rq3.PARTS
        for suffix in ("json", "md")
    }
    assert set(entry.OUTPUTS["rq3"]) == {"rq3-scale.json", "rq3-scale.md"} | reports


def pilot_half(tmp_path, label, names, empty=()):
    """A part of pure wheels; the wheels of `empty` hold no Python module,
    so they have no analysis target."""
    work, cache = tmp_path / "corpus" / label, tmp_path / "corpus" / "cache"
    rows, recorded = [], {}
    for rank, name in enumerate([*names, *empty], start=1):
        file = f"{name}-1.0-py3-none-any.whl"
        files = {f"{name}/__init__.py": "x = 1\n"}
        if name in empty:
            files = {f"{name}-1.0.dist-info/METADATA": ""}
        fields, recorded[name] = fakes.pinned_archive(cache, work, name, files, file)
        rows.append(
            {
                "name": name,
                "rank": rank,
                "version": "1.0",
                "kind": "pure wheel",
                "url": "https://files.pythonhosted.org/packages/" + file,
                **fields,
            }
        )
    rows.append({"name": f"{label}-gone", "rank": len(rows) + 1, "reason": "gone"})
    (work / "fetch.json").write_text(json.dumps(recorded))
    manifest = tmp_path / f"{label}.json"
    manifest.write_text(
        json.dumps({"top": len(rows), "rank_list": {"file": "t", "last_update": "x"},
                    "rows": rows})
    )  # fmt: skip
    return manifest


def pilot_parts(tmp_path, monkeypatch):
    top = pilot_half(tmp_path, "top", [f"t{i}" for i in range(9)], empty=["t-empty"])
    dy = pilot_half(tmp_path, "dy", [f"d{i}" for i in range(7)], empty=["d-empty"])
    parts = (("PyPI top 100", top, "top"), ("DyPyBench", dy, "dy"))
    monkeypatch.setattr(rq3, "PARTS", parts)
    monkeypatch.setattr(rq3, "PILOT_PARTS", parts)
    return parts


def test_rq3_pilot_draws_five_packages_with_a_target_from_each_half(
    tmp_path, monkeypatch
):
    parts = pilot_parts(tmp_path, monkeypatch)
    for label, path, directory in parts:
        manifest = json.loads(path.read_text())
        work = tmp_path / "corpus" / directory
        first = rq3.pilot_sample(manifest, work)
        assert first == rq3.pilot_sample(manifest, work)  # deterministic
        sample, skipped = first
        assert len(sample) == rq3.PILOT_SIZE == 5, label
        assert len(set(sample)) == 5
        names = {r["name"] for r in manifest["rows"] if "file" in r}
        assert set(sample) <= names - {"t-empty", "d-empty"}
        assert all(s["name"] in {"t-empty", "d-empty"} for s in skipped)
    assert isinstance(rq3.PILOT_SEED, int)


def test_a_tool_that_imports_the_analysed_code_never_runs_on_an_unscanned_corpus():
    rows = dict(rq3.tool_rows({}, rq3.STEM, Path("/nonexistent")))
    headergen = "HeaderGen (hybrid: imports the analysed modules)"
    assert rows[headergen] == tables.missing(tables.UNSCANNED)
    # One row per tool: its headline row (the first of its variants).
    assert list(rows) == [
        rq3.tool_job.rows(tool)[0][0] for tool in rq3.tool_job._build().TOOLS
    ]


@pytest.mark.parametrize("question", ["rq3", "rq3-pilot"])
def test_rq3_runs_the_headline_row_of_a_tool_only(question, capsys):
    tool_job = rq3.tool_job
    assert tool_job.headline("jarvis") == tool_job.rows("jarvis")[:1]
    with pytest.raises(SystemExit):
        tool_job.main(["jarvis", question, "--variant", "1"])
    assert "runs the headline row of a tool only" in capsys.readouterr().err


def stored_rq3(directory, row, parts, sha256, pin=None):
    """A result file of tool_job.rq3: one run of `row` per package with a
    run of PyCG."""
    stored = {}
    for label, part in parts.items():
        packages = {}
        for name, package in part["packages"].items():
            packages[name] = {k: v for k, v in package.items() if k != "runs"}
            if "runs" in package:
                run = {
                    "status": "ok",
                    "measured": {"wall_seconds": 9.0, "peak_rss_mib": 900},
                }
                packages[name]["runs"] = {row: run}
        stored[label] = {"sha256": sha256, "packages": packages}
    path = directory / "pyan3" / "rq3.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tool_job = rq3.tool_job
    adapter = tool_job._build().load_adapter("pyan")
    facts = tool_job.facts("pyan", adapter, tool_job.LIMITS["rq3"])
    facts["pin"] = pin or facts["pin"]
    data = {"row": row, "settings": dict(tool_job.rows("pyan"))[row]}
    path.write_text(json.dumps(data | {"tool": facts, "halves": stored}))
    return path


def test_rq3_compares_a_tool_only_at_the_k_of_pycg(tmp_path):
    parts = {"PyPI top 100": robustness_result(), "DyPyBench": dypybench_result()}
    for part in parts.values():
        part["manifest"]["sha256"] = "1" * 64
    stored_rq3(tmp_path, "pyan3", parts, "1" * 64)  # one job at a time
    assert ("pyan3", "pyan3") in rq3.tool_rows(parts, rq3.STEM, tmp_path)
    for part in parts.values():
        part["measured"] = {"jobs": 3}
    rows = dict(rq3.tool_rows(parts, rq3.STEM, tmp_path))
    assert rows["pyan3"]["reason"] == (
        "needs rerun: it ran 1 jobs at a time, PyCG 3 (--jobs)"
    )
    _, markdown = rq3.build(parts, [("PyCG", "PyCG")])
    assert ", 3 runs at a time; limits" in markdown


def test_rq3_reads_the_prior_tools_runs_where_they_are(tmp_path):
    parts = {"PyPI top 100": robustness_result(), "DyPyBench": dypybench_result()}
    for part in parts.values():
        part["manifest"]["sha256"] = "1" * 64
    rows = dict(rq3.tool_rows(parts, rq3.STEM, tmp_path))
    assert rows["pyan3"]["reason"] == (
        "not run: no results/tools/pyan3/rq3.json (docker compose -f "
        "evaluation/compose.yaml run --rm pyan rq3)"
    )
    assert rows["JARVIS"]["status"] == "missing"
    assert "JARVIS --precision" not in rows  # RQ3 runs the headline row only
    stored_rq3(tmp_path, "pyan3", parts, "1" * 64)
    rows = rq3.tool_rows(parts, rq3.STEM, tmp_path)
    assert ("pyan3", "pyan3") in rows
    data, markdown = rq3.build(parts, [("PyCG", "PyCG"), *rows])
    assert "| pyan3 | 8e8bd90646 | all | 6 | 4 | 4 | 100.0% | - |" in markdown
    # A run on another manifest of the corpus is never mixed in.
    parts = {"PyPI top 100": robustness_result(), "DyPyBench": dypybench_result()}
    for part in parts.values():
        part["manifest"]["sha256"] = "2" * 64
    rows = dict(rq3.tool_rows(parts, rq3.STEM, tmp_path))
    assert rows["pyan3"]["reason"].startswith("needs rerun: it ran on another")
    assert "pyan3" not in parts["DyPyBench"]["packages"]["x"]["runs"]
    # A run of another version of the tool, or of fewer packages, neither.
    parts = {"PyPI top 100": robustness_result(), "DyPyBench": dypybench_result()}
    for part in parts.values():
        part["manifest"]["sha256"] = "1" * 64
    path = tmp_path / "pyan3" / "rq3.json"
    path.unlink()
    stored_rq3(tmp_path, "pyan3", parts, "1" * 64, pin={"commit": "0" * 40})
    rows = dict(rq3.tool_rows(parts, rq3.STEM, tmp_path))
    assert rows["pyan3"]["reason"] == "needs rerun: it ran another version of the tool"
    path.unlink()
    stored_rq3(tmp_path, "pyan3", parts, "1" * 64)
    data = json.loads(path.read_text())
    del data["halves"]["DyPyBench"]["packages"]["x"]["runs"]
    path.write_text(json.dumps(data))
    rows = dict(rq3.tool_rows(parts, rq3.STEM, tmp_path))
    assert rows["pyan3"]["reason"] == (
        "needs rerun: it ran on other packages of the corpus"
    )


def test_a_prior_tool_that_names_no_outside_callee_says_so_in_its_outside_cells(
    tmp_path,
):
    path = prior_tools_file(tmp_path / "prior-tools.json")
    data = json.loads(path.read_text())
    data["tools"]["pyan3"]["outside"] = "it names no callee outside the package"
    path.write_text(json.dumps(data))
    cell = tables.prior_tool_cells(path, CURRENT, TABLE)["pyan3"]["macro"]
    assert cell["status"] == "measured"
    assert tables.cell_text(cell, "inside").startswith("P 0.900")
    assert tables.cell_text(cell, "outside") == (
        "not measured: it names no callee outside the package"
    )


def test_the_inputs_of_a_benchmark_are_its_files_and_nothing_written_by_python(
    tmp_path,
):
    set_dir = tmp_path / "macro-benchmark"
    (set_dir / "plugins").mkdir(parents=True)
    (set_dir / "plugins" / "m.py").write_text("x = 1\n")
    first = tables.inputs_digest(set_dir)
    (set_dir / "plugins" / "__pycache__").mkdir()
    (set_dir / "plugins" / "__pycache__" / "m.pyc").write_bytes(b"x")
    assert tables.inputs_digest(set_dir) == first
    # A copy of the sources for reading is no input: projects.toml pins them.
    (set_dir / "projects" / "a").mkdir(parents=True)
    (set_dir / "projects" / "a" / "m.py").write_text("x = 1\n")
    assert tables.inputs_digest(set_dir) == first
    (set_dir / "plugins" / "m.py").write_text("x = 2\n")
    assert tables.inputs_digest(set_dir) != first
