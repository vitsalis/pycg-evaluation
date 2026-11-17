"""Behaviour tests for evaluation/rq4-test-selection/rts.py, regression test selection.

Git runs on small repositories built in tmp_path; the network is a local
clone (file://); the analysis is a fake PyCG checkout that reads its call
graph from the analysed tree as data. They run on every supported Python
version.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "evaluation_fakes", Path(__file__).resolve().parent / "evaluation_fakes.py"
)
fakes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fakes)
rts = fakes.load("rq4-test-selection/rts")


# --- changed definitions ---------------------------------------------------

SOURCE = """\
import os


def outer():
    def inner():
        return 1

    return inner()


@decorate(
    option=True,
)
def decorated():
    return 2

# a comment between functions
LIMIT = 3


class Box:
    size = 4

    def method(self):
        return self.size


def factory():
    class Local:
        pass

    return Local


def text():
    return '''
# not a comment

'''
"""


def touched(old, new, before=SOURCE):
    """The definitions that replacing `old` by `new` in `before` touches."""
    assert before.count(old) == 1, old
    after = before.replace(old, new)
    return rts.changed_names("m", before.encode(), after.encode())


def test_a_line_of_a_nested_function_belongs_to_it():
    assert touched("return 1", "return 9") == {"m.outer.inner"}
    assert touched("return inner()", "return inner() + 1") == {"m.outer"}


def test_a_decorator_runs_in_the_scope_around_the_function():
    assert touched("option=True", "option=False") == {"m.decorated", "m"}


def test_a_comment_or_a_blank_line_changes_nothing():
    assert touched("# a comment between", "# another comment between") == set()
    assert touched("LIMIT = 3\n\n", "LIMIT = 3\n\n\n") == set()


def test_text_inside_a_string_is_code():
    assert touched("# not a comment", "# changed text") == {"m.text"}
    assert touched("# not a comment\n\n", "# not a comment\n\n\n") == {"m.text"}


def test_a_class_body_belongs_to_the_class_and_the_scope_that_runs_it():
    assert touched("size = 4", "size = 5") == {"m.Box", "m"}
    assert touched("return self.size", "return -self.size") == {"m.Box.method"}
    assert touched("    class Local:", "    class Local(object):") == {
        "m.factory.Local",
        "m.factory",
    }


def test_a_new_method_changes_its_class():
    names = touched(
        "    def method(self):",
        "    def __eq__(self, other):\n        return True\n\n    def method(self):",
    )
    assert names == {"m.Box", "m"}


def test_a_new_file_touches_nothing_of_the_parent():
    assert rts.changed_names("m", None, SOURCE.encode()) == set()


def test_a_deleted_function_is_changed():
    names = touched("    def method(self):\n        return self.size\n", "")
    # Its `def` ran in the class body: Box changes (an override is gone).
    assert names == {"m.Box.method", "m.Box", "m"}


def test_a_file_that_does_not_parse_changes_its_module():
    broken = b"def f(:\n    return 1\n"
    assert rts.changed_names("m", broken, broken.replace(b"1", b"2")) == {"m"}


def test_binary_or_undecodable_content_changes_the_module():
    before = b"def a():\n    return 1\n"
    assert rts.changed_names("m", before, before + b"\0") == {"m"}
    assert rts.changed_names("m", before, b"def a():\n    return '\xff'\n") == {"m"}


# --- files and modules -------------------------------------------------------


def test_module_names_follow_the_analysis_root():
    assert rts.module_name("pkg/__init__.py", "") == "pkg"
    assert rts.module_name("pkg/sub/mod.py", "") == "pkg.sub.mod"
    assert rts.module_name("lib/pkg/mod.py", "lib") == "pkg.mod"
    assert rts.module_name("src/pkg/mod.py", "lib") is None
    assert rts.module_name("pkg/data.txt", "") is None


def test_test_files_are_recognised_by_namerts_rule():
    assert rts.is_test_file("tests/test_a.py")
    assert rts.is_test_file("pkg/testing/sub/unittest_b.py")
    assert not rts.is_test_file("tests/helpers.py")
    assert not rts.is_test_file("test_top.py")  # no tests directory
    assert not rts.is_test_file("tests/test_data.txt")


def test_the_conftests_of_a_test_file_are_those_of_its_directories():
    conftests = ["conftest.py", "tests/conftest.py", "tests/sub/conftest.py"]
    conftests.append("other/conftest.py")
    assert rts.conftests_of("tests/sub/test_a.py", conftests) == conftests[:3]
    assert rts.conftests_of("tests/test_b.py", conftests) == conftests[:2]


# --- reachability and the client rules ------------------------------------------

GRAPH = {
    "t.test_x": ["a.f"],
    "a.f": ["a.g"],
    "a.g": ["a.f"],  # a cycle
    "a": ["b.h"],  # a call of a's module-level code
}
MODULES = {"t", "a", "b"}


def test_reachability_is_direct_transitive_and_ends_on_cycles():
    assert rts.reaching(GRAPH, {"a.f"}, MODULES, False) == {"a.f", "a.g", "t.test_x"}
    assert "t.test_x" in rts.reaching(GRAPH, {"a.g"}, MODULES, False)
    assert rts.reaching(GRAPH, {"nowhere"}, MODULES, False) == {"nowhere"}


def test_module_edges_reach_through_the_code_of_a_module():
    assert "t.test_x" not in rts.reaching(GRAPH, {"b.h"}, MODULES, False)
    assert "t.test_x" in rts.reaching(GRAPH, {"b.h"}, MODULES, True)
    assert rts.reaching(GRAPH, {"a"}, MODULES, False) == {"a"}
    assert "t.test_x" in rts.reaching(GRAPH, {"a"}, MODULES, True)


def test_module_edges_reach_the_parent_package():
    graph = {"t.test_x": ["p.m.f"]}
    modules = {"t", "p", "p.m"}
    assert "t.test_x" in rts.reaching(graph, {"p"}, modules, True)
    assert "t.test_x" not in rts.reaching(graph, {"p"}, modules, False)


def test_module_edges_cross_a_namespace_package():
    graph = {"t.test_x": ["p.ns.mod.f"]}
    modules = {"t", "p", "p.ns.mod"}  # p/ns has no __init__.py
    assert "t.test_x" in rts.reaching(graph, {"p"}, modules, True)


def _change(names=(), tests=(), conftests=()):
    return rts.Change(tuple(names), tuple(tests), tuple(conftests), (), ())


SOURCES = {
    "pkg/__init__.py": b"",
    "pkg/core.py": b"def f():\n    g()\ndef g():\n    pass\ndef h():\n    pass\n",
    "pkg/base.py": b"class Base:\n    def f(self):\n        pass\n",
    "pkg/child.py": b"from .base import Base\nclass Child(Base):\n    pass\n",
    "tests/conftest.py": b"def fixture():\n    h()\n",
    "tests/test_a.py": b"def test_one():\n    f()\n",
    "tests/test_b.py": b"def test_two():\n    pass\n",
    "tests/base.py": b"class BaseTests:\n    def test_f(self):\n        pass\n",
    "tests/test_child.py": (
        b"from .base import BaseTests\nclass TestChild(BaseTests):\n    pass\n"
    ),
    "tests/test_uses_child.py": b"def test_child():\n    Child().f()\n",
}
TESTS = [
    "other/tests/test_new.py",
    "tests/test_a.py",
    "tests/test_b.py",
    "tests/test_child.py",
    "tests/test_uses_child.py",
]
CLIENT_GRAPH = {
    "tests.test_a.test_one": ["pkg.core.f"],
    "pkg.core.f": ["pkg.core.g"],
    "tests.conftest.fixture": ["pkg.core.h"],
    "pkg.core": ["pkg.core.setup"],
    "tests.base.BaseTests.test_f": ["pkg.core.helper"],
    "tests.test_uses_child.test_child": ["pkg.base.Base.f"],
}


CLIENT_MODULES = rts.modules_of(SOURCES, "")


def selected(change, rules):
    return rts.select_tests(CLIENT_GRAPH, change, TESTS, CLIENT_MODULES, "", rules)


def test_a_test_file_whose_function_reaches_a_change_is_selected():
    assert selected(_change(["pkg.core.g"]), set()) == ["tests/test_a.py"]
    assert selected(_change(["pkg.core.unused"]), set()) == []


def test_a_change_in_a_test_file_selects_it():
    assert selected(_change(["tests.test_b"]), set()) == ["tests/test_b.py"]
    assert selected(_change(["tests.test_b.test_two"]), set()) == ["tests/test_b.py"]


def test_r1_a_conftest_counts_as_part_of_its_test_files():
    below = ["tests/test_a.py", "tests/test_b.py"]
    below += ["tests/test_child.py", "tests/test_uses_child.py"]
    assert selected(_change(["pkg.core.h"]), {"r1"}) == below
    assert selected(_change(["pkg.core.h"]), set()) == []


def test_r1_a_new_conftest_selects_the_test_files_below_it():
    change = _change(conftests=["other/conftest.py"])
    assert selected(change, {"r1"}) == ["other/tests/test_new.py"]
    assert selected(change, set()) == []


def test_r2_a_module_level_change_selects_the_tests_that_reach_the_module():
    change = _change(["pkg.core"])
    assert selected(change, {"r2"}) == ["tests/test_a.py"]
    assert selected(change, set()) == []
    assert selected(_change(["pkg.core.setup"]), {"r2"}) == ["tests/test_a.py"]


def test_r3_a_new_or_changed_test_file_selects_itself():
    change = _change([], ["other/tests/test_new.py"])
    assert selected(change, {"r3"}) == ["other/tests/test_new.py"]
    assert selected(change, set()) == []


def test_each_variant_of_the_client_has_its_selection():
    change = _change(["pkg.core.h", "pkg.core"], ["other/tests/test_new.py"])
    result = rts.selections(CLIENT_GRAPH, change, TESTS, CLIENT_MODULES, "")
    assert set(result) == {"client", "no r1", "no r2", "no r3", "no rule"}
    assert result["client"] == TESTS
    assert result["no r1"] == ["other/tests/test_new.py", "tests/test_a.py"]
    assert result["no r3"] == TESTS[1:]
    assert result["no rule"] == []


def test_each_rule_alone_decides_some_selection():
    cases = {
        "r1": _change(["pkg.core.h"]),
        "r2": _change(["pkg.core"]),
        "r3": _change([], ["other/tests/test_new.py"]),
    }
    for rule, change in cases.items():
        result = rts.selections(CLIENT_GRAPH, change, TESTS, CLIENT_MODULES, "")
        assert result["client"] != result[f"no {rule}"], rule


# --- metrics -------------------------------------------------------------------


def test_metrics_are_namerts_definitions():
    rows = [
        (["a", "b"], ["a"], 4),  # safe, one of two selected is affected
        (["a"], ["a", "c"], 4),  # unsafe: c is missed
        ([], [], 2),  # nothing affected, nothing selected: safe
    ]
    m = rts.metrics(rows)
    assert (m["commits"], m["safe"]) == (3, 2)
    assert m["safe_rate"] == pytest.approx(2 / 3)
    assert m["test_reduction"] == pytest.approx(7 / 10)
    assert m["precision"] == pytest.approx(2 / 3)


def test_metrics_without_a_selection_have_no_precision():
    m = rts.metrics([([], ["a"], 3)])
    assert m["precision"] is None
    assert m["safe_rate"] == 0
    assert m["test_reduction"] == 1


# --- git ------------------------------------------------------------------------


def commit_files(repo, files, message="change"):
    """Write `files` ({path: text or bytes, or None to delete}) and commit
    them; return the commit."""
    for path, text in files.items():
        target = repo / path
        if text is None:
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            target.write_bytes(text)
        else:
            target.write_text(text)
    fakes.git(repo, "add", "-A")
    fakes.git(repo, "commit", "-q", "-m", message)
    done = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


CORE = """\
def f():
    return g()


def g():
    return 1


def h():
    return 2


LIMIT = 3
"""
EDGES = {
    "tests.test_a.test_one": ["pkg.core.f"],
    "pkg.core.f": ["pkg.core.g"],
    "tests.conftest.fixture": ["pkg.core.h"],
}


@pytest.fixture
def repo(tmp_path):
    """A project with a package, two test files and a conftest, and a call
    graph that the fake tool reads from `edges.py`."""
    repo = tmp_path / "project"
    repo.mkdir()
    fakes.git(repo, "init", "-q")
    parent = commit_files(
        repo,
        {
            "pkg/__init__.py": "",
            "pkg/core.py": CORE,
            "tests/conftest.py": "def fixture():\n    return h()\n",
            "tests/test_a.py": "def test_one():\n    f()\n",
            "tests/test_b.py": "def test_two():\n    pass\n",
            "edges.py": f"EDGES = {EDGES!r}\n",
            "README.txt": "a project\n",
        },
    )
    return repo, parent


def allow_local(monkeypatch):
    monkeypatch.setattr(rts, "HOSTS", ("file://",))
    monkeypatch.setattr(rts, "PROTOCOLS", "file")


def serve(repo):
    fakes.git(repo, "config", "uploadpack.allowFilter", "true")
    fakes.git(repo, "config", "uploadpack.allowAnySHA1InWant", "true")
    return f"file://{repo}"


# --- select, with a fake analysis ------------------------------------------------

FAKE_TOOL = """
import ast, os

class CallGraphGenerator:
    def __init__(self, entry_points, package, *rest):
        self.entry_points, self.package = entry_points, package

    def analyze(self):
        if os.path.exists(os.path.join(self.package, "crash.py")):
            raise RuntimeError("the analysis failed")

    def output(self):
        with open(os.path.join(self.package, "edges.py")) as f:
            tree = ast.parse(f.read())
        edges = ast.literal_eval(tree.body[0].value)
        edges["entry_points"] = sorted(
            os.path.relpath(p, self.package) for p in self.entry_points
        )
        return edges
"""


def fake_tool(tmp_path, **changes):
    checkout = tmp_path / "fake-pycg"
    (checkout / "pycg").mkdir(parents=True, exist_ok=True)
    (checkout / "pycg" / "__init__.py").write_text("")
    (checkout / "pycg" / "pycg.py").write_text(FAKE_TOOL)
    return fakes.run.Tool("fake", "new", checkout, sys.executable)._replace(**changes)


def manifest_for(repo, targets):
    entry = {"root": "", "packages": ["pkg"], "targets": targets, "url": ""}
    entry.update(dataset="d.json", truth="t.json", published=[])
    namerts = {"commit": "0" * 40, "sha256": {}, "url": ""}
    return {"namerts": namerts, "projects": {"demo": entry}}


def work_with(repo, tmp_path):
    work = tmp_path / "work"
    (work / "repos").mkdir(parents=True)
    (work / "repos" / "demo").symlink_to(repo)
    return work


# --- the manifest and the scores ----------------------------------------------------


LOGURU = rts.Project("", "", ("loguru",), "d.json", "t.json", ("n.json", "b.json"))
TARGETS = [{"current": "c1", "parent": "p"}, {"current": "c2", "parent": "p"}]


def namerts_files_for(project=LOGURU):
    truth = [
        {
            "current": "c1",
            "true_parent": "p",
            "tests_to_run": ["t/test_a.py"],
            "total_tests": "4",
        },
        {"current": "c2", "true_parent": "p", "tests_to_run": [], "total_tests": "4"},
    ]
    namerts = [
        {"current": "c1", "tests_to_run": ["t/test_a.py", "t/test_b.py"]},
        {"current": "c2", "tests_to_run": []},
    ]
    babel = [
        {"current": "c1", "tests_to_run": []},
        {"current": "c2", "tests_to_run": ["t/test_a.py"]},
    ]
    return {
        f"ground truth/{project.truth}": truth,
        project.published[0]: namerts,
        project.published[1]: babel,
    }


ALL_TESTS = ["t/test_a.py", "t/test_b.py", "t/test_c.py", "t/test_d.py"]


def selection_data(status="ok", c1=("t/test_a.py",), c2=("t/test_c.py",)):
    """The data of select on the two commits; a failed commit selects all."""

    def record(current, client):
        chosen = list(client) if status == "ok" else ALL_TESTS
        return {
            "current": current,
            "parent": "p",
            "status": status,
            "settings": "s",
            "selected": {v: chosen for v in rts.VARIANTS},
            "changed": ["x"],
            "changed_not_in_graph": [],
            "other_files": [],
            "outside_files": ["setup.py"],
            "select_seconds": 0.1,
            "analysis": {
                "cached": current == "c2",
                "wall_seconds": 2.0,
                "peak_rss_mib": 50.0,
            },
        }

    return {
        "commits": [record("c1", c1), record("c2", c2)],
        "settings": "s",
        "client": rts.client_fingerprint(),
        "dataset": "d",
        "configuration": {"root": "", "packages": ["loguru"]},
        "mapper_python": "CPython 3.15.2",
    }


def test_score_project_scores_tools_published_selections_and_the_truth():
    files = namerts_files_for()
    scores = rts.score_project(TARGETS, files, LOGURU, {"new": selection_data()})
    new = scores["tools"]["new"]["variants"]["client"]
    assert (new["safe_rate"], new["test_reduction"], new["precision"]) == (
        1.0,
        0.75,
        0.5,
    )
    assert scores["tools"]["new"]["safe_by_analysis"] == 1.0
    assert scores["tools"]["new"]["safe_by_fallback"] == 0.0
    namerts = scores["published"]["NameRTS"]
    assert (namerts["safe_rate"], namerts["test_reduction"]) == (1.0, 0.75)
    babel = scores["published"]["BabelRTS"]
    assert babel["safe_rate"] == 0.5 and babel["precision"] == 0.0
    assert scores["ground_truth"]["test_reduction"] == 7 / 8
    runs = scores["tools"]["new"]["runs"]
    assert runs["analyses"] == 1 and runs["max_peak_rss_mib"] == 50.0
    assert scores["tools"]["new"]["unsafe"] == []
    assert scores["tools"]["new"]["commits_with_outside_files"] == 2


def test_failed_commits_are_safe_by_the_fallback_only():
    files = namerts_files_for()
    data = selection_data("timeout")
    scores = rts.score_project(TARGETS, files, LOGURU, {"old": data})
    old = scores["tools"]["old"]
    assert old["failed"] == ["c1", "c2"]
    assert old["runs"]["statuses"] == {"timeout": 2}
    client = old["variants"]["client"]
    assert (client["safe_rate"], client["test_reduction"]) == (1.0, 0.0)
    assert client["precision"] == pytest.approx(1 / 8)
    assert (old["safe_by_analysis"], old["safe_by_fallback"]) == (0.0, 1.0)


@pytest.mark.parametrize(
    "spoil",
    [
        lambda d: d["commits"].pop(),  # a target without a record
        lambda d: d["commits"].append(dict(d["commits"][0])),  # twice
        lambda d: d["commits"][0].update(parent="direct"),  # another parent
        lambda d: d["commits"][1].update(settings="other"),  # another analysis
        lambda d: d.update(client="other"),  # another client
        lambda d: d.update(dataset="other"),  # another dataset
        lambda d: d.update(configuration={"root": "", "packages": ["x"]}),
    ],
)
def test_selections_of_another_experiment_are_refused(spoil):
    data = selection_data()
    spoil(data)
    with pytest.raises(ValueError):
        rts.check_selection(data, TARGETS, "d", {"root": "", "packages": ["loguru"]})


def test_the_selections_of_one_tool_must_share_their_settings():
    first, second = selection_data(), selection_data()
    second["settings"] = "other"
    with pytest.raises(ValueError):
        rts.check_experiment({("new", "a"): first, ("new", "b"): second})
    rts.check_experiment({("new", "a"): first, ("new", "b"): selection_data()})
    # Two tools have their own settings.
    rts.check_experiment({("new", "a"): first, ("old", "a"): second})


def test_every_selection_must_map_lines_with_one_python():
    first, second = selection_data(), selection_data()
    second["mapper_python"] = "CPython 3.14.0"
    with pytest.raises(ValueError):
        rts.check_experiment({("new", "a"): first, ("old", "a"): second})


def report_data(projects):
    version = {
        "tool": {"flavour": "new", "commit": "0123456789ab", "content": "c" * 64},
        "python": "3.15",
        "hash_seed": 0,
        "timeout": 60,
        "memory_limit_mib": 6144,
        "mapper_python": "3.15.2",
    }
    return {
        "namerts": {"commit": "f1acdae0f2b2"},
        "versions": {"new": version},
        "projects": projects,
    }


def test_the_report_has_the_tables_and_the_published_rows():
    scores = rts.score_project(
        TARGETS, namerts_files_for(), LOGURU, {"new": selection_data()}
    )
    text = rts.render(report_data({"loguru": scores}))
    for heading in ("## Results", "## Client rules", "## Unsafe commits", "## Runs"):
        assert heading in text
    assert "| loguru | new | 2 | 0 | 100.0% | 100.0% | 0.0% | 75.0% | 50.0% |" in text
    assert "| loguru | NameRTS (published) | 2 | - | 100.0% | - | - | 75.0% |" in text
    assert "| loguru | BabelRTS (published) | 2 | - | 50.0% |" in text
    assert rts.render(report_data({"loguru": scores})) == text


def test_the_means_are_over_the_projects_and_name_their_denominator():
    files = namerts_files_for()
    one = rts.score_project(TARGETS, files, LOGURU, {"new": selection_data()})
    # The second project selects nothing: its precision is undefined.
    two = rts.score_project(
        TARGETS, files, LOGURU, {"new": selection_data(c1=(), c2=())}
    )
    text = rts.render(report_data({"loguru": one, "other": two}))
    # Safe 100% and 50%; skipped 75% and 100%; precision 50% and undefined.
    assert (
        "| mean of 2 | new | 4 | - | 75.0% | 75.0% | 0.0% | 87.5% | 50.0% (1 of 2) |"
        in text
    )


# --- round 2: the code that runs, the bytes that are read, the names ----------


# --- what the client cannot see -----------------------------------------------
# Each test pins a stated limit (README, "What this client cannot see"). A
# small project is committed twice; the graph is the one the parent's code
# gives, and a comment names the edge that it lacks. The affected test file,
# named here and not taken from the client's output, is not selected; a
# control test file of the same commit is, through a rule. So neither
# "select nothing" nor "select everything" passes these tests.


# --- the export: the inputs of select and score as plain files (no git) --------


def hash_object(data):
    done = subprocess.run(
        ["git", "hash-object", "--stdin"], input=data, capture_output=True, check=True
    )
    return done.stdout.decode().strip()


def test_the_blob_id_is_that_of_git():
    assert rts.git_blob_id(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    assert rts.git_blob_id(b"x = 1\n") == hash_object(b"x = 1\n")


def exported(repo, tmp_path, monkeypatch):
    """A work directory with the project's clone and NameRTS, its export,
    and the manifest of one target that changes pkg.core.g."""
    repo, parent = repo
    commit = commit_files(repo, {"pkg/core.py": CORE.replace("return 1", "return 9")})
    namerts = tmp_path / "namerts"
    namerts.mkdir()
    fakes.git(namerts, "init", "-q")
    pinned = commit_files(
        namerts, {"dataset/d.json": "[]", "ground truth/t.json": "[]"}
    )
    allow_local(monkeypatch)
    monkeypatch.setattr(rts, "NAMERTS_URL", serve(namerts))
    manifest = manifest_for(repo, [{"parent": parent, "current": commit}])
    digest = rts.sha256(b"[]")
    manifest["namerts"] = {
        "commit": pinned,
        "sha256": {"dataset/d.json": digest, "ground truth/t.json": digest},
        "url": "",
    }
    pin_trees(manifest, repo)
    work = work_with(repo, tmp_path)
    rts.clone(rts.NAMERTS_URL, work / "NameRTS")
    out = tmp_path / "export"
    rts.export(work, manifest, ["demo"], out, progress=None)
    return manifest, work, out, parent, commit


def pin_trees(manifest, repo):
    """Pin the file lists of the targets of `demo`, as the manifest does."""
    entry = manifest["projects"]["demo"]
    entry["trees"] = rts.tree_pins(repo, entry["targets"])


# --- the export takes regular Python files; an unavailable blob is a stated target


def test_a_target_that_is_not_exported_is_left_out_of_every_row():
    files = namerts_files_for()
    data = selection_data()
    data["commits"][1] = {
        "current": "c2",
        "parent": "p",
        "status": "not exported",
        "reason": "the export has no blob b of pkg/core.py",
        "settings": "s",
    }
    scores = rts.score_project(TARGETS, files, LOGURU, {"new": data})
    assert scores["excluded"] == [
        {"current": "c2", "reason": "the export has no blob b of pkg/core.py"}
    ]
    # Only c1 counts, in the tool's row and in the published ones.
    assert scores["tools"]["new"]["variants"]["client"]["safe_rate"] == 1.0
    assert scores["published"]["BabelRTS"]["safe_rate"] == 0.0  # c1 alone
    assert scores["ground_truth"]["test_reduction"] == 3 / 4


# --- review round 1: the file lists are pinned by the manifest ------------------


@pytest.mark.parametrize("spoil", ["substituted", "omitted", "renamed", "unpinned"])
def test_a_file_list_that_the_manifest_does_not_pin_is_refused(
    repo, tmp_path, monkeypatch, spoil
):
    manifest, _, out, parent, commit = exported(repo, tmp_path, monkeypatch)
    tree = out / "trees" / "demo" / f"{commit}.json"
    files = json.loads(tree.read_text())
    if spoil == "substituted":  # the parent's list under the commit's name
        files = json.loads((out / "trees" / "demo" / f"{parent}.json").read_text())
    elif spoil == "omitted":
        del files["pkg/core.py"]
    elif spoil == "renamed":
        files["pkg/other.py"] = files.pop("pkg/core.py")
    else:
        del manifest["projects"]["demo"]["trees"][commit]
    tree.write_text(json.dumps(files))
    tool = fake_tool(tmp_path)
    reason = "pins no file list" if spoil == "unpinned" else "not the one"
    with pytest.raises(ValueError) as refused:
        rts.select(
            tmp_path / "alone", manifest, "demo", tool, 60,
            allow_dirty=True, progress=None, export=out,
        )  # fmt: skip
    assert reason in str(refused.value) and commit in str(refused.value)


def test_the_published_manifest_pins_the_file_list_of_every_target_commit():
    manifest = json.loads(rts.MANIFEST.read_text())
    for name, project in manifest["projects"].items():
        wanted = {s for t in project["targets"] for s in (t["parent"], t["current"])}
        assert set(project["trees"]) == wanted, name
        assert all(len(pin) == 64 for pin in project["trees"].values()), name
