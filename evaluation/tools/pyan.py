"""pyan3 v2.9.0: run it on one job and convert its graph to the scorer's names.

Run. In the image of pyan3 (docker/pyan.Dockerfile, built by build.py),
evaluation/container/tool_job.py runs this file as the driver, `python3 -S
-s -P -B WORK/driver/pyan.py WORK/request.json`, WORK the job's directory.
It calls the API of the CLI with its defaults,
`pyan.analyzer.CallGraphVisitor(files, root=root)`, and writes the native
output: every uses edge of `visitor.uses_edges`, each end with its full name
(`Node.get_name()`), its flavor and whether it is defined in the analysed
files. Input: every module of the job (pyan reads only the files that it
gets; it does not follow imports), with the package root as `root`, so that
module names are relative to it.

Ordering shim. pyan's Node has no hash of its own, so it hashes by its
address, and sets of Nodes iterate in an order that changes from process
to process. resolve_imports() takes the first same-name candidate of such
a set, so an ambiguous import (a module that imports two functions named
`f`, re-exported through a third module) can resolve differently on two
runs. Before the analysis, the driver gives Node a hash of its namespace and
name (fields that pyan sets once, in the constructor); equality stays
identity. This makes pyan's traversal deterministic without changing its
inference rules; where pyan has an ambiguous choice, the choice can differ
from a run without the shim. The other orders of pyan are insertion orders
of dicts and lists, or sets of strings (PYTHONHASHSEED=0); id() is used
only to test membership (anonscopes.py).

Conversion. A uses edge becomes a call edge when the CLI draws it and its
target is code:
- both ends are defined in the analysed files (the CLI draws only those);
- the target's flavor is function, method, staticmethod or classmethod.
Names are kept as written: pyan's full names are the dotted names of the
benchmarks (`pkg.mod.Class.method`, a package `__init__.py` is the
package). A caller that is a class stays a class (the scorer's normalised
mode moves it to the scope that runs the class body).

Losses, stated in evaluation/README.md:
- Outside callees: pyan defines no node outside the analysed files, so it
  has no outside edge.
- Instantiation: pyan adds an edge to `C.__init__` of a class in the files;
  the edge to the class itself is dropped (a class is not a callee of the
  benchmarks).
- Lambdas, comprehensions: pyan folds them into the enclosing function;
  there is no lambda node, and calls of a lambda are lost.
- "Uses" is not "calls": a reference to a function that is not a call (a
  callback passed as an argument, `x = f`) is an edge. Kept: the tool
  cannot tell them apart, and the benchmark counts some of them.
- Dropped, counted per reason in the run record: targets that are
  wildcards (`*.name`, pyan could not resolve them), names, attributes,
  imported items, modules, classes, and edges with an end that is not
  defined in the files.
"""

import json
import sys
from pathlib import Path

PIN = {
    "name": "pyan3",
    "version": "v2.9.0",
    "repository": "https://github.com/Technologicat/pyan",
    "commit": "8e8bd90646de039696b91fdf2d970e02fe8d1f62",
    "source": "PyPI wheel pyan3==2.9.0 (pyan-requirements.txt)",
}
# The image (build.py): pyan3 and its two dependencies, by hash, in LIB.
IMAGE = {"dockerfile": "pyan.Dockerfile", "requirements": "pyan-requirements.txt"}
LIB = "/opt/tool/lib"
TOOL = {"paths": [LIB]}
CODE_FLAVORS = frozenset({"function", "method", "staticmethod", "classmethod"})


def command(tool, job, work):
    """How to run `job`, its paths those of the container (tool_job.analyse):
    the job's directory is `work`."""
    request = {
        "paths": tool["paths"],
        "files": [str(f) for f in job.files],
        "root": str(job.root),
        "output": str(work / "out" / "native.json"),
    }
    driver = str(work / "driver" / Path(__file__).name)
    return {
        "argv": ["python3", "-S", "-s", "-P", "-B", driver, str(work / "request.json")],
        "request": request,
        "output": "native.json",
    }


def convert(native, job=None, tool=None):
    """{caller: [callees]} and {reason: number of dropped edges} of the
    native output (the parsed JSON of a run)."""
    graph, dropped = {}, {}
    for edge in native["uses"]:
        if not all(isinstance(edge[end], str) for end in ("from", "to")):
            raise TypeError(f"a name of a uses edge is not a string: {edge!r:.80}")
        reason = _dropped(edge)
        if reason:
            dropped[reason] = dropped.get(reason, 0) + 1
            continue
        graph.setdefault(edge["from"], set()).add(edge["to"])
    return {k: sorted(v) for k, v in sorted(graph.items())}, dict(
        sorted(dropped.items())
    )


def _dropped(edge):
    if not (edge["from_defined"] and edge["to_defined"]):
        return "an end not defined in the files"
    if edge["to_flavor"] not in CODE_FLAVORS:
        return f"target flavor {edge['to_flavor']}"
    return None


def _stable_hash(node):
    return hash((node.namespace, node.name))


def _dump(request):
    """The child: analyse and write the native output."""
    sys.path[:0] = request["paths"]
    from pyan.analyzer import CallGraphVisitor
    from pyan.node import Node

    # The ordering shim (see the module docstring): a Node hashes by its
    # namespace and name, which pyan never changes after construction;
    # equality stays identity. Sets of Nodes then iterate in the same order
    # in every process (PYTHONHASHSEED=0), and so does the choice that
    # resolve_imports() makes among same-name candidates.
    Node.__hash__ = _stable_hash

    visitor = CallGraphVisitor(request["files"], root=request["root"])
    rows = []
    for source, targets in visitor.uses_edges.items():
        for target in targets:
            rows.append(
                {
                    "from": source.get_name(),
                    "from_flavor": source.flavor.value,
                    "from_defined": bool(source.defined),
                    "to": target.get_name(),
                    "to_flavor": target.flavor.value,
                    "to_defined": bool(target.defined),
                }
            )
    rows.sort(key=lambda row: json.dumps(row, sort_keys=True))
    text = json.dumps({"uses": rows}, indent=0, sort_keys=True)
    Path(request["output"]).write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    _dump(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
