"""HeaderGen 1ea5226 (hybrid: imports the analysed modules): run it on one
job and convert its graph to the scorer's names.

HeaderGen is not a static tool. Its import manager puts the package root
first on sys.path and imports, for real, every module that the analysed
code imports, with that module's own imports; its type lookup imports the
libraries that the code names and reads names of live modules with eval.
A module whose import fails is treated as outside code, so the result
depends on what is installed. It therefore runs only in its image, a new
container for every job (evaluation/container/tool_job.py), without
network, as a non-root user, with a read-only root and the analysed source
read-only (round-2 security scan, `.git/handover/round2/prior-tools-scan-2.md`).

Run. In the image of HeaderGen (docker/headergen.Dockerfile, built by
build.py; for a macro project with runtime dependencies, its own image
docker/headergen-deps.Dockerfile on top of it), tool_job runs this file as
the driver, `python3 -S -s -P -B WORK/driver/headergen.py WORK/request.json`. The
driver adds the dependencies of HeaderGen (LIB) and of the project (DEPS,
if the image has them) as site directories, puts the shim and the
HeaderGen tree first on sys.path, and calls the API of the tool:
`CallGraphGenerator(entry_points, root, 500, "call-site")`, `analyze()`,
then `formats.CallSites(cg).get_cg()`. 500 is the iteration limit of the
tool's own product path (static_analysis_helpers.py); the `call-graph`
operation and the CLI are not used (formats.Simple fails on any edge, scan
section 2). The native output is {"cg": {caller: [callees]}}. The stub
cache that HeaderGen writes at its first import
(pycg_extended/machinery/pytd_cache.pickle) is built in the image and is
read-only at run time.

Shim, stated in the table as those of JARVIS and pyan3. The analysis uses
one function of the tool's `framework_models` package: `check_alias`
(typestubs.py:20). The real package loads scikit-learn, scipy, numpy,
nltk, eight pickled classifiers and downloads nltk data at import. Our
module `framework_models` (headergen-shim/), first on sys.path, has the
same function over the pinned `framework_models/aliases/*.json`; the image
holds no other `framework_models`.

Conversion. HeaderGen names nodes as the PyCG of 2021, with line numbers.
- Every dotted part loses a trailing `:<line>` (a definition) and `<n>`
  (an object-sensitive name, processing/base.py); get_cg already strips the
  first. Nodes that become equal are merged, their callees joined, and the
  merges are counted.
- `<dict>.m` and `<list>.m` become `<builtin>.dict.m` and
  `<builtin>.list.m` (csprocessor.py names the methods of dict and list
  variables so); a bare `<dict>` or `<list>` becomes `<builtin>.dict` or
  `<builtin>.list`.
- Every other name is kept as written. A name that keeps another marker
  (`<RETURN>`, `<**INVALID**>`, `<external>`, `<dict1>`) cannot be mapped:
  its edges are kept (they match nothing) and counted.
- Outside callees are named by their definition (`posixpath.join`), the
  benchmarks by access path (`os.path.join`): kept as written; the scorer's
  trace aliases match some of them.

Losses, stated in evaluation/README.md: the graph cannot show HeaderGen's
flow-sensitivity (its own unit is the call site of one line); an analysed
module whose import fails in the container is outside code for it.
"""

import json
import re
import sys
from pathlib import Path

PIN = {
    "name": "HeaderGen",
    "version": "1ea5226",
    "repository": "https://github.com/secure-software-engineering/HeaderGen",
    "commit": "1ea52265ca4e76bb202a2deb26f3b9394d3caa95",
    "typeshed": "be0ef211679fabf020dcc79cd4b5c43af0fa1839",
    "source": "git archive of the pinned commit (pycg_extended, beniget, "
    "typestub-database, framework_models/aliases) and of its typeshed "
    "submodule (stdlib, stubs)",
}
ROW = "HeaderGen (hybrid: imports the analysed modules)"
VARIANTS = ((ROW, {}),)
NOTE = (
    "a shim of 15 lines replaces its framework_models package: the same "
    "check_alias over the pinned aliases/*.json, without the classifier, its "
    "pickles and the nltk download; HeaderGen imports the analysed modules "
    "and the libraries that they name, so each macro project with runtime "
    "dependencies runs in an image that has them (README, Prior tools)"
)
MAX_ITER = 500
OPERATION = "call-site"
# A macro project with runtime dependencies runs in an image that has them
# (build.py dependencies).
PROJECT_DEPENDENCIES = True
IMAGE = {
    "dockerfile": "headergen.Dockerfile",
    "requirements": "headergen-requirements.txt",
    "trees": {
        "headergen": (
            "headergen",
            PIN["commit"],
            (
                "pycg_extended",
                "beniget",
                "typestub-database",
                "framework_models/aliases",
            ),
        ),
        "typeshed": ("typeshed", PIN["typeshed"], ("stdlib", "stubs")),
    },
    "context": ("headergen-shim",),
}
TOOL = {
    "lib": "/opt/tool/lib",
    "deps": "/opt/deps",
    "shim": "/opt/tool/shim",
    "source": "/opt/tool/headergen",
}

UNMAPPED = "kept: a name that cannot be mapped"
MERGED = "merged: callers equal once line numbers are stripped"
_LINE = re.compile(r"(:\d+|<\d+>)+$")
_TYPES = {"<dict>": "dict", "<list>": "list"}
# A dotted part that the benchmark's names allow.
_PART = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*|<lambda[0-9]+>|<builtin>)$")


def command(tool, job, work):
    """How to run `job`, its paths those of the container (tool_job.analyse):
    the job's directory is `work`."""
    request = dict(tool) | {
        "entry_points": [str(p) for p in job.entry_points],
        "root": str(job.root),
        "max_iter": MAX_ITER,
        "operation": OPERATION,
        "output": str(work / "out" / "native.json"),
    }
    driver = str(work / "driver" / Path(__file__).name)
    return {
        "argv": ["python3", "-S", "-s", "-P", "-B", driver, str(work / "request.json")],
        "request": request,
        "output": "native.json",
    }


def name(written):
    """The benchmark's name for a HeaderGen name, and whether it is one: a
    name with a part that the benchmark's names do not allow is kept."""
    parts = [_LINE.sub("", part) for part in written.split(".")]
    if parts[0] in _TYPES:
        parts[0:1] = ["<builtin>", _TYPES[parts[0]]]
    renamed = ".".join(parts)
    return renamed, all(_PART.match(part) for part in parts)


def convert(native, job=None, tool=None):
    """{caller: [callees]} and {note: number} of the native output (the
    parsed JSON). Names are renamed (see the module docstring); callers that
    become equal are merged and counted; an edge with a name that cannot be
    mapped is kept as written and counted."""
    graph, notes = {}, {}
    cg = native["cg"]
    if not isinstance(cg, dict):
        raise TypeError("the call graph is not an object")
    for caller, callees in cg.items():
        if not (isinstance(callees, list) and all(isinstance(c, str) for c in callees)):
            raise TypeError(f"the callees of {caller!r:.80} are not a list of names")
        source, mapped = name(caller)
        if source in graph:
            notes[MERGED] = notes.get(MERGED, 0) + 1
        targets = graph.setdefault(source, set())
        for callee in callees:
            target, mapped_callee = name(callee)
            if not (mapped and mapped_callee):
                notes[UNMAPPED] = notes.get(UNMAPPED, 0) + 1
            targets.add(target)
    return {k: sorted(v) for k, v in sorted(graph.items())}, dict(sorted(notes.items()))


def _run(request):
    """The driver, in the container: the API of HeaderGen on the job."""
    import site

    for directory in (request["lib"], request["deps"]):
        if Path(directory).is_dir():
            site.addsitedir(directory)  # reads its .pth files
    sys.path[:0] = [request["shim"], request["source"]]
    import framework_models

    shim = Path(request["shim"]) / "framework_models"
    if Path(framework_models.__file__).parent != shim:
        sys.exit(f"framework_models is {framework_models.__file__}, not the shim")
    from pycg_extended import formats
    from pycg_extended.pycg import CallGraphGenerator

    generator = CallGraphGenerator(
        request["entry_points"],
        request["root"],
        request["max_iter"],
        request["operation"],
    )
    generator.analyze()
    graph = formats.CallSites(generator).get_cg()
    native = {"cg": {k: sorted(v) for k, v in sorted(graph.items())}}
    Path(request["output"]).write_text(json.dumps(native), encoding="utf-8")


if __name__ == "__main__":
    _run(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
