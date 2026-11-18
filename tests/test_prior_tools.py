"""Behaviour tests for the prior-tool adapters (evaluation/tools/) and the
inputs of their images.

They need none of the tools and no Docker: the native outputs are small
hand-made fixtures, a driver runs only with a stand-in of its tool, and the
images are checked through their Dockerfiles and the checks they run.
"""

import importlib.util
import json
import sys
from pathlib import Path, PurePosixPath

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "evaluation" / "tools"


def load(name):
    """Load evaluation/tools/<name>.py by path: evaluation/ is not a package."""
    spec = importlib.util.spec_from_file_location(
        f"prior_tools_{name}", TOOLS / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_spec = importlib.util.spec_from_file_location(
    "evaluation_tool_job",
    Path(__file__).resolve().parents[1] / "evaluation" / "container" / "tool_job.py",
)
tool_job = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool_job)
Job = tool_job.Job


# --------------------------------------------------------------------------
# pyan3: uses edges, each end with its flavor and whether it is defined
# --------------------------------------------------------------------------

pyan = load("pyan")


def uses(source, source_flavor, target, target_flavor, defined=(True, True)):
    return {
        "from": source,
        "from_flavor": source_flavor,
        "from_defined": defined[0],
        "to": target,
        "to_flavor": target_flavor,
        "to_defined": defined[1],
    }


def test_pyan_keeps_uses_of_code_and_names_them_as_written():
    native = {
        "uses": [
            uses("main", "module", "main.B.__init__", "method"),
            uses("main", "module", "main.f", "function"),
            uses("main.B.make", "classmethod", "main.B.build", "staticmethod"),
            uses("main.B.build", "staticmethod", "main.B.make", "classmethod"),
            uses("main.B", "class", "main.deco", "function"),
        ]
    }
    graph, dropped = pyan.convert(native)
    assert graph == {
        "main": ["main.B.__init__", "main.f"],
        "main.B": ["main.deco"],
        "main.B.build": ["main.B.make"],
        "main.B.make": ["main.B.build"],
    }
    assert dropped == {}


def test_pyan_drops_targets_that_are_not_code_and_counts_them():
    native = {
        "uses": [
            uses("main", "module", "main.B", "class"),
            uses("main.f", "function", "*.child", "---", (True, False)),
            uses("main.f", "function", "main.x", "name"),
            uses("main.f", "function", "os.path", "import"),
            uses("main.f", "function", "main.g", "function", (True, False)),
            uses("main.f", "function", "main.y", "???", (True, False)),
        ]
    }
    graph, dropped = pyan.convert(native)
    assert graph == {}
    assert dropped == {
        "an end not defined in the files": 3,
        "target flavor class": 1,
        "target flavor import": 1,
        "target flavor name": 1,
    }


def test_pyan_with_no_uses_gives_an_empty_graph():
    assert pyan.convert({"uses": []}) == ({}, {})


def test_pyan_names_that_are_not_strings_are_a_conversion_error():
    with pytest.raises(TypeError):
        pyan.convert({"uses": [uses(["main"], "module", "main.f", "function")]})


# --------------------------------------------------------------------------
# JARVIS: {caller: [callees]}, PyCG 2021 names
# --------------------------------------------------------------------------

jarvis = load("jarvis")


def test_jarvis_names_its_builtin_types_by_the_builtins():
    native = {
        "main": ["<str>.split", "<map>.items", "<list>.append", "<int>.bit_length"],
        "main.f": ["<list>", "<map>", "<builtin>.len"],
    }
    graph, notes = jarvis.convert(native)
    assert graph == {
        "main": [
            "<builtin>.dict.items",
            "<builtin>.int.bit_length",
            "<builtin>.list.append",
            "<builtin>.str.split",
        ],
        "main.f": ["<builtin>.dict", "<builtin>.len", "<builtin>.list"],
    }
    assert notes == {}


def test_jarvis_keeps_other_names_and_empty_callers_as_written():
    native = {
        "main": ["main.<lambda1>", "main.C.__init__", "main.C"],
        "main.<lambda1>": [],
        "<builtin>.len": [],
    }
    graph, notes = jarvis.convert(native)
    assert graph == {
        "<builtin>.len": [],
        "main": ["main.<lambda1>", "main.C", "main.C.__init__"],
        "main.<lambda1>": [],
    }
    assert notes == {}


def test_jarvis_names_that_cannot_be_mapped_are_kept_and_counted():
    native = {
        "main": ["<**INVALID**>", "main.<RETURN>.x", "main.<string>.f", "main.f"],
        "main.<external>": ["main.f"],
    }
    graph, notes = jarvis.convert(native)
    assert graph["main"] == [
        "<**INVALID**>",
        "main.<RETURN>.x",
        "main.<string>.f",
        "main.f",
    ]
    assert graph["main.<external>"] == ["main.f"]
    assert notes == {"kept: a name that cannot be mapped": 4}


def test_jarvis_with_an_empty_graph_gives_an_empty_graph():
    assert jarvis.convert({}) == ({}, {})


MICRO_JOB = Job(
    "micro/a/b", Path("/job"), (Path("/job/main.py"),), (Path("/job/main.py"),)
)


@pytest.mark.parametrize("callees", ["main.f", ["main.f", 3], {"main.f": 1}])
def test_jarvis_callees_that_are_not_a_list_of_names_are_an_error(callees):
    with pytest.raises(TypeError):
        jarvis.convert({"main": callees})


def test_jarvis_has_three_rows_default_dropped_and_precision():
    assert jarvis.VARIANTS == (
        ("JARVIS", {}),
        ("JARVIS, class edges dropped", {"drop_class_callees": True}),
        ("JARVIS --precision", {"flags": ["--precision"]}),
    )


def test_jarvis_class_edges_dropped_drops_callees_that_are_classes(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text(
        "class A:\n    class Inner:\n        pass\n\n"
        "def make():\n    class Local:\n        pass\n    return Local\n\n"
        "class retry:\n    pass\n\ndef retry():\n    pass\n"
    )
    (tmp_path / "pkg" / "broken.py").write_text("def (:\n")  # Python 2 code
    files = tuple(sorted((tmp_path / "pkg").glob("*.py")))
    job = Job("macro/p", tmp_path, files, files)
    native = {
        "pkg": ["pkg.A", "pkg.A.Inner", "pkg.make", "pkg.retry", "<builtin>.len"],
        "pkg.make": ["pkg.make.Local", "pkg.A.__init__"],
    }
    graph, notes = jarvis.convert(native, job, {"drop_class_callees": True})
    assert graph == {
        "pkg": ["<builtin>.len", "pkg.make", "pkg.retry"],  # also a function
        "pkg.make": ["pkg.A.__init__"],
    }
    assert notes == {"dropped: the callee is a class of the analysed source": 3}
    as_written, _ = jarvis.convert(native, job, {})
    assert "pkg.A" in as_written["pkg"]


# --------------------------------------------------------------------------
# CPyGraph: the JSON of pygCG ({"edges": [[caller, callee], ...]}) and the
# names of the code objects, both written by the driver of the adapter
# --------------------------------------------------------------------------

cpygraph = load("cpygraph")


def native_cpygraph(edges, names=None, helpers=()):
    return {
        "pygcg": {"component": "cg", "edges": edges},
        "names": names or {},
        "helpers": list(helpers),
    }


def test_cpygraph_renames_its_three_builtin_type_methods():
    native = native_cpygraph(
        [
            ["main", "<**PyStr**>.join"],
            ["main", "<**PyStr**>.split"],
            ["main", "<**PyDict**>.items"],
            ["main", "<builtin>.len"],
            ["main.f", "main.g"],
            ["main.f", "main.g"],
        ]
    )
    graph, notes = cpygraph.convert(native)
    assert graph == {
        "main": [
            "<builtin>.dict.items",
            "<builtin>.len",
            "<builtin>.str.join",
            "<builtin>.str.split",
        ],
        "main.f": ["main.g"],
    }
    assert notes == {}


def test_cpygraph_with_no_edges_gives_an_empty_graph():
    assert cpygraph.convert(native_cpygraph([])) == ({}, {})


def names_of(source, module="main"):
    return cpygraph.code_names(compile(source, f"{module}.py", "exec"), module)


def test_cpygraph_lambdas_get_the_benchmark_names_by_source_position():
    # pygCG numbers the lambdas of a scope in the order of their code
    # objects: the default-value lambda is compiled first. The benchmark
    # numbers them by the position of the `lambda` keyword.
    source = "def func1():\n    pass\n\nrun = lambda g=lambda: func1(): g()\nrun()\n"
    names = names_of(source)
    assert names["main.<lambda1>"] == "main.<lambda2>"
    assert names["main.<lambda2>"] == "main.<lambda1>"
    assert names["main.func1"] == "main.func1"
    native = native_cpygraph(
        [
            ["main", "main.<lambda2>"],
            ["main.<lambda1>", "main.func1"],
            ["main.<lambda2>", "main.<lambda1>"],
        ],
        names,
    )
    graph, _ = cpygraph.convert(native)
    assert graph == {  # the edges of the benchmark's callgraph.json
        "main": ["main.<lambda1>"],
        "main.<lambda1>": ["main.<lambda2>"],
        "main.<lambda2>": ["main.func1"],
    }


def test_cpygraph_two_lambdas_on_one_line_are_told_apart_by_column():
    names = names_of("pair = (lambda: 1, lambda: 2)\n")
    assert names["main.<lambda1>"] == "main.<lambda1>"
    assert names["main.<lambda2>"] == "main.<lambda2>"
    names = names_of("f = lambda a=lambda: 1, b=lambda: 2: a() + b()\n")
    # Code objects: the two defaults, then the outer lambda.
    assert names == {
        "main": "main",
        "main.<lambda1>": "main.<lambda2>",
        "main.<lambda2>": "main.<lambda3>",
        "main.<lambda3>": "main.<lambda1>",
    }


def test_cpygraph_frames_that_are_not_scopes_take_the_name_of_their_owner():
    source = "def f(xs):\n    return list((lambda: x)() for x in xs)\n"
    names = names_of(source)
    assert names["main.f.<genexpr>"] == "main.f"
    assert names["main.f.<genexpr>.<lambda1>"] == "main.f.<lambda1>"


def test_cpygraph_class_bodies_keep_their_names():
    names = names_of(
        "class C:\n    key = lambda self: 1\n    def m(self):\n        pass\n"
    )
    assert names["main.C"] == "main.C"
    assert names["main.C.<lambda1>"] == "main.C.<lambda1>"
    assert names["main.C.m"] == "main.C.m"


GENERICS = (
    "def f[T](x: T) -> T:\n    return x\n\n"
    "def g[T](x: T) -> T:\n    return x\n\n"
    "f(1)\ng(2)\n"
)


def generic_names():
    helpers = set()
    code = compile(GENERICS, "main.py", "exec")
    names = cpygraph.code_names(code, "main", helpers)
    return names, helpers


def test_cpygraph_helper_frames_of_generic_functions_are_known():
    names, helpers = generic_names()
    frames = {"main.<generic parameters of f>", "main.<generic parameters of g>"}
    assert frames <= helpers
    # From Python 3.14 on, the annotations of f and g are annotation scopes.
    assert all(h in frames or h.endswith(".__annotate__") for h in helpers)
    assert names["main.<generic parameters of f>.f"] == "main.f"
    assert names["main.<generic parameters of g>.g"] == "main.g"


def test_cpygraph_edges_to_a_helper_frame_are_dropped_and_counted():
    # `def f[T]` runs a helper frame that defines f: pygCG has an edge from
    # the module to it. It is not a call of the program; the calls f(1) and
    # g(2) are, and they stay apart.
    names, helpers = generic_names()
    native = native_cpygraph(
        [
            ["main", "main.<generic parameters of f>"],
            ["main", "main.<generic parameters of g>"],
            ["main", "main.<generic parameters of f>.f"],
            ["main", "main.<generic parameters of g>.g"],
        ],
        names,
        helpers,
    )
    graph, notes = cpygraph.convert(native)
    assert graph == {"main": ["main.f", "main.g"]}
    assert notes == {"dropped: the callee is a helper frame (generic parameters)": 2}


def test_cpygraph_a_call_inside_a_helper_frame_belongs_to_its_owner():
    names, helpers = generic_names()
    native = native_cpygraph(
        [["main.<generic parameters of f>", "main.g"]],
        names | {"main.g": "main.g"},
        helpers,
    )
    graph, notes = cpygraph.convert(native)
    assert graph == {"main": ["main.g"]}
    assert notes == {}


def test_cpygraph_keeps_real_recursion():
    source = "def f(n):\n    return f(n - 1) if n else 0\n\nf(3)\n"
    helpers = set()
    names = cpygraph.code_names(compile(source, "main.py", "exec"), "main", helpers)
    native = native_cpygraph([["main", "main.f"], ["main.f", "main.f"]], names, helpers)
    graph, notes = cpygraph.convert(native)
    assert graph == {"main": ["main.f"], "main.f": ["main.f"]}
    assert notes == {}


def test_cpygraph_a_function_named_like_an_annotation_scope_keeps_its_call():
    # Only the compiler's annotation frames (one positional-only parameter
    # `format`) are helper frames, not a function whose name starts with
    # __annotate__.
    source = "def __annotate__helper():\n    pass\n\n__annotate__helper()\n"
    helpers = set()
    names = cpygraph.code_names(compile(source, "main.py", "exec"), "main", helpers)
    assert "main.__annotate__helper" not in helpers
    native = native_cpygraph([["main", "main.__annotate__helper"]], names, helpers)
    graph, notes = cpygraph.convert(native)
    assert graph == {"main": ["main.__annotate__helper"]}
    assert notes == {}
    # Even a function named exactly __annotate__, without the parameter.
    source = "def __annotate__():\n    return 1\n\n__annotate__()\n"
    helpers = set()
    cpygraph.code_names(compile(source, "main.py", "exec"), "main", helpers)
    assert "main.__annotate__" not in helpers


def test_cpygraph_edges_that_are_not_two_names_are_an_error():
    with pytest.raises(TypeError):
        cpygraph.convert(native_cpygraph([["main", 3]]))


# --------------------------------------------------------------------------
# HeaderGen: {"cg": {caller: [callees]}}, PyCG 2021 names with line numbers
# --------------------------------------------------------------------------

headergen = load("headergen")


def test_headergen_strips_line_numbers_and_object_marks_from_every_part():
    native = {
        "cg": {
            "main": ["main.f:3", "main.C:1.m:2", "main.x<5>.run", "main.<lambda1>:7"],
            "main.f:3": ["<builtin>.print"],
        }
    }
    graph, notes = headergen.convert(native)
    assert graph == {
        "main": ["main.<lambda1>", "main.C.m", "main.f", "main.x.run"],
        "main.f": ["<builtin>.print"],
    }
    assert notes == {}


def test_headergen_merges_callers_that_become_equal_and_counts_them():
    native = {"cg": {"main.f:3": ["main.g"], "main.f:9": ["main.h"], "main.g": []}}
    graph, notes = headergen.convert(native)
    assert graph == {"main.f": ["main.g", "main.h"], "main.g": []}
    assert notes == {headergen.MERGED: 1}


def test_headergen_names_dict_and_list_methods_by_the_builtins():
    native = {"cg": {"main": ["<dict>.items", "<list>.append", "<list>", "<dict>"]}}
    graph, notes = headergen.convert(native)
    assert graph == {
        "main": [
            "<builtin>.dict",
            "<builtin>.dict.items",
            "<builtin>.list",
            "<builtin>.list.append",
        ]
    }
    assert notes == {}


def test_headergen_names_with_other_marks_are_kept_and_counted():
    native = {
        "cg": {
            "main": ["main.<RETURN>.x", "<**INVALID**>", "main.<dict1>.get", "main.f"],
            "main.<external>": ["posixpath.join"],
        }
    }
    graph, notes = headergen.convert(native)
    assert graph["main"] == [
        "<**INVALID**>",
        "main.<RETURN>.x",
        "main.<dict1>.get",
        "main.f",
    ]
    assert graph["main.<external>"] == ["posixpath.join"]  # its outside names kept
    assert notes == {headergen.UNMAPPED: 4}


@pytest.mark.parametrize(
    "native",
    [{"cg": []}, {"cg": {"main": "main.f"}}, {"cg": {"main": [1]}}, {"graph": {}}],
)
def test_headergen_an_output_of_another_shape_is_an_error(native):
    with pytest.raises((TypeError, KeyError)):
        headergen.convert(native)


def test_headergen_runs_its_api_with_the_product_settings():
    spec = headergen.command(headergen.TOOL, MICRO_JOB, PurePosixPath("/tmp/run"))
    assert spec["argv"] == [
        "python3",
        "-S",
        "-s",
        "-P",
        "-B",
        "/tmp/run/driver/headergen.py",
        "/tmp/run/request.json",
    ]
    request = spec["request"]
    assert (request["max_iter"], request["operation"]) == (500, "call-site")
    assert request["entry_points"] == ["/job/main.py"]
    assert request["root"] == "/job"
    assert request["shim"] == "/opt/tool/shim" and request["deps"] == "/opt/deps"
    assert headergen.VARIANTS == (
        ("HeaderGen (hybrid: imports the analysed modules)", {}),
    )


# --------------------------------------------------------------------------
# The build of the images (no network, no Docker)
# --------------------------------------------------------------------------

build = load("build")
image_checks = load("image_checks")


def pins(**base):
    found = build.read_pins()
    found["base_images"] |= base
    return found


PROJECT_TOML = """
[{name}]
root = "."
packages = ["{name}"]
exclude = []
dependencies = {dependencies}
pytest_args = []
test_requirements = []
"""


def macro_set(tmp_path, projects):
    """A macro set: {project: (dependencies, requirements text or None)}."""
    directory = tmp_path / "macro-benchmark-x"
    (directory / "requirements").mkdir(parents=True)
    toml = ""
    for name, (dependencies, requirements) in projects.items():
        toml += PROJECT_TOML.format(name=name, dependencies=json.dumps(dependencies))
        if requirements is not None:
            (directory / "requirements" / f"{name}.txt").write_text(requirements)
    (directory / "projects.toml").write_text(toml)
    return directory


# --------------------------------------------------------------------------
# pin.py: what it asks the network, and what it refuses (no network here)
# --------------------------------------------------------------------------

pin = load("pin")


def test_a_resolution_must_pin_every_file_and_hold_no_shimmed_package():
    good = (
        "pygtrie==2.5.0 \\\n    --hash=sha256:aa \\\n    --hash=sha256:bb\n"
        "jedi==0.19.1 --hash=sha256:cc\n"
    )
    assert pin.check_requirements("headergen", good) == {
        "pygtrie": "2.5.0",
        "jedi": "0.19.1",
    }
    with pytest.raises(pin.PinError, match="with hashes"):
        pin.check_requirements("headergen", "jedi>=0.19 --hash=sha256:cc\n")
    with pytest.raises(pin.PinError, match="with hashes"):
        pin.check_requirements("headergen", "jedi==0.19.1\n")
    with pytest.raises(pin.PinError, match="holds numpy"):
        pin.check_requirements("headergen", good + "numpy==1.26.4 --hash=sha256:dd\n")


# --------------------------------------------------------------------------
# PyAnalyzer: its call-graph file and the Call relations of its report
# --------------------------------------------------------------------------

pyanalyzer = load("pyanalyzer")

PYA_MAIN = """\
import pkg
from pkg.mod import Box

square = lambda x: x * x
twice = [lambda: 1, lambda: 2]


def run():
    helper = lambda y: y
    return helper(square(2))


class Holder:
    pick = lambda self: 0
"""


def pyanalyzer_job(tmp_path):
    root = tmp_path / "case"
    (root / "pkg").mkdir(parents=True)
    (root / "main.py").write_text(PYA_MAIN)
    (root / "pkg" / "__init__.py").write_text("def setup():\n    pass\n")
    (root / "pkg" / "mod.py").write_text(
        "class Box:\n    def open(self):\n        pass\n"
    )
    files = tuple(sorted(root.rglob("*.py")))
    return Job("micro/a/b", root, (root / "main.py",), files)


def test_pyanalyzer_names_become_the_benchmark_names(tmp_path):
    job = pyanalyzer_job(tmp_path)
    native = {
        "root": "stage",
        "call_graph": {
            "stage.main": [
                "stage.main.(4)",
                "stage.pkg.__init__.setup",
                "stage.main.run",
            ],
            "stage.main.run": ["stage.main.run.(9)", "stage.main.(4)"],
            "stage.main.Holder": ["stage.main.Holder.(14)"],
            "stage.pkg.mod.Box.open": [],
        },
    }
    graph, notes = pyanalyzer.convert(native, job)
    assert graph == {
        "main": ["main.<lambda1>", "main.run", "pkg.setup"],
        "main.Holder": ["main.Holder.<lambda1>"],
        "main.run": ["main.<lambda1>", "main.run.<lambda1>"],
        "pkg.mod.Box.open": [],
    }
    assert notes == {}


def test_pyanalyzer_two_lambdas_of_one_line_are_kept_and_counted(tmp_path):
    job = pyanalyzer_job(tmp_path)
    native = {"root": "stage", "call_graph": {"stage.main": ["stage.main.(5)"]}}
    graph, notes = pyanalyzer.convert(native, job)
    assert graph == {"main": ["main.(5)"]}  # one name for two lambdas
    assert notes == {pyanalyzer.UNMAPPED: 1}
    names = pyanalyzer._names(job)[1]
    assert names["main.(4)"] == "main.<lambda1>"
    assert names["main.(5)"] is None
    # Their positions, which the report gives, tell them apart.
    positions = pyanalyzer._names(job)[2]
    assert positions[("main.(5)", 5, 9)] == "main.<lambda2>"
    assert positions[("main.(5)", 5, 20)] == "main.<lambda3>"


def test_pyanalyzer_a_name_outside_the_modules_of_the_job_is_kept_and_counted(tmp_path):
    job = pyanalyzer_job(tmp_path)
    native = {
        "root": "stage",
        "call_graph": {"stage.main": ["stage.os.path.join", "x.y"]},
    }
    graph, notes = pyanalyzer.convert(native, job)
    assert graph == {"main": ["os.path.join", "x.y"]}
    assert notes == {pyanalyzer.UNMAPPED: 2}


def pya_report():
    entities = [
        (1, "stage.main", "Module"),
        (2, "stage.main.x", "Variable"),
        (3, "stage.main.(4)", "AnonymousFunction"),
        (4, "stage.main.run", "Function"),
        (5, "stage.pkg.mod.Box", "Class"),
        (6, "stage.main.y", "Parameter"),
    ]
    located = {3: {"startLine": 4, "startColumn": 9}}
    return {
        "variables": [
            {"id": i, "qualifiedName": n, "category": c, "location": located.get(i, {})}
            for i, n, c in entities
        ],
        "cells": [
            {"src": 1, "dest": 2, "values": {"kind": "Call", "resolved": [3]}},
            {"src": 1, "dest": 3, "values": {"kind": "Call"}},
            {"src": 1, "dest": 5, "values": {"kind": "Call"}},
            {"src": 4, "dest": 3, "values": {"kind": "Use"}},
            {"src": 6, "dest": 4, "values": {"kind": "Call"}},
        ],
    }


def test_pyanalyzer_the_call_relations_of_the_report_are_read_once_each():
    main = ["stage.main", "Module", None, None]
    assert pyanalyzer.calls_of(pya_report()) == [
        main + ["stage.main.(4)", "AnonymousFunction", 4, 9],
        main + ["stage.main.x", "Variable", None, None],
        main + ["stage.pkg.mod.Box", "Class", None, None],
        ["stage.main.y", "Parameter", None, None, "stage.main.run", "Function"]
        + [None, None],
    ]


def test_pyanalyzer_the_report_keeps_calls_of_code_and_counts_the_rest(tmp_path):
    native = {"root": "stage", "calls": pyanalyzer.calls_of(pya_report())}
    graph, notes = pyanalyzer.convert(native, pyanalyzer_job(tmp_path))
    assert graph == {"main": ["main.<lambda1>"]}
    assert notes == {
        "dropped: the caller is a Parameter": 1,
        "dropped: the target is a Class": 1,
        "dropped: the target is a Variable": 1,
    }


@pytest.mark.parametrize(
    "native",
    [
        {"root": "stage", "call_graph": {"stage.main": "stage.main.run"}},
        {"root": "stage", "call_graph": []},
        {"root": "stage", "calls": [["stage.main", "Module", 3, "Function"]]},
        {"root": 3, "call_graph": {}},
        {"call_graph": {}},
    ],
)
def test_pyanalyzer_an_output_of_another_shape_is_an_error(tmp_path, native):
    with pytest.raises((TypeError, KeyError, ValueError)):
        pyanalyzer.convert(native, pyanalyzer_job(tmp_path))


def test_pyanalyzer_has_two_rows_and_no_outside_callee():
    assert [name for name, _ in pyanalyzer.VARIANTS] == [
        "PyAnalyzer",
        "PyAnalyzer (call relations of the dependency report)",
    ]
    assert "no callee outside the package" in pyanalyzer.NO_OUTSIDE
    tool = pyanalyzer.TOOL | {"output_kind": "report"}
    spec = pyanalyzer.command(tool, MICRO_JOB, PurePosixPath("/tmp/run"))
    assert spec["argv"][5:] == [
        "/tmp/run/driver/pyanalyzer.py",
        "/tmp/run/request.json",
    ]
    request = spec["request"]
    assert request["stage"] == "/tmp/root/stage"  # absolute, depth 3
    assert request["cwd"] == "/tmp/run/scratch"  # not in the parent of the stage
    assert (request["export"], request["output_kind"]) == ("/job", "report")
    # The third of K jobs at a time: its own stage, at the same depth.
    request = pyanalyzer.command(tool, MICRO_JOB, PurePosixPath("/tmp/run2"))["request"]
    assert (request["stage"], request["cwd"]) == (
        "/tmp/root2/stage",
        "/tmp/run2/scratch",
    )


# --------------------------------------------------------------------------
# The runtime dependencies of a project for HeaderGen (pin.py deps, build.py)
# --------------------------------------------------------------------------

TRACE = """\
# The test environment of demo (a comment).
pytest==9.2.0 \\
    --hash=sha256:aa
pyyaml==6.0.3 \\
    --hash=sha256:cp314only
msgpack==1.1.2 \\
    --hash=sha256:bb
"""


def deps_set(tmp_path, dependencies, trace=TRACE):
    return macro_set(tmp_path, {"demo": (dependencies, trace)})


def test_headergen_runs_a_project_only_with_its_pinned_dependencies(
    tmp_path, monkeypatch
):
    set_dir = deps_set(tmp_path, ["msgpack"])
    monkeypatch.setattr(build, "DEPS", tmp_path / "headergen-deps")
    shown = "tools/headergen-deps/demo.txt"
    runnable, statement = build.dependencies("headergen", set_dir, "demo")
    assert runnable is False
    assert statement.startswith("not pinned: no ") and shown in statement
    pinned = build.deps_file(set_dir, "demo")
    pinned.parent.mkdir(parents=True)
    pinned.write_text("msgpack==1.1.2 --hash=sha256:x\n")
    runnable, statement = build.dependencies("headergen", set_dir, "demo")
    assert runnable is True
    assert statement.startswith("installed: ") and statement.endswith(shown)
    pinned.write_text(f"# header\n{build.UNAVAILABLE}msgpack has no wheel\n")
    assert build.dependencies("headergen", set_dir, "demo") == (
        False,
        "not installable on CPython 3.12: msgpack has no wheel",
    )


THREE_ON_ONE_LINE = """\
def func1(a, b, c):
    a()

func1(lambda x: x + 1, lambda x: x + 2, lambda x: x + 3)
"""


def test_pyanalyzer_the_report_tells_lambdas_of_one_line_apart(tmp_path):
    root = tmp_path / "case"
    root.mkdir()
    (root / "main.py").write_text(THREE_ON_ONE_LINE)
    job = Job("micro/a/b", root, (root / "main.py",), (root / "main.py",))
    columns = (6, 23, 40)
    entities = [{"id": 1, "qualifiedName": "stage.main", "category": "Module"}]
    entities += [
        {
            "id": 10 + i,
            "qualifiedName": "stage.main.(4)",
            "category": "AnonymousFunction",
            "location": {"startLine": 4, "startColumn": column},
        }
        for i, column in enumerate(columns)
    ]
    report = {
        "variables": entities,
        "cells": [
            {"src": 1, "dest": 10 + i, "values": {"kind": "Call"}} for i in range(3)
        ],
    }
    native = {"root": "stage", "calls": pyanalyzer.calls_of(report)}
    graph, notes = pyanalyzer.convert(native, job)
    assert graph == {"main": ["main.<lambda1>", "main.<lambda2>", "main.<lambda3>"]}
    assert notes == {}
    # The call-graph file has the name only: one name for three, counted.
    file_graph, file_notes = pyanalyzer.convert(
        {"root": "stage", "call_graph": {"stage.main": ["stage.main.(4)"]}}, job
    )
    assert file_graph == {"main": ["main.(4)"]}
    assert file_notes == {pyanalyzer.UNMAPPED: 1}


def test_pyanalyzer_a_lambda_of_an_annotation_belongs_to_the_scope(tmp_path):
    root = tmp_path / "case"
    root.mkdir()
    (root / "main.py").write_text(
        "def f(x: (lambda: int)()) -> (lambda: str)():\n    pass\n"
        "g = lambda: f(0)\n"
        "g()\n"
    )
    job = Job("micro/a/b", root, (root / "main.py",), (root / "main.py",))
    lambdas = pyanalyzer._names(job)[1]
    assert lambdas["main.(3)"] == "main.<lambda3>"  # after the two annotations


def test_pyanalyzer_a_name_shared_by_outer_lambdas_is_shared_by_their_inner_ones(
    tmp_path,
):
    root = tmp_path / "case"
    root.mkdir()
    (root / "main.py").write_text("a, b = (lambda: lambda: 1), (lambda: lambda: 2)\n")
    job = Job("micro/a/b", root, (root / "main.py",), (root / "main.py",))
    _, lambdas, positions = pyanalyzer._names(job)
    assert lambdas["main.(1)"] is None
    assert lambdas["main.(1).(1)"] is None  # not one definite inner lambda
    assert positions[("main.(1).(1)", 1, 16)] == "main.<lambda1>.<lambda1>"
    assert positions[("main.(1).(1)", 1, 37)] == "main.<lambda2>.<lambda1>"


def lambdas_of(tmp_path, source):
    root = tmp_path / "case"
    root.mkdir(exist_ok=True)
    (root / "main.py").write_text(source)
    job = Job("micro/a/b", root, (root / "main.py",), (root / "main.py",))
    return job, pyanalyzer._names(job)


# The lambdas of a source, as the benchmark names them: {(line, column of
# `lambda`): name}. The benchmark's tracer (micro-benchmark/validate.py)
# runs on Python 3.14 and later only, and numbers the lambdas that the
# compiled code holds; test_the_lambda_cases_are_the_benchmark_s_names
# checks each case against it.
LAMBDA_CASES = {
    "postponed annotations": (
        "from __future__ import annotations\n"
        "def f(x: (lambda: int)()) -> (lambda: str)():\n    pass\n"
        "g = lambda: f(0)\ng()\n",
        {(4, 4): "main.<lambda1>"},
    ),
    "string annotation": (
        "def f(x: '(lambda: int)()'):\n    pass\ng = lambda: f(0)\n",
        {(3, 4): "main.<lambda1>"},
    ),
    "local annotation": (
        "def f():\n    y: (lambda: int)() = 1\n    g = lambda: y\n    return g\n",
        {(3, 8): "main.f.<lambda1>"},
    ),
    "subscript target": (
        "def target(): pass\nbox = {}\nbox[0]: (lambda: int)() = 1\n"
        "run = lambda: target()\nrun()\n",
        {(4, 6): "main.<lambda1>"},
    ),
    "attribute target": (
        "import os\nos.x: (lambda: int)() = 1\nrun = lambda: 0\n",
        {(3, 6): "main.<lambda1>"},
    ),
    "parenthesised name": (
        "(x): (lambda: int)() = 1\nrun = lambda: 0\n",
        {(2, 6): "main.<lambda1>"},
    ),
    "subscript target in a class": (
        "box = {}\nclass C:\n    box[0]: (lambda: int)() = 1\n    run = lambda: 0\n",
        {(4, 10): "main.C.<lambda1>"},
    ),
    "the target and value of a subscript target": (
        "box = {}\nbox[(lambda: 0)()]: (lambda: int)() = (lambda: 1)()\n"
        "run = lambda: 0\n",
        {(2, 5): "main.<lambda1>", (2, 39): "main.<lambda2>", (3, 6): "main.<lambda3>"},
    ),
    "postponed annotations of variables": (
        "from __future__ import annotations\nx: (lambda: int)() = 1\n"
        "class C:\n    z: (lambda: int)()\n    run = lambda: 0\n"
        "run = lambda: 0\n",
        {(5, 10): "main.C.<lambda1>", (6, 6): "main.<lambda1>"},
    ),
    "a generic function": (
        "def f[T](x=(lambda: 1)()):\n    g = lambda: 0\n    return g\n"
        "class K[T]:\n    h = lambda: 0\n",
        {
            (1, 12): "main.<lambda1>",
            (2, 8): "main.f.<lambda1>",
            (5, 8): "main.K.<lambda1>",
        },
    ),
    "simple names in a module and a class": (
        "x: (lambda: int)() = 1\nclass C:\n    z: (lambda: int)()\nrun = lambda: 0\n",
        {
            (1, 4): "main.<lambda1>",
            (3, 8): "main.C.<lambda1>",
            (4, 6): "main.<lambda2>",
        },
    ),
    "defaults, decorators and parameter annotations": (
        "def deco(f):\n    return f\n"
        "@(lambda f: deco(f))\n"
        "def f(x=(lambda: 1)(), y: (lambda: int)() = 0):\n    pass\n"
        "g = lambda: f()\n",
        {
            (3, 2): "main.<lambda1>",
            (4, 9): "main.<lambda2>",
            (4, 27): "main.<lambda3>",
            (6, 4): "main.<lambda4>",
        },
    ),
}


def benchmark_lambdas(source):
    """{(line, column): name} of the lambdas of a module `main`, by the
    benchmark's own code: micro-benchmark/validate.py numbers the lambdas
    that the compiled code holds. The source is compiled, never run."""
    import importlib.util
    import types

    path = TOOLS.parents[1] / "PyCG" / "micro-benchmark" / "validate.py"
    spec = importlib.util.spec_from_file_location("validate_for_lambdas", path)
    validate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validate)
    found = {}

    def walk(code, scope):
        lambdas = sorted(validate._lambdas(code), key=lambda item: item[0])
        for number, (position, const) in enumerate(lambdas, 1):
            found[position] = f"{scope}.<lambda{number}>"
            walk(const, f"{scope}.<lambda{number}>")
        children(code, scope)

    def children(code, scope):
        # As validate.py's _register_children: a transparent frame (an
        # annotation scope, the generic parameters of a function) is not a
        # scope; the functions in it are the scope's. Its lambdas are already
        # in validate._lambdas of the scope.
        for const in code.co_consts:
            if not isinstance(const, types.CodeType) or const.co_name == "<lambda>":
                continue
            if validate._is_transparent(const):
                children(const, scope)
            else:
                walk(const, f"{scope}.{const.co_name}")

    walk(compile(source, "main.py", "exec"), "main")
    return found


@pytest.mark.skipif(
    sys.version_info < (3, 14), reason="the benchmark's tracer needs Python 3.14"
)
@pytest.mark.parametrize("case", sorted(LAMBDA_CASES))
def test_the_lambda_cases_are_the_benchmark_s_names(case):
    source, expected = LAMBDA_CASES[case]
    assert benchmark_lambdas(source) == expected


@pytest.mark.parametrize("case", sorted(LAMBDA_CASES))
def test_pyanalyzer_names_the_lambdas_as_the_benchmark_does(tmp_path, case):
    # Both rows: the report gives each lambda's position, the call-graph
    # file its line only, so it names only a lambda alone on its line.
    source, expected = LAMBDA_CASES[case]
    job, names = lambdas_of(tmp_path, source)
    positions = {(line, column): b for (_, line, column), b in names[2].items()}
    assert positions == expected
    for (line, column), benchmark in expected.items():
        scope, _, _ = benchmark.rpartition(".")
        written = f"stage.{scope}.({line})"
        report = {
            "root": "stage",
            "calls": [
                ["stage.main", "Module", None, None, written]
                + ["AnonymousFunction", line, column],
            ],
        }
        graph, notes = pyanalyzer.convert(report, job)
        assert graph == {"main": [benchmark]} and notes == {}, case
        if [at[0] for at in expected].count(line) > 1:
            continue
        call_graph = {"root": "stage", "call_graph": {"stage.main": [written]}}
        graph, notes = pyanalyzer.convert(call_graph, job)
        assert graph == {"main": [benchmark]} and notes == {}, case


def test_no_pinned_file_holds_a_distribution_of_headergen_s_own_pins():
    # The image of a project would install it twice: the tool's must win.
    set_dir = build.REPOSITORY / "macro-benchmark"
    pinned = sorted(build.DEPS.glob("*.txt"))
    assert len(pinned) == 7 and not [p for p in build.DEPS.iterdir() if p.is_dir()]
    for path in pinned:
        assert path.stem in build._projects(set_dir), path
        assert build.deps_overlap(set_dir, path.stem) == [], path


def test_a_tree_has_the_hash_of_its_paths_and_bytes(tmp_path):
    tree = tmp_path / "tree"
    (tree / "a").mkdir(parents=True)
    (tree / "a" / "x.py").write_text("x = 1\n")
    (tree / "y.py").write_text("y = 1\n")
    found = build.tree_hash(tree)
    assert image_checks.main(["tree", str(tree), found]) == 0
    with pytest.raises(image_checks.CheckError, match="pins.json 00"):
        image_checks.check_tree(tree, "0" * 64)
    (tree / "y.py").write_text("y = 2\n")
    assert build.tree_hash(tree) != found
    (tree / "y.py").write_text("y = 1\n")
    (tree / "a" / "x.py").rename(tree / "a" / "z.py")
    assert build.tree_hash(tree) != found


def test_the_image_text_is_the_pin_and_the_trees_of_the_build_arguments():
    pins = build.read_pins()
    assert build.image_text("pyan") == (
        "pyan3 v2.9.0 8e8bd90646de039696b91fdf2d970e02fe8d1f62\n\n"
    )
    assert build.image_text("jarvis").splitlines() == [
        build.pin_label(build.load_adapter("jarvis")),
        f"source={pins['trees']['jarvis/source']}",
    ]


# --------------------------------------------------------------------------
# A driver's scratch: the jobs of a service share one container
# --------------------------------------------------------------------------


def moved(value, tmp):
    """A path of the container (/tmp, /job, /out) under `tmp`, as the image
    leads /job and /out to /tmp/run (tool_job.WORK)."""
    if isinstance(value, dict):
        return {k: moved(v, tmp) for k, v in value.items()}
    if isinstance(value, list):
        return [moved(v, tmp) for v in value]
    if isinstance(value, str):
        places = (
            ("/tmp", tmp),
            ("/job", tmp / "run" / "job"),
            ("/out", tmp / "run" / "out"),
        )
        for prefix, there in places:
            if value == prefix or value.startswith(prefix + "/"):
                return str(there) + value[len(prefix) :]
    return value
