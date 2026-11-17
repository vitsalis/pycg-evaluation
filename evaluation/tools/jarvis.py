"""JARVIS 8871b86: run it on one job and convert its graph to the scorer's names.

Run. In the image of JARVIS (docker/jarvis.Dockerfile, built by build.py),
evaluation/container/tool_job.py runs this file as the driver, `python3 -S
-s -P -B WORK/driver/jarvis.py WORK/request.json`, WORK the job's directory.
It runs the tool's own command line, `jarvis_cli.main()`, with the arguments
`<entry points> --package <root> -o <output>`: the CLI defaults, so no
`--precision` and never `--decy`. Without `--entry_point`, every function of
the given modules is an entry. The entry points are those that PyCG gets:
main.py of a micro case, every module of a macro package. The native output
is the JSON `{caller: [callees]}` that the CLI writes.

Conversion. JARVIS names nodes as the PyCG of 2021, which are the names of
the benchmarks, with one difference: the methods of builtin types.
- `<str>.m`, `<list>.m`, `<int>.m` become `<builtin>.str.m`,
  `<builtin>.list.m`, `<builtin>.int.m`; `<map>.m` becomes
  `<builtin>.dict.m` (JARVIS models dicts as `<map>`, utils/constants.py).
  A bare `<list>` or `<map>` becomes `<builtin>.list` or `<builtin>.dict`.
- Every other name is kept as written: modules, `pkg.mod.Class.method`,
  lambdas `<lambdaN>` (numbered by PyCG's rule), `<builtin>.name`.
- A name that keeps a tool marker after this (`<RETURN>`, `<**INVALID**>`,
  any other `<...>` part that is not `<builtin>`, a lambda or a 2021 builtin
  type `<**PyStr**>`) cannot be mapped. Its edges are kept as written (they
  match nothing, so they count as false positives) and counted in the run
  record.
- Callees that are classes are kept as written. JARVIS links the scope that
  defines a class to the class (`main -> main.A` for `class A:` in main.py);
  the benchmarks have no class callee inside the package, so these edges
  count as false positives.

Losses, stated in evaluation/README.md:
- Outside callees: without `--decy`, JARVIS has no callee outside the
  package other than builtins; edges to the standard library and to
  third-party code are lost.
- Its outside names follow the 2021 rules; the scorer matches them by
  string, and its normalised mode renames the 2021 builtin-type names.
"""

import ast
import json
import re
import sys
import warnings
from pathlib import Path

PIN = {
    "name": "JARVIS",
    "version": "8871b86",
    "repository": "https://github.com/pythonJaRvis/pythonJaRvis.github.io",
    "commit": "8871b86494a1aec0095686ffa543994a7361af88",
    "source": "git archive of the pinned commit, directory Jarvis/tool/Jarvis",
}
# JARVIS needs Python 3.12 or 3.13: it uses ast.Num, gone in 3.14.
TOOL_DIRECTORY = "Jarvis/tool/Jarvis"
# The image (build.py): the pin's tree TOOL_DIRECTORY in SOURCE, setuptools
# (for pkg_resources) by hash in LIB.
IMAGE = {
    "dockerfile": "jarvis.Dockerfile",
    "requirements": "jarvis-requirements.txt",
    "trees": {"source": ("jarvis", f"{PIN['commit']}:{TOOL_DIRECTORY}", ())},
}
SOURCE, LIB = "/opt/tool/src", "/opt/tool/lib"
TOOL = {"paths": [SOURCE, LIB]}
# The rows of the table: (name, settings added to the tool record). The CLI
# defaults (no --precision, never --decy) with the edges as written is the
# headline row; the second drops the edges to classes of the analysed
# source; the third adds --precision, edges as written.
VARIANTS = (
    ("JARVIS", {}),
    ("JARVIS, class edges dropped", {"drop_class_callees": True}),
    ("JARVIS --precision", {"flags": ["--precision"]}),
)
NOTE = (
    "the child imports importlib.metadata before the tool starts: JARVIS "
    "puts an import hook with empty module sources first, then calls "
    "importlib.invalidate_caches(), which on Python 3.10 and later imports "
    "importlib.metadata through that hook and fails"
)
DROPPED_CLASS = "dropped: the callee is a class of the analysed source"

_TYPES = {"<str>": "str", "<list>": "list", "<int>": "int", "<map>": "dict"}
_TYPE = re.compile(r"^(<str>|<list>|<int>|<map>)(?=\.|$)")
# A dotted part that the benchmark's names allow.
_PART = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*|<lambda[0-9]+>|<builtin>|<\*\*Py\w+\*\*>)$"
)


def command(tool, job, work):
    """How to run `job`, its paths those of the container (tool_job.analyse):
    the job's directory is `work`."""
    argv = [
        "jarvis_cli.py",
        *(str(p) for p in job.entry_points),
        "--package",
        str(job.root),
        *tool.get("flags", ()),
        "-o",
        str(work / "out" / "native.json"),
    ]
    driver = str(work / "driver" / Path(__file__).name)
    return {
        "argv": ["python3", "-S", "-s", "-P", "-B", driver, str(work / "request.json")],
        "request": {"paths": tool["paths"], "argv": argv},
        "output": "native.json",
    }


def name(written):
    """The benchmark's name for a JARVIS name, and whether it is one: a name
    with a part that the benchmark's names do not allow is kept as written."""
    renamed = _TYPE.sub(lambda m: f"<builtin>.{_TYPES[m[1]]}", written)
    return renamed, all(_PART.match(part) for part in renamed.split("."))


def convert(native, job=None, tool=None):
    """{caller: [callees]} and {note: number of edges} of the native output
    (the parsed JSON). An edge with a name that cannot be mapped is kept as
    written (it cannot match) and counted. With `drop_class_callees` in the
    tool record, an edge whose callee is a class of the job's files (and not
    also a module or function of them) is dropped and counted."""
    classes = set()
    if tool and tool.get("drop_class_callees"):
        classes = _classes(job.files, job.root)
    graph, notes = {}, {}
    for caller, callees in native.items():
        if not (isinstance(callees, list) and all(isinstance(c, str) for c in callees)):
            raise TypeError(f"the callees of {caller!r:.80} are not a list of names")
        source, mapped = name(caller)
        graph.setdefault(source, set())
        for callee in callees:
            target, mapped_callee = name(callee)
            if target in classes:
                notes[DROPPED_CLASS] = notes.get(DROPPED_CLASS, 0) + 1
                continue
            if not (mapped and mapped_callee):
                key = "kept: a name that cannot be mapped"
                notes[key] = notes.get(key, 0) + 1
            graph[source].add(target)
    return {k: sorted(v) for k, v in sorted(graph.items())}, notes


def _classes(files, root):
    """The classes defined in `files`, named as the benchmarks name them
    (relative to `root`, a package __init__.py is the package), without the
    names that are also a module or a function. A file that does not parse
    is skipped."""
    classes, nodes = set(), set()
    for path in files:
        try:
            with warnings.catch_warnings():  # invalid escapes in old code
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(Path(path).read_bytes())
        except (SyntaxError, ValueError):
            continue
        parts = list(Path(path).resolve().relative_to(Path(root).resolve()).parts)
        parts[-1] = parts[-1].removesuffix(".py")
        if parts[-1] == "__init__" and len(parts) > 1:
            parts.pop()
        module = ".".join(parts)
        nodes.add(module)
        _walk(tree, module, classes, nodes)
    return classes - nodes


def _walk(node, scope, classes, nodes):
    for item in ast.iter_child_nodes(node):
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = f"{scope}.{item.name}"
            (classes if isinstance(item, ast.ClassDef) else nodes).add(name)
            _walk(item, name, classes, nodes)
        else:
            _walk(item, scope, classes, nodes)


def _run(request):
    """The child: the CLI of the tool, with the arguments of the request."""
    sys.path[:0] = request["paths"]
    sys.argv = request["argv"]
    # Python 3.10 and later import importlib.metadata in
    # importlib.invalidate_caches(), which JARVIS calls after it has put its
    # import hook (empty sources) first: the import then fails. Imported
    # here, it is already loaded, as on the Python 3.8 of the tool's README.
    import importlib.metadata  # noqa: F401

    import jarvis_cli

    jarvis_cli.main()


if __name__ == "__main__":
    _run(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
