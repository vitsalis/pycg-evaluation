#!/usr/bin/env python3
"""Regression test selection with a call graph, on the dataset of NameRTS.

    python -I evaluation/rq4-test-selection/rts.py fetch --work WORK \\
        [--project NAME ...]
    python -I evaluation/rq4-test-selection/rts.py manifest --work WORK
    python -I evaluation/rq4-test-selection/rts.py export --work WORK [--out DIR]
    python -I evaluation/rq4-test-selection/rts.py select --work WORK --project NAME \\
        --tool NAME:FLAVOUR:CHECKOUT [--timeout SECONDS] [--memory-limit MIB] \\
        [--export DIR]
    python -I evaluation/rq4-test-selection/rts.py score --work WORK \\
        --tool NAME [--tool NAME] [--export DIR]

The client task of the evaluation: after a commit changes code, which test
files must run again? The client selects every test file from which a
changed definition is reachable in the call graph of the parent commit.
NameRTS (Wang, Pradel, Liu, ISSTA 2026; github.com/ZJU-CTAG/NameRTS,
Apache-2.0) gives, for 500 commits of 10 projects, the test files that ran
the changed code (`tests_to_run`, found by instrumentation): the ground
truth. evaluation/README.md, section "Regression test selection", states
what is measured and the client rules.

- fetch: clones NameRTS and the projects as data into WORK (git only,
  github.com only, https only, no checkout, no hooks, no submodules, no
  LFS, no configuration of the user, the system or the caller).
- manifest: writes evaluation/rq4-test-selection/corpus/rts.json, the pinned inputs: the
  NameRTS commit, the SHA-256 of each file read from it, and per project
  its repository, analysis root, package and target commits, with the
  checks of the dataset.
- export: writes what select and score read from git as plain files, so
  that they run without git (in the plain image): for each target, the
  file list of the parent and of the commit (WORK/export/trees/<project>/
  <commit>.json: {path: [mode, blob id]}), the blobs of their regular
  Python files under the analysis root (blobs/<id[:2]>/<id>, checked
  against their git blob id when read; a link is listed, never read or
  fetched; a submodule, a gitlink, is not in the list at all, since ls_tree
  keeps blobs only), and NameRTS's files (namerts/<path>, checked
  against the manifest's SHA-256 when read). It fetches the blobs that
  the partial clone lacks as git itself does (no negotiation), in one
  request, else one at a time; a blob that the remote still refuses is
  written to unavailable.json by project, target, commit and path, and
  the export goes on. With --export DIR, select and score read DIR and
  never the clones; WORK then only receives their output. The trust anchor
  is the committed manifest: each file list must have the digest that the
  manifest pins for its project and commit ("trees", tree_digest), so a
  list that is substituted, renamed, left out or not pinned is refused,
  whatever wrote the export. A target with
  an unavailable file is "not exported": score leaves it out of every row
  of its project, and the table states it with the reason.
- select: for each target commit of a project, analyses the parent commit
  with a PyCG checkout (one child process, run.py's runner; one graph per
  analysis and parent, cached under a key of all its inputs) and selects
  the test files. Writes WORK/selections/<tool>/<project>.json.
- score: the safe rate, test reduction and precision per project and tool,
  as NameRTS defines them, after checking that every selection belongs to
  one experiment; writes WORK/report/rts.json and rts.md.

The analysed sources are data: they are read from git's object store and
written to a scratch directory for the analysis. Nothing of a project is
checked out, installed, imported or run.
"""

import argparse
import ast
import difflib
import hashlib
import importlib.util
import inspect
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tokenize
import warnings
from pathlib import Path, PurePosixPath
from typing import NamedTuple

HERE = Path(__file__).resolve().parent
EVALUATION = HERE.parent
COMMON = EVALUATION / "common"


def _load(name, path):
    """Load a sibling script by path: evaluation/ is not a package."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run = _load("evaluation_run", COMMON / "run.py")
tables = _load("evaluation_tables", COMMON / "tables.py")


def _table(header, rows):
    return tables.table(header, rows) + [""]


# The environment of git children that the selection of the macro-benchmark
# uses: no GIT_* variable and no setting of the caller but http.*.
draw = _load("macro_draw", EVALUATION.parent / "macro-benchmark" / "draw.py")

MANIFEST = HERE / "corpus" / "rts.json"
NAMERTS_URL = "https://github.com/ZJU-CTAG/NameRTS"
HOSTS = ("https://github.com/",)  # the only hosts that fetch clones from
PROTOCOLS = "https"  # GIT_ALLOW_PROTOCOL: the only transport git may use
DEFAULT_TIMEOUT = 1800  # seconds per analysis
DEFAULT_MEMORY_LIMIT_MIB = 16384  # resident set size of an analysis


class Project(NamedTuple):
    url: str
    root: str  # the analysis root, relative to the repository ("" for all of it)
    packages: tuple  # directories or files under the root that hold the package
    dataset: str  # the file of NameRTS's dataset/
    truth: str  # the file of NameRTS's "ground truth/"
    published: tuple  # NameRTS's and BabelRTS's selections, in results/rq1/


def _project(repository, root, packages, name, truth, env):
    return Project(
        f"https://github.com/{repository}",
        root,
        packages,
        f"commit_pairs_{name}.json",
        f"gt_{truth}.json",
        (
            f"results/rq1/namerts/NameBDP_RTSTest_{env}.json",
            f"results/rq1/babelrts/BabelRTS_{name}_clearn.json",
        ),
    )


# The ten projects of NameRTS and their layout, written into the manifest by
# `manifest`; every other command reads the manifest. The analysis root is
# the directory from which the package is imported.
PROJECTS = {
    "dask": _project("dask/dask", "", ("dask",), "dask", "dask", "DAS"),
    "loguru": _project("Delgan/loguru", "", ("loguru",), "loguru", "loguru", "LOG"),
    "matplotlib": _project(
        "matplotlib/matplotlib",
        "lib",
        ("matplotlib", "mpl_toolkits", "pylab.py"),
        "matplotlib",
        "matplotlib",
        "MA",
    ),
    "pvlib": _project("pvlib/pvlib-python", "", ("pvlib",), "pvlib", "pvlib", "PVL"),
    "pylint": _project("pylint-dev/pylint", "", ("pylint",), "pylint", "pylint", "PYL"),
    "scikit-learn": _project(
        "scikit-learn/scikit-learn", "", ("sklearn",), "scikit-learn", "sklearn", "SC"
    ),
    "seaborn": _project(
        "mwaskom/seaborn", "", ("seaborn",), "seaborn", "seaborn", "SE"
    ),
    "sphinx": _project("sphinx-doc/sphinx", "", ("sphinx",), "sphinx", "sphinx", "SPH"),
    "sympy": _project("sympy/sympy", "", ("sympy",), "sympy", "sympy", "SY"),
    "xarray": _project("pydata/xarray", "", ("xarray",), "xarray", "xarray", "XA"),
}

# The client rules (README, "Regression test selection"). Each variant of the
# client is a set of rules; "client" is the client with all of them.
RULES = ("r1", "r2", "r3")
VARIANTS = {"client": frozenset(RULES)}
VARIANTS.update({f"no {rule}": frozenset(RULES) - {rule} for rule in RULES})
VARIANTS["no rule"] = frozenset()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_COMMIT = re.compile(r"[0-9a-f]{40}")


def component(name):
    """`name`, if it is one path component that names nothing else (a
    letter or digit, then letters, digits, `.`, `_`, `-`); else ValueError.
    Project names and tool labels become directory names."""
    if not isinstance(name, str) or not _COMPONENT.fullmatch(name):
        raise ValueError(f"{name!r}: not a name of one path component")
    return name


def commit_id(sha):
    """`sha`, if it is a full commit id (40 hexadecimal digits); else
    ValueError. A target becomes a git argument and a file name."""
    if not isinstance(sha, str) or not _COMMIT.fullmatch(sha):
        raise ValueError(f"{sha!r}: not a commit id")
    return sha


def path_in(base, *parts):
    """base/parts..., each part one path component (so the path stays in
    `base` as written). Every path of the work directory is built here."""
    return Path(base).joinpath(*(component(p) for p in parts))


def writable(path, base):
    """`path`, once checked to stay in `base` with links resolved: a file
    is written there."""
    if not Path(path).resolve().is_relative_to(Path(base).resolve()):
        raise ValueError(f"{path} leaves {base}")
    return Path(path)


def _fingerprint(data):
    return sha256(json.dumps(data, sort_keys=True).encode())


# --- git, as a reader of data ---------------------------------------------


class GitError(Exception):
    """A git command failed."""


class UnsafeClone(Exception):
    """A clone in the work directory that this script did not make as it
    makes them: another remote, a setting or a hook that git could follow."""


def git_env():
    """The environment of every git command: that of draw.git_environment
    (no GIT_* variable of the caller, of its settings only http.*, neither
    the user's nor the system's configuration, no prompt, no LFS download),
    no transport but PROTOCOLS, and no replacement object: git reads the
    objects that their ids name."""
    env = draw.git_environment(os.environ)
    env["GIT_ALLOW_PROTOCOL"] = PROTOCOLS
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    return env


def git(repo, *args, stdin=None, check=True):
    """Run git in `repo`; return the completed process (bytes)."""
    done = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=stdin,
        capture_output=True,
        env=git_env(),
    )
    if check and done.returncode:
        message = done.stderr.decode(errors="replace").strip()[:300]
        raise GitError(f"git {' '.join(args[:2])}: {message}")
    return done


# The local settings of a clone that `clone` makes, and their values (None:
# checked apart); any other setting (a url rewrite, a hook path, a
# file-system monitor, a credential helper) is refused, and so is a second
# value of a setting (git uses the first remote.origin.url).
_BOOLEAN = {"true", "false"}
_CLONE_SETTINGS = {
    "core.repositoryformatversion": {"0", "1"},
    "core.filemode": _BOOLEAN,
    "core.bare": {"false"},
    "core.logallrefupdates": _BOOLEAN,
    "core.ignorecase": _BOOLEAN,
    "core.precomposeunicode": _BOOLEAN,
    "remote.origin.url": None,
    "remote.origin.fetch": {"+refs/heads/*:refs/remotes/origin/*"},
    "remote.origin.promisor": {"true"},
    "remote.origin.partialclonefilter": {"blob:none"},
    "extensions.partialclone": {"origin"},
}
# Files of a git directory that make git read other objects or another
# history than the clone's own.
_FOREIGN_OBJECTS = (
    "commondir",
    "info/grafts",
    "objects/info/alternates",
    "objects/info/http-alternates",
    "shallow",
)


def _clone_setting(key, value):
    """Whether a local setting is one that `clone` writes, with its value."""
    if key.startswith("branch."):
        name = key.rpartition(".")[2]
        return (name == "remote" and value == "origin") or (
            name == "merge" and value.startswith("refs/heads/")
        )
    if key not in _CLONE_SETTINGS:
        return False
    allowed = _CLONE_SETTINGS[key]
    return allowed is None or value in allowed


def check_clone(repo, url=None):
    """Refuse (UnsafeClone) a clone that is not as `clone` makes it: its
    git directory is not `.git` inside it; it has a hook (a sample, which
    git never runs, aside), a replacement ref, or a file that makes git read
    other objects or another history (_FOREIGN_OBJECTS); a local setting is
    not one of _CLONE_SETTINGS with its value, or has two values; or, given
    `url`, its remote is another."""
    directory = Path(repo) / ".git"
    if not directory.is_dir() or directory.is_symlink():
        raise UnsafeClone(f"{repo}: no .git directory of its own")
    hooks = directory / "hooks"
    if hooks.exists() and any(not h.name.endswith(".sample") for h in hooks.iterdir()):
        raise UnsafeClone(f"{repo}: it has hooks")
    for name in _FOREIGN_OBJECTS:
        if (directory / name).exists():
            raise UnsafeClone(f"{repo}: it has {name}")
    replaced = git(repo, "for-each-ref", "--format=%(refname)", "refs/replace/")
    if replaced.stdout.strip():
        raise UnsafeClone(f"{repo}: it has replacement objects")
    listing = git(repo, "config", "--local", "--list", "-z").stdout.decode()
    settings = {}
    for item in listing.split("\0"):
        if not item:
            continue
        key, _, value = item.partition("\n")
        if key in settings:
            raise UnsafeClone(f"{repo}: the setting {key} has two values")
        if not _clone_setting(key, value):
            raise UnsafeClone(f"{repo}: the setting {key}")
        settings[key] = value
    if url is not None and settings.get("remote.origin.url") != url:
        raise UnsafeClone(f"{repo}: its remote is not {url}")


def clone(url, dest):
    """Clone `url` into `dest` as data: no checkout (so no file of it is
    written and no hook can run), an empty template, no submodules, no blobs
    until they are read. Returns False if `dest` already holds a clone,
    after check_clone."""
    if not url.startswith(HOSTS):
        raise ValueError(f"{url}: fetch clones from {', '.join(HOSTS)} only")
    dest = Path(dest)
    if dest.exists():
        check_clone(dest, url)
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "git",
        "clone",
        "--quiet",
        "--no-checkout",
        "--no-recurse-submodules",
        "--template=",
        "--filter=blob:none",
        "--",
        url,
        str(dest),
    ]
    done = subprocess.run(command, capture_output=True, env=git_env())
    if done.returncode:
        message = done.stderr.decode(errors="replace").strip()[:300]
        raise GitError(f"git clone {url}: {message}")
    return True


def has_commit(repo, sha):
    return git(repo, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode == 0


_FETCH = (
    "fetch",
    "--quiet",
    "--no-tags",
    "--no-write-fetch-head",
    "--recurse-submodules=no",
    "--filter=blob:none",
)


def fetch_commits(repo, shas):
    """Fetch the commits of `shas` that the clone lacks (a commit that no
    branch holds any more); return those that are still missing."""
    missing = [sha for sha in dict.fromkeys(shas) if not has_commit(repo, sha)]
    if missing:
        git(repo, *_FETCH, "origin", *missing, check=False)
    return [sha for sha in missing if not has_commit(repo, sha)]


def ls_tree(repo, commit):
    """The files of a commit: {path: (mode, blob id)}; submodules are left out."""
    out = git(repo, "ls-tree", "-r", "-z", "--full-tree", commit).stdout
    files = {}
    for entry in out.split(b"\0"):
        if not entry:
            continue
        meta, path = entry.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        if kind == "blob":
            files[path.decode("utf-8", "surrogateescape")] = (mode, oid)
    return files


# The fetch of blobs by id: the command that git itself runs to fetch the
# objects that a partial clone lacks (promisor-remote.c), without
# negotiation; with it, GitHub refuses some blobs ("bad revision").
_FETCH_BLOBS = ("-c", "fetch.negotiationAlgorithm=noop", *_FETCH)


def _missing(repo, commit, oids):
    """The objects of `oids` that the clone lacks, among those of `commit`
    (found without fetching them)."""
    out = git(repo, "rev-list", "--objects", "--no-walk", "--missing=print", commit)
    missing = {
        line[1:].decode() for line in out.stdout.splitlines() if line.startswith(b"?")
    }
    return sorted(missing & set(oids))


def _prefetch(repo, commit, oids):
    """Fetch, in one request, the blobs of `oids` that a partial clone lacks
    (else git fetches each one alone when it is read)."""
    wanted = _missing(repo, commit, oids)
    if wanted:
        stdin = "\n".join(wanted).encode()
        git(repo, *_FETCH_BLOBS, "--stdin", "origin", stdin=stdin)


def fetch_available(repo, commit, oids):
    """Fetch the blobs of `oids` that the clone lacks: in one request, and,
    if the remote refuses it, one blob at a time. Returns the blobs that the
    remote does not give (never an error: the export reports them)."""
    wanted = _missing(repo, commit, oids)
    if not wanted:
        return []
    batch = git(
        repo, *_FETCH_BLOBS, "--stdin", "origin", stdin="\n".join(wanted).encode(),
        check=False,
    )  # fmt: skip
    if batch.returncode:
        for oid in _missing(repo, commit, wanted):
            git(
                repo,
                *_FETCH_BLOBS,
                "--stdin",
                "origin",
                stdin=oid.encode(),
                check=False,
            )
    return _missing(repo, commit, wanted)


def cat_blobs(repo, oids):
    """{oid: content} of blobs, read from the object store."""
    oids = sorted(set(oids))
    if not oids:
        return {}
    stdin = "".join(o + "\n" for o in oids).encode()
    data = git(repo, "cat-file", "--batch", stdin=stdin).stdout
    blobs, at = {}, 0
    for oid in oids:
        end = data.index(b"\n", at)
        header = data[at:end].decode().split()
        if header[0] != oid or header[1] != "blob":
            raise GitError(f"git cat-file: {' '.join(header)} for {oid}")
        size = int(header[2])
        blobs[oid] = data[end + 1 : end + 1 + size]
        at = end + 1 + size + 1
    return blobs


def read_files(repo, commit, files, paths):
    """{path: content} of the files `paths` of a commit (`files` is its
    ls_tree)."""
    oids = {files[p][1] for p in paths}
    _prefetch(repo, commit, oids)
    blobs = cat_blobs(repo, oids)
    return {p: blobs[files[p][1]] for p in paths}


def show(repo, commit, path):
    """The content of one file of a commit."""
    return git(repo, "show", f"{commit}:{path}").stdout


REGULAR = ("100644", "100755")  # file modes of git; a symbolic link is 120000


def git_blob_id(data):
    """The id that git gives a blob of these bytes (SHA-1 object format)."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class NotExported(Exception):
    """A commit or a file that the export does not hold."""


def tree_digest(files):
    """The pin of the file list of a commit ({path: (mode, blob id)}, as
    ls_tree gives it): the SHA-256 of its canonical JSON (keys sorted, no
    space, ASCII). The manifest records it per project and commit."""
    canonical = {path: [mode, oid] for path, (mode, oid) in files.items()}
    text = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return sha256(text.encode("ascii"))


def tree_pins(repo, targets):
    """{commit: tree_digest} of the parent and the commit of each target,
    from a clone."""
    commits = [commit_id(s) for t in targets for s in (t["parent"], t["current"])]
    return {c: tree_digest(ls_tree(repo, c)) for c in sorted(set(commits))}


class Export:
    """The files of one project as `export` wrote them: read like a clone
    (files, read), without git. The trust anchor is the manifest: a file
    list is used only if its digest is the one that the manifest pins for
    that commit (`pins`, the project's "trees"); each blob is checked
    against its id."""

    def __init__(self, directory, project_name, pins):
        self.directory = Path(directory)
        self.project = component(project_name)
        self.pins = pins

    def files(self, commit):
        """The files of a commit: {path: (mode, blob id)}. A commit that the
        manifest does not pin, or a list that is not the pinned one (an
        entry substituted, left out, added or renamed), is a ValueError."""
        commit = commit_id(commit)
        pin = self.pins.get(commit)
        if pin is None:
            raise ValueError(
                f"the manifest pins no file list of {self.project} {commit}"
            )
        path = self.directory / "trees" / self.project / f"{commit}.json"
        if not path.is_file():
            raise ValueError(f"the export has no file list of {self.project} {commit}")
        data = json.loads(path.read_text(encoding="utf-8"))
        files = {name: (mode, oid) for name, (mode, oid) in data.items()}
        if tree_digest(files) != pin:
            raise ValueError(
                f"the exported file list of {self.project} {commit} is not the one "
                "that the manifest pins"
            )
        return files

    def read(self, commit, files, paths):
        """{path: content} of the files `paths` of a commit; a blob that the
        export lacks is NotExported, naming the path and the commit."""
        contents = {}
        for path in paths:
            try:
                contents[path] = self.blob(files[path][1])
            except NotExported as error:
                raise NotExported(f"{error} ({path} at {commit})") from None
        return contents

    def blob(self, oid):
        oid = commit_id(oid)  # the same form: 40 hex digits
        path = self.directory / "blobs" / oid[:2] / oid
        if not path.is_file():
            raise NotExported(f"the export has no blob {oid}")
        data = path.read_bytes()
        if git_blob_id(data) != oid:
            raise ValueError(f"the exported blob {oid} has other bytes")
        return data


def tree_of(repo, commit):
    """The files of a commit of a clone (a path) or of an Export."""
    return repo.files(commit) if isinstance(repo, Export) else ls_tree(repo, commit)


def contents(repo, commit, files, paths):
    """{path: content} of files of a commit of a clone or of an Export."""
    if isinstance(repo, Export):
        return repo.read(commit, files, paths)
    return read_files(repo, commit, files, paths)


def write_tree(contents, dest):
    """Write {path: content} under `dest`; a path that would leave it is a
    ValueError."""
    dest = Path(dest).resolve()
    for path, content in contents.items():
        parts = PurePosixPath(path).parts
        if ".." in parts or path.startswith("/"):
            raise ValueError(f"{path}: not inside the tree")
        target = dest.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


# --- files, modules and tests ----------------------------------------------


def under(path, root):
    """The path relative to `root` ("" is the repository), or None."""
    if not root:
        return path
    prefix = root.rstrip("/") + "/"
    return path[len(prefix) :] if path.startswith(prefix) else None


def module_name(path, root):
    """The dotted module name of a Python file under the analysis root, as
    PyCG names it (`pkg/__init__.py` is `pkg`); None outside the root."""
    relative = under(path, root)
    if relative is None or not relative.endswith(".py"):
        return None
    parts = list(PurePosixPath(relative).parts)
    if parts[-1] == "__init__.py":
        parts.pop()
    else:
        parts[-1] = parts[-1][: -len(".py")]
    return ".".join(parts) or None


def is_test_file(path):
    """NameRTS's rule (src/utils.py, is_test_file_pytest): a `.py` file whose
    name starts with `test_` or `unittest_`, in a directory that has an
    ancestor (itself included) named `tests` or `testing`."""
    p = PurePosixPath(path)
    return (
        p.suffix == ".py"
        and p.name.startswith(("test_", "unittest_"))
        and any(part in ("tests", "testing") for part in p.parts[:-1])
    )


def _below(path, directory):
    """Whether `path` is in `directory` or below it."""
    return directory == PurePosixPath(".") or directory in PurePosixPath(path).parents


def conftests_of(test, conftests):
    """The conftest.py files that pytest loads for a test file: those in its
    directory and in each directory above it."""
    return sorted(c for c in conftests if _below(test, PurePosixPath(c).parent))


def is_entry_point(path, project):
    """A file that the analysis starts from: a file of the package, a test
    file or a conftest.py (each under the analysis root)."""
    relative = under(path, project.root)
    if relative is None or not relative.endswith(".py"):
        return False
    first = PurePosixPath(relative).parts[0]
    return (
        first in project.packages
        or is_test_file(path)
        or PurePosixPath(path).name == "conftest.py"
    )


# --- source text -------------------------------------------------------------


def decode(source):
    """(text, encoding) of a Python source as Python reads it: its declared
    encoding (or UTF-8), the lines ended by \\n whatever the file used (\\r\\n
    and \\r too); None if it cannot be decoded."""
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(source).readline)
        text = source.decode(encoding)
    except (SyntaxError, LookupError, UnicodeDecodeError):
        return None
    return text.replace("\r\n", "\n").replace("\r", "\n"), encoding


def code_lines(text):
    """The lines that hold a token of code (a string spans its lines); None
    if the text does not tokenize, so that every line counts as code."""
    skipped = {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENDMARKER,
    }
    lines = set()
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type not in skipped:
                lines.update(range(token.start[0], token.end[0] + 1))
    # Python 3.12's tokenizer raises SystemError on a NUL byte.
    except (tokenize.TokenError, SyntaxError, ValueError, SystemError):
        return None
    return lines


def line_changes(old, new):
    """(the lines of `old` that the change removes, the lines of `new` that
    it adds), numbered from 1, from difflib on the lines."""
    old_lines, new_lines = old.split("\n"), new.split("\n")
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    removed, added = [], []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            removed += range(i1 + 1, i2 + 1)
            added += range(j1 + 1, j2 + 1)
    return removed, added


# --- changed definitions ---------------------------------------------------


class Scope(NamedTuple):
    name: str
    kind: str  # "function" or "class"
    start: int  # the first line, the decorators included
    header_end: int  # the last line of the decorators and the signature
    end: int


def _parse(text):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return None


def _walk(tree, module, visit):
    """Call visit(node, name, prefix) for each function and class, by its
    qualified name as PyCG names it."""

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}.{child.name}"
                visit(child, name, prefix)
                walk(child, name)
            else:
                walk(child, prefix)

    walk(tree, module)


def scopes(text, module):
    """The functions and classes of a module, with their lines; None if it
    does not parse."""
    tree = _parse(text)
    if tree is None:
        return None
    found = []

    def visit(node, name, prefix):
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        header_end = max(node.lineno, node.body[0].lineno - 1)
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        found.append(Scope(name, kind, start, header_end, node.end_lineno))

    _walk(tree, module, visit)
    return found


def _chain(found, line, known):
    """The scopes that hold `line` and whose names are `known`, innermost
    first."""
    return sorted(
        (s for s in found if s.start <= line <= s.end and s.name in known),
        key=lambda s: (s.end - s.start, -s.start),
    )


def _owners(chain, line, module):
    """The definitions that a change of `line` touches, given the scopes
    that hold it, innermost first. A line of a function's body belongs to
    the function. A decorator or a signature (defaults, annotations) runs in
    the scope around the function, and so does a class's body: they belong
    to it too. A class's body also belongs to the class.
    A line outside every scope belongs to the module."""
    names = set()
    for depth, scope in enumerate(chain):
        names.add(scope.name)
        if scope.kind == "function" and (depth > 0 or line > scope.header_end):
            return names
    names.add(module)
    return names


def changed_names(module, old, new):
    """The definitions and classes of the parent that a change of one file
    touches. `old` and `new` are its content in the parent and in the
    commit (bytes, None if absent).

    A removed line maps to the parent's scopes (_owners); an added line to
    the scopes of the commit that the parent has too, so a new function
    belongs to the function, class or module around it. A line that holds
    no code token (blank, or only a comment) changes nothing. A file that
    the parent lacks touches nothing of the parent. The lines are compared
    as decoded text, so a new declared encoding changes the lines whose
    text it changes. If a version does not decode or parse, the module
    changes."""
    if old is None:
        return set()
    before = decode(old)
    after = decode(new) if new is not None else ("", None)
    if before is None or after is None:
        return {module} if old != new else set()
    names = set()
    removed, added = line_changes(before[0], after[0])
    old_scopes, new_scopes = scopes(before[0], module), scopes(after[0], module)
    known = {s.name for s in old_scopes or ()}
    for text, found, lines in (
        (before[0], old_scopes, removed),
        (after[0], new_scopes, added),
    ):
        code = code_lines(text)
        for line in lines:
            if code is not None and line not in code:
                continue
            if found is None:
                names.add(module)
            else:
                names |= _owners(_chain(found, line, known), line, module)
    return names


class Change(NamedTuple):
    names: tuple  # the changed definitions, classes and modules of the parent
    tests: tuple  # the test files that the commit adds or changes
    conftests: tuple  # the conftest.py files that the commit adds
    other: tuple  # changed files that are not Python
    outside: tuple  # changed Python files outside the analysis root


def changes(repo, parent, commit, project, parent_files, commit_files):
    """What a commit changes, against its parent: the files whose blob or
    mode differ, and in each Python file under the root the changed
    definitions (changed_names)."""
    paths = sorted(
        p
        for p in set(parent_files) | set(commit_files)
        if parent_files.get(p) != commit_files.get(p)
    )
    python = [
        p
        for p in paths
        if module_name(p, project.root) is not None
        and _regular(p, parent_files, commit_files)
    ]
    tests = [p for p in paths if p in commit_files and is_test_file(p)]
    conftests = [
        p
        for p in python
        if p not in parent_files and PurePosixPath(p).name == "conftest.py"
    ]
    other = [
        p
        for p in paths
        if not p.endswith(".py") or not _regular(p, parent_files, commit_files)
    ]
    outside = [
        p for p in paths if p.endswith(".py") and module_name(p, project.root) is None
    ]
    before = contents(
        repo, parent, parent_files, [p for p in python if p in parent_files]
    )
    after = contents(
        repo, commit, commit_files, [p for p in python if p in commit_files]
    )
    names = set()
    for path in python:
        module = module_name(path, project.root)
        names |= changed_names(module, before.get(path), after.get(path))
    return Change(
        tuple(sorted(names)),
        tuple(tests),
        tuple(conftests),
        tuple(other),
        tuple(outside),
    )


def _regular(path, *trees):
    """Whether a path is a regular file (not a link) in each tree that has it."""
    return all(tree[path][0] in REGULAR for tree in trees if path in tree)


# --- selection ---------------------------------------------------------------


def owner(name, modules):
    """The module that defines a node: the longest prefix of its name that
    is a module; None for a name outside the analysed modules."""
    parts = name.split(".")
    for i in range(len(parts), 0, -1):
        prefix = ".".join(parts[:i])
        if prefix in modules:
            return prefix
    return None


def _package(module, modules):
    """The nearest module above `module` (a namespace directory between
    them is crossed), or None."""
    parts = module.split(".")[:-1]
    while parts:
        name = ".".join(parts)
        if name in modules:
            return name
        parts.pop()
    return None


def reaching(graph, targets, modules, module_edges):
    """The nodes from which a target is reachable, the targets included.

    With `module_edges` (rule r2), each node has an edge to its module and
    each module to the package above it: a function runs only after the
    code of its module ran, and a module is imported after its package."""
    reverse = {}
    for caller, callees in graph.items():
        for callee in callees:
            reverse.setdefault(callee, set()).add(caller)
    if module_edges:
        nodes = set(graph).union(*graph.values()) if graph else set()
        for node in nodes:
            module = owner(node, modules)
            if module is not None and module != node:
                reverse.setdefault(module, set()).add(node)
        for module in modules:
            package = _package(module, modules)
            if package is not None:
                reverse.setdefault(package, set()).add(module)
    seen, stack = set(targets), list(targets)
    while stack:
        for caller in reverse.get(stack.pop(), ()):
            if caller not in seen:
                seen.add(caller)
                stack.append(caller)
    return seen


def modules_of(sources, root):
    """{module name: path} of the parent's Python files under the root."""
    modules = {}
    for path in sorted(sources):
        name = module_name(path, root)
        if name is not None:
            modules[name] = path
    return modules


def select_tests(graph, change, tests, modules, root, rules):
    """The test files that a change selects under a set of client rules.

    A test file is selected if its module, or a node of its module, reaches
    a changed definition or module. r1: so do its conftest.py files, and a
    new conftest.py selects the test files below it. r2: the module edges of
    `reaching`. r3: a test file that the commit adds or changes is
    selected."""
    reached = reaching(graph, change.names, modules, "r2" in rules)
    reached_modules = {owner(node, modules) for node in reached} - {None}
    conftests = sorted(
        p for p in modules.values() if PurePosixPath(p).name == "conftest.py"
    )
    selected = []
    for test in tests:
        sources = {module_name(test, root)}
        if "r1" in rules:
            sources |= {
                m for m, p in modules.items() if p in conftests_of(test, conftests)
            }
        hit = bool(sources & reached_modules)
        if "r1" in rules and not hit:
            hit = any(_below(test, PurePosixPath(c).parent) for c in change.conftests)
        if "r3" in rules and test in change.tests:
            hit = True
        if hit:
            selected.append(test)
    return selected


def selections(graph, change, tests, modules, root):
    """The selection of each variant of the client."""
    return {
        name: select_tests(graph, change, tests, modules, root, rules)
        for name, rules in VARIANTS.items()
    }


# --- the analysis of a parent commit -----------------------------------------


def _source_files(directory):
    """The files under a directory that are not bytecode, sorted."""
    directory = Path(directory)
    return sorted(
        p
        for p in directory.rglob("*")
        if p.is_file()
        and "__pycache__" not in p.relative_to(directory).parts
        and p.suffix not in (".pyc", ".pyo")
    )


def _content_hash(directory):
    """The SHA-256 of the source files under a directory (their paths and
    contents)."""
    digest = hashlib.sha256()
    for path in _source_files(directory):
        digest.update(path.relative_to(directory).as_posix().encode() + b"\0")
        digest.update(sha256(path.read_bytes()).encode())
    return digest.hexdigest()


def snapshot(checkout, work):
    """(hash, directory): a copy of the source files of the checkout's
    `pycg/`, in WORK/tools/<their SHA-256>/pycg. The analysis runs the tool
    from it: Python reads no bytecode there, since there is none and the
    child writes none (`-B`), so the hash names the code that runs."""
    source = Path(checkout) / "pycg"
    content = _content_hash(source)
    target = writable(path_in(work, "tools", content), work)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(prefix="snapshot-", dir=target.parent))
        for file in _source_files(source):
            copy = scratch / "pycg" / file.relative_to(source)
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, copy)
        try:
            scratch.rename(target)
        except OSError:  # another select of the same tool made it just now
            shutil.rmtree(scratch)
            if not target.exists():
                raise
    tree = target / "pycg"
    if any(p.name == "__pycache__" for p in tree.rglob("*")) or (
        _content_hash(tree) != content
    ):
        raise ValueError(f"{target}: not the source files of {content}")
    return content, target


def client_fingerprint():
    """The SHA-256 of the code of this client and of its runner."""
    files = (HERE / "rts.py", COMMON / "run.py", COMMON / "analyse.py")
    parts = [path.read_bytes() for path in files]
    return sha256(b"\0".join(parts))


def mapping_runtime():
    """The Python whose ast and tokenize map the changed lines."""
    return f"{platform.python_implementation()} {platform.python_version()}"


def resolve_python(python):
    """The interpreter as an absolute path, found once on PATH if it is a
    bare name; the path is probed and launched (a link, as the interpreter
    of a virtual environment, is kept)."""
    if os.sep not in str(python):
        found = shutil.which(python)
        if found is None:
            raise ValueError(f"{python}: not found on PATH")
        python = found
    return os.path.abspath(python)


class Setup(NamedTuple):
    tool: object  # run.Tool, with the snapshot and the resolved interpreter
    timeout: float
    memory_limit_mib: object  # int or None
    settings: dict  # what an analysis depends on, but its input files
    fingerprint: str


def setup(tool, timeout, memory_limit_mib, work):
    """The settings of the analyses of a tool: its code (the SHA-256 of
    the source files of its `pycg/`, which the analysis runs from a
    snapshot), API, interpreter, hash seed, budget and runner."""
    version = run.git_version(tool.checkout)
    python = resolve_python(tool.python)
    content, copy = snapshot(tool.checkout, work)
    settings = {
        "flavour": tool.flavour,
        "commit": version["commit"],
        "dirty": version["dirty"],
        "content": content,
        "python": python,
        "python_version": ".".join(map(str, run.python_version(python))),
        "hash_seed": tool.hash_seed,
        "timeout": timeout,
        "memory_limit_mib": memory_limit_mib,
        "runner": sha256(
            (COMMON / "run.py").read_bytes() + (COMMON / "analyse.py").read_bytes()
        ),
    }
    launched = tool._replace(python=python, checkout=copy)
    return Setup(launched, timeout, memory_limit_mib, settings, _fingerprint(settings))


def _preparation():
    """The SHA-256 of the code that prepares the input of an analysis."""
    functions = (graph_for, write_tree, is_entry_point, module_name, under)
    return sha256("".join(inspect.getsource(f) for f in functions).encode())


# A run that says nothing about the tool is not kept: the next run repeats it.
_NOT_KEPT = ("cleanup failed", "unmeasured")


def analysis_inputs(project, sources):
    """What an analysis of a parent commit is given: the layout, the SHA-256
    of the bytes of each file, the entry points and the code that prepares
    them."""
    python = sorted(sources)
    return {
        "root": project.root,
        "packages": list(project.packages),
        "files": [[p, sha256(sources[p])] for p in python],
        "entry_points": [p for p in python if is_entry_point(p, project)],
        "preparation": _preparation(),
    }


def analysis_key(project, sources):
    """The key of the input of an analysis, which a stored graph of a prior
    tool records (container/tool_job.py rq4)."""
    return _fingerprint(analysis_inputs(project, sources))


def parent_sources(repo, parent, project):
    """{path: bytes} of the modules of a parent commit that an analysis is
    given (as select_commit reads them)."""
    parent_files = tree_of(repo, parent)
    python = sorted(
        p
        for p in parent_files
        if module_name(p, project.root) is not None and parent_files[p][0] in REGULAR
    )
    return contents(repo, parent, parent_files, python)


class StaleGraph(ValueError):
    """A stored graph of a prior tool that is not of this input: its tool
    must run again."""


def stored_analysis(stored, setup, project, parent, sources):
    """(record, graph or None) of a parent commit from the stored run of a
    prior tool (the file of container/tool_job.py rq4); StaleGraph if there
    is none, or if its input is not this one."""
    entry = stored["analyses"].get(parent)
    if entry is None:
        raise StaleGraph(f"{parent[:10]}: no stored analysis")
    if entry.get("key") != analysis_key(project, sources):
        raise StaleGraph(f"{parent[:10]}: its input changed since the tool ran")
    record = dict(entry["record"]) | {"settings": setup.fingerprint}
    return record | {"cached": False}, entry.get("graph")


def graph_for(setup, project_name, project, parent, files, sources, work):
    """The call graph of a parent commit and its run record: (record, graph
    or None). Cached under a key of the settings, of the code that prepares
    the input, and of the input itself (the SHA-256 of the bytes of each
    file given to the analysis), so that a graph, or a failure, is reused
    only for the same analysis."""
    inputs = analysis_inputs(project, sources)
    key = _fingerprint({"settings": setup.fingerprint, "inputs": inputs})
    stem = f"{commit_id(parent)}.{key}"
    directory = path_in(work, "graphs", setup.tool.name, project_name)
    graph_path = path_in(directory, f"{stem}.json")
    record_path = path_in(directory, f"{stem}.run.json")
    if record_path.exists():
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if record["status"] != "ok" or graph_path.exists():
            graph = None
            if record["status"] == "ok":
                graph = json.loads(graph_path.read_text(encoding="utf-8"))
            return {**record, "cached": True}, graph
    writable(graph_path, work)
    writable(record_path, work)
    with tempfile.TemporaryDirectory(prefix="pycg-rts-") as scratch:
        write_tree(sources, scratch)
        tree = Path(scratch).resolve()
        entry_points = tuple(tree / p for p in inputs["entry_points"])
        package = tree.joinpath(*PurePosixPath(project.root).parts)
        job = run.Job(f"rts/{project_name}/{parent}", entry_points, package)
        record = run.run_job(
            setup.tool, job, graph_path, setup.timeout, setup.memory_limit_mib
        )
    record.update(key=key, settings=setup.fingerprint)
    if record["status"] not in _NOT_KEPT:
        directory.mkdir(parents=True, exist_ok=True)
        text = json.dumps(record, sort_keys=True) + "\n"
        record_path.write_text(text, encoding="utf-8")
    graph = None
    if record["status"] == "ok":
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
    return {**record, "cached": False}, graph


def select_commit(setup, project_name, project, repo, target, work, stored=None):
    """The record of one target commit: what changed, the selection of each
    variant of the client, and the run of the analysis (`stored`: the
    stored run of a prior tool, see stored_analysis)."""
    parent, commit = commit_id(target["parent"]), commit_id(target["current"])
    parent_files = tree_of(repo, parent)
    commit_files = tree_of(repo, commit)
    tests = sorted(p for p in commit_files if is_test_file(p))
    python = sorted(
        p
        for p in parent_files
        if module_name(p, project.root) is not None and parent_files[p][0] in REGULAR
    )
    sources = contents(repo, parent, parent_files, python)
    if stored is not None:
        analysis, graph = stored_analysis(stored, setup, project, parent, sources)
    else:
        analysis, graph = graph_for(
            setup, project_name, project, parent, parent_files, sources, work
        )
    start = time.perf_counter()
    change = changes(repo, parent, commit, project, parent_files, commit_files)
    absent = []
    if graph is None:  # a failed analysis selects every test file
        selected = {name: list(tests) for name in VARIANTS}
    else:
        modules = modules_of(sources, project.root)
        selected = selections(graph, change, tests, modules, project.root)
        nodes = set(graph).union(*graph.values()) if graph else set()
        absent = [n for n in change.names if n not in nodes]
    return {
        "current": commit,
        "parent": parent,
        "status": analysis["status"],
        "settings": analysis["settings"],
        "tests": len(tests),
        "changed": list(change.names),
        "changed_not_in_graph": absent,
        "changed_tests": list(change.tests),
        "added_conftests": list(change.conftests),
        "other_files": list(change.other),
        "outside_files": list(change.outside),
        "selected": selected,
        "analysis": {
            k: analysis.get(k)
            for k in ("cached", "seconds", "wall_seconds", "peak_rss_mib", "error")
        },
        "select_seconds": round(time.perf_counter() - start, 3),
    }


def project_config(manifest, name):
    """The project of the manifest: its pinned layout and files."""
    entry = manifest["projects"][name]
    return Project(
        entry["url"],
        entry["root"],
        tuple(entry["packages"]),
        entry["dataset"],
        entry["truth"],
        tuple(entry["published"]),
    )


def dataset_fingerprint(manifest, name):
    """The SHA-256 of the NameRTS files and of the project's entry of the
    manifest."""
    return _fingerprint(
        {"namerts": manifest["namerts"], "project": manifest["projects"][name]}
    )


def _configuration(project):
    return {"root": project.root, "packages": list(project.packages)}


def select(
    work,
    manifest,
    project_name,
    tool,
    timeout,
    memory_limit_mib=None,
    allow_dirty=False,
    limit=None,
    progress=print,
    export=None,
    stored=None,
):
    """Select the test files of every target commit of a project (of the
    first `limit` ones, to try a project); write and return
    WORK/selections/<tool>/<project>.json. With `export` (the directory of
    `export`), the project's files are read from it, not from a clone. With
    `stored` (the stored run of a prior tool on the project), its graphs
    are used: no analysis runs, and `tool` gives the name only."""
    component(project_name)
    component(tool.name)
    targets = manifest["projects"][project_name]["targets"][:limit]
    for target in targets:
        commit_id(target["parent"])
        commit_id(target["current"])
    if stored is None:
        version = run.git_version(tool.checkout)
        if not allow_dirty and (version["dirty"] or version["commit"] is None):
            problem = "has changes" if version["commit"] else "is not a git checkout"
            raise run.DirtyCheckout(f"{tool.checkout} {problem}")
    project = project_config(manifest, project_name)
    if export is not None:
        repo = Export(export, project_name, manifest["projects"][project_name]["trees"])
    else:
        repo = path_in(work, "repos", project_name)
        check_clone(repo, project.url or None)
    if stored is None:
        analyses = setup(tool, timeout, memory_limit_mib, work)
        python, hash_seed = analyses.settings["python_version"], tool.hash_seed
    else:  # the tool ran in its service: its facts are the settings
        facts = stored["tool"]
        timeout = facts["limits"]["seconds"]
        memory_limit_mib = facts["memory_mib"]
        analyses = Setup(None, timeout, memory_limit_mib, facts, _fingerprint(facts))
        version = {"commit": facts["pin"]["commit"], "dirty": False}
        python, hash_seed = facts["python"], 0
    records = []
    for target in targets:
        try:
            record = select_commit(
                analyses, project_name, project, repo, target, work, stored
            )
        except NotExported as error:  # a blob that the remote refused
            record = {
                "current": commit_id(target["current"]),
                "parent": commit_id(target["parent"]),
                "status": "not exported",
                "reason": str(error),
                "settings": analyses.fingerprint,
            }
            records.append(record)
            if progress:
                progress(
                    f"{tool.name} {project_name} {target['current'][:10]}: {error}"
                )
            continue
        records.append(record)
        if progress:
            a = record["analysis"]
            progress(
                f"{tool.name} {project_name} {target['current'][:10]}: "
                f"{record['status']}{' (cached)' if a['cached'] else ''}, "
                f"{len(record['selected']['client'])}/{record['tests']} selected"
            )
    data = {
        "tool": {"name": tool.name, "flavour": tool.flavour, **version},
        "content": analyses.settings.get("content"),
        "settings": analyses.fingerprint,
        "settings_detail": analyses.settings,
        "project": project_name,
        "configuration": _configuration(project),
        "dataset": dataset_fingerprint(manifest, project_name),
        "client": client_fingerprint(),
        "python": python,
        "hash_seed": hash_seed,
        "timeout": timeout,
        "memory_limit_mib": memory_limit_mib,
        "mapper_python": mapping_runtime(),
        "variants": {k: sorted(v) for k, v in VARIANTS.items()},
        "commits": records,
    }
    path = writable(
        path_in(work, "selections", tool.name, f"{project_name}.json"), work
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    return data


# --- the manifest -------------------------------------------------------------


def namerts_files(project):
    """The files of NameRTS that the evaluation reads for a project."""
    return [f"dataset/{project.dataset}", f"ground truth/{project.truth}"] + list(
        project.published
    )


def check_dataset(pairs, truth):
    """The target commits of a project (NameRTS's dataset: entries
    [direct_parent, true_parent, current, target]; a target's change is
    true_parent..current), and the notes on what does not match between the
    dataset and the ground truth."""
    notes, targets, by_current = [], [], {}
    for entry in truth:
        if entry["current"] in by_current:
            notes.append(f"{entry['current'][:10]}: twice in the ground truth")
        by_current[entry["current"]] = entry
    if len(truth) != len(pairs):
        notes.append(f"{len(pairs)} dataset entries, {len(truth)} ground-truth entries")
    for direct, parent, current, flag in pairs:
        entry = by_current.get(current)
        if entry is None:
            notes.append(f"{current[:10]}: no ground-truth entry")
        elif (entry["direct_parent"], entry["true_parent"]) != (direct, parent):
            notes.append(f"{current[:10]}: other parents in the ground truth")
        elif bool(entry["target"]) != bool(flag):
            notes.append(f"{current[:10]}: target in one file only")
        if flag:
            targets.append(
                {"current": current, "parent": parent, "direct_parent": direct}
            )
    empty = [
        t["current"][:10]
        for t in targets
        if t["current"] in by_current and not by_current[t["current"]]["tests_to_run"]
    ]
    if empty:
        notes.append(
            f"{len(empty)} of {len(targets)} targets have an empty tests_to_run "
            f"(every selection is safe on them): {', '.join(empty)}"
        )
    older = sum(t["parent"] != t["direct_parent"] for t in targets)
    if older:
        notes.append(
            f"{older} of {len(targets)} targets are compared with an older commit "
            "than their git parent (true_parent): their change spans several commits"
        )
    return targets, notes


def check_repository(repo, project, targets, truth):
    """Check the target commits against the clone; add each target's `span`
    (the commits from its parent to it). Returns the notes."""
    notes = []
    shas = [s for t in targets for s in (t["parent"], t["current"])]
    missing = fetch_commits(repo, shas)
    notes += [f"{sha[:10]}: not in the repository upstream" for sha in missing]
    for target in targets:
        parent, current = target["parent"], target["current"]
        if parent in missing or current in missing:
            continue
        short = current[:10]
        span = git(repo, "rev-list", "--count", f"{parent}..{current}").stdout
        target["span"] = int(span)
        ancestor = git(
            repo, "merge-base", "--is-ancestor", parent, current, check=False
        )
        if ancestor.returncode:
            notes.append(f"{short}: its parent is not an ancestor")
        tests = {p for p in ls_tree(repo, current) if is_test_file(p)}
        entry = truth.get(current)
        if entry is not None:
            if len(tests) != int(entry["total_tests"]):
                notes.append(
                    f"{short}: {len(tests)} test files, "
                    f"total_tests {entry['total_tests']}"
                )
            unknown = sorted(set(entry["tests_to_run"]) - tests)
            if unknown:
                notes.append(
                    f"{short}: tests_to_run has {len(unknown)} files that are not "
                    f"test files of the commit: {', '.join(unknown[:3])}"
                )
        outside = sorted(p for p in tests if module_name(p, project.root) is None)
        if outside:
            notes.append(
                f"{short}: {len(outside)} test files outside the analysis root"
            )
        parent_files = ls_tree(repo, parent)
        for package in project.packages:
            path = f"{project.root}/{package}" if project.root else package
            if path not in parent_files and not any(
                p.startswith(path + "/") for p in parent_files
            ):
                notes.append(f"{short}: the parent has no {path}")
    return notes


def build_manifest(work, projects=PROJECTS):
    """The manifest of the evaluation, from the clones in WORK."""
    work = Path(work).resolve()
    namerts = path_in(work, "NameRTS")
    check_clone(namerts, NAMERTS_URL)
    commit = git(namerts, "rev-parse", "HEAD").stdout.decode().strip()
    hashes, entries = {}, {}
    for name, project in projects.items():
        repo = path_in(work, "repos", name)
        if not (repo / ".git").is_dir():
            raise FileNotFoundError(f"{repo}: no clone; run fetch first")
        check_clone(repo, project.url)
        contents = {p: show(namerts, commit, p) for p in namerts_files(project)}
        hashes.update({p: sha256(c) for p, c in contents.items()})
        pairs = json.loads(contents[f"dataset/{project.dataset}"])
        truth = json.loads(contents[f"ground truth/{project.truth}"])
        targets, notes = check_dataset(pairs, truth)
        by_current = {e["current"]: e for e in truth}
        notes += check_repository(repo, project, targets, by_current)
        entries[name] = {
            "trees": tree_pins(repo, targets),
            "url": project.url,
            "root": project.root,
            "packages": list(project.packages),
            "dataset": project.dataset,
            "truth": project.truth,
            "published": list(project.published),
            "entries": len(pairs),
            "targets": targets,
            "notes": notes,
        }
    return {
        "namerts": {"url": NAMERTS_URL, "commit": commit, "sha256": hashes},
        "projects": entries,
    }


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=1, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")


def namerts_data(work, manifest, project, export=None):
    """{path: content} of NameRTS's files for a project, read at the commit of
    the manifest (from the export, if one is given); a file whose SHA-256
    differs from the manifest's is a ValueError. The clone must be as
    `fetch` makes it (check_clone)."""
    data = {}
    for path, content in namerts_bytes(work, manifest, project, export).items():
        data[path] = json.loads(content)
    return data


def namerts_bytes(work, manifest, project, export=None):
    """{path: bytes} of NameRTS's files for a project, checked against the
    manifest's SHA-256."""
    if export is None:
        namerts = path_in(work, "NameRTS")
        check_clone(namerts, NAMERTS_URL)
    data = {}
    for path in namerts_files(project):
        if export is None:
            content = show(namerts, manifest["namerts"]["commit"], path)
        else:
            parts = PurePosixPath(path).parts
            if ".." in parts or path.startswith("/"):
                raise ValueError(f"{path}: not inside the export")
            exported = Path(export, "namerts", *parts)
            if not exported.is_file():
                raise NotExported(f"the export has no NameRTS file {path}")
            content = exported.read_bytes()
        if sha256(content) != manifest["namerts"]["sha256"].get(path):
            raise ValueError(f"NameRTS {path}: its SHA-256 differs from the manifest")
        data[path] = content
    return data


def export(work, manifest, names, out, progress=print):
    """Write what select and score read from the clones of WORK as plain
    files under `out` (see the docstring): the file lists of each target's
    parent and commit, the blobs of their Python files under the analysis
    root, and NameRTS's files. Returns {project: commits written}."""
    out = Path(out)
    unavailable_path = Path(out).resolve() / "unavailable.json"
    unavailable = {}
    if unavailable_path.is_file():
        unavailable = json.loads(unavailable_path.read_text(encoding="utf-8"))
    written = {}
    for name in names:
        project = project_config(manifest, name)
        repo = path_in(work, "repos", name)
        check_clone(repo, project.url or None)
        targets_of = {}  # commit -> the targets whose parent or commit it is
        for target in manifest["projects"][name]["targets"]:
            for sha in (target["parent"], target["current"]):
                targets_of.setdefault(commit_id(sha), []).append(
                    commit_id(target["current"])
                )
        commits = list(targets_of)
        unavailable[name] = []
        pins = manifest["projects"][name].get("trees", {})
        for commit in commits:
            files = ls_tree(repo, commit)
            if commit in pins and tree_digest(files) != pins[commit]:
                raise ValueError(
                    f"{name} {commit}: the clone's file list is not the pinned one"
                )
            python = [
                p
                for p in files
                if module_name(p, project.root) is not None and files[p][0] in REGULAR
            ]
            oids = {files[p][1] for p in python}
            lost = set(fetch_available(repo, commit, oids))
            blobs = cat_blobs(repo, oids - lost)
            for path in python:
                oid = files[path][1]
                if oid in lost:
                    unavailable[name] += [
                        {"target": t, "commit": commit, "path": path, "blob": oid}
                        for t in targets_of[commit]
                    ]
                    continue
                target_path = out / "blobs" / oid[:2] / oid
                if not target_path.is_file():
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    target_path.write_bytes(blobs[oid])
            tree_path = out / "trees" / component(name) / f"{commit}.json"
            tree_path.parent.mkdir(parents=True, exist_ok=True)
            tree = {path: list(value) for path, value in sorted(files.items())}
            tree_path.write_text(json.dumps(tree) + "\n", encoding="utf-8")
        for path, content in namerts_bytes(work, manifest, project).items():
            exported = Path(out, "namerts", *PurePosixPath(path).parts)
            exported.parent.mkdir(parents=True, exist_ok=True)
            exported.write_bytes(content)
        written[name] = len(commits)
        out.mkdir(parents=True, exist_ok=True)
        text = json.dumps(unavailable, indent=1, sort_keys=True) + "\n"
        unavailable_path.write_text(text, encoding="utf-8")
        if progress:
            lost = sorted({u["target"] for u in unavailable[name]})
            progress(
                f"{name}: {len(commits)} commits exported"
                + (f"; targets with an unavailable file: {len(lost)}" if lost else "")
            )
    return written


def fetch(work, manifest, names, progress=print):
    """Clone NameRTS and the projects `names` into WORK, and fetch the target
    commits that no branch holds; return {project: missing commits}."""
    work = Path(work).resolve()
    namerts = writable(path_in(work, "NameRTS"), work)
    clone(NAMERTS_URL, namerts)
    if manifest is not None:
        fetch_commits(namerts, [manifest["namerts"]["commit"]])
    missing = {}
    for name in names:
        if manifest is not None:
            project = project_config(manifest, name)
        else:
            project = PROJECTS[name]
        repo = writable(path_in(work, "repos", name), work)
        cloned = clone(project.url, repo)
        if manifest is not None:
            namerts_data(work, manifest, project)  # checks the hashes
            targets = manifest["projects"][name]["targets"]
        else:
            path = f"dataset/{project.dataset}"
            targets, _ = check_dataset(json.loads(show(namerts, "HEAD", path)), [])
        shas = [commit_id(s) for t in targets for s in (t["parent"], t["current"])]
        missing[name] = fetch_commits(repo, shas)
        if progress:
            state = "cloned" if cloned else "present"
            progress(f"{name}: {state}, {len(missing[name])} target commits missing")
    return missing


# --- scores --------------------------------------------------------------------


def metrics(rows):
    """NameRTS's metrics (its section 4.1) over the commits of a project;
    `rows` holds (selected, affected, number of test files) per commit.
    Safe rate: the share of commits whose every affected test file is
    selected. Test reduction: (Tall - Tsel) / Tall, each summed over the
    commits. Precision: Taff / Tsel, Taff the selected test files that are
    affected."""
    total = sum(n for _, _, n in rows)
    selected = sum(len(s) for s, _, _ in rows)
    affected = sum(len(set(s) & set(a)) for s, a, _ in rows)
    safe = sum(set(a) <= set(s) for s, a, _ in rows)
    return {
        "commits": len(rows),
        "safe": safe,
        "safe_rate": safe / len(rows) if rows else None,
        "test_reduction": (total - selected) / total if total else None,
        "precision": affected / selected if selected else None,
        "total": total,
        "selected": selected,
        "affected": affected,
    }


def _median(values):
    values = sorted(values)
    if not values:
        return None
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


def _runs(records):
    """Time and memory of the analyses of a tool on a project."""
    fresh = [r["analysis"] for r in records if not r["analysis"]["cached"]]
    statuses = {}
    for r in records:
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1
    seconds = [a["wall_seconds"] for a in fresh if a.get("wall_seconds") is not None]
    memory = [a["peak_rss_mib"] for a in fresh if a.get("peak_rss_mib") is not None]
    return {
        "analyses": len(fresh),
        "statuses": dict(sorted(statuses.items())),
        "median_seconds": _median(seconds),
        "max_seconds": max(seconds, default=None),
        "max_peak_rss_mib": max(memory, default=None),
        "median_select_seconds": _median([r["select_seconds"] for r in records]),
    }


def check_selection(data, targets, dataset, configuration):
    """Refuse (ValueError) the selection of a tool on a project that is not
    of this experiment: another client, dataset or project layout; not
    exactly one record per target, with the target's parent; a record of
    another analysis than the file's."""
    if data.get("client") != client_fingerprint():
        raise ValueError("selected by another version of the client: select again")
    if data.get("dataset") != dataset:
        raise ValueError("selected on another dataset or manifest")
    if data.get("configuration") != configuration:
        raise ValueError("selected with another root or package")
    parents = {t["current"]: t["parent"] for t in targets}
    seen = set()
    for record in data["commits"]:
        current = record["current"]
        if current in seen:
            raise ValueError(f"{current[:10]}: selected twice")
        seen.add(current)
        if parents.get(current) != record["parent"]:
            raise ValueError(f"{current[:10]}: not a target with this parent")
        if record["settings"] != data["settings"]:
            raise ValueError(f"{current[:10]}: analysed with other settings")
    if seen != set(parents):
        raise ValueError(f"{len(set(parents) - seen)} target commits not selected")


def check_experiment(loaded):
    """Refuse (ValueError) selections ({(tool, project): data}) that are not
    of one experiment: changed lines mapped by more than one Python (its ast
    and tokenize decide what a line holds), or one tool analysed with other
    settings on two projects."""
    runtimes = {data["mapper_python"] for data in loaded.values()}
    if len(runtimes) > 1:
        raise ValueError(f"lines mapped by {len(runtimes)} Pythons: select again")
    for tool in {t for t, _ in loaded}:
        settings = {data["settings"] for (t, _), data in loaded.items() if t == tool}
        if len(settings) > 1:
            raise ValueError(f"{tool}: its projects were analysed with other settings")


def score_project(targets, files, project, selections):
    """The scores of one project: of each tool of `selections` ({tool: the
    data of select}), of the published selections of NameRTS and BabelRTS,
    and of the ground truth itself."""
    truth = {e["current"]: e for e in files[f"ground truth/{project.truth}"]}
    excluded = {}
    for data in selections.values():
        for r in data["commits"]:
            if r["status"] == "not exported":
                excluded.setdefault(r["current"], r["reason"])
    commits = [t["current"] for t in targets if t["current"] not in excluded]

    def rows(chosen):
        return [
            (chosen[c], truth[c]["tests_to_run"], int(truth[c]["total_tests"]))
            for c in commits
        ]

    published = {}
    for label, path in zip(("NameRTS", "BabelRTS"), project.published):
        published[label] = metrics(
            rows({e["current"]: e["tests_to_run"] for e in files[path]})
        )
    ground = metrics(rows({c: truth[c]["tests_to_run"] for c in commits}))
    tools = {}
    for tool, data in selections.items():
        by_current = {r["current"]: r for r in data["commits"]}
        records = [by_current[c] for c in commits]
        unsafe = []
        safe_analysed = safe_fallback = 0
        for r in records:
            missed = sorted(
                set(truth[r["current"]]["tests_to_run"]) - set(r["selected"]["client"])
            )
            if missed:
                unsafe.append(
                    {
                        "current": r["current"],
                        "missed": missed,
                        "status": r["status"],
                        "other_files": r["other_files"],
                    }
                )
            elif r["status"] != "ok":
                safe_fallback += 1
            else:
                safe_analysed += 1
        tools[tool] = {
            "variants": {
                v: metrics(rows({r["current"]: r["selected"][v] for r in records}))
                for v in VARIANTS
            },
            "safe_by_analysis": safe_analysed / len(records) if records else None,
            "safe_by_fallback": safe_fallback / len(records) if records else None,
            "failed": [r["current"] for r in records if r["status"] != "ok"],
            "unsafe": unsafe,
            "runs": _runs(records),
            "changed": sum(len(r["changed"]) for r in records),
            "changed_not_in_graph": sum(
                len(r["changed_not_in_graph"]) for r in records
            ),
            "commits_with_other_files": sum(bool(r["other_files"]) for r in records),
            "commits_with_outside_files": sum(
                bool(r["outside_files"]) for r in records
            ),
        }
    return {
        "published": published,
        "ground_truth": ground,
        "tools": tools,
        "excluded": [{"current": c, "reason": r} for c, r in excluded.items()],
    }


def score(work, manifest, tools, names, export=None):
    """The scores of every tool on every project of `names`. Each selection
    must exist and belong to this experiment (check_selection,
    check_experiment): the means compare all rows on the same projects.
    NameRTS's files come from the export, if one is given."""
    paths = {
        (tool, name): path_in(work, "selections", tool, f"{component(name)}.json")
        for tool in tools
        for name in names
    }
    absent = sorted(f"{t}/{n}" for (t, n), p in paths.items() if not p.exists())
    if absent:
        raise ValueError(f"no selection of {', '.join(absent)}")
    loaded = {
        key: json.loads(path.read_text(encoding="utf-8")) for key, path in paths.items()
    }
    projects, versions = {}, {}
    check_experiment(loaded)
    for tool in tools:
        data = loaded[tool, names[0]]
        versions[tool] = {
            k: data[k]
            for k in (
                "tool",
                "content",
                "settings",
                "python",
                "hash_seed",
                "timeout",
                "memory_limit_mib",
                "mapper_python",
            )
        }
    for name in names:
        project = project_config(manifest, name)
        targets = manifest["projects"][name]["targets"]
        dataset = dataset_fingerprint(manifest, name)
        for tool in tools:
            check_selection(
                loaded[tool, name], targets, dataset, _configuration(project)
            )
        files = namerts_data(work, manifest, project, export)
        chosen = {tool: loaded[tool, name] for tool in tools}
        projects[name] = score_project(targets, files, project, chosen)
    return {
        "namerts": {k: manifest["namerts"][k] for k in ("url", "commit")},
        "versions": versions,
        "projects": projects,
    }


def _percent(x):
    return "-" if x is None else f"{100 * x:.1f}%"


def _number(x, digits=1):
    return "-" if x is None else f"{x:.{digits}f}"


def _mean(values):
    """The mean of the defined values, and how many there are."""
    values = [v for v in values if v is not None]
    return (sum(values) / len(values) if values else None), len(values)


def render(data):
    """The Markdown report of `score`."""
    projects = data["projects"]
    tools = list(data["versions"])
    lines = [
        "# Regression test selection",
        "",
        "Generated by `evaluation/rq4-test-selection/rts.py score`; do not edit. See",
        '`evaluation/README.md`, section "Regression test selection". The',
        "metrics are NameRTS's: *safe*, the share of commits whose every affected",
        "test file is selected; *skipped*, (Tall - Tsel) / Tall summed over the",
        "commits; *precision*, Taff / Tsel. *Safe by fallback* counts the safe",
        "commits whose analysis failed, so that every test file was selected;",
        "*safe by analysis* the other safe commits. Both are measured against",
        "the ground truth, not guaranteed. Rows marked *published*",
        "are computed from NameRTS's published selections (`results/rq1/` of",
        f"NameRTS at `{data['namerts']['commit'][:10]}`); with every target kept"
        " they equal its Table 2 (a target left out of a project changes them).",
        "*Ground truth* selects exactly the affected test files.",
        "",
        "## Versions",
        "",
    ]
    rows = []
    for tool, v in data["versions"].items():
        t = v["tool"]
        rows.append(
            [
                tool,
                t["flavour"],
                f"`{t['commit'][:10]}`" if t.get("commit") else "-",
                f"`{v['content'][:10]}`" if v.get("content") else "-",
                "yes" if t.get("dirty") else "no",
                v["python"],
                v["hash_seed"],
                v["timeout"],
                v.get("memory_limit_mib") or "-",
                v.get("mapper_python") or "-",
            ]
        )
    header = ["Tool", "API", "Commit", "Code", "Changes", "Python", "Hash seed"]
    header += ["Limit (s)", "Limit (MiB)", "Mapping Python"]
    lines += _table(header, rows)
    lines += [
        "*Code*: the SHA-256 of the tool's `pycg/` files. *Mapping Python*: the",
        "interpreter whose `ast` and `tokenize` mapped the changed lines.",
        "",
        "## Results",
        "",
        "The client with all its rules (r1, r2, r3).",
        "",
    ]
    rows, means = [], {}
    keys = ("safe_rate", "safe_by_analysis", "safe_by_fallback")
    keys += ("test_reduction", "precision")

    def add(project, label, m, failed="-", analysed=None, fallback=None):
        values = {**m, "safe_by_analysis": analysed, "safe_by_fallback": fallback}
        rows.append(
            [project, label, m["commits"], failed] + [_percent(values[k]) for k in keys]
        )
        means.setdefault(label, []).append(values)

    for name, p in projects.items():
        for tool in tools:
            t = p["tools"][tool]
            add(
                name,
                tool,
                t["variants"]["client"],
                len(t["failed"]),
                t["safe_by_analysis"],
                t["safe_by_fallback"],
            )
        for label, m in p["published"].items():
            add(name, f"{label} (published)", m)
        add(name, "ground truth", p["ground_truth"])
    if len(projects) > 1:
        for label, values in means.items():
            cells = []
            for k in keys:
                mean, count = _mean([v[k] for v in values])
                cell = _percent(mean)
                if mean is not None and count < len(values):
                    cell += f" ({count} of {len(values)})"
                cells.append(cell)
            commits = sum(v["commits"] for v in values)
            rows.append([f"mean of {len(values)}", label, commits, "-"] + cells)
    header = ["Project", "Selection", "Commits", "Failed", "Safe"]
    header += ["Safe by analysis", "Safe by fallback", "Skipped", "Precision"]
    lines += _table(header, rows)
    lines += [
        "A failed analysis (crash, timeout, memory) selects every test file of",
        "its commit; *Runs* lists the failures. The means are those of the",
        "projects' values, as in NameRTS's Table 2; `(k of n)` marks a mean over",
        "the k projects where the value is defined (a precision without any",
        "selected test file is not).",
        "",
        "## Client rules",
        "",
        "Each variant drops one rule, or all of them (`no rule`).",
        "",
    ]
    rows = []
    for name, p in projects.items():
        for tool in tools:
            for variant, m in p["tools"][tool]["variants"].items():
                rows.append(
                    [name, tool, variant]
                    + [
                        _percent(m[k])
                        for k in ("safe_rate", "test_reduction", "precision")
                    ]
                    + [m["selected"]]
                )
    header = ["Project", "Tool", "Variant", "Safe", "Skipped", "Precision"]
    lines += _table(header + ["Selected"], rows)
    lines += ["## Unsafe commits", "", "Of the client with all its rules.", ""]
    rows = []
    for name, p in projects.items():
        for tool in tools:
            for u in p["tools"][tool]["unsafe"]:
                shown = ", ".join(f"`{m}`" for m in u["missed"][:3])
                if len(u["missed"]) > 3:
                    shown += ", ..."
                rows.append(
                    [name, tool, f"`{u['current'][:10]}`", u["status"]]
                    + [len(u["missed"]), shown, len(u["other_files"])]
                )
    header = ["Project", "Tool", "Commit", "Analysis", "Missed", "Missed test files"]
    if rows:
        lines += _table(header + ["Non-Python files changed"], rows)
    else:
        lines += ["None.", ""]
    lines += [
        "## Runs",
        "",
        "One analysis per tool and parent commit: commits with one parent share",
        "it. Time: wall-clock seconds of the child process; memory: its peak",
        "resident set size. *Changed, not in graph*: changed definitions that are",
        "no node of the graph (no edge reaches them). The last two columns count",
        "the commits that change a file that is not Python, and a Python file",
        "outside the analysis root: changes that the client does not map.",
        "",
    ]
    rows = []
    for name, p in projects.items():
        for tool in tools:
            t = p["tools"][tool]
            r = t["runs"]
            rows.append(
                [name, tool, r["analyses"]]
                + [", ".join(f"{k} {v}" for k, v in r["statuses"].items())]
                + [_number(r["median_seconds"]), _number(r["max_seconds"])]
                + [_number(r["max_peak_rss_mib"], 0)]
                + [_number(r["median_select_seconds"], 2)]
                + [f"{t['changed_not_in_graph']}/{t['changed']}"]
                + [t["commits_with_other_files"], t["commits_with_outside_files"]]
            )
    header = ["Project", "Tool", "Analyses", "Commits by status", "Median s", "Max s"]
    header += ["Max MiB", "Select s", "Changed, not in graph"]
    header += ["Non-Python changes", "Outside the root"]
    lines += _table(header, rows)
    return "\n".join(lines).rstrip() + "\n"


# --- the command line ----------------------------------------------------------


def _read_manifest(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    subs = {}
    for name in ("fetch", "manifest", "export", "select", "score"):
        sub = commands.add_parser(name)
        sub.add_argument("--work", type=Path, required=True, help="work directory")
        sub.add_argument("--manifest", type=Path, default=MANIFEST)
        subs[name] = sub
    for name in ("fetch", "export", "score"):
        subs[name].add_argument(
            "--project", action="append", help="default: all of the manifest"
        )
    subs["export"].add_argument("--out", type=Path, help="default: WORK/export")
    for name in ("select", "score"):
        subs[name].add_argument(
            "--export", type=Path, help="read the export of `export`, not the clones"
        )
    subs["select"].add_argument("--project", required=True)
    subs["select"].add_argument(
        "--tool", required=True, metavar="NAME:FLAVOUR:CHECKOUT"
    )
    subs["select"].add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    subs["select"].add_argument(
        "--memory-limit",
        type=int,
        default=DEFAULT_MEMORY_LIMIT_MIB,
        help="MiB of resident memory of an analysis",
    )
    subs["select"].add_argument("--python", default=sys.executable)
    subs["select"].add_argument("--hash-seed", type=int, default=0)
    subs["select"].add_argument("--allow-dirty", action="store_true")
    subs["select"].add_argument(
        "--limit", type=int, help="only the first N targets, to try a project"
    )
    subs["score"].add_argument("--tool", action="append", required=True)
    subs["score"].add_argument("--out", type=Path, help="default: WORK/report")
    args = parser.parse_args(argv)
    if args.command == "fetch":
        manifest = _read_manifest(args.manifest) if args.manifest.exists() else None
        known = manifest["projects"] if manifest else PROJECTS
        fetch(args.work, manifest, args.project or sorted(known))
    elif args.command == "manifest":
        write_json(args.manifest, build_manifest(args.work))
    elif args.command == "export":
        manifest = _read_manifest(args.manifest)
        names = args.project or sorted(manifest["projects"])
        export(args.work, manifest, names, args.out or args.work / "export")
    elif args.command == "select":
        name, flavour, checkout = args.tool.split(":", 2)
        if flavour not in run.analyse.FLAVOURS:
            parser.error(f"unknown flavour {flavour}")
        tool = run.Tool(name, flavour, Path(checkout).resolve(), args.python)
        tool = tool._replace(hash_seed=args.hash_seed)
        manifest = _read_manifest(args.manifest)
        if args.project not in manifest["projects"]:
            parser.error(f"{args.project} is not a project of the manifest")
        try:
            select(
                args.work,
                manifest,
                args.project,
                tool,
                args.timeout,
                memory_limit_mib=args.memory_limit,
                allow_dirty=args.allow_dirty,
                limit=args.limit,
                export=args.export,
            )
        except run.DirtyCheckout as error:
            parser.error(f"{error}; commit it, or pass --allow-dirty")
    else:
        manifest = _read_manifest(args.manifest)
        names = args.project or sorted(manifest["projects"])
        if args.out is None:  # the default destination stays inside WORK
            out = args.work / "report"
            for name in ("rts.json", "rts.md"):
                writable(out / name, args.work)
        else:
            out = args.out
        data = score(args.work, manifest, args.tool, names, args.export)
        write_json(out / "rts.json", data)
        (out / "rts.md").write_text(render(data), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
