#!/usr/bin/env python3
"""Resolve the pins of the prior tools' images that need the network, once.

    python3 -I evaluation/tools/pin.py images [TOOL...]
    python3 -I evaluation/tools/pin.py trees [--sources DIR] [TOOL...]
    python3 -I evaluation/tools/pin.py requirements TOOL
    python3 -I evaluation/tools/pin.py deps

images asks the registry for the digest of each base image of pins.json
that has none (`docker buildx imagetools inspect`) and writes NAME@sha256:...
trees copies each tree of a tool from its plain tree in SOURCES (default
/inputs/tools: it runs in the inputs image) and writes its SHA-256. requirements
resolves TOOL-requirements.in (uv pip compile: for the tool's Python and
Linux, wheels only, the newest releases uploaded before the tool's cutoff,
the SHA-256 of every file) into TOOL-requirements.txt, and refuses a result
that is not name==version with hashes or that holds a distribution that the
tool must not have. deps writes, for HeaderGen, the runtime dependencies of
each macro project that lists some (projects.toml `dependencies`) and has
trace requirements: the versions of its trace environment and their
closure, resolved for CPython 3.12 on Linux aarch64 and x86_64 (both hosts
of the evaluation), with the hashes of the files of both, to
headergen-deps/<project>.txt; or, if that is not possible (no wheel
for 3.12, say), a file that says why, so that the project's HeaderGen job
is a stated "not run" and the other builds go on.

requirements and deps resolve against PyPI: they run in the image of the
maintenance commands (the target `maintain` of
evaluation/container/Dockerfile.inputs), with evaluation/tools mounted
writable, never on the host. images asks the registry through docker,
and build.py builds with it. uv reads the metadata of wheels only.
uv gets PATH, HOME, TMPDIR and the proxy variables of the environment, no
UV_* or PIP_* variable and no configuration file.
"""

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("prior_tools_build", HERE / "build.py")
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)
INDEX = "https://pypi.org/simple"

# The resolution of each requirements file: its Python, its upload cutoff
# (the newest release before it), and the distributions it must not have.
RESOLUTIONS = {
    "headergen": {
        "python": "3.12",
        # The day after the pin of HeaderGen (1ea5226, 2024-10-18).
        "cutoff": "2024-10-19T00:00:00Z",
        # Shim B: framework_models is replaced, so none of these is needed.
        "forbidden": ("numpy", "scipy", "scikit-learn", "nltk"),
    },
    "cpygraph": {
        "python": "3.14",
        # The day of this pin; CMake only.
        "cutoff": "2026-11-14T00:00:00Z",
        "forbidden": (),
    },
}
PLATFORM = "linux"  # manylinux on x86_64; the hashes are those of every file
_KEEP = {"path", "home", "tmpdir", "http_proxy", "https_proxy", "all_proxy", "no_proxy"}


class PinError(Exception):
    """A pin that cannot be resolved; the message says why."""


def write_pins(pins, path=build.PINS):
    Path(path).write_text(json.dumps(pins, indent=1, sort_keys=True) + "\n")


def digest_of(image):
    """The digest of `image` in its registry (the network)."""
    done = build_docker(
        "buildx", "imagetools", "inspect", image, "--format", "{{json .Manifest}}"
    )
    if done.returncode:
        raise PinError(f"no digest of {image}: {done.stderr.strip()[:200]}")
    digest = json.loads(done.stdout)["digest"]
    if not digest.startswith("sha256:") or len(digest) != 71:
        raise PinError(f"{image}: {digest!r} is not a SHA-256 digest")
    return digest


def build_docker(*args):
    return subprocess.run(
        ["docker", *args],
        env=build.docker_environment(),
        capture_output=True,
        text=True,
    )


def pin_images(tools, pins):
    for tool in tools:
        name = pins["base_images"][tool]
        if build.PINNED.fullmatch(name):
            print(f"{tool}: {name} (pinned already)")
            continue
        pins["base_images"][tool] = f"{name}@{digest_of(name)}"
        print(f"{tool}: {pins['base_images'][tool]}")
    return pins


def pin_trees(tools, sources, pins):
    for tool in tools:
        trees = build.load_adapter(tool).IMAGE.get("trees", {})
        for name, (clone, treeish, paths) in sorted(trees.items()):
            with tempfile.TemporaryDirectory() as scratch:
                build.export_tree(
                    Path(sources) / clone, treeish, paths, Path(scratch) / "t"
                )
                pins["trees"][f"{tool}/{name}"] = build.tree_hash(Path(scratch) / "t")
            print(f"{tool}/{name}: {pins['trees'][f'{tool}/{name}']}")
    return pins


def uv_environment(environ):
    return {k: v for k, v in environ.items() if k.lower() in _KEEP}


def compile_command(tool, source, target):
    """The uv command that resolves the requirements of `tool`."""
    resolution = RESOLUTIONS[tool]
    return [
        "uv",
        "pip",
        "compile",
        str(source),
        "--output-file",
        str(target),
        "--no-config",
        "--python-version",
        resolution["python"],
        "--python-platform",
        PLATFORM,
        "--only-binary",
        ":all:",
        "--generate-hashes",
        "--exclude-newer",
        resolution["cutoff"],
        "--index-url",
        INDEX,
        "--no-header",
        "--no-annotate",
    ]


def check_requirements(tool, text):
    """{name: version} of a resolved requirements text; PinError for a line
    that is not name==version with a hash, or a forbidden distribution."""
    found = {}
    joined = text.replace("\\\n", " ")
    for line in joined.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        head = line.split()[0]
        if "==" not in head or "--hash=sha256:" not in line:
            raise PinError(f"not name==version with hashes: {line[:80]!r}")
        name, version = head.split("==", 1)
        found[name.lower().replace("_", "-")] = version
    forbidden = sorted(set(found) & set(RESOLUTIONS[tool]["forbidden"]))
    if forbidden:
        raise PinError(
            f"the resolution holds {', '.join(forbidden)}, which {tool} must not have"
        )
    return found


def pin_requirements(tool):
    """Resolve HERE/TOOL-requirements.in into TOOL-requirements.txt."""
    if shutil.which("uv") is None:
        raise PinError("uv is not on PATH")
    source = HERE / f"{tool}-requirements.in"
    target = HERE / f"{tool}-requirements.txt"
    with tempfile.TemporaryDirectory() as scratch:
        out = Path(scratch) / "out.txt"
        done = subprocess.run(
            compile_command(tool, source, out), env=uv_environment(os.environ)
        )
        if done.returncode:
            raise PinError(f"uv pip compile failed (exit status {done.returncode})")
        text = out.read_text(encoding="utf-8")
    found = check_requirements(tool, text)
    resolution = RESOLUTIONS[tool]
    header = (
        f"# Written by evaluation/tools/pin.py requirements {tool}: {source.name}\n"
        f"# resolved for CPython {resolution['python']} on Linux, wheels only,\n"
        f"# the newest releases uploaded before {resolution['cutoff']},\n"
        "# every file by SHA-256. Install it with\n"
        "#   pip install --require-hashes --only-binary :all: --no-deps -r FILE\n"
    )
    target.write_text(header + text, encoding="utf-8")
    return found


# The runtime dependencies of the macro projects for HeaderGen (pin.py deps).
# projects.toml names top-level modules; these have a distribution of
# another name.
MODULE_DISTRIBUTIONS = {
    "cv2": "opencv-python",
    "dns": "dnspython",
    "mpl_toolkits": "matplotlib",
    "win32api": "pywin32",
    "yaml": "pyyaml",
}
DEPS_PYTHON = "3.12"
# Both platforms of the evaluation's Docker hosts (Apple silicon, x86_64).
DEPS_PLATFORMS = ("aarch64-manylinux_2_28", "x86_64-manylinux_2_28")
DEPS_CUTOFF = "2026-11-14T00:00:00Z"  # the day of this pin


def _normal(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def read_requirements(text):
    """{normal name: (name, version, {hashes})} of a requirements text."""
    found = {}
    for line in text.replace("\\\n", " ").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        head = line.split()[0]
        if "==" not in head:
            raise PinError(f"not name==version: {line[:80]!r}")
        name, version = head.split("==", 1)
        hashes = set(re.findall(r"--hash=sha256:(\S+)", line))
        found[_normal(name)] = (name, version, hashes)
    return found


def constraints(trace_requirements):
    """The name==version lines of a project's trace requirements."""
    text = Path(trace_requirements).read_text(encoding="utf-8")
    return [f"{n}=={v}" for n, v, _ in read_requirements(text).values()]


TOOL_REQUIREMENTS = HERE / "headergen-requirements.txt"
TOOL_VERSION = "# tool version: "  # a distribution that the tool's environment gives


def deps_inputs(set_dir, project, tool=None):
    """(the requirements of a project's runtime dependencies, sorted; the
    modules that its trace environment does not hold). Each is the version
    of the trace environment, unless the tool's environment (`tool`: its
    pins) has that distribution: the tool's version then wins (a bare name,
    its version fixed by the constraints)."""
    tool = tool or {}
    modules = sorted(build._projects(set_dir)[project].dependencies)
    trace = Path(set_dir) / "requirements" / f"{project}.txt"
    pinned = read_requirements(trace.read_text(encoding="utf-8"))
    found, missing = set(), []
    for module in modules:
        key = _normal(MODULE_DISTRIBUTIONS.get(module, module))
        entry = pinned.get(key)
        if entry is None:
            missing.append(module)
        elif key in tool:
            found.add(entry[0])
        else:
            found.add(f"{entry[0]}=={entry[1]}")
    return sorted(found), missing


def deps_constraints(trace_requirements, tool):
    """The constraints of a project's resolution: the tool's pins, and the
    trace's pins of every other distribution."""
    text = Path(trace_requirements).read_text(encoding="utf-8")
    trace = read_requirements(text)
    lines = [f"{n}=={v}" for n, v, _ in tool.values()]
    lines += [f"{n}=={v}" for k, (n, v, _) in trace.items() if k not in tool]
    return lines, trace


def deps_command(source, constraints_file, target, platform):
    """The uv command that resolves a project's dependencies for one
    platform: Python 3.12, wheels only, every file by hash."""
    return [
        "uv",
        "pip",
        "compile",
        str(source),
        "--constraints",
        str(constraints_file),
        "--output-file",
        str(target),
        "--no-config",
        "--python-version",
        DEPS_PYTHON,
        "--python-platform",
        platform,
        "--only-binary",
        ":all:",
        "--generate-hashes",
        "--exclude-newer",
        DEPS_CUTOFF,
        "--index-url",
        INDEX,
        "--no-header",
        "--no-annotate",
    ]


def uv_compile(source, constraints_file, target, platform):
    """Run uv for one platform; return (exit status, its standard error)."""
    done = subprocess.run(
        deps_command(source, constraints_file, target, platform),
        env=uv_environment(os.environ),
        capture_output=True,
        text=True,
    )
    return done.returncode, done.stderr


def pin_deps(set_dir, project, target, compile_=uv_compile, tool_requirements=None):
    """Write the pinned runtime dependencies of a project for HeaderGen to
    `target`: the versions of its trace environment and their closure,
    resolved for CPython 3.12 on both platforms, with HeaderGen's own pins
    as constraints, the hashes of both platforms. The tool's environment
    wins: a distribution that it has is left out of the file, and one that
    it has at another version than the trace's is recorded (TOOL_VERSION
    lines). If no resolution exists, `target` says why (build.UNAVAILABLE).
    Return a one-line statement."""
    set_dir, target = Path(set_dir), Path(target)
    tool_text = Path(tool_requirements or TOOL_REQUIREMENTS).read_text(encoding="utf-8")
    tool = read_requirements(tool_text)
    found, missing = deps_inputs(set_dir, project, tool)
    where = f"{set_dir.name}/requirements/{project}.txt"
    header = [
        "# Written by evaluation/tools/pin.py deps: the runtime dependencies of",
        f"# {set_dir.name}/{project} for HeaderGen, at the versions of {where}",
        f"# and their closure, for CPython {DEPS_PYTHON} on",
        f"# {' and '.join(DEPS_PLATFORMS)}; every file by SHA-256.",
    ]
    if missing:
        header.append(
            f"# Not in the trace environment, not installed: {', '.join(missing)}"
        )
    reason, merged = None, {}
    if not found:
        reason = f"none of {', '.join(missing)} is in the trace environment"
    with tempfile.TemporaryDirectory() as scratch:
        scratch = Path(scratch)
        (scratch / "in.txt").write_text("".join(f"{p}\n" for p in found))
        trace = set_dir / "requirements" / f"{project}.txt"
        lines, trace_pins = deps_constraints(trace, tool)
        (scratch / "constraints.txt").write_text("\n".join(lines) + "\n")
        for platform in DEPS_PLATFORMS if found else ():
            out = scratch / f"{platform}.txt"
            status, error = compile_(
                scratch / "in.txt", scratch / "constraints.txt", out, platform
            )
            if status != 0:
                tail = " ".join(error.split())[-300:]
                reason = (
                    f"no resolution against HeaderGen's own pins for CPython "
                    f"{DEPS_PYTHON} on {platform}: {tail}"
                )
                break
            for key, (name, version, hashes) in read_requirements(
                out.read_text()
            ).items():
                if key in merged and merged[key][1] != version:
                    reason = f"{name} resolves to {merged[key][1]} and {version}"
                    break
                merged.setdefault(key, (name, version, set()))[2].update(hashes)
            if reason:
                break
    target.parent.mkdir(parents=True, exist_ok=True)
    if reason:
        target.write_text("\n".join([*header, f"{build.UNAVAILABLE}{reason}"]) + "\n")
        return f"unavailable: {reason}"
    lines = list(header)
    lines.append("# The tool's environment wins; at another version than the trace's:")
    for key, (name, version, _) in sorted(merged.items()):
        trace_pin = trace_pins.get(key)
        if key in tool and trace_pin and trace_pin[1] != tool[key][1]:
            lines.append(f"{TOOL_VERSION}{name}=={tool[key][1]} (trace {trace_pin[1]})")
    for key, (name, version, hashes) in sorted(merged.items()):
        if key in tool:
            continue  # the tool's environment has it
        lines.append(f"{name}=={version} \\")
        ordered = sorted(hashes)
        for i, digest in enumerate(ordered):
            end = " \\" if i < len(ordered) - 1 else ""
            lines.append(f"    --hash=sha256:{digest}{end}")
    target.write_text("\n".join(lines) + "\n")
    return f"pinned: {', '.join(found)}"


def pin_all_deps(set_dir=None, compile_=uv_compile):
    """pin_deps for every project of the macro-benchmark (`set_dir`: its
    directory) with runtime dependencies and trace requirements; return
    {project: statement}."""
    set_dir = Path(set_dir or build.REPOSITORY / "macro-benchmark")
    statements = {}
    for project, entry in sorted(build._projects(set_dir).items()):
        trace = set_dir / "requirements" / f"{project}.txt"
        if not entry.dependencies or not trace.is_file():
            continue
        target = build.deps_file(set_dir, project)
        statements[project] = pin_deps(set_dir, project, target, compile_)
    return statements


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("what", choices=("images", "trees", "requirements", "deps"))
    parser.add_argument("tools", nargs="*", metavar="TOOL")
    parser.add_argument("--sources", type=Path, default=build.DEFAULT_SOURCES)
    args = parser.parse_args(argv)
    if args.what == "deps":
        if args.tools:
            parser.error("deps takes no argument: it pins every project")
        try:
            for key, statement in pin_all_deps().items():
                print(f"{key}: {statement}")
        except PinError as error:
            print(f"pin.py: {error}", file=sys.stderr)
            return 1
        return 0
    tools = args.tools or list(build.TOOLS)
    unknown = [t for t in tools if t not in build.TOOLS]
    if unknown:
        parser.error(f"unknown tools {unknown}")
    try:
        if args.what == "requirements":
            if len(args.tools) != 1 or args.tools[0] not in RESOLUTIONS:
                parser.error(f"requirements takes one of {', '.join(RESOLUTIONS)}")
            found = pin_requirements(args.tools[0])
            print(f"{args.tools[0]}: {len(found)} distributions")
            return 0
        pins = build.read_pins()
        if args.what == "images":
            write_pins(pin_images(tools, pins))
        else:
            write_pins(pin_trees(tools, args.sources, pins))
    except (PinError, build.BuildError) as error:
        print(f"pin.py: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
