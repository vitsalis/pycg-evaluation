#!/usr/bin/env python3
"""RQ1, trace recall: the share of the calls that real executions made
that each tool's call graph has, against DyPyBench's traces.

    python -I evaluation/rq1-accuracy/trace_recall.py fetch --work CORPUS_WORK
    python -I evaluation/rq1-accuracy/trace_recall.py run --checkout PYCG \\
        --work CORPUS_WORK [--original ORIGINAL_PYCG] [--graphs DIR] \\
        [--timeout S] [--memory-limit GIB] [--jobs K] [--results DIR]

The reference. DyPyBench (Bouzenia, Krishan and Pradel, FSE 2024;
github.com/sola-st/DyPyBench, MIT) ran the test suites of its projects
under DynaPyt and published the dynamic call graphs in
experiments/DynaPyt_callgraphs.zip. `fetch` downloads that zip, at
DyPyBench's commit bccfda7, as a raw file from raw.githubusercontent.com
(TRACES_URL, only there, only over https), checks its size and SHA-256
and keeps it as CORPUS_WORK/dypybench-traces/DynaPyt_callgraphs.zip. It is
read in memory, never unpacked: only its members
DynaPyt_output/dynapyt_<N>.json, N the line of the project in DyPyBench's
older list of 58 (text/dynapyt_instrument_src.txt), which the corpus
manifest records as each project's `trace`.

The projects. The trees are those of RQ3's corpus: DyPyBench's projects at
their pinned commits (evaluation/rq3-scale/corpus/dypybench.json), fetched
and unpacked by `corpus.py fetch` into CORPUS_WORK/dypybench (in the build
of the inputs image). Nothing of a project is run, imported or installed;
no git. Each tool analyses the project's target of RQ3 (its root and
modules), one run per project in a child process of run.py, under a time
and a memory limit (30 min and 8 GiB by default).

Mapping. The names of a trace are mapped to ours by the rules of branch
eval-dypybench (evaluation/README.md, "Trace recall"): a caller through
the files of the project, a callee inside the project to its definition
(a class to the first __init__ of its method resolution order, only with
the proof of the plain case: CONSTRUCTOR_PROOF), a callee outside it to
`<builtin>.` names or as written. An edge that cannot be mapped is counted
apart, by reason, never as missed. Then the callees of both graphs go
through score.Names for the project's packages, so that the table of
names of one function (score.SAME_FUNCTION) applies as in every result.

What is measured. Per project and tool, recall: the share of the mapped
traced edges that the static graph has, inside the project (the callee is
a definition of the project: the headline) and of all mapped edges. Sums
over the projects whose run finished, and over all projects with a trace,
where a run that did not finish finds none of its edges. Recall only:
an edge that the tests did not run may exist, so precision cannot be
measured against a trace. A project without a trace, or whose commit
cannot be recovered, is a stated row. The prior tools run in their
services (`docker compose run --rm TOOL rq1-trace`: container/tool_job.py,
on the same targets), which store their graphs in the volume tool-graphs;
this maps the traces once and scores those graphs as PyCG's. A row without
a run is "not run" with the command; one whose input changed is "needs
rerun". HeaderGen's row says that it imports the analysed code and the
corpus is not scanned.

Writes RESULTS/rq1-trace-recall.json and .md (counts only: no edge and no
graph; the graphs stay under --graphs), with the scorer and the SHA-256 of
its table, as every result.
"""

import argparse
import ast
import builtins
import hashlib
import importlib.util
import io
import json
import re
import sys
import tempfile
import threading
import warnings
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
EVALUATION = HERE.parent
COMMON = EVALUATION / "common"
STEM = "rq1-trace-recall"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rq3 = _load("evaluation_rq3_table", EVALUATION / "rq3-scale" / "table.py")
run = rq3.run  # one module, so that its exceptions are the ones raised
corpus = rq3.robustness.corpus
score = run.load_sibling("evaluation_score", COMMON / "score.py")
tables = rq3.tables
tool_job = rq3.tool_job
TOOL_GRAPHS = tool_job.GRAPHS  # the volume tool-graphs of compose.yaml

DYPYBENCH_COMMIT = "bccfda78e3b7e4d8a65dfa58d1b0cd3bd35c127d"
TRACES_URL = (
    f"https://raw.githubusercontent.com/sola-st/DyPyBench/{DYPYBENCH_COMMIT}/"
    "experiments/DynaPyt_callgraphs.zip"
)
TRACES_SHA256 = "1b0c7d5551464ccd5bbd54864c7b45b89b1cd775cef374fd5330be5fa84b0c44"
TRACES_SIZE = 91547
TRACES_FILE = Path("dypybench-traces") / "DynaPyt_callgraphs.zip"
TRACE_MEMBER = re.compile(r"DynaPyt_output/dynapyt_([0-9]{1,3})\.json")
MAX_TRACE_BYTES = 64 << 20  # all members, uncompressed
CORPUS = EVALUATION / "rq3-scale" / "corpus" / "dypybench.json"
CORPUS_DIRECTORY = "dypybench"  # under CORPUS_WORK, as the inputs image holds it
TRACE_PREFIX = re.compile(r"^\.DyPyBench\.temp\.project(\d+)\.(.+)$")
NO_TRACE = "no trace: DyPyBench published no dynamic call graph of the project"
NOT_STARTED = rq3.robustness.NOT_STARTED
# SEAM: a runner of the prior tools on DyPyBench's projects. Until it
# exists, these tools have no run here; then each gives one graph per
# project through it, scored by score_project like PyCG's.
REFERENCE_TEXT = (
    "The reference is DyPyBench's traces (`experiments/DynaPyt_callgraphs.zip` "
    "at DyPyBench `{commit}`, SHA-256 `{sha}`): {edges:,} distinct edges over "
    "{traces} traces, from {smallest:,} to {largest:,} per project. The DynaPyt "
    "runs look cut short (a project's trace holds far fewer calls than its "
    "tests execute), so the traces are a small and biased sample of what the "
    "tests execute. The table measures recall only: an edge that the tests "
    "did not run may exist, so precision cannot be measured against a trace."
)


# --------------------------------------------------------------------------
# Names: the files of a project, as DynaPyt and as Python name them
# --------------------------------------------------------------------------


def _parts(relpath):
    """The dotted parts of a file, `__init__` kept: DynaPyt's key."""
    return tuple(PurePosixPath(relpath).with_suffix("").parts)


class Source:
    """The `.py` files of a project (paths relative to its clone, `/`
    separators) and the names of what they define, read with `ast`.

    `root` is the clone on the disk (None if only the file list is known);
    `source_root` the directory that its packages are imported from."""

    def __init__(self, files, root=None, source_root=""):
        self.files = sorted(files)
        self.root = Path(root) if root is not None else None
        self.source_root = source_root
        self.present = frozenset(self.files)
        self.keys = {".".join(_parts(f)): f for f in self.files}
        self._scopes = {}

    # -- files ----------------------------------------------------------

    def caller_file(self, dotted):
        """The file whose DynaPyt key is the longest prefix of `dotted`
        that leaves at most two names, and those names; else None."""
        parts = dotted.split(".")
        for end in range(len(parts), max(len(parts) - 3, 0), -1):
            relpath = self.keys.get(".".join(parts[:end]))
            if relpath is not None:
                return relpath, parts[end:]
        return None

    def module_file(self, name):
        """(file, rule) of a runtime module name, as Python imports it
        (a package before a module file): under the source root, else from
        the repository root; (None, None) if it is not a project module."""
        path = name.replace(".", "/")
        for directory, rule in (
            (self.source_root, "source root"),
            ("", "repository root"),
        ):
            prefix = f"{directory}/" if directory else ""
            for candidate in (f"{prefix}{path}/__init__.py", f"{prefix}{path}.py"):
                if candidate in self.present:
                    return candidate, rule
        return None, None

    def import_name(self, relpath):
        """The name under which Python imports a file: relative to the
        source root if the file is under it, else to the repository root,
        as module_file finds it. A directory without `__init__.py` is a
        namespace package, so it is part of the name."""
        for directory in (self.source_root, ""):
            if _under(relpath, directory):
                inner = (
                    relpath[len(directory.rstrip("/")) + 1 :] if directory else relpath
                )
                return ".".join(_module_parts(inner))
        return ".".join(_module_parts(relpath))

    def module_names(self):
        """The module names of the files, from the repository root."""
        return {".".join(_module_parts(f)) for f in self.files}

    # -- scopes ---------------------------------------------------------

    def scopes(self, relpath):
        """The scope tree of a file (a module Scope), or None if it does
        not parse."""
        if relpath not in self._scopes:
            self._scopes[relpath] = _scope_tree(self.root / relpath)
        return self._scopes[relpath]


def _module_parts(relpath):
    parts = _parts(relpath)
    return parts[:-1] if parts[-1] == "__init__" and len(parts) > 1 else parts


def _under(relpath, directory):
    return not directory or relpath.startswith(directory.rstrip("/") + "/")


def _relative_module(relpath, directory):
    """The module name of a file relative to `directory`; None if the file
    is not under it or is the directory's own __init__.py."""
    if not _under(relpath, directory):
        return None
    inner = relpath[len(directory.rstrip("/")) + 1 :] if directory else relpath
    if inner == "__init__.py":
        return None
    return ".".join(_module_parts(inner))


class Scope:
    """A module, class, function or lambda of a file."""

    def __init__(self, kind, name, nearest_class=None, node=None):
        self.kind = kind
        self.name = name
        self.nearest_class = nearest_class  # DynaPyt: the class around it
        self.node = node
        self.children = {}  # name -> [Scope] (classes and functions)
        self.lambdas = []
        self.functions = []  # of a module: (scope path, Scope) of every def

    def add(self, scope):
        self.children.setdefault(scope.name, []).append(scope)


def _scope_tree(path):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(Path(path).read_bytes())
    except (SyntaxError, ValueError, OSError):
        return None
    module = Scope("module", "", node=tree)
    module.postponed = _postponed(tree)
    _walk(tree, module, module, (), None)
    for scope in _all_scopes(module):
        scope.lambdas.sort(key=lambda s: (s.node.lineno, s.node.col_offset))
        for number, lam in enumerate(scope.lambdas, 1):
            lam.name = f"<lambda{number}>"
    return module


def _all_scopes(scope):
    yield scope
    for group in scope.children.values():
        for child in group:
            yield from _all_scopes(child)
    for lam in scope.lambdas:
        yield from _all_scopes(lam)


def _postponed(tree):
    """Whether the module has `from __future__ import annotations`: then no
    annotation is evaluated."""
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            if any(alias.name == "annotations" for alias in node.names):
                return True
    return False


def _annotations(function, postponed):
    """The annotations of a function that run where it is defined: all of
    them, unless they are postponed."""
    if postponed:
        return []
    args = function.args
    every = [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]
    found = [a.annotation for a in every if a is not None and a.annotation]
    return found + ([function.returns] if function.returns else [])


def _walk(node, scope, module, path, nearest_class):
    """Build the scope tree. Decorators, default values, annotations and
    bases are evaluated by the enclosing scope; comprehensions are not
    scopes. `path` is the qualname of `scope`, as (scope, ...) for lambdas.

    A lambda counts only where code creates it, as the compiler of the
    traced runtime (Python 3.10, DyPyBench's) produces it: no annotation
    runs under `from __future__ import annotations`; a variable annotation
    runs only at the level of a module or a class (inside a function it is
    never evaluated); the annotations of a function run where it is
    defined. Defaults, targets and values always run."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = child.args
            parts = [*child.decorator_list, *args.defaults, *args.kw_defaults]
            parts += _annotations(child, module.postponed)
            for part in parts:
                if part is not None:
                    _walk_one(part, scope, module, path, nearest_class)
            function = Scope("function", child.name, nearest_class, child)
            scope.add(function)
            module.functions.append(((*path, function), function))
            for statement in child.body:
                _walk_one(statement, function, module, (*path, function), nearest_class)
        elif isinstance(child, ast.ClassDef):
            for part in [*child.decorator_list, *child.bases, *child.keywords]:
                _walk_one(part, scope, module, path, nearest_class)
            cls = Scope("class", child.name, nearest_class, child)
            scope.add(cls)
            for statement in child.body:
                _walk_one(statement, cls, module, (*path, cls), child.name)
        elif isinstance(child, ast.AnnAssign):
            parts = [child.target, child.value]
            if not module.postponed and scope.kind in ("module", "class"):
                parts.append(child.annotation)
            for part in parts:
                if part is not None:
                    _walk_one(part, scope, module, path, nearest_class)
        elif isinstance(child, ast.Lambda):
            _walk_one(child.args, scope, module, path, nearest_class)
            lam = Scope("lambda", "<lambda>", nearest_class, child)
            scope.lambdas.append(lam)
            _walk_one(child.body, lam, module, (*path, lam), nearest_class)
        else:
            _walk(child, scope, module, path, nearest_class)


def _walk_one(node, scope, module, path, nearest_class):
    """_walk on one node, itself included."""
    _walk(ast.Module(body=[node], type_ignores=[]), scope, module, path, nearest_class)


def qualname(path):
    """The qualname of a scope path, as our graphs write it."""
    return ".".join(scope.name for scope in path)


# --------------------------------------------------------------------------
# Mapping the names of a trace to our names
# --------------------------------------------------------------------------

# Parts of a runtime __qualname__ that our names do not have.
_QUALNAME_ONLY = {"<locals>", "<listcomp>", "<dictcomp>", "<setcomp>", "<genexpr>"}
_RUNTIME_NAME = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(\.([A-Za-z_][A-Za-z0-9_]*|<[a-z]+>))*$"
)
_BUILTIN_TYPES = frozenset(
    name for name, value in vars(builtins).items() if isinstance(value, type)
)

# The rules, in the order of the report: (key, text).
CALLER_RULES = (
    ("caller: module", "a call at the top level of a file: the module"),
    ("caller: function", "`file.f`: the function `f` of the file outside any class"),
    (
        "caller: method",
        "`file.C.f`: the function `f` whose nearest enclosing class is `C`",
    ),
)
CALLEE_RULES = (
    ("inside: function", "a function of the project, by module and qualname"),
    ("inside: lambda", "`<lambda>`: the only lambda of its scope (`<lambda1>`)"),
    (
        "inside: re-export",
        "a name that a module imports from another module of the project",
    ),
    ("inside: class, own __init__", "a class: its own `__init__`"),
    (
        "inside: class, inherited __init__",
        "a class: the first `__init__` of its method resolution order",
    ),
    ("outside: builtins", "`builtins.f` becomes `<builtin>.f`"),
    ("outside: builtin type", "`str.join` (no module) becomes `<builtin>.str.join`"),
    ("outside: as written", "any other name, `<locals>` removed"),
)
MODULE_RULES = (
    ("module: source root", "the module's file under the source root"),
    ("module: repository root", "the module's file named from the repository root"),
)
# The conditions of the proof that a call of a project class runs a known
# `__init__` (evaluation/README.md, "The proof of a constructor"), in their
# order: the reason why an edge is counted apart is the first condition, in
# this order, that the class fails; if it fails none, the first that its
# bases fail, base by base in the order of its class statement. The proof
# reads the syntax of the modules of the hierarchy and makes one
# assumption, the closed-class assumption: no other code changes a class
# after its `class` statement.
CONSTRUCTOR_PROOF = (
    (
        "class: not at the top level",
        "the class, or a base, is defined inside a class or a function",
    ),
    (
        "class: name not bound once",
        "the name of a class of the hierarchy, or a name that its bases, "
        "metaclass or decorators read, is not bound exactly once by one "
        "top-level `class` statement or import; or a module of the hierarchy "
        "writes an attribute named as the last name of one of these",
    ),
    (
        "class: decorator",
        "a class of the hierarchy has a decorator other than one of the "
        "harmless decorators",
    ),
    (
        "class: keyword",
        "a class of the hierarchy has a keyword other than `metaclass=` naming "
        "a plain metaclass, or a `**` keyword",
    ),
    (
        "class: base not proved",
        "a base is neither a project class nor one of the known bases",
    ),
    (
        "class: __new__ or __init_subclass__",
        "a class of the hierarchy binds `__new__` or `__init_subclass__`",
    ),
    (
        "class: __init__ not one def",
        "a class of the hierarchy binds `__init__` other than by one "
        "undecorated `def` of its body",
    ),
    (
        "class: scope declaration",
        "the body of a class of the hierarchy has a `global` or `nonlocal` statement",
    ),
    (
        "class: attribute set",
        "the module of a class of the hierarchy sets or deletes an attribute "
        "of WRITTEN_ATTRIBUTES on any object, or calls setattr or delattr "
        "(by any name that the module binds to them) with one of them",
    ),
    (
        "class: namespace access",
        "the module of a class of the hierarchy uses `locals`, `vars`, "
        "`globals`, `exec` or `eval`, as a name or an attribute",
    ),
    (
        "class: __set_name__ in the project",
        "a file of the project defines or sets `__set_name__`, which class "
        "creation calls on the values of a class body",
    ),
    (
        "callee: no method resolution order",
        "bases that C3 cannot order, or a class that inherits from itself",
    ),
    (
        "callee: constructor not in the project",
        "a known base other than `object` and `abc.ABC` comes before every "
        "project `__init__` in the order: the constructor may be the builtin's",
    ),
    (
        "callee: class without __init__",
        "no class of the order before `object` has an `__init__`: our graphs "
        "have no edge",
    ),
)
PROOF_ORDER = [key for key, _ in CONSTRUCTOR_PROOF]
CLOSED_CLASS = (
    "A traced call of a class maps to an `__init__` only with the proof of\n"
    'evaluation/README.md ("The proof of a constructor"). The proof reads the\n'
    "syntax of the modules of the class's hierarchy and makes one assumption,\n"
    "the closed-class assumption: no other code changes a class after its\n"
    "`class` statement (a helper or another module that receives the class and\n"
    "sets its attributes, `setattr` with a computed name, a decorator applied\n"
    "by hand elsewhere, a library's `__set_name__`, another module that rebinds\n"
    "a name of a module of the hierarchy, code that reads or writes another\n"
    "frame's namespace). A project that breaks it gets a wrong mapping for\n"
    "the classes concerned, for every tool alike. Two stated limits of the\n"
    "proof have the same consequence: a namespace function reached through an\n"
    "import alias (`from builtins import locals as namespace`) is not seen, nor\n"
    "a rebinding behind an import alias (`from m import Base as Imported` while\n"
    "the write names `Base`) or of a prefix of a dotted name (`base.inner =\n"
    "other` before `class Child(base.inner.Base)`).\n"
    "\n"
    "So the table measures recall over the traced edges that are mapped,\n"
    "given the mapping. The share of traced edges counted apart does not bound\n"
    "the errors among the mapped edges, and the mapping treats every tool\n"
    "alike, but equal treatment does not prove equal bias."
)
NOT_ONCE = object()  # a name bound otherwise than once (see _bound_once)
# The attributes whose change after the class statement changes what a
# call of the class runs: set or deleted on any object of the module of a
# class of the hierarchy, they fail the proof.
WRITTEN_ATTRIBUTES = frozenset(
    {
        "__init__",
        "__new__",
        "__call__",
        "__class__",
        "__bases__",
        "__mro__",
        "__init_subclass__",
    }
)
# The members by which a metaclass changes how its classes are made,
# ordered or called (bound in its body, or set in its module).
METACLASS_MEMBERS = frozenset(
    {"mro", "__call__", "__new__", "__init__", "__prepare__", "__init_subclass__"}
)
# The names by which code reads or changes a namespace while it runs: a
# module that uses one of them, as a name or an attribute, fails the proof.
NAMESPACES = frozenset({"locals", "vars", "globals", "exec", "eval"})
NOT_BOUND = "class: name not bound once"
# The names of the functions that set or delete an attribute.
SETTERS = frozenset({"setattr", "delattr", "__setattr__", "__delattr__"})
# Metaclasses outside the project that are plain: type itself, and ABCMeta,
# whose __new__ only records the abstract methods (Lib/abc.py, _py_abc.py).
# Not EnumMeta: its __call__ looks a member up instead of making an object.
PLAIN_METACLASSES = frozenset({"builtins.type", "abc.ABCMeta"})
# Bases outside the project whose constructor is known, with their method
# resolution order (the same on every Python from 3.10, the traced one; an
# alias names its class): their metaclass is type (abc.ABC: ABCMeta),
# their __new__ makes an object of exactly the class called (OSError picks
# a subclass by errno only when OSError itself is called) and they define
# no __init_subclass__. The builtins are those that the traced projects
# subclass, and their bases.
_EXCEPTION = ("builtins.Exception", "builtins.BaseException", "builtins.object")
KNOWN_BASES = {
    "builtins.object": ("builtins.object",),
    "builtins.BaseException": ("builtins.BaseException", "builtins.object"),
    "builtins.Exception": _EXCEPTION,
    "builtins.ValueError": ("builtins.ValueError", *_EXCEPTION),
    "builtins.RuntimeError": ("builtins.RuntimeError", *_EXCEPTION),
    "builtins.OSError": ("builtins.OSError", *_EXCEPTION),
    "builtins.IOError": ("builtins.OSError", *_EXCEPTION),
    "builtins.dict": ("builtins.dict", "builtins.object"),
    "builtins.list": ("builtins.list", "builtins.object"),
    "builtins.tuple": ("builtins.tuple", "builtins.object"),
    "abc.ABC": ("abc.ABC", "builtins.object"),
}
# Known bases that define no `__init__` (abc.ABC is `__slots__ = ()` only).
WITHOUT_INIT = frozenset({"abc.ABC"})
# Class decorators outside the project that return the class they are given
# and set no attribute of WRITTEN_ATTRIBUTES: total_ordering only adds the
# comparisons that the class lacks (__lt__, __le__, __gt__, __ge__).
HARMLESS_DECORATORS = frozenset({"functools.total_ordering"})


# The reasons why an edge is counted apart, by the side that decides.
UNMAPPED = (
    (
        "caller: no project prefix",
        "the caller is not `.DyPyBench.temp.project<N>.` of this trace",
    ),
    ("caller: file not in the commit", "no file of the commit has the caller's path"),
    (
        "caller: not a function of the file",
        "no function of the file has that name and class",
    ),
    (
        "caller: several functions",
        "several functions of the file have that name and class",
    ),
    ("caller: file does not parse", "the caller's file does not parse"),
    (
        "caller: outside the analysis root",
        "the caller's file is outside the analysis root",
    ),
    (
        "callee: not a name",
        "the text of a called object (a mock, a string), not a name",
    ),
    ("callee: no module", "a function or method whose module the trace does not give"),
    (
        "callee: not a definition",
        "the callee's module is the project's, the name is not defined there",
    ),
    ("callee: one of several lambdas", "`<lambda>` in a scope with several lambdas"),
    *CONSTRUCTOR_PROOF,
    ("callee: file does not parse", "the callee's file does not parse"),
    (
        "callee: outside the analysis root",
        "the callee's file is outside the analysis root",
    ),
)


class Mapper:
    """Maps the names of one trace to our names for one analysis root.

    `root` is the analysis root relative to the clone ('' for the clone).
    Each method returns (name, rule) or (None, reason); `inside` says
    whether a callee is a definition of the project."""

    def __init__(self, source, number, root=""):
        self.source = source
        self.number = number
        self.root = root
        self.module_rules = {}  # callee -> how its module's file was found
        self._classes = {}  # (file, name) -> Scope of a proved class
        self._bindings = {}  # file -> its _module_bindings
        self._writes = {}  # file -> its _module_writes
        self._set_name = None  # whether the project has __set_name__
        self._reach = {}  # (file, name) -> (modules, qualified names) of a proof
        self._imports = None  # the import names of the project's modules

    def our_name(self, relpath, qual):
        module = _relative_module(relpath, self.root)
        if module is None:
            return None
        return f"{module}.{qual}" if qual else module

    def caller(self, name):
        match = TRACE_PREFIX.match(name)
        if not match or int(match[1]) != self.number:
            return None, "caller: no project prefix"
        found = self.source.caller_file(match[2])
        if found is None:
            return None, "caller: file not in the commit"
        relpath, rest = found
        if not rest:
            rule, qual = "caller: module", ""
        else:
            tree = self.source.scopes(relpath)
            if tree is None:
                return None, "caller: file does not parse"
            cls, function = (None, rest[0]) if len(rest) == 1 else rest
            matches = sorted(
                {
                    qualname(path)
                    for path, scope in tree.functions
                    if scope.name == function and scope.nearest_class == cls
                }
            )
            if not matches:
                return None, "caller: not a function of the file"
            if len(matches) > 1:
                return None, "caller: several functions"
            rule = "caller: function" if cls is None else "caller: method"
            qual = matches[0]
        ours = self.our_name(relpath, qual)
        if ours is None:
            return None, "caller: outside the analysis root"
        return ours, rule

    def callee(self, name):
        """(name, rule, inside) or (None, reason, inside)."""
        if not _RUNTIME_NAME.match(name):
            return None, "callee: not a name", False
        parts = name.split(".")
        if parts[0] == "builtins":  # built in: no file of the project shadows it
            return (*_outside(parts), False)
        for end in range(len(parts) - 1, 0, -1):
            relpath, module_rule = self.source.module_file(".".join(parts[:end]))
            if relpath is not None:
                rest = [p for p in parts[end:] if p not in _QUALNAME_ONLY]
                ours, rule = self._inside(relpath, rest, set())
                self.module_rules[name] = f"module: {module_rule}"
                return ours, rule, True
        return (*_outside(parts), False)

    def _inside(self, relpath, rest, seen):
        tree = self.source.scopes(relpath)
        if tree is None:
            return None, "callee: file does not parse"
        if not rest:
            return None, "callee: not a definition"
        path, problem = _find(tree, rest)
        if problem:
            return None, problem
        if path is None:
            return self._reexport(relpath, rest, seen)
        rule = "inside: function"
        last = path[-1]
        if last.kind == "lambda":
            rule = "inside: lambda"
        elif last.kind == "class":
            found = self._constructor(relpath, path)
            if found[0] is None:
                return found
            (relpath, path), rule = found
        ours = self.our_name(relpath, qualname(path))
        if ours is None:
            return None, "callee: outside the analysis root"
        return ours, rule

    def _reexport(self, relpath, rest, seen):
        """A name that the module imports from another project module."""
        if (relpath, rest[0]) in seen or len(seen) > 20:
            return None, "callee: not a definition"
        seen.add((relpath, rest[0]))
        target = self._imported(relpath, rest)
        if target is None:
            return None, "callee: not a definition"
        ours, rule = self._inside(*target, seen)
        if ours is not None and rule == "inside: function":
            rule = "inside: re-export"
        return ours, rule

    def _imported(self, relpath, names):
        """Follow a top-level import of the module that binds `names[0]`:
        (file, the names in it) of the longest module prefix of what the
        import names, or None if the import is not of a project module."""
        bound = self._binding(relpath, names[0])
        if bound is None:
            return None
        dotted = [*bound, *names[1:]]
        for end in range(len(dotted), 0, -1):
            found, _ = self.source.module_file(".".join(dotted[:end]))
            if found is not None:
                return found, dotted[end:]
        return None

    def _binding(self, relpath, name):
        """The dotted name, as a list, that a top-level `import` or `from
        ... import` of the module binds to `name`; None if none does."""
        tree = self.source.scopes(relpath).node
        package = self.source.import_name(relpath).split(".")
        if not relpath.endswith("__init__.py"):
            package = package[:-1]
        for node in _top_level(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if (alias.asname or alias.name) == name:
                        base = (
                            package[: len(package) - node.level + 1]
                            if node.level
                            else []
                        )
                        module = (node.module or "").split(".")
                        return [p for p in (*base, *module, alias.name) if p]
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname == name:
                        return alias.name.split(".")
                    if alias.asname is None and alias.name.split(".")[0] == name:
                        return [name]
        return None

    # -- constructors: the proof of the plain case -------------------------

    def _constructor(self, relpath, path):
        """The `__init__` that a call of a project class runs, as ((file,
        scope path), rule), or (None, reason). Only with the proof of the
        plain case (CONSTRUCTOR_PROOF): then type.__call__ makes the
        object with the `__new__` of a known base and runs the first
        `__init__` of the class's method resolution order."""
        if len(path) != 1:
            return None, "class: not at the top level"
        order, problem = self._proved(relpath, path[0], frozenset())
        if order is None:
            return None, problem
        for index, entry in enumerate(order):
            if entry in KNOWN_BASES:
                if entry == "builtins.object":
                    return None, "callee: class without __init__"
                if entry in WITHOUT_INIT:
                    continue
                return None, "callee: constructor not in the project"
            scope = self._classes[entry]
            init = [
                s for s in scope.children.get("__init__", []) if s.kind == "function"
            ]
            if init:
                rule = (
                    "inside: class, own __init__"
                    if index == 0
                    else "inside: class, inherited __init__"
                )
                return (entry[0], (scope, init[0])), rule
        return None, "callee: class without __init__"

    def _proved(self, relpath, scope, active):
        """The method resolution order of a project class whose hierarchy
        meets every condition of CONSTRUCTOR_PROOF, as a list of (file,
        name) for project classes and names of KNOWN_BASES; or (None, the
        reason): the first condition in PROOF_ORDER that the class fails,
        else the reason of its first base that fails, in source order."""
        key = (relpath, scope.name)
        if key in active:
            return None, "callee: no method resolution order"
        problems, bases = self._class_problems(relpath, scope)
        if problems:
            return None, min(problems, key=PROOF_ORDER.index)
        orders = []
        for kind, found in bases:
            if kind == "class":
                order, problem = self._proved(*found, active | {key})
                if order is None:
                    return None, problem
            else:
                order = list(KNOWN_BASES[found])
            orders.append(order)
        # Condition 2 for the hierarchy: no module of it writes an attribute
        # named as the last name of a name that a class reads.
        modules, names = {relpath}, set(_last_names(_read_by(scope.node)))
        for kind, found in bases:
            if kind == "class":
                reach = self._reach[(found[0], found[1].name)]
                modules |= reach[0]
                names |= reach[1]
        if any(self._module_writes(module)[0] & names for module in modules):
            return None, NOT_BOUND
        bases = [order[0] for order in orders] or ["builtins.object"]
        merged = _c3_merge([*orders, bases] if orders else [bases])
        if merged is None:
            return None, "callee: no method resolution order"
        self._classes[key] = scope
        self._reach[key] = (frozenset(modules), frozenset(names))
        return [key, *merged], None

    def _class_problems(self, relpath, scope):
        """The conditions of CONSTRUCTOR_PROOF that a class fails by itself
        (its bases not followed), as a set of reasons, and its bases
        resolved, in source order: ("class", (file, Scope)) or ("outside",
        a name of KNOWN_BASES)."""
        node = scope.node
        problems, bases = set(), []
        if self._bound_once(relpath, scope.name) is not node:
            problems.add(NOT_BOUND)
        for decorator in node.decorator_list:  # resolved as a name of condition 2
            kind, found = self._read(relpath, decorator)
            if kind != "outside" or found not in HARMLESS_DECORATORS:
                problems.add("class: decorator")
        for keyword in node.keywords:
            if keyword.arg != "metaclass" or not self._plain_metaclass(
                relpath, keyword.value, frozenset()
            ):
                problems.add("class: keyword")
        for base in node.bases:
            kind, found = self._read(relpath, base)
            if kind is None:
                problems.add(found)
            elif kind == "class" or (kind == "outside" and found in KNOWN_BASES):
                bases.append((kind, found))
            else:
                problems.add("class: base not proved")
        body, _, declarations = _scope_bindings(node.body)
        if "__new__" in body or "__init_subclass__" in body:
            problems.add("class: __new__ or __init_subclass__")
        inits = body.get("__init__", [])
        if inits and not (
            len(inits) == 1
            and isinstance(inits[0], ast.FunctionDef)
            and not inits[0].decorator_list
            and any(inits[0] is statement for statement in node.body)
        ):
            problems.add("class: __init__ not one def")
        if declarations:
            problems.add("class: scope declaration")
        written, namespace = self._module_writes(relpath)
        if written & WRITTEN_ATTRIBUTES:
            problems.add("class: attribute set")
        if namespace:
            problems.add("class: namespace access")
        if self._set_name_in_project():
            problems.add("class: __set_name__ in the project")
        return problems, bases

    def _plain_metaclass(self, relpath, expression, active):
        """Whether a metaclass is plain: one of PLAIN_METACLASSES, or a
        project class that meets the conditions of a class (no decorator,
        no keyword), binds none of METACLASS_MEMBERS, has no scope
        declaration, whose module sets none of them nor WRITTEN_ATTRIBUTES
        nor a name that its bases read and uses no name of NAMESPACES, and
        whose bases are all plain metaclasses."""
        kind, found = self._read(relpath, expression)
        if kind == "outside":
            return found in PLAIN_METACLASSES
        if kind != "class":
            return False
        owner, scope = found
        key = (owner, scope.name)
        node = scope.node
        if (
            key in active
            or self._bound_once(owner, scope.name) is not node
            or node.decorator_list
            or node.keywords
            or not node.bases
        ):
            return False
        body, _, declarations = _scope_bindings(node.body)
        if METACLASS_MEMBERS & body.keys() or declarations:
            return False
        written, namespace = self._module_writes(owner)
        if written & (WRITTEN_ATTRIBUTES | METACLASS_MEMBERS) or namespace:
            return False
        if written & _last_names(node.bases):
            return False
        return all(
            self._plain_metaclass(owner, base, active | {key}) for base in node.bases
        )

    def _read(self, relpath, expression):
        """_value of an expression of a class statement."""
        return self._value(relpath, _dotted(expression), frozenset())

    # -- names read at the top level of a module ---------------------------

    def _value(self, relpath, names, seen):
        """What the dotted name `names` read at the top level of a module
        is, each name bound once: ("class", (file, Scope)) for a project
        class, ("module", name) for a project module, ("outside", dotted
        name) for anything outside the project (`builtins.` for a builtin
        that nothing binds), or (None, reason)."""
        if names is None:
            return None, "class: base not proved"
        node = self._bound_once(relpath, names[0])
        if node is NOT_ONCE:
            return None, "class: name not bound once"
        if node is None:
            if not hasattr(builtins, names[0]):
                return None, "class: name not bound once"
            value = ("outside", f"builtins.{names[0]}")
        elif isinstance(node, ast.ClassDef):
            tree = self.source.scopes(relpath)
            scope = [s for s in tree.children[node.name] if s.node is node][0]
            value = ("class", (relpath, scope))
        elif isinstance(node, ast.Import):
            alias = [a for a in node.names if _import_binds(a) == names[0]][0]
            value = self._module(alias.name if alias.asname else names[0])
        else:
            alias = [a for a in node.names if _from_binds(a) == names[0]][0]
            module = self._absolute(relpath, node.level, node.module)
            value = self._attribute(self._module(module), alias.name, seen)
        for name in names[1:]:
            value = self._attribute(value, name, seen)
        return value

    def _attribute(self, value, name, seen):
        """The attribute `name` of a value of _value."""
        kind, found = value
        if kind == "outside":
            return "outside", f"{found}.{name}"
        if kind is None:
            return value
        if kind == "class":  # a class inside a class, or another attribute
            nested = any(s.kind == "class" for s in found[1].children.get(name, []))
            reason = "not at the top level" if nested else "base not proved"
            return None, f"class: {reason}"
        submodule = f"{found}.{name}"
        is_submodule = submodule in self._import_names()
        relpath, _ = self.source.module_file(found)
        if relpath is None:  # a namespace package: only its submodules
            if is_submodule:
                return "module", submodule
            return None, "class: name not bound once"
        node = self._bound_once(relpath, name)
        if node is None and is_submodule:
            return "module", submodule
        if node in (None, NOT_ONCE) or is_submodule or (relpath, name) in seen:
            return None, "class: name not bound once"
        return self._value(relpath, [name], seen | {(relpath, name)})

    def _module(self, dotted):
        """A module name: ("module", it) if the project has it (a file or a
        namespace package), else ("outside", it)."""
        names = self._import_names()
        if dotted in names or any(n.startswith(f"{dotted}.") for n in names):
            return "module", dotted
        return "outside", dotted

    def _import_names(self):
        """The names under which Python imports the modules of the project."""
        if self._imports is None:
            self._imports = frozenset(
                name
                for name in map(self.source.import_name, self.source.files)
                if self.source.module_file(name)[0] is not None
            )
        return self._imports

    def _absolute(self, relpath, level, module):
        """The absolute name of the module of a `from` import in a file."""
        package = self.source.import_name(relpath).split(".")
        if not relpath.endswith("__init__.py"):
            package = package[:-1]
        base = package[: len(package) - level + 1] if level else []
        return ".".join(p for p in (*base, *(module or "").split(".")) if p)

    def _bound_once(self, relpath, name):
        """The one statement that binds `name` at the top level of a module,
        if it is a `class` statement or an import there; None if nothing
        binds it; NOT_ONCE for anything else: several bindings, another
        kind, a binding inside a block, a `global` declaration, or a `*`
        import that may bind it."""
        tree = self.source.scopes(relpath)
        if tree is None:
            return NOT_ONCE
        names, stars, declared = self._module_bindings(relpath)
        if name in declared or any(
            self._star_may_bind(relpath, star, name, frozenset()) for star in stars
        ):
            return NOT_ONCE
        nodes = names.get(name, [])
        if not nodes:
            return None
        node = nodes[0]
        if (
            len(nodes) == 1
            and isinstance(node, (ast.ClassDef, ast.Import, ast.ImportFrom))
            and any(node is statement for statement in tree.node.body)
        ):
            return node
        return NOT_ONCE

    def _star_may_bind(self, relpath, star, name, seen):
        """Whether `from M import *` in a file may bind `name`: unless M is
        a project module whose literal `__all__` leaves it out, or that has
        no `__all__` and binds no public `name` (nor has such a submodule)."""
        module = self._absolute(relpath, star.level, star.module)
        target, _ = self.source.module_file(module)
        if target is None or target in seen or self.source.scopes(target) is None:
            return True
        exported = _literal_all(self.source.scopes(target).node)
        if exported is not None:
            return exported is NOT_ONCE or name in exported
        if name.startswith("_"):
            return False
        names, stars, declared = self._module_bindings(target)
        if name in names or name in declared:
            return True
        if f"{module}.{name}" in self._import_names():
            return True
        return any(
            self._star_may_bind(target, other, name, seen | {relpath})
            for other in stars
        )

    def _module_bindings(self, relpath):
        """({name: [node]}, `*` imports, names declared `global`) of a
        module's top level."""
        if relpath not in self._bindings:
            tree = self.source.scopes(relpath).node
            names, stars, _ = _scope_bindings(tree.body)
            declared = {
                name
                for node in ast.walk(tree)
                if isinstance(node, ast.Global)
                for name in node.names
            }
            self._bindings[relpath] = (names, stars, declared)
        return self._bindings[relpath]

    def _module_writes(self, relpath):
        """(the attribute names that a module sets or deletes on any object:
        by an assignment of any form with a value, `del`, a `for` or `with`
        target, or by setattr or delattr with a literal name, whatever name
        the module calls them by; whether it uses a name of NAMESPACES)."""
        if relpath not in self._writes:
            tree = self.source.scopes(relpath).node
            spellings = _setter_spellings(tree)
            written, namespace = set(), False
            annotations = {
                id(node.target)
                for node in ast.walk(tree)
                if isinstance(node, ast.AnnAssign) and node.value is None
            }
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    namespace = namespace or node.id in NAMESPACES
                elif isinstance(node, ast.Attribute):
                    namespace = namespace or node.attr in NAMESPACES
                    if isinstance(node.ctx, (ast.Store, ast.Del)) and (
                        id(node) not in annotations
                    ):
                        written.add(node.attr)
                elif isinstance(node, ast.Call):
                    func, arguments = node.func, []
                    if isinstance(func, ast.Name) and func.id in spellings:
                        arguments = node.args[1:2]  # setattr(object, name, ...)
                    elif isinstance(func, ast.Attribute) and func.attr in SETTERS:
                        arguments = node.args[:2]  # builtins.setattr, o.__setattr__
                    written.update(
                        a.value
                        for a in arguments
                        if isinstance(a, ast.Constant) and isinstance(a.value, str)
                    )
            self._writes[relpath] = (frozenset(written), namespace)
        return self._writes[relpath]

    def _set_name_in_project(self):
        """Whether a file of the project defines or sets `__set_name__`."""
        if self._set_name is None:
            self._set_name = False
            for relpath in self.source.files:
                tree = self.source.scopes(relpath)
                if tree is not None and any(
                    _names_set_name(node) for node in ast.walk(tree.node)
                ):
                    self._set_name = True
                    break
        return self._set_name


def _last_name(expression):
    """The last name of `a.b.c` or `c`; None for another expression."""
    if isinstance(expression, ast.Attribute):
        return expression.attr
    if isinstance(expression, ast.Name):
        return expression.id
    return None


def _setter_spellings(tree):
    """The names by which a module may call setattr or delattr: theirs, an
    import of them under another name, and the names that an assignment
    binds to one of these (`a = setattr`, `b = a`, followed to a fixed
    point)."""
    spellings = set(SETTERS)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            spellings.update(
                a.asname for a in node.names if a.asname and a.name in SETTERS
            )
    assignments = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)]
    while True:
        found = {
            target.id
            for node in assignments
            if _last_name(node.value) in spellings
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        if found <= spellings:
            return spellings
        spellings |= found


def _read_by(class_node):
    """The expressions of a class statement that the proof reads: its bases,
    its keywords' values and its decorators."""
    return [
        *class_node.bases,
        *(keyword.value for keyword in class_node.keywords),
        *class_node.decorator_list,
    ]


def _last_names(expressions):
    """The last names of the expressions that are a name (`b`, `a.b`): a
    write to an attribute of that name may rebind what they read (`a.b =`,
    or `me.b =` on the module itself)."""
    return frozenset(names[-1] for names in map(_dotted, expressions) if names)


def _names_set_name(node):
    """Whether a node defines or sets `__set_name__`."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name == "__set_name__"
    if isinstance(node, ast.Name):
        return node.id == "__set_name__" and isinstance(node.ctx, ast.Store)
    if isinstance(node, ast.Attribute):
        return node.attr == "__set_name__" and isinstance(node.ctx, ast.Store)
    return isinstance(node, ast.Constant) and node.value == "__set_name__"


def _import_binds(alias):
    """The name that `import a.b [as c]` binds."""
    return alias.asname or alias.name.split(".")[0]


def _from_binds(alias):
    """The name that `from m import a [as c]` binds."""
    return alias.asname or alias.name


def _dotted(expression):
    """The names of `a.b.c` as a list, or None for another expression."""
    names = []
    while isinstance(expression, ast.Attribute):
        names.insert(0, expression.attr)
        expression = expression.value
    if not isinstance(expression, ast.Name):
        return None
    return [expression.id, *names]


def _scope_bindings(statements):
    """({name: [node]}, `*` imports, `global` and `nonlocal` statements) of
    a block of statements of a module or class body: every node that binds
    a name in that scope. Definitions, imports, targets of every form,
    `except ... as`, match captures and walrus; the bodies of functions,
    lambdas and classes are other scopes, but their decorators, bases,
    keywords, defaults and annotations run here. Comprehension targets
    count too (an over-approximation)."""
    names, stars, declarations = {}, [], []
    pending = list(statements)
    while pending:
        node = pending.pop()
        bound = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.append(node.name)
            pending.extend(node.decorator_list)
            if isinstance(node, ast.ClassDef):
                pending.extend([*node.bases, *node.keywords])
            else:
                pending.append(node.args)
                pending.extend([node.returns] if node.returns else [])
        elif isinstance(node, ast.Lambda):
            pending.append(node.args)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            declarations.append(node)
        elif isinstance(node, ast.Import):
            bound.extend(_import_binds(alias) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    stars.append(node)
                else:
                    bound.append(_from_binds(alias))
        else:
            if isinstance(node, ast.Name) and isinstance(
                node.ctx, (ast.Store, ast.Del)
            ):
                bound.append(node.id)
            elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
                bound.extend([node.name] if node.name else [])
            elif isinstance(node, ast.MatchMapping) and node.rest:
                bound.append(node.rest)
            pending.extend(ast.iter_child_nodes(node))
        for name in bound:
            names.setdefault(name, []).append(node)
    return names, stars, declarations


def _literal_all(tree):
    """The names of a module's `__all__`: a set if one top-level assignment
    binds it to a literal list or tuple of strings and the module names it
    nowhere else; None if the module never names it; else NOT_ONCE."""
    uses = [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == "__all__"]
    if not uses:
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if (
            len(uses) == 1
            and targets == [uses[0]]
            and isinstance(node.value, (ast.List, ast.Tuple))
            and all(
                isinstance(e, ast.Constant) and isinstance(e.value, str)
                for e in node.value.elts
            )
        ):
            return {e.value for e in node.value.elts}
    return NOT_ONCE


def _c3_merge(sequences):
    """C3's merge of lists of classes, or None if no order exists."""
    pending = [list(s) for s in sequences if s]
    merged = []
    while pending:
        for sequence in pending:
            head = sequence[0]
            if not any(head in s[1:] for s in pending):
                break
        else:
            return None
        merged.append(head)
        pending = [[c for c in s if c != head] for s in pending]
        pending = [s for s in pending if s]
    return merged


def _top_level(tree):
    """The statements of a module outside functions and classes."""
    pending = list(tree.body)
    while pending:
        node = pending.pop(0)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield node
        for field in ("body", "orelse", "finalbody", "handlers"):
            pending.extend(getattr(node, field, []) or [])


def _find(tree, parts):
    """The scope path of a qualname in a module: (path, None), (None,
    None) if the first name is not defined there, or (None, reason)."""
    current = [tree]
    path = []
    for depth, part in enumerate(parts):
        if part == "<lambda>":
            found = [lam for scope in current for lam in scope.lambdas]
            if len(found) > 1:
                return None, "callee: one of several lambdas"
        else:
            found = [s for scope in current for s in scope.children.get(part, [])]
        if not found:
            return (None, None) if depth == 0 else (None, "callee: not a definition")
        functions = [s for s in found if s.kind != "class"]
        chosen = functions[0] if functions else found[0]
        path.append(chosen)
        current = [s for s in found if s.kind == chosen.kind]
    return tuple(path), None


def _outside(parts):
    """The name of a callee outside the project: (name, rule) or (None, reason)."""
    parts = [p for p in parts if p not in _QUALNAME_ONLY]
    if parts[0] == "builtins" and len(parts) > 1:
        return "<builtin>." + ".".join(parts[1:]), "outside: builtins"
    if len(parts) > 1 and parts[0] in _BUILTIN_TYPES:
        return "<builtin>." + ".".join(parts), "outside: builtin type"
    if len(parts) == 1 or (len(parts) == 2 and parts[0][0].isupper()):
        # `open`, `BufferedReader.read`, `Pattern.match`: no module given
        return None, "callee: no module"
    return ".".join(parts), "outside: as written"


def map_trace(trace, mapper):
    """Map the distinct edges of a trace. Returns a dict: `edges` the
    distinct raw pairs; `inside` and `outside` the mapped pairs (by the
    callee); `rules` and `unmapped` counters of raw pairs."""
    raw = sorted(
        {(caller, callee) for caller, callees in trace.items() for callee in callees}
    )
    inside, outside = set(), set()
    rules, unmapped = Counter(), Counter()
    callers, callees = {}, {}
    for caller, callee in raw:
        if caller not in callers:
            callers[caller] = mapper.caller(caller)
        if callee not in callees:
            callees[callee] = mapper.callee(callee)
        ours_caller, caller_rule = callers[caller]
        ours_callee, callee_rule, is_inside = callees[callee]
        if ours_caller is None:
            unmapped[caller_rule] += 1
            continue
        if ours_callee is None:
            unmapped[callee_rule] += 1
            continue
        rules[caller_rule] += 1
        rules[callee_rule] += 1
        if is_inside:
            rules[mapper.module_rules[callee]] += 1
        (inside if is_inside else outside).add((ours_caller, ours_callee))
    return {
        "edges": len(raw),
        "inside": inside,
        "outside": outside,
        "rules": rules,
        "unmapped": unmapped,
    }


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def compare(mapped, graph, names):
    """Counts of one project and one tool: `graph` is {caller: [callees]},
    or None for a run that failed (every edge counts as missed). `names`
    (a score.Names for the project's packages) maps the callees of both
    graphs, so that two names of one function outside the project (the
    table of score.SAME_FUNCTION) are one callee."""
    static = set()
    nodes, static_callees = set(), set()
    if graph is not None:
        for caller, targets in graph.items():
            nodes.add(caller)
            for target in targets:
                target = names.callee(target)
                static.add((caller, target))
                static_callees.add(target)
        nodes |= static_callees
    inside = {(caller, names.callee(callee)) for caller, callee in mapped["inside"]}
    outside = {(caller, names.callee(callee)) for caller, callee in mapped["outside"]}
    every = inside | outside
    dynamic_callers = {caller for caller, _ in every}
    dynamic_callees = {callee for _, callee in every}
    inside_callees = {callee for _, callee in inside}
    return {
        "dynamic": mapped["edges"],
        "unmapped": sum(mapped["unmapped"].values()),
        "inside": len(inside),
        "inside_found": len(inside & static),
        "all": len(every),
        "all_found": len(every & static),
        "callers": len(dynamic_callers),
        "callers_found": len(dynamic_callers & nodes),
        "callees": len(dynamic_callees),
        "callees_found": len(dynamic_callees & static_callees),
        "inside_callees": len(inside_callees),
        "inside_callees_found": len(inside_callees & static_callees),
    }


COUNT_KEYS = (
    "dynamic", "unmapped", "inside", "inside_found", "all", "all_found",
    "callers", "callers_found", "callees", "callees_found",
    "inside_callees", "inside_callees_found",
)  # fmt: skip


def summed(rows):
    total = dict.fromkeys(COUNT_KEYS, 0)
    for row in rows:
        for key in COUNT_KEYS:
            total[key] += row[key]
    total["projects"] = len(rows)
    return total


# --------------------------------------------------------------------------
# The reference
# --------------------------------------------------------------------------


class TracesRefused(Exception):
    """A traces zip that is not the pinned one, or that cannot be read."""


def fetch(work, download=corpus.download):
    """Download the pinned traces zip into WORK/TRACES_FILE once; check its
    size and SHA-256. Returns "fetched" or "fetched before"; raises
    TracesRefused (and keeps nothing) on another file."""
    path = Path(work) / TRACES_FILE
    if path.is_file() and _sha256(path.read_bytes()) == TRACES_SHA256:
        return "fetched before"
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    try:
        download(TRACES_URL, partial, TRACES_SIZE)
        data = partial.read_bytes()
        if len(data) != TRACES_SIZE or _sha256(data) != TRACES_SHA256:
            raise TracesRefused(f"{TRACES_URL} has another size or SHA-256")
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)
    return "fetched"


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_traces(path):
    """{trace number: {caller: [callees]}} of the pinned zip at `path`, read
    in memory: only the members DynaPyt_output/dynapyt_<N>.json."""
    data = Path(path).read_bytes()
    if _sha256(data) != TRACES_SHA256:
        raise TracesRefused(f"{path} does not have the pinned SHA-256")
    traces = {}
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            match = TRACE_MEMBER.fullmatch(info.filename)
            if not match:
                continue
            total += info.file_size
            if total > MAX_TRACE_BYTES:
                raise TracesRefused(f"{path} holds more than {MAX_TRACE_BYTES} bytes")
            traces[int(match[1])] = json.loads(archive.read(info))
    return dict(sorted(traces.items()))


def _distinct(trace):
    return {(caller, callee) for caller, callees in trace.items() for callee in callees}


def reference(traces):
    """The size of the reference: traces, distinct edges, the fewest and the
    most distinct edges of one trace."""
    sizes = [len(_distinct(trace)) for trace in traces.values()]
    return {
        "traces": len(sizes),
        "edges": sum(sizes),
        "smallest": min(sizes) if sizes else 0,
        "largest": max(sizes) if sizes else 0,
    }


def read_corpus(path=None):
    return json.loads(Path(path or CORPUS).read_text(encoding="utf-8"))


def corpus_facts(manifest, projects):
    """The source trees of the mapping: the corpus manifest by its SHA-256,
    and the commit and archive SHA-256 of each traced project."""
    rows = {row["name"]: row for row in manifest["rows"]}
    return {
        "file": CORPUS.name,
        "sha256": _sha256(CORPUS.read_bytes()),
        "projects": {
            name: {"commit": rows[name]["commit"], "sha256": rows[name]["sha256"]}
            for name, project in projects.items()
            if "trace" in project
        },
    }


# --------------------------------------------------------------------------
# A project
# --------------------------------------------------------------------------


def tree_source(base, source_root):
    """The Source of an unpacked tree: its regular `.py` files (no link),
    with paths relative to `base`."""
    files = [
        p.relative_to(base).as_posix()
        for p in sorted(Path(base).rglob("*.py"))
        if p.is_file() and not p.is_symlink()
    ]
    return Source(files, base, source_root)


def project_inputs(work, row):
    """(target, mapper) of a corpus row with a trace, or the reason why the
    row has no run."""
    directory = Path(work) / CORPUS_DIRECTORY / row["name"]
    target = corpus.target(directory, row["kind"], row.get("root"), row.get("modules"))
    if isinstance(target, str):
        return target
    base = next(directory.iterdir())  # the single directory of the archive
    source = tree_source(base, row.get("root") or "")
    return target, Mapper(source, row["trace"], row.get("root") or "")


def score_project(mapped, graph, packages):
    """The counts of one project and tool (graph None: the run did not
    finish, so every edge is missed)."""
    return compare(mapped, graph, score.Names().for_packages(packages))


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------


def stored_runs(graphs=None):
    """The runs of the prior tools on DyPyBench's targets, which their
    services stored (GRAPHS/<row>/rq1-trace.json of container/tool_job.py):
    ({row: (adapter, data)}, {row: the cell of a row without a usable run})."""
    graphs = Path(graphs or TOOL_GRAPHS)
    found, cells = {}, {}
    for tool in tool_job._build().TOOLS:
        adapter = tool_job._build().load_adapter(tool)
        for row, _ in tool_job.rows(tool):
            if row.startswith(tables.IMPORTS_ANALYSED_CODE):
                cells[row] = tables.missing(tables.UNSCANNED)
                continue
            path = tool_job.result_path(graphs, row, "rq1-trace")
            if not path.is_file():
                command = f"docker compose -f evaluation/compose.yaml run --rm {tool}"
                reason = f"not run: no graphs of the tool ({command} rq1-trace)"
                cells[row] = tables.missing(reason)
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            problem = tool_job.stored_problem(tool, row, data, "rq1-trace")
            if problem:
                cells[row] = tables.missing(problem)
                continue
            if data.get("sha256") != _sha256(CORPUS.read_bytes()):
                reason = "needs rerun: it ran on another manifest of the corpus"
                cells[row] = tables.missing(reason)
                continue
            found[row] = (adapter, data)
    return found, cells


STALE = (
    "needs rerun: the input of a project changed since the tool ran, or "
    "its run or its graph is missing"
)


def run_projects(
    tools,
    work,
    graphs,
    timeout,
    memory_limit_mib,
    progress=print,
    stored=None,
    jobs=1,
):
    """Run each tool on each traced project and score it, and score the
    stored runs of the prior tools (`stored`, of stored_runs) as PyCG's.
    `jobs` runs at a time (run.run_tasks), each with its own limits; the
    results do not depend on `jobs`. Returns (projects, reference facts,
    whether a cleanup failed, the rows of `stored` whose input changed).
    After a run whose processes could not be killed, no run starts: each
    later one is "not run" with the reason, and finds none of its edges."""
    traces = read_traces(Path(work) / TRACES_FILE)
    projects, stale, planned = {}, set(), {}
    for row in read_corpus()["rows"]:
        entry = {"rank": row["rank"]}
        if "file" not in row:
            projects[row["name"]] = entry | {"reason": row["reason"]}
            continue
        if "trace" not in row or row["trace"] not in traces:
            projects[row["name"]] = entry | {"reason": NO_TRACE}
            continue
        inputs = project_inputs(work, row)
        if isinstance(inputs, str):
            projects[row["name"]] = entry | {"reason": inputs}
            continue
        target, mapper = inputs
        mapped = map_trace(traces[row["trace"]], mapper)
        entry |= {
            "trace": row["trace"],
            "edges": mapped["edges"],
            "unmapped": dict(sorted(mapped["unmapped"].items())),
            "rules": dict(sorted(mapped["rules"].items())),
            "runs": {},
        }
        job = run.Job(f"dypybench/{row['name']}", tuple(target.files), target.root)
        projects[row["name"]] = entry
        planned[row["name"]] = (target, mapped, job)

    cleanup_failed = threading.Event()  # the processes would disturb later runs

    def analyse(task):
        name, tool = task
        if cleanup_failed.is_set():
            return {"status": "not run", "error": NOT_STARTED}, None
        graph_path = Path(graphs) / tool.name / f"{name}.json"
        record = run.run_job(
            tool, planned[name][2], graph_path, timeout, memory_limit_mib
        )
        if record["status"] == "cleanup failed":
            cleanup_failed.set()
        if progress:
            progress(f"{tool.name} {name}: {record['status']}")
        graph = None
        if record["status"] == "ok":
            graph = score.check.load_graph(graph_path)
        return record, graph

    tasks = [(name, tool) for name in planned for tool in tools]
    done = dict(
        zip(((n, t.name) for n, t in tasks), run.run_tasks(tasks, analyse, jobs))
    )
    for name, (target, mapped, job) in planned.items():
        entry = projects[name]
        for tool in tools:
            record, graph = done[name, tool.name]
            # No time or memory: the accuracy result is reproducible.
            kept = ("error", "where")
            entry["runs"][tool.name] = {
                "status": record["status"],
                **{k: record[k] for k in kept if k in record},
                **score_project(mapped, graph, target.modules),
            }
        files = tuple(target.files)
        for row_name, (adapter, data) in (stored or {}).items():
            record = data["runs"].get(name)
            if record is None:  # a project that the run did not have
                stale.add(row_name)
                continue
            prior = tool_job.Job(job.key, target.root, files, files)
            graph = data["graphs"].get(name) if record["status"] == "ok" else None
            if tool_job.input_changed(adapter, prior, record) or (
                tool_job.graph_problem(record, graph)
            ):
                stale.add(row_name)
            entry["runs"][row_name] = {
                "status": record["status"],
                **({"error": record["error"]} if "error" in record else {}),
                **score_project(mapped, graph, target.modules),
            }
    for project in projects.values():
        for name in stale:
            project.get("runs", {}).pop(name, None)
    return projects, reference(traces), cleanup_failed.is_set(), stale


def tool_rows(projects, names, versions):
    """The rows of the tools that ran: sums over the finished runs and over
    all projects with a trace."""
    rows = []
    for name in names:
        runs = [p["runs"][name] for p in projects.values() if "runs" in p]
        finished = [r for r in runs if r["status"] == "ok"]
        rows.append(
            {
                "tool": name,
                "version": (versions[name]["commit"] or "?")[:10],
                "finished": summed(finished),
                "all": summed(runs),
            }
        )
    return rows


# --------------------------------------------------------------------------
# The table
# --------------------------------------------------------------------------


def _recall(found, total):
    return "-" if not total else f"{100 * found / total:.1f}% ({found}/{total})"


def render(data):
    facts = data["reference"]["facts"]
    lines = [
        "# RQ1: recall against traces of real executions",
        "",
        "Generated by `evaluation/rq1-accuracy/trace_recall.py`; do not edit. See",
        '`evaluation/README.md`, section "Trace recall".',
        "",
        REFERENCE_TEXT.format(
            commit=DYPYBENCH_COMMIT[:7], sha=TRACES_SHA256[:16], **facts
        ),
        "",
        "Inside: the traced edges whose callee is a definition of the project",
        "(the headline); all: every mapped traced edge. Finished: the projects",
        "whose run finished; all projects: every project with a trace, where a",
        "run that did not finish finds none of its edges. Limits per run:",
        f"{data['limits']['seconds']:g} s and {data['limits']['memory_mib']:g} MiB."
        " Scored with the table of names of one function"
        f" `{data['scorer']['table'][:10]}`.",
        "",
    ]
    if data.get("stopped"):
        lines += [f"Stopped after a failed cleanup: {data['stopped']}.", ""]
    header = ["Tool", "Version", "Finished", "Inside, finished", "All, finished"]
    header += ["Inside, all projects", "All, all projects"]
    body = []
    for row in data["rows"]:
        if "cell" in row:
            body.append([row["tool"], "-", row["cell"]["reason"], "", "", "", ""])
            continue
        done, every = row["finished"], row["all"]
        body.append(
            [
                row["tool"],
                row["version"],
                f"{done['projects']} of {every['projects']}",
                _recall(done["inside_found"], done["inside"]),
                _recall(done["all_found"], done["all"]),
                _recall(every["inside_found"], every["inside"]),
                _recall(every["all_found"], every["all"]),
            ]
        )
    lines += tables.table(header, body)
    for row in data["rows"]:
        if "cell" in row:
            continue
        lines += ["", f"## {row['tool']}, per project", ""]
        project_rows = []
        for name, project in data["projects"].items():
            if "reason" in project:
                project_rows.append([name, project["reason"], "", "", ""])
                continue
            counts = project["runs"][row["tool"]]
            project_rows.append(
                [
                    name,
                    counts["status"],
                    f"{counts['inside_found']} / {counts['inside']}",
                    f"{counts['all_found']} / {counts['all']}",
                    counts["unmapped"],
                ]
            )
        lines += tables.table(
            ["Project", "Run", "Inside found", "All found", "Unmapped"], project_rows
        )
    unmapped = Counter()
    for project in data["projects"].values():
        unmapped.update(project.get("unmapped", {}))
    lines += ["", "Traced edges counted apart, by reason:", ""]
    lines += tables.table(
        ["Reason", "Edges"], [[k, n] for k, n in sorted(unmapped.items())]
    )
    lines += ["", CLOSED_CLASS]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    get = commands.add_parser("fetch", help="download the pinned traces zip")
    get.add_argument("--work", type=Path, required=True, help="CORPUS_WORK")
    table = commands.add_parser("run", help="run the tools and write the table")
    table.add_argument("--checkout", type=Path, required=True, help="PyCG under test")
    table.add_argument("--original", type=Path, help="a checkout of the 2021 PyCG")
    table.add_argument("--work", type=Path, required=True, help="CORPUS_WORK")
    table.add_argument("--graphs", type=Path, help="default: a temporary directory")
    table.add_argument("--timeout", type=float, default=1800, help="seconds")
    table.add_argument("--memory-limit", type=float, default=16, help="GiB")
    table.add_argument("--results", type=Path, default=tables.RESULTS)
    table.add_argument("--python", default=sys.executable)
    table.add_argument(
        "--jobs", type=int, default=1, help="K runs at a time, each with its limits"
    )
    args = parser.parse_args(argv)
    if args.command == "fetch":
        print(fetch(args.work))
        return 0
    if not (args.work / TRACES_FILE).is_file():
        parser.error(f"no {args.work / TRACES_FILE}; build the inputs image")
    manifest = read_corpus()
    try:
        rq3.robustness.plan(manifest, args.work / CORPUS_DIRECTORY)
    except rq3.robustness.NotFetched as error:
        parser.error(
            f"the corpus in {args.work / CORPUS_DIRECTORY} is {error}; "
            "build the inputs image"
        )
    except rq3.robustness.InvalidRow as error:
        parser.error(str(error))
    tools, versions = [], {}
    for name, flavour, checkout in (
        ("PyCG", "new", args.checkout),
        ("original PyCG", "original", args.original),
    ):
        if checkout is None:
            continue
        info = run.git_version(checkout)
        if info["commit"] is None or info["dirty"]:
            problem = "has changes" if info["commit"] else "is not a git checkout"
            parser.error(f"{checkout} {problem}; commit it")
        tools.append(run.Tool(name, flavour, checkout, args.python))
        versions[name] = {"name": name, "flavour": flavour, **info}
    memory_limit_mib = round(args.memory_limit * 1024, 1)
    stored, cells = stored_runs()
    with tempfile.TemporaryDirectory(prefix="trace-recall-") as scratch:
        graphs = args.graphs or Path(scratch)
        projects, facts, stopped, stale = run_projects(
            tools,
            args.work,
            graphs,
            args.timeout,
            memory_limit_mib,
            stored=stored,
            jobs=args.jobs,
        )
    rows = tool_rows(projects, [tool.name for tool in tools], versions)
    if args.original is None:
        rows.append(
            {
                "tool": "original PyCG",
                "cell": tables.missing("not run: no --original checkout given"),
            }
        )
    for name, (_, stored_data) in stored.items():
        if name in stale:
            cells[name] = tables.missing(STALE)
        else:
            versions[name] = {"name": name, **stored_data["tool"]["pin"]}
    for tool in tool_job._build().TOOLS:
        for name, _ in tool_job.rows(tool):
            if name in cells:
                rows.append({"tool": name, "cell": cells[name]})
            else:
                rows += tool_rows(projects, [name], versions)
    data = {
        "reference": {
            "url": TRACES_URL,
            "sha256": TRACES_SHA256,
            "facts": facts,
        },
        "corpus": corpus_facts(manifest, projects),
        "scorer": score.scorer(),
        "python": ".".join(map(str, run.python_version(args.python))),
        "limits": {"seconds": args.timeout, "memory_mib": memory_limit_mib},
        "tools": versions,
        "rows": rows,
        "projects": projects,
    }
    if stopped:
        data["stopped"] = NOT_STARTED
    for path in tables.write(args.results, STEM, data, render(data)):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
