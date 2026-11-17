"""CPyGraph v0.1.0: run it on one job and convert its graph to the scorer's names.

Run. In the image of CPyGraph (docker/cpygraph.Dockerfile, built by
build.py), evaluation/container/tool_job.py runs this file as the driver,
`/usr/local/bin/python3.14 -I -S -B WORK/driver/cpygraph.py WORK/request.json`, with
the interpreter that the build embedded in pygCG (the build checks in the
CMake cache that CMake chose it). The driver:
0. copies the export of the job (/job, read-only) to SOURCE on the tmpfs;
1. runs `pygCG SOURCE --no-profile` (built in the image) and reads the JSON
   that it prints, with its `edges`, a list of [caller, callee];
2. compiles each staged module with the same interpreter (compile() only;
   nothing runs) and names every code object twice, as pygCG names it and
   as the benchmarks name it (see code_names);
3. writes them as the native output: {"pygcg": ..., "names": ...,
   "helpers": the pygCG names of the helper frames}.

Staging. pygCG takes a directory, not a list of files, and makes every
module in it a root. SOURCE is a new directory, WORK/stage, with a copy of
the job's modules at their paths below the package root (`files` chooses
them on the host, and the runner exports only them): the package and
nothing else of the project (no tests, no setup.py). Module names are then
relative to the root, as in the benchmarks. Two rules of pygCG's compiler
(package/package.cpp) would change the names, so the staging avoids them:
- An `__init__.py` at the import root makes pygCG prefix every module with
  the directory's name (`stage.main`), and the case's imports no longer
  resolve. The benchmark's import root is the case directory, whose own
  `__init__.py` no import reaches: it is not staged.
- If SOURCE has a child `src` with Python files, pygCG analyses that child
  instead. The staging directory is not named `src`, and a job whose root
  has a `src` directory with modules is refused (none of ours has one).

Isolation. pygCG starts Python twice: the compiler, `<interpreter> -c
"import py_compile ..."` (the interpreter's absolute path is built in, not
looked up on PATH), and an embedded interpreter (`Py_Initialize()`) that
loads the bytecode. Neither runs with -I or -S, and CPyGraph's sources are
not changed. The environment of pygCG holds only PYTHONHASHSEED=0,
PYTHONNOUSERSITE=1 (no user site), PYTHONSAFEPATH=1 (the working directory
is not put on sys.path by `-c`), PYTHONDONTWRITEBYTECODE=1 and TMPDIR (a new
empty directory, where pygCG makes its directory of compiled files);
no PYTHONPATH, no PATH. The working directory is a new empty directory.
What stays: `site` runs in both, so `.pth` files and `sitecustomize` of the
interpreter's own site-packages would run; the image build refuses an
interpreter whose site-packages or standard library has any
(image_checks.py site).

Names. pygCG names code objects as the PyCG of 2021: modules,
`mod.Class.method`, and `<lambdaN>`, numbered per enclosing code object in
the order of the code objects among its constants (main.cpp,
QualifiedNames). The benchmark numbers the lambdas of a scope by the
(line, column) of the `lambda` keyword, and a generator expression or an
annotation scope is not a scope: its lambdas belong to the enclosing one.
code_names reproduces both on the same code objects, and convert renames
callers and callees by it. The identity is the code object itself; its
benchmark number comes from the position of the instruction that loads it,
which points at the `lambda` keyword (as micro-benchmark/validate.py does).
Two lambdas on one line differ by column. No ground truth is read.
The three builtin-type methods of pygCG's fixed list are renamed:
`<**PyDict**>.items`, `<**PyStr**>.join` and `<**PyStr**>.split` become
`<builtin>.dict.items`, `<builtin>.str.join` and `<builtin>.str.split`.

Losses, stated in evaluation/README.md:
- Outside callees: only the 12 names of its fixed list (`<builtin>.len`,
  `map`, `range`, `super`, `input`, `eval`, `exec`, `__import__`,
  `importlib.import_module` and the three above); every other outside edge
  is lost.
- Roots: pygCG passes no entry points to its analysis, so only module
  bodies are roots; a function that no module body reaches is never
  analysed (most of a library).
- A class body is a code object: an edge to `mod.Class` is the execution of
  the class body, not a call; it is kept as written.
"""

import inspect
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

PIN = {
    "name": "CPyGraph",
    "version": "v0.1.0",
    "repository": "https://github.com/awen-li/CPyGraph",
    "commit": "984d34ed2f6b81eecb60b975723ad20394ec4a69",
    "source": "git archive of the pinned commit, built with CMake",
}
# pygCG supports 3.10 to 3.14; the name mapping reads Instruction.positions,
# new in 3.11.
PYTHONS = ((3, 11), (3, 14))
# The image (build.py): the full Python image (a C++ compiler and make,
# CPython's headers and library), CMake from PyPI by hash, the pin's tree in
# /opt/tool/source, built in /opt/tool/build with CPYGRAPH_ABLATION=none.
IMAGE = {
    "dockerfile": "cpygraph.Dockerfile",
    "requirements": "cpygraph-requirements.txt",
    "trees": {"source": ("cpygraph", PIN["commit"], ())},
    "context": ("image_checks.py",),
}
TOOL = {"python": "/usr/local/bin/python3.14", "binary": "/opt/tool/build/tools/pygCG"}
RENAMES = {
    "<**PyDict**>.items": "<builtin>.dict.items",
    "<**PyStr**>.join": "<builtin>.str.join",
    "<**PyStr**>.split": "<builtin>.str.split",
}
# The stage, in the job's directory, which tool_job.py makes new for each
# job (the jobs of a service share one container): not "src", as pygCG would
# analyse a child named src instead.
STAGE = "stage"
# Code objects that are not scopes of the benchmarks: generator expressions
# and the frames of generic parameters (names that no identifier can take),
# the compiler's annotation scopes (`__annotate__` with one positional-only
# parameter `format`) and lazy scopes (parameter `.format`).
_TRANSPARENT_PREFIXES = ("<genexpr>", "<generic parameters of ")
_ANNOTATION_PARAMETERS = ("format",)
_LAZY_SCOPE_PARAMETERS = (".format",)


def files(job):
    """The files of `job` that the container gets, chosen on the host (see
    Staging): the job's modules without the root __init__.py. Raises
    ValueError for a job that cannot be staged."""
    root = Path(job.root).resolve()
    kept = []
    for path in job.files:
        relative = Path(path).resolve().relative_to(root)
        if relative.parts[0] == "src" and len(relative.parts) > 1:
            raise ValueError(
                "cannot stage: the root has a src directory with modules, which "
                "pygCG would analyse instead of the root"
            )
        if relative == Path("__init__.py"):
            if _has_code(path):
                raise ValueError(
                    "cannot stage: the __init__.py at the import root has code, "
                    "which leaving it out would lose"
                )
            continue  # the import root is not a package (see the docstring)
        kept.append(path)
    return tuple(kept)


def command(tool, job, work):
    """How to run `job`, its paths those of the container (tool_job.analyse):
    the job's directory is `work`. The driver copies the export (`job.root`)
    to work/STAGE on the tmpfs, as pygCG may write next to its input, and
    runs pygCG there."""
    stage = str(work / STAGE)
    request = {
        "argv": [tool["binary"], stage, "--no-profile"],
        "export": str(job.root),
        "source": stage,
        "cwd": str(work / "cwd"),
        "env": {
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "PYTHONSAFEPATH": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TMPDIR": str(work / "pygcg"),
        },
        "output": str(work / "out" / "native.json"),
    }
    driver = str(work / "driver" / Path(__file__).name)
    return {
        "argv": [tool["python"], "-I", "-S", "-B", driver, str(work / "request.json")],
        "request": request,
        "output": "native.json",
    }


def convert(native, job=None, tool=None):
    """{caller: [callees]} and {reason: number of dropped edges} of the
    native output: pygCG's edges, renamed by the code-object names.

    A helper frame (the frame of generic parameters, `def f[T]`; an
    annotation scope; a generator expression) is not a scope of the
    benchmarks. As a caller it is its owner: a call made inside it belongs to
    the scope that runs it. As a callee it is an activation of an
    implementation artefact, not a call of the program: the edge is dropped
    and counted. No other edge is dropped; a self-edge stays."""
    names, helpers = native["names"], set(native["helpers"])
    if not all(isinstance(v, str) for v in names.values()):
        raise TypeError("a benchmark name of the driver is not a string")
    graph, dropped = {}, {}
    for caller, callee in native["pygcg"]["edges"]:
        if not (isinstance(caller, str) and isinstance(callee, str)):
            raise TypeError(f"an edge of pygCG is not two names: {caller!r:.40}")
        if callee in helpers:
            reason = f"dropped: the callee is a helper frame ({_kind(callee)})"
            dropped[reason] = dropped.get(reason, 0) + 1
            continue
        callee = RENAMES.get(callee, names.get(callee, callee))
        graph.setdefault(names.get(caller, caller), set()).add(callee)
    return {k: sorted(v) for k, v in sorted(graph.items())}, dropped


def _kind(helper):
    last = helper.rsplit(".", 1)[-1] if "<generic" not in helper else helper
    if "<generic parameters of " in last:
        return "generic parameters"
    if last.startswith("__annotate__"):
        return "annotation scope"
    if last.startswith("<genexpr>"):
        return "generator expression"
    return "lazy scope"


def code_names(code, module, helpers=None):
    """{pygCG's name: the benchmark's name} for the code object of a module
    and every code object nested in it. The pygCG names of the helper frames
    (code objects that are not scopes) are added to the set `helpers`."""
    names = {}
    helpers = set() if helpers is None else helpers
    _name_children(code, module, module, module, names, helpers)
    return names


def _name_children(code, native, scope, owner, names, helpers):
    """Name `code` (pygCG: `native`; benchmark: `owner`) and its children.
    `scope` is the benchmark name under which its children's names nest."""
    names[native] = owner
    numbers = {id(const): n for n, (_, const) in enumerate(sorted(_lambdas(code)), 1)}
    _children(code, native, scope, owner, numbers, names, helpers)


def _children(code, native, scope, owner, numbers, names, helpers):
    count = 0  # pygCG numbers the lambdas of each code object apart
    for const in code.co_consts:
        if not isinstance(const, types.CodeType):
            continue
        if const.co_name == "<lambda>":
            count += 1
            native_name = f"{native}.<lambda{count}>"
        else:
            native_name = f"{native}.{const.co_name}"
        if _is_transparent(const):
            # Not a scope: its names belong to `scope`, its calls to `owner`.
            names[native_name] = owner
            helpers.add(native_name)
            _children(const, native_name, scope, owner, numbers, names, helpers)
            continue
        if const.co_name == "<lambda>":
            name = f"{scope}.<lambda{numbers[id(const)]}>"
        else:
            name = f"{scope}.{const.co_name}"
        if const.co_flags & inspect.CO_OPTIMIZED:  # a function or lambda
            _name_children(const, native_name, name, name, names, helpers)
        else:  # a class body keeps its name, as other tools write it
            names[native_name] = name
            lambdas = sorted(_lambdas(const))
            inner = {id(c): n for n, (_, c) in enumerate(lambdas, 1)}
            _children(const, native_name, name, name, inner, names, helpers)


def _is_transparent(code):
    parameters = code.co_varnames[: code.co_argcount]
    return (
        code.co_name.startswith(_TRANSPARENT_PREFIXES)
        or (
            code.co_name == "__annotate__"
            and code.co_posonlyargcount == 1
            and parameters == _ANNOTATION_PARAMETERS
        )
        or parameters == _LAZY_SCOPE_PARAMETERS
    )


def _lambdas(code):
    """((line, column), code) for the lambdas that `code` names: the
    position of the instruction that loads a lambda's code is that of the
    `lambda` keyword (micro-benchmark/validate.py, _lambdas)."""
    import dis

    starts = {}
    for instruction in dis.get_instructions(code):
        if isinstance(instruction.argval, types.CodeType):
            position = (instruction.positions.lineno, instruction.positions.col_offset)
            starts.setdefault(id(instruction.argval), position)
    found = []
    for const in code.co_consts:
        if not isinstance(const, types.CodeType):
            continue
        if _is_transparent(const):
            found += _lambdas(const)
        elif const.co_name == "<lambda>":
            found.append((starts[id(const)], const))
    return found


def _has_code(path):
    """Whether a module has anything but blank lines, comments and a
    docstring-free nothing: its AST body is not empty."""
    import ast

    try:
        return bool(ast.parse(Path(path).read_bytes()).body)
    except (SyntaxError, ValueError):
        return True


def _module_name(path, root):
    parts = list(Path(path).relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__" and len(parts) > 1:
        parts.pop()
    return ".".join(parts)


def _run(request):
    """The child: pygCG, then the names of the code objects of the stage."""
    if sys.version_info < PYTHONS[0]:
        sys.exit("the name mapping needs Python 3.11 or later")
    shutil.copytree(request["export"], request["source"], symlinks=True)
    for directory in (request["cwd"], request["env"]["TMPDIR"]):
        Path(directory).mkdir(parents=True, exist_ok=True)
    done = subprocess.run(
        request["argv"],
        cwd=request["cwd"],
        env=request["env"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
    )
    if done.returncode != 0:
        sys.exit(done.returncode)
    root = Path(request["source"])
    names, helpers = {}, set()
    for path in sorted(root.rglob("*.py")):
        try:
            code = compile(path.read_bytes(), str(path), "exec", dont_inherit=True)
        except (SyntaxError, ValueError):
            continue  # pygCG has no code object for it either
        names.update(code_names(code, _module_name(path, root), helpers))
    native = {
        "pygcg": json.loads(done.stdout),
        "names": dict(sorted(names.items())),
        "helpers": sorted(helpers),
    }
    Path(request["output"]).write_text(json.dumps(native), encoding="utf-8")


if __name__ == "__main__":
    _run(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
