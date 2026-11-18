#!/usr/bin/env python3
"""The pins of the prior tools' images, which Docker Compose builds.

    docker compose -f evaluation/compose.yaml build TOOL

TOOL is a tool of TOOLS. Each tool's adapter (evaluation/tools/<tool>.py)
declares its image in IMAGE: the Dockerfile of docker/, the hash-pinned
requirements file, the trees of pinned commits that the image holds, and
our own files that the build uses. evaluation/compose.yaml builds it
(context evaluation/, and the inputs image as the additional context
`inputs`: its /inputs/tools holds the plain tree of each pinned commit,
container/Dockerfile.inputs), with the base image by digest, the pin and
the SHA-256 of each tree as build arguments; tests/test_compose.py checks
them against pins.json (`pin.py images` and `pin.py trees` write it). The
image checks each tree against its hash (image_checks.py tree) and holds
/opt/tool/image.txt, its pin and its trees (`image_text`), which the driver
(container/tool_job.py) compares with the adapter's: an image of another
pin does not run. The build is the only step with network: the registry for
the base image, pypi.org and files.pythonhosted.org for the wheels (no
resolution, every file by hash).

HeaderGen imports the modules that it analyses. A macro project with
runtime dependencies (projects.toml `dependencies`) runs in its own image,
the service headergen-deps with NAME: the HeaderGen image with that
project's pinned dependencies (headergen-deps/<project>.txt, written by
pin.py deps for CPython 3.12, HeaderGen's own pins winning) in /opt/deps. A
project that lists no dependency runs in the HeaderGen image. A project
whose dependencies are not available (no trace requirements, not pinned,
not installable on 3.12) is "unavailable", never run (`dependencies`). A
pinned file must hold no distribution of HeaderGen's own requirements
(`deps_overlap`; tests/test_prior_tools.py checks every pinned file).
"""

import importlib.util
import json
import os
import re
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVALUATION = HERE.parent
REPOSITORY = EVALUATION.parent
DOCKER = HERE / "docker"
PINS = HERE / "pins.json"
# The plain trees of the pinned commits, in the inputs image.
DEFAULT_SOURCES = Path("/inputs/tools")
# One more tool is one adapter here (with its IMAGE), one Dockerfile in
# docker/ and one entry in tables.PRIOR_TOOLS.
TOOLS = ("pyan", "jarvis", "cpygraph", "headergen", "pyanalyzer")
PINNED = re.compile(r"[a-z0-9][a-z0-9._/:-]*@sha256:[0-9a-f]{64}")
# The runtime dependencies of each macro project for HeaderGen's Python, one
# hash-pinned file per project (pin.py deps); a file whose line starts with
# UNAVAILABLE says why they cannot be installed.
DEPS = HERE / "headergen-deps"
UNAVAILABLE = "# unavailable: "
DOCKER_ENVIRONMENT = ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONFIG")


class BuildError(Exception):
    """An image that cannot be built here; the message says what is missing."""


def load_adapter(name):
    """Load evaluation/tools/<name>.py by path."""
    spec = importlib.util.spec_from_file_location(
        f"prior_tool_{name}", HERE / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def image_text(tool, pins=None):
    """What /opt/tool/image.txt of the tool's image holds: its pin and its
    trees (TOOL_PIN and TREES)."""
    pins = pins or read_pins()
    return f"{pin_label(load_adapter(tool))}\n{trees_label(tool, pins)}\n"


def read_pins(path=PINS):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def pin_label(adapter):
    """The pin of a tool's image (the build argument TOOL_PIN)."""
    pin = adapter.PIN
    return f"{pin['name']} {pin['version']} {pin['commit']}"


def trees_label(tool, pins):
    """Each tree of a tool's image with its pinned SHA-256 (the build
    argument TREES)."""
    trees = load_adapter(tool).IMAGE.get("trees", {})
    return ";".join(
        f"{name}={pins['trees'].get(f'{tool}/{name}')}" for name in sorted(trees)
    )


def _projects(set_dir):
    spec = importlib.util.spec_from_file_location(
        "macro_projects", REPOSITORY / "macro-benchmark" / "projects.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load_projects(Path(set_dir) / "projects.toml")


def deps_file(set_dir, project):
    """The pinned runtime dependencies of a project for HeaderGen."""
    return DEPS / f"{project}.txt"


def dependencies(tool, set_dir=None, project=None):
    """(whether a job of `tool` can run, statement): for a tool that needs
    the runtime dependencies of a macro project (PROJECT_DEPENDENCIES), what
    is installed for it ("installed: ..." runs in the project's own image,
    the service headergen-deps); the statement is None for any other job. A
    job cannot run if its dependencies are not pinned yet, or cannot be
    installed (the statement says why)."""
    if not getattr(load_adapter(tool), "PROJECT_DEPENDENCIES", False):
        return True, None
    if set_dir is None:
        return True, "none: not a macro project"
    set_dir = Path(set_dir)
    names = sorted(_projects(set_dir)[project].dependencies)
    if not names:
        return True, "none: projects.toml lists no runtime dependency"
    requirements = set_dir / "requirements" / f"{project}.txt"
    where = f"{set_dir.name}/requirements/{project}.txt"
    if not requirements.is_file():  # no versions to pin: no fallback
        return False, f"not available: no {where} for {', '.join(names)}"
    pinned = deps_file(set_dir, project)
    shown = f"tools/{DEPS.name}/{project}.txt"
    if not pinned.is_file():
        return False, f"not pinned: no {shown} (pin.py deps)"
    for line in pinned.read_text(encoding="utf-8").splitlines():
        if line.startswith(UNAVAILABLE):
            reason = line.removeprefix(UNAVAILABLE)
            return False, f"not installable on CPython 3.12: {reason}"
    return True, f"installed: {shown}"


TOOL_VERSION = "# tool version: "  # pin.py: from the tool's environment


def deps_tool_versions(set_dir, project):
    """The distributions of a project's dependencies that HeaderGen's own
    environment gives at another version than the trace's: [{distribution,
    tool, trace}] from its pinned file (empty if there is none)."""
    path = deps_file(set_dir, project)
    if not path.is_file():
        return []
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(TOOL_VERSION):
            pin, _, trace = line.removeprefix(TOOL_VERSION).partition(" (trace ")
            name, _, version = pin.partition("==")
            found.append(
                {"distribution": name, "tool": version, "trace": trace.rstrip(")")}
            )
    return found


def deps_overlap(set_dir, project):
    """The distributions that a project's pinned file and HeaderGen's own
    requirements both hold: the build refuses them, as the tool's wins."""

    def names(path):
        found = set()
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            head = line.split("#", 1)[0].strip().split(" ")[0]
            if "==" in head:
                found.add(re.sub(r"[-_.]+", "-", head.split("==")[0]).lower())
        return found

    tool = HERE / load_adapter("headergen").IMAGE["requirements"]
    return sorted(names(deps_file(set_dir, project)) & names(tool))


def export_tree(source, treeish, paths, destination):
    """Copy the files of `treeish` (a commit, or commit:dir), limited to
    `paths` if any, from `source`, the plain tree of that commit in the
    inputs image, into a new `destination` (links as links); tree_hash
    checks the copy against pins.json."""
    source = Path(source)
    _, _, directory = treeish.partition(":")
    if not source.is_dir():
        raise BuildError(f"no tree at {source} (pin.py trees runs in the inputs image)")
    base = source / directory if directory else source
    Path(destination).mkdir(parents=True)
    for path in paths or [p.name for p in sorted(base.iterdir())]:
        old, new = base / path, Path(destination) / path
        if old.is_dir() and not old.is_symlink():
            shutil.copytree(old, new, symlinks=True)
        else:
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old, new, follow_symlinks=False)


def tree_hash(directory):
    """The SHA-256 of the files under `directory`: their paths and bytes
    (image_checks.py tree, which checks it in the image)."""
    spec = importlib.util.spec_from_file_location(
        "image_checks", HERE / "image_checks.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.tree_hash(directory)


def docker_environment():
    return {k: os.environ[k] for k in DOCKER_ENVIRONMENT if k in os.environ}
