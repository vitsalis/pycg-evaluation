"""Behaviour tests for evaluation/rq1-accuracy/trace_recall.py: recall
against DyPyBench's traces of real executions.

The mapping tests come from branch eval-dypybench (tests/test_dypybench.py).
The traces zip and the projects are written to tmp_path; the analysis is
the fake PyCG checkout of tests/evaluation_fakes.py, run in real child
processes. Nothing touches the network.
"""

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path
from textwrap import dedent

import pytest

if sys.version_info < (3, 14):
    pytest.skip(
        "the scorer loads macro-benchmark/check.py, which needs Python 3.14",
        allow_module_level=True,
    )

from evaluation_fakes import fake_checkout, git, load, pinned_archive  # noqa: E402
from test_annotation_lambdas import ANNOTATIONS  # noqa: E402

trace_recall = load("rq1-accuracy/trace_recall")
score = trace_recall.score
tables = trace_recall.tables
NAMES = score.Names().for_packages(("pkg",))


def write(root, files):
    for relpath, text in files.items():
        path = Path(root) / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(text))


def source(tmp_path, files, source_root=""):
    """A Source of the files {relpath: text}, written to tmp_path/clone."""
    clone = tmp_path / "clone"
    write(clone, files)
    return trace_recall.Source(list(files), clone, source_root)


def mapper(tmp_path, files, source_root="", root=""):
    return trace_recall.Mapper(source(tmp_path, files, source_root), 7, root)


P = ".DyPyBench.temp.project7."


# --------------------------------------------------------------------------
# Mapping callers
# --------------------------------------------------------------------------

MODULE = {
    "pkg/__init__.py": "from .mod import helper as exported\n",
    "pkg/mod.py": """\
        import os

        def helper():
            return os.getcwd()

        def outer():
            def inner():
                return 1
            return inner

        class C:
            def m(self):
                def nested():
                    return 2
                return nested

            def helper(self):
                pass

        def twice():
            def wrapper():
                pass

        def again():
            def wrapper():
                pass
        """,
}


@pytest.mark.parametrize(
    "caller, expected",
    [
        (P + "pkg.mod", ("pkg.mod", "caller: module")),
        (P + "pkg.__init__", ("pkg", "caller: module")),
        (P + "pkg.mod.helper", ("pkg.mod.helper", "caller: function")),
        # DynaPyt names a nested function by its own name only.
        (P + "pkg.mod.inner", ("pkg.mod.outer.inner", "caller: function")),
        (P + "pkg.mod.C.m", ("pkg.mod.C.m", "caller: method")),
        # A function and a method of one name are told apart by the class.
        (P + "pkg.mod.C.helper", ("pkg.mod.C.helper", "caller: method")),
        # ... and by its nearest class, not by the method around it.
        (P + "pkg.mod.C.nested", ("pkg.mod.C.m.nested", "caller: method")),
        (P + "pkg.mod.wrapper", (None, "caller: several functions")),
        (P + "pkg.mod.missing", (None, "caller: not a function of the file")),
        (P + "pkg.mod.helper.x", (None, "caller: not a function of the file")),
        (P + "pkg.other.f", (None, "caller: file not in the commit")),
        (
            ".DyPyBench.temp.project8.pkg.mod.helper",
            (None, "caller: no project prefix"),
        ),
        ("pkg.mod.helper", (None, "caller: no project prefix")),
    ],
)
def test_callers_are_mapped_through_the_files_of_the_project(
    tmp_path, caller, expected
):
    assert mapper(tmp_path, MODULE).caller(caller) == expected


def test_caller_names_are_relative_to_the_analysis_root(tmp_path):
    files = {"src/pkg/__init__.py": "", "src/pkg/mod.py": "def f():\n    pass\n"}
    repository = mapper(tmp_path, files, source_root="src")
    assert repository.caller(P + "src.pkg.mod.f") == (
        "src.pkg.mod.f",
        "caller: function",
    )
    in_source = trace_recall.Mapper(repository.source, 7, "src")
    assert in_source.caller(P + "src.pkg.mod.f") == ("pkg.mod.f", "caller: function")


def test_a_caller_outside_the_analysis_root_is_counted_apart(tmp_path):
    files = {"src/pkg/__init__.py": "", "tests/test_a.py": "def test():\n    pass\n"}
    m = mapper(tmp_path, files, source_root="src", root="src")
    assert m.caller(P + "tests.test_a.test") == (
        None,
        "caller: outside the analysis root",
    )


def test_a_caller_in_a_file_that_does_not_parse_is_counted_apart(tmp_path):
    m = mapper(tmp_path, {"old.py": "print 'python 2'\n"})
    assert m.caller(P + "old.f") == (None, "caller: file does not parse")
    assert m.caller(P + "old") == ("old", "caller: module")


# --------------------------------------------------------------------------
# Mapping callees
# --------------------------------------------------------------------------

CLASSES = {
    "pkg/__init__.py": "from pkg.base import Base\n",
    "pkg/base.py": """\
        class Base:
            def __init__(self):
                pass
        """,
    "pkg/mod.py": """\
        import dataclasses
        import pkg.base
        from . import base
        from .base import Base as Renamed

        class Own:
            def __init__(self):
                pass

        class Child(Renamed):
            pass

        class Grandchild(Child):
            pass

        class ThroughModule(base.Base):
            pass

        class ThroughImport(pkg.base.Base):
            pass

        class Plain:
            pass

        class Error(Exception):
            pass

        @dataclasses.dataclass
        class Data:
            x: int

        def f():
            return lambda: 1

        def g():
            a = lambda: 1
            b = lambda: 2
            return a, b

        def h():
            return [lambda: 3 for _ in range(2)]
        """,
}


@pytest.mark.parametrize(
    "callee, expected",
    [
        ("pkg.mod.f", ("pkg.mod.f", "inside: function", True)),
        ("pkg.mod.Own", ("pkg.mod.Own.__init__", "inside: class, own __init__", True)),
        (
            "pkg.mod.Child",
            ("pkg.base.Base.__init__", "inside: class, inherited __init__", True),
        ),
        (
            "pkg.mod.Grandchild",
            ("pkg.base.Base.__init__", "inside: class, inherited __init__", True),
        ),
        (
            "pkg.mod.ThroughModule",
            ("pkg.base.Base.__init__", "inside: class, inherited __init__", True),
        ),
        (
            "pkg.mod.ThroughImport",
            ("pkg.base.Base.__init__", "inside: class, inherited __init__", True),
        ),
        ("pkg.mod.Plain", (None, "callee: class without __init__", True)),
        ("pkg.mod.Error", (None, "callee: constructor not in the project", True)),
        ("pkg.mod.Data", (None, "class: decorator", True)),
        (
            "pkg.mod.f.<locals>.<lambda>",
            ("pkg.mod.f.<lambda1>", "inside: lambda", True),
        ),
        ("pkg.mod.g.<locals>.<lambda>", (None, "callee: one of several lambdas", True)),
        # A comprehension is not a scope of our names.
        (
            "pkg.mod.h.<locals>.<listcomp>.<lambda>",
            ("pkg.mod.h.<lambda1>", "inside: lambda", True),
        ),
        ("pkg.Base", ("pkg.base.Base.__init__", "inside: class, own __init__", True)),
        ("pkg.mod.namedtuple_made", (None, "callee: not a definition", True)),
        ("pkg.mod.f.missing", (None, "callee: not a definition", True)),
    ],
)
def test_callees_inside_the_project(tmp_path, callee, expected):
    assert mapper(tmp_path, CLASSES).callee(callee) == expected


def test_a_reexported_function_maps_to_its_definition(tmp_path):
    m = mapper(tmp_path, MODULE)
    assert m.callee("pkg.exported") == ("pkg.mod.helper", "inside: re-export", True)


def test_nested_functions_lose_locals(tmp_path):
    m = mapper(tmp_path, MODULE)
    assert m.callee("pkg.mod.outer.<locals>.inner") == (
        "pkg.mod.outer.inner",
        "inside: function",
        True,
    )


@pytest.mark.parametrize(
    "callee, expected",
    [
        ("builtins.len", ("<builtin>.len", "outside: builtins", False)),
        ("str.join", ("<builtin>.str.join", "outside: builtin type", False)),
        (
            "Exception.__init__",
            ("<builtin>.Exception.__init__", "outside: builtin type", False),
        ),
        ("os.path.join", ("os.path.join", "outside: as written", False)),
        (
            "contextlib.contextmanager.<locals>.helper",
            ("contextlib.contextmanager.helper", "outside: as written", False),
        ),
        ("PIL.Image.open", ("PIL.Image.open", "outside: as written", False)),
        ("BufferedReader.read", (None, "callee: no module", False)),
        ("run_local_topology", (None, "callee: no module", False)),
        ("<MagicMock name='run' id='1402'>", (None, "callee: not a name", False)),
        ("#00ff00", (None, "callee: not a name", False)),
        ("('1', '3')", (None, "callee: not a name", False)),
        (" hello", (None, "callee: not a name", False)),
    ],
)
def test_callees_outside_the_project(tmp_path, callee, expected):
    assert mapper(tmp_path, MODULE).callee(callee) == expected


def test_a_src_layout_maps_runtime_names_under_the_source_root(tmp_path):
    files = {
        "src/pkg/__init__.py": "",
        "src/pkg/mod.py": "def f():\n    pass\n",
        "tests/pkg/__init__.py": "",
        "tests/pkg/mod.py": "def f():\n    pass\n",
    }
    m = mapper(tmp_path, files, source_root="src")
    assert m.callee("pkg.mod.f") == ("src.pkg.mod.f", "inside: function", True)
    in_source = trace_recall.Mapper(m.source, 7, "src")
    assert in_source.callee("pkg.mod.f") == ("pkg.mod.f", "inside: function", True)


def test_a_module_is_found_through_directories_without_packages(tmp_path):
    # PythonRobotics: tests import scripts as PathPlanning.AStar.a_star.
    files = {"Planning/AStar/a_star.py": "def plan(): pass\n"}
    m = mapper(tmp_path, files)
    assert m.callee("Planning.AStar.a_star.plan") == (
        "Planning.AStar.a_star.plan",
        "inside: function",
        True,
    )
    assert m.module_rules["Planning.AStar.a_star.plan"] == "module: source root"
    assert m.callee("a_star.plan")[1] == "outside: as written"


def test_a_module_outside_the_source_root_is_found_from_the_repository_root(
    tmp_path,
):
    files = {"src/pkg/__init__.py": "", "tests/helpers.py": "def make(): pass\n"}
    m = mapper(tmp_path, files, source_root="src")
    assert m.callee("tests.helpers.make") == (
        "tests.helpers.make",
        "inside: function",
        True,
    )
    assert m.module_rules["tests.helpers.make"] == "module: repository root"


def test_a_file_whose_import_name_differs_is_not_the_module(tmp_path):
    # black: tests/data/simple_cases/collections.py is not the standard library's
    files = {"src/black/__init__.py": "", "tests/data/collections.py": "x = 1\n"}
    m = mapper(tmp_path, files, source_root="src")
    assert m.callee("collections.abc.Mapping.get") == (
        "collections.abc.Mapping.get",
        "outside: as written",
        False,
    )


def test_a_project_package_named_builtins_does_not_shadow_the_builtins(tmp_path):
    # python-future ships src/builtins/, a backport for Python 2.
    files = {"src/builtins/__init__.py": "def len(x): pass\n"}
    m = mapper(tmp_path, files, source_root="src")
    assert m.callee("builtins.len") == ("<builtin>.len", "outside: builtins", False)


def test_a_package_comes_before_a_module_file_of_its_name(tmp_path):
    files = {"pkg/__init__.py": "def f(): pass\n", "pkg.py": "def g(): pass\n"}
    m = mapper(tmp_path, files)
    assert m.callee("pkg.f") == ("pkg.f", "inside: function", True)
    assert m.callee("pkg.g") == (None, "callee: not a definition", True)


def test_a_module_inside_a_package_is_not_found_by_its_short_name(tmp_path):
    files = {"pkg/__init__.py": "", "pkg/json.py": "def loads(): pass\n"}
    m = mapper(tmp_path, files)
    assert m.callee("json.loads") == ("json.loads", "outside: as written", False)


def test_a_callee_outside_the_analysis_root_is_counted_apart(tmp_path):
    files = {"src/pkg/__init__.py": "", "tests/helpers.py": "def make(): pass\n"}
    m = mapper(tmp_path, files, source_root="src", root="src")
    assert m.callee("tests.helpers.make") == (
        None,
        "callee: outside the analysis root",
        True,
    )


# --------------------------------------------------------------------------
# Whole traces
# --------------------------------------------------------------------------


def test_unmapped_edges_are_counted_apart_by_reason_and_never_as_edges(tmp_path):
    trace = {
        P + "pkg.mod.helper": [
            "os.getcwd",
            "os.getcwd",
            "pkg.mod.outer",
            "<Mock id=1>",
        ],
        P + "pkg.mod.wrapper": ["pkg.mod.helper"],
        P + "pkg.gone.f": ["pkg.mod.helper"],
    }
    result = trace_recall.map_trace(trace, mapper(tmp_path, MODULE))
    assert result["edges"] == 5  # distinct raw pairs
    assert result["inside"] == {("pkg.mod.helper", "pkg.mod.outer")}
    assert result["outside"] == {("pkg.mod.helper", "os.getcwd")}
    assert result["unmapped"] == {
        "callee: not a name": 1,
        "caller: several functions": 1,
        "caller: file not in the commit": 1,
    }
    assert result["rules"]["caller: function"] == 2


def test_recall_counts_found_edges_and_the_nodes_of_the_static_graph(tmp_path):
    trace = {
        P + "pkg.mod.helper": ["os.getcwd", "pkg.mod.outer"],
        P + "pkg.mod": ["pkg.mod.C"],
    }
    mapped = trace_recall.map_trace(trace, mapper(tmp_path, MODULE))
    graph = {"pkg.mod.helper": ["pkg.mod.outer"], "pkg.mod": ["os.getcwd"]}
    counts = trace_recall.compare(mapped, graph, NAMES)
    assert (counts["inside"], counts["inside_found"]) == (1, 1)
    assert (counts["all"], counts["all_found"]) == (2, 1)
    assert (counts["callers"], counts["callers_found"]) == (1, 1)
    assert (counts["callees"], counts["callees_found"]) == (2, 2)
    assert counts["unmapped"] == 1  # pkg.mod.C: object's __init__


def test_a_failed_run_finds_no_edge(tmp_path):
    trace = {P + "pkg.mod.helper": ["os.getcwd", "pkg.mod.outer"]}
    mapped = trace_recall.map_trace(trace, mapper(tmp_path, MODULE))
    counts = trace_recall.compare(mapped, None, NAMES)
    assert (counts["inside"], counts["inside_found"]) == (1, 0)
    assert (counts["all"], counts["all_found"]) == (2, 0)
    assert counts["callers_found"] == counts["callees_found"] == 0


def test_outside_callees_go_through_the_table_of_names_of_one_function(tmp_path):
    # The trace names the defining object; a static tool may name the
    # subclass's attribute: one function, one callee, in both graphs.
    trace = {P + "pkg.mod.helper": ["collections.abc.Mapping.get"]}
    mapped = trace_recall.map_trace(trace, mapper(tmp_path, MODULE))
    graph = {"pkg.mod.helper": ["os._Environ.get"]}
    counts = trace_recall.compare(mapped, graph, NAMES)
    assert (counts["all"], counts["all_found"]) == (1, 1)
    plain = trace_recall.compare(mapped, graph, score.Names())
    assert plain["all_found"] == 0  # without the table they differ


def test_the_files_of_a_tree_are_its_regular_python_files(tmp_path):
    write(tmp_path / "t", {"pkg/__init__.py": "", "pkg/a.py": "", "README.md": ""})
    (tmp_path / "t" / "pkg" / "link.py").symlink_to(tmp_path / "t" / "pkg" / "a.py")
    found = trace_recall.tree_source(tmp_path / "t", "")
    assert found.files == ["pkg/__init__.py", "pkg/a.py"]


# --------------------------------------------------------------------------
# The reference: DyPyBench's traces
# --------------------------------------------------------------------------


def traces_zip(traces, extra=None):
    """The bytes of a zip like DynaPyt_callgraphs.zip: {number: trace}. The
    same bytes on every call: the members have a fixed time."""
    members = {
        f"DynaPyt_output/dynapyt_{number}.json": json.dumps(trace)
        for number, trace in traces.items()
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in {**members, **(extra or {})}.items():
            archive.writestr(zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0)), content)
    return buffer.getvalue()


def pin(monkeypatch, data):
    monkeypatch.setattr(trace_recall, "TRACES_SHA256", hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(trace_recall, "TRACES_SIZE", len(data))


TRACES = {
    7: {P + "main": ["main.a", "main.b", "os.getcwd", "<Mock id=1>"]},
    9: {P + "x.f": ["x.g", "x.g"], P + "x.g": ["builtins.len"]},
}


def test_the_traces_are_read_from_the_pinned_zip_without_unpacking(
    tmp_path, monkeypatch
):
    duplicate = json.dumps(TRACES[7])
    data = traces_zip(TRACES, {"DynaPyt_output/dynapyt_.json": duplicate})
    pin(monkeypatch, data)
    path = tmp_path / "traces.zip"
    path.write_bytes(data)
    traces = trace_recall.read_traces(path)
    assert traces == TRACES  # numbered traces only, not the duplicate
    assert trace_recall.reference(traces) == {
        "traces": 2,
        "edges": 6,
        "smallest": 2,
        "largest": 4,
    }


def test_traces_with_another_hash_are_refused(tmp_path, monkeypatch):
    data = traces_zip(TRACES)
    pin(monkeypatch, data)
    path = tmp_path / "traces.zip"
    path.write_bytes(data + b"x")
    with pytest.raises(trace_recall.TracesRefused, match="SHA-256"):
        trace_recall.read_traces(path)


def test_a_fetched_zip_with_another_hash_is_not_kept(tmp_path, monkeypatch):
    pin(monkeypatch, traces_zip(TRACES))

    def download(url, path, size):
        Path(path).write_bytes(b"tampered")

    with pytest.raises(trace_recall.TracesRefused):
        trace_recall.fetch(tmp_path, download)
    assert not (tmp_path / trace_recall.TRACES_FILE).exists()
    assert list((tmp_path / trace_recall.TRACES_FILE).parent.iterdir()) == []


def test_the_published_manifest_names_the_trace_of_each_project():
    rows = {r["name"]: r for r in trace_recall.read_corpus()["rows"]}
    assert rows["grab"]["trace"] == 1
    assert rows["blinker"]["trace"] == 35  # numbered by the list of 58
    assert rows["structlog"]["trace"] == 58
    assert "trace" not in rows["python-diskcache"]
    assert "file" not in rows["akshare"]
    assert sum(1 for r in rows.values() if "trace" in r) == 48


# --------------------------------------------------------------------------
# The table
# --------------------------------------------------------------------------

COMMIT = "c0ffee" + "0" * 34


def corpus(tmp_path, monkeypatch, behaviour="ok"):
    """CORPUS_WORK with one traced project (demo, trace 7, whose BEHAVIOUR
    tells the fake tool what to do), one without a trace and one that is
    not recoverable; and the traces zip."""
    work = tmp_path / "corpus"
    top = f"demo-{COMMIT}"
    main = {f"{top}/main.py": "def a():\n    pass\n\ndef b():\n    pass\n"}
    demo_files = {**main, f"{top}/BEHAVIOUR": behaviour}
    demo_fields, demo_record = pinned_archive(
        work / "cache", work / "dypybench", "demo", demo_files, f"demo-{COMMIT}.tar.gz"
    )
    quiet_fields, quiet_record = pinned_archive(
        work / "cache", work / "dypybench", "quiet", {f"{top}/main.py": "x = 1\n"},
        f"quiet-{COMMIT}.tar.gz",
    )  # fmt: skip
    demo = {
        "name": "demo",
        "rank": 1,
        "repository": "https://github.com/owner/demo",
        "version": COMMIT,
        "kind": "git archive",
        "commit": COMMIT,
        "url": f"https://codeload.github.com/owner/demo/tar.gz/{COMMIT}",
        **demo_fields,
        "root": "",
        "modules": ["main"],
        "trace": 7,
    }
    untraced = dict(demo, name="quiet", rank=2, **quiet_fields)
    del untraced["trace"]
    gone = {"name": "gone", "rank": 3, "repository": "x", "reason": "not recoverable"}
    manifest = tmp_path / "dypybench.json"
    manifest.write_text(json.dumps({"rows": [demo, untraced, gone]}))
    monkeypatch.setattr(trace_recall, "CORPUS", manifest)
    data = traces_zip(TRACES)
    pin(monkeypatch, data)
    (work / trace_recall.TRACES_FILE).parent.mkdir(parents=True)
    (work / trace_recall.TRACES_FILE).write_bytes(data)
    fetched = {"demo": demo_record, "quiet": quiet_record}
    (work / "dypybench" / "fetch.json").write_text(json.dumps(fetched))
    return work


def committed(tmp_path):
    checkout = fake_checkout(tmp_path / "tool")
    git(checkout, "init", "-q")
    git(checkout, "add", ".")
    git(checkout, "commit", "-q", "-m", "fake")
    return checkout


def run_table(tmp_path, monkeypatch, *extra):
    work = corpus(tmp_path, monkeypatch)
    argv = ["run", "--checkout", str(committed(tmp_path)), "--work", str(work)]
    argv += ["--graphs", str(tmp_path / "graphs")]
    argv += ["--results", str(tmp_path / "results"), *extra]
    assert trace_recall.main(argv) == 0
    results = tmp_path / "results"
    data = json.loads((results / "rq1-trace-recall.json").read_text())
    return data, (results / "rq1-trace-recall.md").read_text()


def test_the_table_scores_each_traced_project_and_states_the_others(
    tmp_path, monkeypatch
):
    data, markdown = run_table(tmp_path, monkeypatch)
    demo = data["projects"]["demo"]
    assert demo["trace"] == 7
    assert demo["unmapped"] == {"callee: not a name": 1}
    run = demo["runs"]["PyCG"]
    assert run["status"] == "ok"
    assert (run["inside"], run["inside_found"]) == (2, 2)
    assert (run["all"], run["all_found"]) == (3, 2)
    assert data["projects"]["quiet"] == {"rank": 2, "reason": trace_recall.NO_TRACE}
    assert data["projects"]["gone"] == {"rank": 3, "reason": "not recoverable"}
    pycg = data["rows"][0]
    assert pycg["tool"] == "PyCG" and len(pycg["version"]) == 10
    assert pycg["finished"]["inside_found"] == 2
    assert pycg["finished"]["projects"] == pycg["all"]["projects"] == 1
    assert data["scorer"]["table"] == score.SAME_FUNCTION_SHA256
    assert "| demo | ok | 2 / 2 | 2 / 3 | 1 |" in markdown
    assert f"| quiet | {trace_recall.NO_TRACE} |" in markdown


def stored_trace_run(tmp_path, record, graph, **changes):
    """A stored run of pyan3 on rq1-trace, as container/tool_job.py writes it
    (`changes`: of its tool facts)."""
    tool_job = trace_recall.tool_job
    graphs = tmp_path / "tool-graphs"
    path = tool_job.result_path(graphs, "pyan3", "rq1-trace")
    adapter = tool_job._build().load_adapter("pyan")
    facts = tool_job.facts("pyan", adapter, tool_job.LIMITS["rq1-trace"]) | changes
    if record.get("status") == "ok" and graph is not None:
        record = {"graph_sha256": tool_job.graph_digest(graph)} | record
    data = {
        "row": "pyan3",
        "settings": dict(tool_job.rows("pyan"))["pyan3"],
        "tool": facts,
        "sha256": trace_recall._sha256(trace_recall.CORPUS.read_bytes()),
        "runs": {"demo": record},
        "graphs": {"demo": graph} if graph is not None else {},
    }
    trace_recall.tool_job.write(path, data)
    return graphs


def test_a_prior_tool_without_a_stored_run_is_not_run_and_headergen_is_stated(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(trace_recall, "TOOL_GRAPHS", tmp_path / "none")
    data, markdown = run_table(tmp_path, monkeypatch)
    rows = {row["tool"]: row for row in data["rows"]}
    assert rows["original PyCG"]["cell"] == tables.missing(
        "not run: no --original checkout given"
    )
    for name in ("pyan3", "JARVIS", "CPyGraph", "PyAnalyzer"):
        assert rows[name]["cell"]["reason"].startswith("not run: no graphs of the tool")
    assert "run --rm pyan rq1-trace" in rows["pyan3"]["cell"]["reason"]
    headergen = next(name for name in rows if name.startswith("HeaderGen"))
    assert rows[headergen]["cell"]["reason"].startswith(
        "not run: the tool imports the analysed code"
    )


def test_a_stored_run_of_a_prior_tool_is_mapped_and_scored_as_pycg_s(
    tmp_path, monkeypatch
):
    corpus(tmp_path, monkeypatch)  # the manifest that the stored run names
    graph = {"main": ["main.a", "main.b"], "main.a": [], "main.b": []}
    graphs = stored_trace_run(tmp_path / "s", {"status": "ok"}, graph)
    monkeypatch.setattr(trace_recall, "TOOL_GRAPHS", graphs)
    data, markdown = run_table(tmp_path / "t", monkeypatch)
    pyan = next(row for row in data["rows"] if row["tool"] == "pyan3")
    assert pyan["version"] == "8e8bd90646"
    assert pyan["finished"]["projects"] == 1
    run = data["projects"]["demo"]["runs"]["pyan3"]
    pycg = data["projects"]["demo"]["runs"]["PyCG"]
    assert run["status"] == "ok" and run["inside"] == pycg["inside"]  # one mapping
    assert run["inside_found"] == 2
    assert "## pyan3, per project" in markdown


def test_a_stored_run_of_another_input_needs_a_rerun(tmp_path, monkeypatch):
    corpus(tmp_path, monkeypatch)
    record = {"status": "ok", "input_sha256": "0" * 64}
    graphs = stored_trace_run(tmp_path / "s", record, {})
    monkeypatch.setattr(trace_recall, "TOOL_GRAPHS", graphs)
    data, _ = run_table(tmp_path / "t", monkeypatch)
    pyan = next(row for row in data["rows"] if row["tool"] == "pyan3")
    assert pyan["cell"] == tables.missing(trace_recall.STALE)
    assert "pyan3" not in data["projects"]["demo"]["runs"]


def test_a_failed_run_counts_its_edges_as_missed_over_all_projects(
    tmp_path, monkeypatch
):
    work = corpus(tmp_path, monkeypatch, behaviour="crash")
    argv = ["run", "--checkout", str(committed(tmp_path)), "--work", str(work)]
    argv += ["--graphs", str(tmp_path / "graphs"), "--results", str(tmp_path / "r")]
    assert trace_recall.main(argv) == 0
    data = json.loads((tmp_path / "r" / "rq1-trace-recall.json").read_text())
    pycg = data["rows"][0]
    assert pycg["finished"]["projects"] == 0
    assert (pycg["all"]["projects"], pycg["all"]["inside"]) == (1, 2)
    assert pycg["all"]["inside_found"] == 0
    assert data["projects"]["demo"]["runs"]["PyCG"]["status"] == "crash"


def test_the_table_refuses_a_tree_that_is_not_its_pinned_archive(
    tmp_path, monkeypatch, capsys
):
    work = corpus(tmp_path, monkeypatch)
    main = work / "dypybench" / "demo" / f"demo-{COMMIT}" / "main.py"
    main.write_text("def a():\n    pass\n")  # b is gone: another target
    argv = ["run", "--checkout", str(committed(tmp_path)), "--work", str(work)]
    argv += ["--results", str(tmp_path / "r")]
    with pytest.raises(SystemExit) as stopped:
        trace_recall.main(argv)
    assert stopped.value.code == 2
    assert f"demo-{COMMIT}/main.py: changed" in capsys.readouterr().err
    assert not (tmp_path / "r").exists()


# --------------------------------------------------------------------------
# Review round 1: the MRO, annotations, namespace packages, reproducibility
# --------------------------------------------------------------------------

DIAMOND = {
    "pkg/__init__.py": "",
    "pkg/d.py": """\
        class A:
            def __init__(self):
                pass

        class B(A):
            pass

        class C(A):
            def __init__(self):
                pass

        class D(B, C):
            pass

        class X(A, C):  # C3 cannot order A before C, its subclass
            pass

        class O(object):
            pass
        """,
}


def test_a_constructor_follows_the_method_resolution_order(tmp_path):
    m = mapper(tmp_path, DIAMOND)
    # Python's MRO of D is D, B, C, A: C's __init__, not A's (depth first).
    assert m.callee("pkg.d.D") == (
        "pkg.d.C.__init__",
        "inside: class, inherited __init__",
        True,
    )
    assert m.callee("pkg.d.O") == (None, "callee: class without __init__", True)


def test_a_class_without_a_method_resolution_order_is_counted_apart(tmp_path):
    m = mapper(tmp_path, DIAMOND)
    assert m.callee("pkg.d.X") == (None, "callee: no method resolution order", True)


def test_an_outside_base_leaves_the_order_unknown_even_after_a_project_init(
    tmp_path,
):
    files = {
        "pkg/__init__.py": "",
        "pkg/m.py": """\
            import json

            class Base:
                def __init__(self):
                    pass

            class Mixed(Base, json.JSONDecoder):
                pass
            """,
    }
    m = mapper(tmp_path, files)
    assert m.callee("pkg.m.Mixed") == (None, "class: base not proved", True)


@pytest.mark.parametrize(("source", "scope", "lambdas", "stable"), ANNOTATIONS)
def test_only_an_annotation_that_runs_creates_a_lambda(
    tmp_path, source, scope, lambdas, stable
):
    # The counts are the compiler's (test_annotation_lambdas.py checks them).
    path = tmp_path / "m.py"
    path.write_text(source)
    tree = trace_recall._scope_tree(path)
    found = tree if scope == "<module>" else tree.children[scope][0]
    assert len(found.lambdas) == lambdas


def test_a_lambda_beside_an_annotation_that_does_not_run_is_unique(tmp_path):
    files = {"pkg/m.py": "def factory():\n    x: (lambda: 0)\n    return lambda: 1\n"}
    m = mapper(tmp_path, files)
    assert m.callee("pkg.m.factory.<locals>.<lambda>") == (
        "pkg.m.factory.<lambda1>",
        "inside: lambda",
        True,
    )


def test_a_relative_import_in_a_namespace_package_finds_its_module(tmp_path):
    files = {
        "ns/base.py": "class Base:\n    def __init__(self):\n        pass\n",
        "ns/mod.py": "from .base import Base\n\nclass Child(Base):\n    pass\n",
    }
    m = mapper(tmp_path, files)
    assert m.callee("ns.mod.Child") == (
        "ns.base.Base.__init__",
        "inside: class, inherited __init__",
        True,
    )


# --------------------------------------------------------------------------
# Constructors: a class maps to an __init__ only with the proof of the
# plain case (CONSTRUCTOR_PROOF); one test per condition
# --------------------------------------------------------------------------


def classes(tmp_path, text, extra=None):
    files = {"pkg/__init__.py": "", "pkg/m.py": text, **(extra or {})}
    return mapper(tmp_path, files)


PRELUDE = """\
import abc
import functools
import json
from abc import ABCMeta

class Left:
    def __init__(self):
        pass

class Right:
    def __init__(self):
        pass

class Base(Left):
    pass

"""
LEFT = ("pkg.m.Left.__init__", "inside: class, inherited __init__")


def child(tmp_path, text, extra=None, callee="pkg.m.Child"):
    """The mapping of a call of `callee` with PRELUDE and `text` in pkg/m.py."""
    found = classes(tmp_path, PRELUDE + dedent(text), extra).callee(callee)
    assert found[2] is True
    return found[:2]


def apart(reason):
    return (None, f"class: {reason}")


@pytest.mark.parametrize(
    ("text", "callee", "expected"),
    [
        ("class Child(Base):\n    pass\n", "pkg.m.Child", LEFT),
        (
            "class Outer:\n    class Child(Left):\n        pass\n",
            "pkg.m.Outer.Child",
            apart("not at the top level"),
        ),
        (
            "def f():\n    class Child(Left):\n        pass\n    return Child\n",
            "pkg.m.f.<locals>.Child",
            apart("not at the top level"),
        ),
        # Codex, round 3: Outer.Child reads Outer's Base, not the module's.
        (
            "class Outer:\n    class Base(Right):\n        pass\n\n"
            "    class Child(Base):\n        pass\n",
            "pkg.m.Outer.Child",
            apart("not at the top level"),
        ),
        (
            "class Outer:\n    class Inner(Left):\n        pass\n\n"
            "class Child(Outer.Inner):\n    pass\n",
            "pkg.m.Child",
            apart("not at the top level"),
        ),
    ],
)
def test_only_a_class_at_the_top_level_of_its_module_is_proved(
    tmp_path, text, callee, expected
):
    assert child(tmp_path, text, callee=callee) == expected


@pytest.mark.parametrize(
    "change",
    [
        "Child.__init__ = Right.__init__",
        "del Child.__init__",
        "Child.__bases__ = (Right,)",
        "Base.__new__ = staticmethod(Right)",
        "def later():\n    delattr(Base, '__init__')",
        # Codex, round 4: an alias, and setattr by another spelling.
        "Alias = Child\nAlias.__init__ = Right.__init__",
        "import builtins\nbuiltins.setattr(Child, '__init__', Right.__init__)",
        "from builtins import setattr as put\nput(Child, '__init__', None)",
        "put = setattr\nput(Child, '__init__', None)",
        "object.__setattr__(Child, '__init__', None)",
        "Child.__init__: object = None",
        "Child.__init__ += ()",
        "def patch(cls):\n    cls.__call__ = None",  # on any object
    ],
)
def test_a_module_that_sets_a_special_attribute_is_not_proved(tmp_path, change):
    text = f"class Child(Base):\n    pass\n\n{change}\n"
    assert child(tmp_path, text) == apart("attribute set")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Codex, round 4: a rebound base and a decorator: the README's
        # order (bound once before decorator), not the order of the checks.
        (
            "Base = Right\n\n@json.loads\nclass Child(Base):\n    pass\n",
            "name not bound once",
        ),
        # The class first, then its bases.
        (
            "class Middle(Left, flag=1):\n    pass\n\n@json.loads\n"
            "class Child(Middle):\n    pass\n",
            "decorator",
        ),
        (
            "class Middle(Left, flag=1):\n    pass\n\nclass Child(Middle):\n    pass\n",
            "keyword",
        ),
        # Base by base, in the order of the class statement.
        (
            "@json.loads\nclass One(Left):\n    pass\n\n"
            "class Two(Right, flag=1):\n    pass\n\n"
            "class Child(One, Two):\n    pass\n",
            "decorator",
        ),
        (
            "@json.loads\nclass One(Left):\n    pass\n\n"
            "class Two(Right, flag=1):\n    pass\n\n"
            "class Child(Two, One):\n    pass\n",
            "keyword",
        ),
    ],
)
def test_the_reason_is_the_first_condition_of_the_class_then_of_its_bases(
    tmp_path, text, expected
):
    assert child(tmp_path, text) == apart(expected)


def test_every_condition_of_the_proof_is_a_reason():
    reasons = trace_recall.PROOF_ORDER
    assert reasons == [key for key, _ in trace_recall.CONSTRUCTOR_PROOF]
    assert set(reasons) <= {key for key, _ in trace_recall.UNMAPPED}


def test_the_known_bases_are_those_of_python():
    import abc
    import builtins

    for name, order in trace_recall.KNOWN_BASES.items():
        module, _, attribute = name.partition(".")
        value = getattr(builtins if module == "builtins" else abc, attribute)
        assert order == tuple(f"{c.__module__}.{c.__qualname__}" for c in value.__mro__)
        assert set(order) <= set(trace_recall.KNOWN_BASES)
        for cls in value.__mro__[:-1]:
            assert type(cls) in (type, abc.ABCMeta)
            assert "__init_subclass__" not in vars(cls)
    for name in trace_recall.WITHOUT_INIT:
        cls = getattr(abc, name.partition(".")[2])
        assert not {"__init__", "__new__"} & set(vars(cls))


def test_a_stored_run_without_its_graph_or_of_another_pin_needs_a_rerun(
    tmp_path, monkeypatch
):
    corpus(tmp_path, monkeypatch)
    graph = {"main": ["main.a"]}
    record = {"status": "ok", "graph_sha256": trace_recall.tool_job.graph_digest(graph)}
    graphs = stored_trace_run(tmp_path / "s", record, None)  # the graph is gone
    monkeypatch.setattr(trace_recall, "TOOL_GRAPHS", graphs)
    data, _ = run_table(tmp_path / "t", monkeypatch)
    pyan = next(row for row in data["rows"] if row["tool"] == "pyan3")
    assert pyan["cell"] == tables.missing(trace_recall.STALE)
    other = {"name": "pyan3", "version": "v2.8.0", "commit": "0" * 40}
    graphs = stored_trace_run(tmp_path / "o", {"status": "ok"}, graph, pin=other)
    monkeypatch.setattr(trace_recall, "TOOL_GRAPHS", graphs)
    data, _ = run_table(tmp_path / "u", monkeypatch)
    pyan = next(row for row in data["rows"] if row["tool"] == "pyan3")
    assert pyan["cell"]["reason"] == "needs rerun: it ran another version of the tool"
