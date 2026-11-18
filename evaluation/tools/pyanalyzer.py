"""PyAnalyzer ea143ef: run it on one job and convert its graph to the scorer's names.

PyAnalyzer (ICSE 2024) is static: it reads the files as text and parses them
with `ast`; it imports and runs nothing of them (round-3 security scan,
`.git/handover/round3/pyanalyzer-scan-fable.md`). The pin has no licence
file; ENRE-py tag zdh-stable (eb6d0f6, MIT) is the same code but 15 lines.

Run. In the image of PyAnalyzer (docker/pyanalyzer.Dockerfile, built by
build.py; only SourceCode/pyanalyzer of the pin, no PyPI),
evaluation/container/tool_job.py runs this file as the driver,
`python3 -S -s -P -B WORK/driver/pyanalyzer.py WORK/request.json`, WORK the
job's directory. The driver:
1. copies the export of the job (WORK/job) to STAGE, an absolute path of depth
   3 whose parent holds nothing else (its import resolution looks one level
   up, and a root under / or a relative one resolves too few imports);
2. works in a scratch directory (CWD), where the tool writes its outputs;
3. sorts `Path.iterdir`, the shim stated in the table (see NOTE);
4. runs the tool's command line, `pyanalyzer STAGE --cfg --cg`, without a
   builtins stub (the paper's stub is not shipped), standard output
   discarded;
5. writes the native output: {"root": "stage", "call_graph": the JSON of
   stage-call-graph-PyAnalyzer.json} for the row "PyAnalyzer", or
   {"root": "stage", "calls": calls_of(the dependency report)} for the
   row of the report.
An exception of the tool ends the run without a graph: the job is a failed
run with the last line of its error, never a crash of the harness.

Two rows, one per output of the same command (VARIANTS):
- "PyAnalyzer": the call-graph file, the command of its README and of its
  own time measurements.
- "PyAnalyzer (call relations of the dependency report)": each Call relation
  of stage-report-PyAnalyzer.json with its `resolved` targets, the relation
  that the authors' own evaluation read. An edge is kept when its caller is
  a module, function, lambda or class and its target a function or lambda;
  every other one is dropped and counted by kind.

Conversion. Every name starts with the root directory (`stage.`), which is
stripped. A module is named by its path: `pkg.__init__` becomes `pkg`, by
the modules of the job. A lambda is `<scope>.(<line>)`: it becomes the
benchmark's `<scope>.<lambdaN>` (the lambdas of a scope numbered by line and
column, as the compiled code has them: comprehensions and annotation scopes
are not scopes; defaults, decorators, bases, type-parameter bounds and
evaluated annotations count in the enclosing scope; postponed annotations,
the annotations of local variables and the annotation of a target that is
not a plain name (`box[0]: T`, `obj.x: T`, `(x): T`, which Python 3.14 and
later do not compile) do not count; the target and value of an annotated
assignment always count). The
report row names each lambda by its position, which the report gives. The
call-graph file gives the name only: two lambdas of a scope on one line
share a name there, and so do the lambdas inside them; such a name is kept
as written and counted. Class-body code
has the class as its caller (the scorer's normalised mode moves it). A name
that keeps no module of the job is kept as written and counted.

Losses, stated in evaluation/README.md: every callee outside the package
(NO_OUTSIDE; its rows are scored on the edges inside the package); `self`
has values only where the class is instantiated in the analysed code, so
methods of library classes lose their `self.m()` edges in the call-graph
file (the report keeps them); `from . import x`, package objects, calls of
instances and `super()` are not modelled; an inherited `__init__` gives no
edge; defaults are not evaluated.
"""

import ast
import json
import re
import sys
import warnings
from pathlib import Path, PurePosixPath

PIN = {
    "name": "PyAnalyzer",
    "version": "ea143ef",
    "repository": "https://github.com/2024icse/PyAnalyzer",
    "commit": "ea143ef8bdec2b5f8841bb600b348f0975b90349",
    "source": "git archive of the pinned commit, directory SourceCode/pyanalyzer "
    "only (never its .exe, .jar files, Scripts/ or Data/)",
    "licence": "none in the repository; ENRE-py tag zdh-stable (eb6d0f6, MIT) "
    "is the same code but 15 lines",
}
VARIANTS = (
    ("PyAnalyzer", {"output_kind": "call-graph"}),
    (
        "PyAnalyzer (call relations of the dependency report)",
        {"output_kind": "report"},
    ),
)
NOTE = (
    "a shim sorts Path.iterdir, so the order of the files does not depend on "
    "the file system; its heap objects hash by id() and one rule returns after "
    "the first pair of a product of such sets (Resolver.py:394-403), which no "
    "shim removes: the pilot measures that spread; no builtins stub, as the "
    "paper's is not shipped; the pin has no licence file, and ENRE-py tag "
    "zdh-stable (eb6d0f6, MIT) is the same code but 15 lines"
)
NO_OUTSIDE = (
    "PyAnalyzer names no callee outside the package (without a builtins stub "
    "it models neither builtins nor the standard library nor third-party code)"
)
IMAGE = {
    "dockerfile": "pyanalyzer.Dockerfile",
    "trees": {
        "source": ("pyanalyzer", PIN["commit"], ("SourceCode/pyanalyzer",)),
    },
}
# The root keeps the name `stage` and its depth, and its parent holds
# nothing else: the driver makes the parent new for each job (the jobs of a
# service share one container). The working directory is in the job's
# directory, which tool_job.py makes new for each job. These are the paths
# of a job alone; the Nth of K jobs at a time (tool_job.work_of: /tmp/runN)
# stages in /tmp/rootN and works in /tmp/runN/scratch (command).
TOOL = {
    "source": "/opt/tool",
    "stage": "/tmp/root/stage",
    "cwd": "/tmp/run/scratch",
}

CALLERS = frozenset({"Module", "Function", "AnonymousFunction", "Class"})
TARGETS = frozenset({"Function", "AnonymousFunction"})
UNMAPPED = "kept: a name that cannot be mapped"
_LAMBDA = re.compile(r"^\(\d+\)$")


def files(job):
    """The files of `job` that the container gets: the job's modules without
    an empty __init__.py at the root, which no import reaches (a root
    __init__.py with code is refused: leaving it out would lose code)."""
    root = Path(job.root).resolve()
    kept = []
    for path in job.files:
        if Path(path).resolve() == root / "__init__.py":
            if _has_code(path):
                raise ValueError(
                    "cannot stage: the __init__.py at the import root has code"
                )
            continue
        kept.append(path)
    return tuple(kept)


def _has_code(path):
    try:
        return bool(ast.parse(Path(path).read_bytes()).body)
    except (SyntaxError, ValueError):
        return True


def command(tool, job, work):
    """How to run `job`, its paths those of the container (tool_job.analyse):
    the job's directory is `work`, /tmp/run or /tmp/runN, and the stage's
    parent beside it, /tmp/root or /tmp/rootN (the same depth)."""
    slot = work.name.removeprefix("run")
    stage = PurePosixPath(tool["stage"])
    request = dict(tool) | {
        "export": str(job.root),
        "output": str(work / "out" / "native.json"),
        "stage": str(stage.parent.with_name(stage.parent.name + slot) / stage.name),
        "cwd": str(work / PurePosixPath(tool["cwd"]).name),
    }
    driver = str(work / "driver" / Path(__file__).name)
    return {
        "argv": ["python3", "-S", "-s", "-P", "-B", driver, str(work / "request.json")],
        "request": request,
        "output": "native.json",
    }


def calls_of(report):
    """[[caller, its kind, its line, its column, target, its kind, its line,
    its column]] of the Call relations of a dependency report and of their
    resolved targets, sorted, once each. The positions are those of the
    entities: the name of a lambda holds only its line, and its position
    tells two lambdas of one line apart."""
    entities = {entity["id"]: entity for entity in report["variables"]}

    def described(identity):
        entity = entities[identity]
        location = entity.get("location") or {}
        return (
            entity["qualifiedName"],
            entity["category"],
            location.get("startLine"),
            location.get("startColumn"),
        )

    found = set()
    for cell in report["cells"]:
        values = cell["values"]
        if values.get("kind") != "Call":
            continue
        source = described(cell["src"])
        for target_id in (cell["dest"], *values.get("resolved", ())):
            found.add(source + described(target_id))
    return [list(row) for row in sorted(found, key=json.dumps)]


def _names(job):
    """The modules of the job ({PyAnalyzer's module name: the benchmark's}),
    its lambdas by name ({PyAnalyzer's name: the benchmark's, or None when
    the name stands for more than one lambda}) and by position ({(name,
    line, column): the benchmark's})."""
    root = Path(job.root).resolve()
    modules, lambdas, positions = {}, {}, {}
    for path in job.files:
        parts = Path(path).resolve().relative_to(root).with_suffix("").parts
        written = ".".join(parts)
        named = ".".join(
            parts[:-1] if parts[-1] == "__init__" and parts[:-1] else parts
        )
        modules[written] = named
        try:
            with warnings.catch_warnings():  # invalid escapes in old code
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(Path(path).read_bytes())
        except (SyntaxError, ValueError):
            continue
        _scope(tree.body, written, named, lambdas, positions, (_postponed(tree), False))
    return modules, lambdas, positions


def _postponed(tree):
    """Whether a module postpones its annotations (`from __future__ import
    annotations`): they are then strings, and no lambda of theirs runs."""
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )


def _arguments(arguments, postponed):
    """The defaults of a parameter list, and its annotations unless they
    are postponed."""
    found = [*arguments.defaults, *(d for d in arguments.kw_defaults if d)]
    if postponed:
        return found
    every = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
    every += [a for a in (arguments.vararg, arguments.kwarg) if a]
    return found + [a.annotation for a in every if a.annotation is not None]


def _type_parameters(node):
    found = []
    for parameter in getattr(node, "type_params", ()):
        for field in ("bound", "default_value"):
            value = getattr(parameter, field, None)
            if value is not None:
                found.append(value)
    return found


def _evaluated(nodes, rules):
    """The nodes that a scope evaluates itself: everything below `nodes`
    but the bodies of nested functions, classes and lambdas. Their
    decorators, defaults, annotations, bases and type-parameter bounds are
    the scope's: comprehensions and annotation scopes are not scopes, as in
    the benchmark's names, which come from the compiled code. `rules` is
    (postponed, local): postponed annotations are strings, and the
    annotation of a variable inside a function is never evaluated, so
    neither holds a lambda that runs. The annotation of a target that is
    not a plain name (`box[0]: ...`, `obj.x: ...`, `(x): ...`) is not
    compiled from Python 3.14 on, where the benchmark's tracer runs; its
    target and value are."""
    postponed, local = rules
    found = []
    stack = list(reversed(list(nodes)))
    while stack:
        node = stack.pop()
        found.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            outer = [*node.decorator_list, *_arguments(node.args, postponed)]
            if node.returns is not None and not postponed:
                outer.append(node.returns)
            outer += _type_parameters(node)
        elif isinstance(node, ast.AnnAssign):
            outer = [node.target] + ([node.value] if node.value is not None else [])
            if node.simple and not (postponed or local):
                outer.append(node.annotation)
        elif isinstance(node, ast.ClassDef):
            outer = [*node.decorator_list, *node.bases, *node.keywords]
            outer += _type_parameters(node)
        elif isinstance(node, ast.Lambda):
            outer = [*node.args.defaults, *(d for d in node.args.kw_defaults if d)]
        else:
            outer = list(ast.iter_child_nodes(node))
        stack.extend(reversed(outer))
    return found


def _scope(body, written, named, lambdas, positions, rules, unclear=False):
    """Name the lambdas of a scope and of the scopes in it. A name that
    stands for two lambdas (one line, or an outer name that already does)
    maps to None; the position of each lambda maps to its own name. `rules`
    is (postponed, local), see _evaluated."""
    evaluated = _evaluated(body, rules)
    postponed = rules[0]
    own = sorted(
        (n for n in evaluated if isinstance(n, ast.Lambda)),
        key=lambda n: (n.lineno, n.col_offset),
    )
    lines = [n.lineno for n in own]
    for number, node in enumerate(own, 1):
        name = f"{written}.({node.lineno})"
        benchmark = f"{named}.<lambda{number}>"
        shared = unclear or lines.count(node.lineno) > 1 or name in lambdas
        lambdas[name] = None if shared else benchmark
        positions[(name, node.lineno, node.col_offset)] = benchmark
        inner_rules = (postponed, True)
        _scope([node.body], name, benchmark, lambdas, positions, inner_rules, shared)
    for node in evaluated:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            inner = (f"{written}.{node.name}", f"{named}.{node.name}")
            local = not isinstance(node, ast.ClassDef)  # a class body runs its own
            _scope(node.body, *inner, lambdas, positions, (postponed, local), unclear)


def name(written, root, names, at=None):
    """The benchmark's name for a PyAnalyzer name, and whether it is one.
    `names` is what _names gives; `at` the (line, column) of the entity,
    which the report gives and the call-graph file does not."""
    modules, lambdas, positions = names
    prefix = f"{root}."
    if not written.startswith(prefix):
        return written, False
    rest = written[len(prefix) :]
    if at is not None and (rest, *at) in positions:
        return positions[(rest, *at)], True
    if rest in lambdas:
        found = lambdas[rest]
        return (rest, False) if found is None else (found, True)
    parts = rest.split(".")
    for end in range(len(parts), 0, -1):
        module = ".".join(parts[:end])
        if module in modules:
            renamed = ".".join([modules[module], *parts[end:]])
            return renamed, not any(_LAMBDA.match(p) for p in parts[end:])
    return rest, False


def _position(line, column):
    if not all(isinstance(x, int) or x is None for x in (line, column)):
        raise TypeError(f"a position of the report is not a number: {line!r:.20}")
    return None if line is None or column is None else (line, column)


def _edges(native, notes):
    """((caller, its position), (callee, its position)) of the native output
    of either row; the edges of the report that are not calls of code are
    dropped and counted."""
    if "call_graph" in native:
        graph = native["call_graph"]
        if not isinstance(graph, dict):
            raise TypeError("the call graph is not an object")
        for caller, callees in graph.items():
            if not (
                isinstance(callees, list) and all(isinstance(c, str) for c in callees)
            ):
                raise TypeError(
                    f"the callees of {caller!r:.80} are not a list of names"
                )
            for callee in callees or [None]:
                yield (caller, None), (None if callee is None else (callee, None))
        return
    for row in native["calls"]:
        caller, caller_kind, line, column, target, target_kind, at_line, at_column = row
        if not all(
            isinstance(x, str) for x in (caller, caller_kind, target, target_kind)
        ):
            raise TypeError(f"a call of the report is not four names: {row!r:.80}")
        if caller_kind not in CALLERS:
            reason = f"dropped: the caller is a {caller_kind}"
        elif target_kind not in TARGETS:
            reason = f"dropped: the target is a {target_kind}"
        else:
            yield (
                (caller, _position(line, column)),
                (
                    target,
                    _position(at_line, at_column),
                ),
            )
            continue
        notes[reason] = notes.get(reason, 0) + 1


def convert(native, job, tool=None):
    """{caller: [callees]} and {note: number} of the native output (the
    parsed JSON) of either row. Entities are named first, then the edges
    are joined, so that two entities of one name stay apart."""
    root = native["root"]
    if not isinstance(root, str):
        raise TypeError("the root is not a name")
    names = _names(job)
    graph, notes = {}, {}
    for (caller, at), callee in list(_edges(native, notes)):
        source, mapped = name(caller, root, names, at)
        if callee is None:  # a caller without callees
            graph.setdefault(source, set())
            continue
        target, mapped_callee = name(callee[0], root, names, callee[1])
        if not (mapped and mapped_callee):
            notes[UNMAPPED] = notes.get(UNMAPPED, 0) + 1
        graph.setdefault(source, set()).add(target)
    return {k: sorted(v) for k, v in sorted(graph.items())}, dict(sorted(notes.items()))


def _run(request):
    """The driver, in the container: the tool's command line on a copy of
    the job, then its outputs as the native output."""
    import os
    import pathlib
    import shutil

    stage, cwd = Path(request["stage"]), Path(request["cwd"])
    if stage.parent.exists():  # of the job before, in the same container
        shutil.rmtree(stage.parent)
    stage.parent.mkdir(parents=True)  # new: the parent of the root is empty
    shutil.copytree(request["export"], stage)
    cwd.mkdir(parents=True)
    os.chdir(cwd)
    # The shim: the files in the order of their names, not of the file system.
    iterdir = pathlib.Path.iterdir
    pathlib.Path.iterdir = lambda self: iter(sorted(iterdir(self)))
    sys.path[:0] = [request["source"]]
    sys.argv = ["pyanalyzer", str(stage), "--cfg", "--cg"]
    from pyanalyzer.__main__ import main

    with open(os.devnull, "w") as quiet:
        sys.stdout = quiet
        try:
            main()
        finally:
            sys.stdout = sys.__stdout__
    native = {"root": stage.name}
    if request["output_kind"] == "call-graph":
        path = cwd / f"{stage.name}-call-graph-PyAnalyzer.json"
        native["call_graph"] = json.loads(path.read_text(encoding="utf-8"))
    else:
        path = cwd / f"{stage.name}-report-PyAnalyzer.json"
        native["calls"] = calls_of(json.loads(path.read_text(encoding="utf-8")))
    Path(request["output"]).write_text(json.dumps(native), encoding="utf-8")


if __name__ == "__main__":
    _run(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
