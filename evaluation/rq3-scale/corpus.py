#!/usr/bin/env python3
"""A pinned corpus of PyPI packages, of DyPyBench's projects and of ten
large projects, for the robustness runs (robustness.py).

    python -I evaluation/rq3-scale/corpus.py manifest --top 100 [--rank-list top.json]
    python -I evaluation/rq3-scale/corpus.py pin \\
        --manifest evaluation/rq3-scale/corpus/large-projects.json --work WORK
    python -I evaluation/rq3-scale/corpus.py fetch \\
        --manifest evaluation/rq3-scale/corpus/pypi-top-100.json \\
        --cache CACHE --work WORK
    (or --manifest evaluation/rq3-scale/corpus/dypybench.json, large-projects.json)

`pin` pins a manifest of rows of kind "git archive" (a project of GitHub at
a commit): it downloads the archive of each row from the row's own URL,
which must be GitHub's archive of the row's repository and commit, at most
MAX_ARCHIVE_BYTES, into WORK (once), and writes its SHA-256 and size into
the row. A row that has them already is checked: an archive of another
SHA-256 or size is refused. Any refusal leaves the manifest as it was, and
the command fails.

`manifest` reads a download-rank list (JSON with `last_update` and `rows` of
`{project, download_count}`) and writes evaluation/rq3-scale/corpus/pypi-top-<N>.json,
which is committed: the top N projects in the order of the list, and for
each one file of one release, by these rules.
- The release: the highest version by PEP 440 that is final (no pre-release
  and no development release) and has a file that is not yanked and was
  uploaded on or before the date of the list (`last_update`, UTC, the whole
  day). Files uploaded after that date are left out. Versions that PEP 440
  cannot parse, and versions with a local label (`+...`, not a public
  release), are left out. Yanking is as PyPI reports it today.
- The file, of the wheels with a Python 3 tag (`py3`, `py3N`, `cp3N`): a
  pure wheel (ABI `none`, platform `any`, a Python tag `py3` or `py3N`);
  else the platform wheel first in this order: the interpreter
  (`cp312-cp312`, then `cp3N-abi3` with N <= 12, newest first, then `py3`
  with ABI `none`, then any other tag), then the platform (manylinux
  x86_64, musllinux x86_64, macOS x86_64 or universal2, macOS arm64,
  Windows amd64, any other), then the file name; else the sdist (`.tar.gz`
  first, then by file name). A project with no such release or file, or
  not on PyPI, is a row with the reason.
A row has the name, rank, version, kind, file, URL, SHA-256, size and
upload time. The manifest records the rank list by its SHA-256 and its
path in the repository (RANK_LIST, the default; a list outside the
repository by its file name).

`fetch` downloads each file of the manifest from files.pythonhosted.org (a
git archive from codeload.github.com) into CACHE (once), checks its SHA-256
and size, and unpacks it as data into WORK/<name>/: regular files and
directories only. A member with an absolute path, a `..` component or a
backslash, a link and a special file (device, FIFO) are not written; they
are recorded. Files are written without execute permission (the modes of
the archive are not used), and never over another file. An archive over a
bound (MAX_MEMBERS, MAX_MEMBER_BYTES, MAX_TOTAL_BYTES: on the declared
sizes before anything is written, and on the bytes written) or with
ambiguous names (two members whose paths, or the paths of their
directories, are equal after Unicode normalisation and case folding; an
exact duplicate) is not unpacked: WORK/fetch.json records the reason, and
the package has no run. The bounds cover the number of members, the
declared size of each member (refused ones included) and the bytes written.
The metadata of an archive (PAX and GNU headers of a tar, the central
directory of a zip) is read by the standard library before any bound
applies: the unpacking is not a defence against a crafted archive, and a
corpus from an untrusted source belongs in a container. Nothing is
installed, built, imported or run. WORK/fetch.json records what is
unpacked, with the refused members. A transfer that fails or is cut short
goes on from where it stopped (fetch_body). Every request and every
redirect goes to https on pypi.org, files.pythonhosted.org,
codeload.github.com or raw.githubusercontent.com (DyPyBench's traces) only;
a file must have the size of the manifest (a body that announces another or
sends more is stopped), a project's JSON at most METADATA_MAX_BYTES; one
body has DOWNLOAD_DEADLINE (METADATA_DEADLINE) seconds in all.

The analysis target of a package (`target`), by the kind of its file:
- a wheel: the analysis root is the unpack directory; the target is each
  entry at its top, but `*.dist-info` and `*.data`, that is a `.py` file or
  a directory that holds one (a namespace package counts);
- an sdist: the base is the single directory at the top of the archive (or
  the unpack directory if there is not exactly one); the analysis root is
  base/src if a directory with `__init__.py` is directly under it, else the
  base; the target is each directory with `__init__.py` directly under the
  root. Anything else is a reason, not a guess.
- a git archive (a project pinned by its commit, as DyPyBench's are): the
  base is the single directory at the top of the archive; the analysis
  root is base/<root of the row>; the target is each module of the row
  (`modules`): a directory under the root that holds a `.py` file, or a
  `.py` file. A module or root that is not there is a reason.
The entry points are all `.py` files of the target.

A row of kind "git archive" (evaluation/rq3-scale/corpus/dypybench.json)
pins a project of GitHub by its commit: `repository`, `commit` (40 hex
digits) and the archive that GitHub makes of that commit, at
https://codeload.github.com/<owner>/<repository>/tar.gz/<commit>, with its
SHA-256 and size as fetched when the manifest was written. `fetch` takes
it like any file: the URL must be exactly that of the row's repository and
commit, and the SHA-256 and size must match. GitHub does not promise that
the bytes of an archive never change; if they do, the row is a hash
mismatch, never another tree.
"""

import argparse
import hashlib
import http.client
import importlib.util
import io
import json
import lzma
import os
import re
import shutil
import stat
import sys
import tarfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from typing import NamedTuple

HERE = Path(__file__).resolve().parent
EVALUATION = HERE.parent
COMMON = EVALUATION / "common"
REPO = EVALUATION.parent
# The frozen download-rank list of the corpus, as the repository keeps it.
RANK_LIST = REPO / "macro-benchmark" / "selection" / "top-pypi-packages-2026-11-01.json"
CORPUS = HERE / "corpus"
FILES_HOST = "https://files.pythonhosted.org/"
GIT_ARCHIVE = "git archive"  # the kind of a row pinned by a commit of GitHub
GIT_ARCHIVE_HOST = "https://codeload.github.com/"
GITHUB = "https://github.com/"
_COMMIT = re.compile(r"[0-9a-f]{40}")


def _load(name, path):
    """Load a sibling script by path: evaluation/ is not a package."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# PEP 440, Appendix B (the canonical form, case-insensitive).
_VERSION = re.compile(
    r"""
    v?
    (?:(?P<epoch>[0-9]+)!)?
    (?P<release>[0-9]+(?:\.[0-9]+)*)
    (?P<pre>[-_.]?(?:a|b|c|rc|alpha|beta|pre|preview)[-_.]?[0-9]*)?
    (?P<post>(?:-(?P<post_implicit>[0-9]+))|(?:[-_.]?(?:post|rev|r)[-_.]?(?P<post_n>[0-9]*)))?
    (?P<dev>[-_.]?dev[-_.]?[0-9]*)?
    (?P<local>\+[a-z0-9]+(?:[-_.][a-z0-9]+)*)?
    """,
    re.VERBOSE | re.IGNORECASE,
)
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def final_version_key(version):
    """The order of a final version by PEP 440, or None for a pre-release, a
    development release, a version with a local label (`+...`, which PyPI
    refuses: not a public release) or a version that PEP 440 cannot parse."""
    match = _VERSION.fullmatch(version.strip())
    if not match or match["pre"] or match["dev"] or match["local"]:
        return None
    release = [int(part) for part in match["release"].split(".")]
    while len(release) > 1 and release[-1] == 0:
        release.pop()  # 1.0.0 == 1.0
    if match["post"] is None:
        post = -1
    elif match["post_implicit"] is not None:  # 1.0-1
        post = int(match["post_implicit"])
    else:  # 1.0.post1, 1.0-post1, 1.0-r1, 1.0rev1
        post = int(match["post_n"] or 0)
    return (int(match["epoch"] or 0), tuple(release), post)


def choose_release(releases, cutoff):
    """(version, files) of the release that the rules choose from PyPI's
    `releases` ({version: [file]}), or None. `cutoff` is a date, YYYY-MM-DD."""
    candidates = []
    for version, files in releases.items():
        key = final_version_key(version)
        usable = [
            f
            for f in files
            if not f.get("yanked") and f["upload_time_iso_8601"][:10] <= cutoff
        ]
        if key is not None and usable:
            candidates.append((key, version, usable))
    if not candidates:
        return None
    _, version, files = max(candidates)  # equal keys: the larger string
    return version, files


def _wheel_tags(filename):
    """(python tags, ABI tags, platform tags) of a wheel's file name."""
    parts = filename.removesuffix(".whl").split("-")
    python, abi, platform = parts[-3:]
    return python.split("."), abi.split("."), platform.split(".")


def _is_python_3(file):
    python, _, _ = _wheel_tags(file["filename"])
    return any(re.fullmatch(r"(?:py|cp)3\d*", tag) for tag in python)


def _is_pure(filename):
    python, abi, platform = _wheel_tags(filename)
    return (
        "none" in abi
        and "any" in platform
        and any(re.fullmatch(r"py3\d*", tag) for tag in python)
    )


def _interpreter_rank(python, abi):
    if python == "cp312" and abi == "cp312":
        return (0, 0)
    minor = re.fullmatch(r"cp3(\d+)", python)
    if abi == "abi3" and minor and int(minor[1]) <= 12:
        return (1, -int(minor[1]))
    if abi == "none" and re.fullmatch(r"py3\d*", python):
        return (2, 0)
    return (3, 0)


_PLATFORMS = (
    r"manylinux\w*_x86_64",
    r"musllinux\w*_x86_64",
    r"macosx\w*_(?:x86_64|universal2|intel)",
    r"macosx\w*_arm64",
    r"win_amd64",
)


def _platform_rank(platform):
    for rank, pattern in enumerate(_PLATFORMS):
        if re.fullmatch(pattern, platform):
            return rank
    return len(_PLATFORMS)


def _platform_wheel_key(filename):
    python, abi, platform = _wheel_tags(filename)
    interpreter = min(_interpreter_rank(p, a) for p in python for a in abi)
    return (interpreter, min(_platform_rank(p) for p in platform), filename)


_SDIST_SUFFIXES = (".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".zip")


def choose_file(files):
    """(file, kind) of the release's files that the rules choose, or None.
    The kind is `pure wheel`, `platform wheel` or `sdist`."""
    wheels = sorted(
        (f for f in files if f["filename"].endswith(".whl") and _is_python_3(f)),
        key=lambda f: f["filename"],
    )
    pure = [f for f in wheels if _is_pure(f["filename"])]
    if pure:
        return pure[0], "pure wheel"
    if wheels:
        chosen = min(wheels, key=lambda f: _platform_wheel_key(f["filename"]))
        return chosen, "platform wheel"
    sdists = [
        f
        for f in files
        if f.get("packagetype") == "sdist" and f["filename"].endswith(_SDIST_SUFFIXES)
    ]
    if sdists:
        chosen = min(
            sdists,
            key=lambda f: (not f["filename"].endswith(".tar.gz"), f["filename"]),
        )
        return chosen, "sdist"
    return None


# Requests for one body: a body cut short is continued with a Range request.
ATTEMPTS = 10
_BLOCK = 1 << 16
# The hosts of every request, and of every redirect: https only.
# raw.githubusercontent.com: DyPyBench's traces (rq1-accuracy/trace_recall.py).
ALLOWED_HOSTS = (
    "pypi.org",
    "files.pythonhosted.org",
    "codeload.github.com",
    "raw.githubusercontent.com",
)
# The longest that one body may take, all its requests together, in seconds.
DOWNLOAD_DEADLINE = 1800
METADATA_DEADLINE = 300
# The most bytes of a project's JSON; a file's size is known from the manifest.
METADATA_MAX_BYTES = 256 << 20
clock = time.monotonic  # the clock of the deadlines


class TransferRefused(Exception):
    """A transfer that is stopped, not tried again: a host that is not
    allowed, a length other than the one expected, a deadline passed."""


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to https on an allowed host."""

    def redirect_request(self, request, fp, code, message, headers, new_url):
        try:
            _check_url(new_url)
        except TransferRefused:
            fp.close()  # the answer that redirects
            raise
        return super().redirect_request(request, fp, code, message, headers, new_url)


_OPENER = urllib.request.build_opener(_CheckedRedirects)


def _open(request, timeout):
    """The network boundary of fetch_body."""
    return _OPENER.open(request, timeout=timeout)


def _check_url(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
        raise TransferRefused(f"not an allowed https URL: {url}")


def fetch_body(url, out, timeout, deadline, size=None, max_bytes=None):
    """Write the body at `url` to the binary file `out`. `size` is the exact
    length expected (a file of the manifest), `max_bytes` a bound when it
    is not known (a project's JSON).

    A transfer that fails or is cut short goes on from where it stopped
    with a request for the rest (`Range: bytes=<n>-`), up to ATTEMPTS
    requests in all; a server that answers such a request with the whole
    body (200) starts it again. The body is complete when its length is the
    one the server announced, or `size`. Raised at once: an HTTP error
    status (404); TransferRefused for a URL or redirect that is not https
    on an allowed host, an announced length other than `size` or over
    `max_bytes`, more bytes than either, and `deadline` seconds passed
    since the first request, checked after each read of the body and at its
    end (headers, redirects and the framing of a chunked body are bounded
    only by `timeout` per read). The last failure is raised after
    ATTEMPTS."""
    _check_url(url)
    start = clock()
    limit = size if size is not None else max_bytes
    written, total = 0, None

    def check_time():
        if clock() - start > deadline:
            raise TransferRefused(f"not done in {deadline} s")

    for attempt in range(1, ATTEMPTS + 1):
        check_time()
        request = urllib.request.Request(url)
        if written:
            request.add_header("Range", f"bytes={written}-")
        try:
            with _open(request, timeout) as response:
                if response.status == 206:
                    first, total = _content_range(response.headers)
                    if first != written:
                        raise http.client.HTTPException("an unasked range")
                else:  # the whole body, from its start
                    out.seek(0)
                    out.truncate()
                    written = 0
                    length = response.headers.get("Content-Length")
                    total = _length(length) if length is not None else None
                if total is not None and size is not None and total != size:
                    raise TransferRefused(f"{total} bytes announced, {size} expected")
                if total is not None and limit is not None and total > limit:
                    raise TransferRefused(f"{total} bytes announced, at most {limit}")
                # read1: one read of the socket at most, so that the deadline
                # is checked after each (read waits for a whole block).
                while block := response.read1(_BLOCK):
                    if limit is not None and written + len(block) > limit:
                        raise TransferRefused(f"more than {limit} bytes")
                    out.write(block)
                    written += len(block)
                    check_time()
                check_time()  # the end of the body can come late too
            expected = total if total is not None else size
            if expected is None or written == expected:
                return
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, http.client.HTTPException, OSError):
            if attempt == ATTEMPTS:
                raise
        else:
            if attempt == ATTEMPTS:
                raise http.client.IncompleteRead(b"", expected - written)
        time.sleep(min(attempt, 5))


# A length is 1 to 18 digits (under 10**18 bytes); anything else in a
# Content-Length or a Content-Range is a refused transfer.
_NUMBER = r"[0-9]{1,18}"


def _length(text):
    """A Content-Length as a number."""
    if not re.fullmatch(_NUMBER, text):
        raise TransferRefused(f"a malformed length: {text[:20]!r}")
    return int(text)


def _content_range(headers):
    """(first byte, total length) of a 206 answer's Content-Range."""
    text = headers.get("Content-Range", "")
    match = re.fullmatch(f"bytes ({_NUMBER})-{_NUMBER}/({_NUMBER})", text)
    if not match:
        raise TransferRefused(f"a malformed range: {text[:40]!r}")
    return int(match[1]), int(match[2])


def get_json(name):
    """PyPI's JSON of a project, or None if PyPI does not know it (404).
    The network boundary of `manifest`."""
    url = f"https://pypi.org/pypi/{urllib.parse.quote(name)}/json"
    body = io.BytesIO()
    try:
        fetch_body(url, body, 60, METADATA_DEADLINE, max_bytes=METADATA_MAX_BYTES)
    except urllib.error.HTTPError as error:
        error.close()
        if error.code == 404:
            return None
        raise
    return json.loads(body.getvalue())


def manifest_row(name, rank, cutoff):
    """The manifest row of one project of the rank list."""
    row = {"name": name, "rank": rank}
    if not is_path_component(name):
        return row | {"reason": "the name is not one path component"}
    data = get_json(name)
    if data is None:
        return row | {"reason": "not on PyPI"}
    release = choose_release(data.get("releases", {}), cutoff)
    if release is None:
        return row | {"reason": "no final release on or before the date"}
    version, files = release
    chosen = choose_file(files)
    if chosen is None:
        return row | {"reason": f"no wheel or sdist in release {version}"}
    file, kind = chosen
    return row | {
        "version": version,
        "kind": kind,
        "file": file["filename"],
        "url": file["url"],
        "sha256": file["digests"]["sha256"],
        "size": file["size"],
        "uploaded": file["upload_time_iso_8601"],
    }


def manifest(rank_list, top, progress=None):
    """The manifest of the top `top` projects of the rank list file."""
    data = Path(rank_list).read_bytes()
    ranks = json.loads(data)
    cutoff = ranks["last_update"][:10]
    rows = []
    for rank, entry in enumerate(ranks["rows"][:top], start=1):
        rows.append(manifest_row(entry["project"], rank, cutoff))
        if progress:
            progress(f"{rank} {entry['project']}: {rows[-1].get('kind', 'none')}")
    return {
        "rank_list": {
            "file": _rank_list_name(rank_list),
            "sha256": hashlib.sha256(data).hexdigest(),
            "last_update": ranks["last_update"],
        },
        "cutoff": cutoff,
        "top": top,
        "rows": rows,
    }


def _rank_list_name(path):
    """The path of a rank list in the repository, or its file name if it is
    not in the repository."""
    path = Path(path).resolve()
    if path.is_relative_to(REPO.resolve()):
        return path.relative_to(REPO.resolve()).as_posix()
    return path.name


def is_path_component(name):
    return bool(_NAME.fullmatch(name))


# --------------------------------------------------------------------------
# Fetching and unpacking
# --------------------------------------------------------------------------


def git_archive_url(repository, commit):
    """The URL of GitHub's archive of `commit` of `repository`
    (https://github.com/<owner>/<name>), or None if either is not one."""
    if not repository.startswith(GITHUB) or not _COMMIT.fullmatch(commit):
        return None
    parts = repository.removeprefix(GITHUB).split("/")
    if len(parts) != 2 or not all(is_path_component(part) for part in parts):
        return None
    return f"{GIT_ARCHIVE_HOST}{parts[0]}/{parts[1]}/tar.gz/{commit}"


_SHA256 = re.compile(r"[0-9a-f]{64}")


def row_problem(row):
    """Why fetch must not trust a manifest row with a file, or None: a name
    or file that is not one path component, a URL other than that of its
    file (PyPI) or of its repository and commit (a git archive), a SHA-256
    that is not 64 hex digits, a git archive whose version is not its
    commit. Checked before anything is downloaded or reused from the cache,
    and when a fetched corpus is planned (robustness.plan)."""
    if not is_path_component(row["name"]) or not is_path_component(row["file"]):
        return "refused name"
    if not isinstance(row.get("url"), str):
        return "refused URL"
    if not _url_allowed(row):
        return "refused URL"
    if not _SHA256.fullmatch(str(row.get("sha256", ""))):
        return "refused SHA-256"
    size = row.get("size")
    if type(size) is not int or size <= 0:
        return "refused size"
    if row.get("kind") == GIT_ARCHIVE and row.get("version") != row.get("commit"):
        return "refused version: not the commit"
    return None


def _url_allowed(row):
    """Whether the URL of a row is one that fetch may download: a file of
    PyPI, or the archive of the row's own repository and commit."""
    if row.get("kind") == GIT_ARCHIVE:
        expected = git_archive_url(row.get("repository", ""), row.get("commit", ""))
        return expected is not None and row["url"] == expected
    return row["url"].startswith(FILES_HOST) and row["url"].endswith("/" + row["file"])


MAX_ARCHIVE_BYTES = 2 << 30  # a git archive that `pin` downloads, its size unknown


def pin(manifest, work, download=None, progress=None):
    """(the manifest with the SHA-256 and size of each archive, {name: why a
    row is refused}) of a manifest of git archives (see the module
    docstring); the archives go to `work`."""
    download = download or _download_unsized
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    rows, refused = [], {}
    for row in manifest["rows"]:
        name = row.get("name")
        commit = row.get("commit", "")
        if row.get("kind") != GIT_ARCHIVE:
            refused[name] = "not a git archive"
            continue
        if (
            not is_path_component(str(name))
            or row.get("file") != f"{name}-{commit}.tar.gz"
        ):
            refused[name] = "refused name or file"
            continue
        if row.get("url") != git_archive_url(row.get("repository", ""), commit):
            refused[name] = "refused URL: not the archive of its repository and commit"
            continue
        if row.get("version") != commit:
            refused[name] = "refused version: not the commit"
            continue
        archive = work / row["file"]
        if not archive.is_file():
            partial = archive.with_name(archive.name + ".partial")
            download(row["url"], partial, MAX_ARCHIVE_BYTES)
            partial.replace(archive)
        found = {"sha256": _sha256(archive), "size": archive.stat().st_size}
        if "sha256" in row or "size" in row:
            pinned = {"sha256": row.get("sha256"), "size": row.get("size")}
            if pinned != found:
                refused[name] = f"another archive: {found}, the row pins {pinned}"
                continue
        entry = {}
        for key, value in row.items():  # sha256 and size after the URL
            if key in ("sha256", "size"):
                continue
            entry[key] = value
            if key == "url":
                entry |= found
        rows.append(entry)
        if progress:
            progress(f"{name}: {found['size']} bytes, sha256 {found['sha256']}")
    return manifest | {"rows": rows}, refused


def _download_unsized(url, path, max_bytes):
    with open(path, "wb") as out:
        fetch_body(url, out, 300, DOWNLOAD_DEADLINE, max_bytes=max_bytes)


def download(url, path, size):
    """Write the file at `url`, of `size` bytes, to `path`. The network
    boundary of `fetch`."""
    with open(path, "wb") as out:
        fetch_body(url, out, 300, DOWNLOAD_DEADLINE, size=size)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _member_problem(name):
    """Why a member name is not written, or None."""
    if "\\" in name or "\0" in name:
        return "backslash"
    path = PurePosixPath(name)
    if path.is_absolute() or re.match(r"[A-Za-z]:", name):
        return "absolute path"
    if ".." in path.parts:
        return "parent directory"
    return None


# Bounds of one archive. A package over one is a row with the reason and is
# not unpacked. The largest package of the top 100 unpacks to about 150 MB.
MAX_MEMBERS = 200_000
MAX_MEMBER_BYTES = 1 << 30  # 1 GiB, expanded
MAX_TOTAL_BYTES = 4 << 30  # 4 GiB, expanded, all members


class ArchiveRefused(Exception):
    """An archive that is not unpacked: over a bound, or with ambiguous
    names."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class Member(NamedTuple):
    name: str
    kind: str  # file, dir, link or special file
    size: int  # the declared size, expanded
    info: object  # the ZipInfo or TarInfo


def _zip_members(data):
    for info in data.infolist():
        mode = stat.S_IFMT(info.external_attr >> 16)
        if info.is_dir():
            kind = "dir"
        elif mode in (0, stat.S_IFREG):
            kind = "file"
        elif mode == stat.S_IFLNK:
            kind = "link"
        else:
            kind = "special file"
        yield Member(info.filename, kind, info.file_size, info)


def _tar_members(data):
    for info in data:  # reads the headers one by one
        if info.isreg():
            kind = "file"
        elif info.isdir():
            kind = "dir"
        elif info.issym() or info.islnk():
            kind = "link"
        else:
            kind = "special file"
        yield Member(info.name, kind, info.size, info)


def _name_key(part):
    """A path component as a file system that ignores case and Unicode
    normalisation may see it (Unicode caseless matching)."""
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", part).casefold())


def _claim(seen, member):
    """Record the path of a member and of its parent directories in `seen`;
    refuse two spellings of one path, two files of one path, and a file
    that is also a directory."""
    parts = PurePosixPath(member.name).parts
    for end in range(1, len(parts) + 1):
        spelling = "/".join(parts[:end])
        kind = member.kind if end == len(parts) else "dir"
        key = tuple(_name_key(part) for part in parts[:end])
        if key not in seen:
            seen[key] = (spelling, kind)
            continue
        other, other_kind = seen[key]
        if other != spelling or "file" in (kind, other_kind):
            raise ArchiveRefused(f"ambiguous names: {other}, {spelling}")


def _plan(members):
    """The members to write and the refused ones ([name, reason]), in the
    archive's order. Raises ArchiveRefused before anything is written: the
    bounds are checked on the declared sizes as the headers are read."""
    written, refused, seen, total = [], [], {}, 0
    for count, member in enumerate(members, start=1):
        if count > MAX_MEMBERS:
            raise ArchiveRefused(f"more than {MAX_MEMBERS} members")
        total += member.size  # a refused member too: it is read past
        _check_size(member.name, member.size, total)
        problem = _member_problem(member.name)
        if not problem and member.kind in ("link", "special file"):
            problem = member.kind
        if problem:
            refused.append([member.name, problem])
            continue
        _claim(seen, member)
        written.append(member)
    return written, refused


def _check_size(name, size, total):
    if size > MAX_MEMBER_BYTES:
        raise ArchiveRefused(f"a member of more than {MAX_MEMBER_BYTES} bytes: {name}")
    if total > MAX_TOTAL_BYTES:
        raise ArchiveRefused(f"more than {MAX_TOTAL_BYTES} bytes in all")


def _open_archive(archive):
    """(archive object, its member reader, the opener of a member)."""
    archive = Path(archive)
    if archive.name.endswith((".whl", ".zip")):
        data = zipfile.ZipFile(archive)
        return data, _zip_members, data.open
    data = tarfile.open(archive, "r:*")
    return data, _tar_members, data.extractfile


def verify_unpacked(archive, directory):
    """None if `directory` holds exactly what unpack writes from `archive`:
    its files, byte for byte, and its directories, those that the archive
    names and those that hold its members, and nothing else; else the first
    difference, as "<path>: changed", "<path>: missing", "<path>: not in
    the archive" or "<path>: not a regular file". So what reads the tree
    (the target of the package) reads the archive. The tree is never
    repaired: the archive, whose SHA-256 the committed manifest pins, is
    the trust anchor, not the unpacked files nor fetch.json."""
    expected = {}  # path -> the SHA-256 of a file, or DIRECTORY
    data, members, open_member = _open_archive(archive)
    with data:
        written, _ = _plan(members(data))
        for member in written:
            parts = PurePosixPath(member.name).parts
            for end in range(1, len(parts)):
                expected["/".join(parts[:end])] = DIRECTORY
            if member.kind == "dir":
                if parts:  # `.` or `./` names the root: `directory` itself
                    expected["/".join(parts)] = DIRECTORY
                continue
            digest = hashlib.sha256()
            with open_member(member.info) as source:
                while block := source.read(1 << 20):
                    digest.update(block)
            expected["/".join(parts)] = digest.hexdigest()
    found, problems = {}, []
    directory = Path(directory)
    for current, directories, names in os.walk(directory):
        for name in sorted(directories + names):
            path = Path(current) / name
            relative = path.relative_to(directory).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                found[relative] = DIRECTORY
            elif stat.S_ISREG(mode):
                found[relative] = _sha256(path)
            else:
                problems.append(f"{relative}: not a regular file")
    for relative in sorted(set(expected) | set(found)):
        if relative not in found:
            problems.append(f"{relative}: missing")
        elif relative not in expected:
            problems.append(f"{relative}: not in the archive")
        elif found[relative] != expected[relative]:
            problems.append(f"{relative}: changed")
    return min(problems) if problems else None


DIRECTORY = "directory"  # what verify_unpacked records of a directory


def unpack(archive, destination):
    """Unpack a wheel, zip or tar archive as data into `destination`; return
    the members not written, as [name, reason], in the archive's order.
    Raises ArchiveRefused for an archive over a bound or with ambiguous
    names (checked before anything is written, and the bounds again on the
    bytes written); a file is never overwritten."""
    destination = Path(destination)
    data, members, open_member = _open_archive(archive)
    with data:
        written, refused = _plan(members(data))
        destination.mkdir(parents=True)
        root = destination.resolve()
        total = 0
        for member in written:
            path = root.joinpath(*PurePosixPath(member.name).parts)
            if member.kind == "dir":
                path.mkdir(parents=True, exist_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            size = 0
            with open_member(member.info) as source, open(path, "xb") as target:
                while block := source.read(1 << 20):
                    size += len(block)
                    total += len(block)
                    _check_size(member.name, size, total)
                    target.write(block)
    return refused


# The errors of a damaged archive: of the formats (zip, tar) and of their
# decompressors (zlib, lzma; bz2 raises OSError). RuntimeError covers
# NotImplementedError (an unknown zip compression) and an encrypted member.
_UNPACK_ERRORS = (
    OSError,
    EOFError,
    RuntimeError,
    ValueError,
    zipfile.BadZipFile,
    tarfile.TarError,
    zlib.error,
    lzma.LZMAError,
)


def _write_json(path, data):
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    temporary.replace(path)


def fetch(manifest, cache, work, download=download, progress=None):
    """Download, check and unpack each file of the manifest; return
    {name: status} for the rows that have a file. Statuses: unpacked,
    unpacked before, archive refused: <reason> (recorded: the package has
    no run), refused name, refused URL, download failed: <error type>,
    download refused: <reason>, hash mismatch, work directory holds another
    file, unpack failed: <error type>."""
    cache, work = Path(cache).resolve(), Path(work).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    record_path = work / "fetch.json"
    recorded = json.loads(record_path.read_text()) if record_path.exists() else {}
    status = {}
    for row in manifest["rows"]:
        if "file" not in row:
            continue
        name = row["name"]
        status[name] = _fetch_row(row, cache, work, recorded, download)
        _write_json(record_path, recorded)
        if progress:
            progress(f"{name}: {status[name]}")
    return status


def cached(archive, row):
    """Whether `archive` is the file that the row pins: its size and its
    SHA-256."""
    archive = Path(archive)
    return (
        archive.is_file()
        and archive.stat().st_size == row["size"]
        and _sha256(archive) == row["sha256"]
    )


def _fetch_row(row, cache, work, recorded, download):
    name, filename = row["name"], row["file"]
    problem = row_problem(row)  # before the cache is trusted
    if problem:
        return problem
    unpacked = work / name
    reused = name in recorded or unpacked.exists()
    if reused:
        record = recorded.get(name, {})
        if record.get("sha256") != row["sha256"]:
            return "work directory holds another file"
        if "archive refused" in record:
            return f"archive refused: {record['archive refused']}"
        if not unpacked.is_dir():
            return "work directory holds another file"
    archive = cache / filename
    if not cached(archive, row):
        partial = cache / (filename + ".partial")
        try:
            download(row["url"], partial, row["size"])
        except TransferRefused as refusal:
            partial.unlink(missing_ok=True)
            return f"download refused: {refusal}"
        except (urllib.error.URLError, http.client.HTTPException, OSError) as error:
            partial.unlink(missing_ok=True)
            return f"download failed: {type(error).__name__}"
        if partial.stat().st_size != row["size"] or _sha256(partial) != row["sha256"]:
            partial.unlink()
            return "hash mismatch"
        partial.replace(archive)
    if reused:  # checked against the pinned archive, never repaired
        changed = verify_unpacked(archive, unpacked)
        return f"unpacked tree changed: {changed}" if changed else "unpacked before"
    staging = work / f".{name}.partial"
    if staging.exists():
        shutil.rmtree(staging)  # an unpack that an earlier run did not finish
    try:
        refused = unpack(archive, staging)
    except ArchiveRefused as refusal:
        shutil.rmtree(staging, ignore_errors=True)
        recorded[name] = {
            "file": filename,
            "sha256": row["sha256"],
            "archive refused": refusal.reason,
        }
        return f"archive refused: {refusal.reason}"
    except _UNPACK_ERRORS as error:
        shutil.rmtree(staging, ignore_errors=True)
        return f"unpack failed: {type(error).__name__}"
    staging.replace(unpacked)
    recorded[name] = {"file": filename, "sha256": row["sha256"], "refused": refused}
    return "unpacked"


# --------------------------------------------------------------------------
# The analysis target
# --------------------------------------------------------------------------


class Target(NamedTuple):
    root: Path  # the analysis root
    modules: list  # the top-level modules and packages of the target
    files: list  # the entry points: every .py file of the target, sorted


def _python_files(path):
    if path.is_file():
        return [path] if path.suffix == ".py" else []
    return sorted(p for p in path.rglob("*.py") if p.is_file() and not p.is_symlink())


def target(directory, kind, root=None, modules=None):
    """The Target of an unpacked package, or the reason why the rule cannot
    give one (a string). `root` and `modules` are those of a row of kind
    "git archive"."""
    directory = Path(directory)
    if kind == GIT_ARCHIVE:
        return _stated_target(directory, root or "", modules or [])
    if kind != "sdist":
        root = directory
        entries = [
            p
            for p in sorted(root.iterdir())
            if not p.name.endswith((".dist-info", ".data")) and _python_files(p)
        ]
        if not entries:
            return "no Python module at the top"
        files = [f for p in entries for f in _python_files(p)]
        return Target(root, [p.name.removesuffix(".py") for p in entries], files)
    top = list(directory.iterdir())
    base = top[0] if len(top) == 1 and top[0].is_dir() else directory
    root = base
    if (base / "src").is_dir() and _packages(base / "src"):
        root = base / "src"
    packages = _packages(root)
    if not packages:
        return "no package directory (with __init__.py) under the root or src/"
    files = [f for p in packages for f in _python_files(p)]
    return Target(root, [p.name for p in packages], files)


def _stated_target(directory, root, modules):
    """The Target of a git archive: the modules that its row states, under
    the root that its row states."""
    relative = PurePosixPath(root)
    if relative.is_absolute() or ".." in relative.parts or "\\" in root:
        return f"not a relative path in the archive: {root}"
    if not modules:
        return "no module stated"
    for name in modules:
        if not name.isidentifier():
            return f"not a module name: {name}"
    top = list(directory.iterdir())
    if len(top) != 1 or not top[0].is_dir():
        return "not one directory at the top of the archive"
    base = top[0] / Path(*relative.parts) if relative.parts else top[0]
    files = []
    for name in modules:
        found = [p for p in (base / name, base / f"{name}.py") if _python_files(p)]
        if not found:
            return f"no module {name} under the root {root}"
        files += _python_files(found[0])
    return Target(base, list(modules), files)


def _packages(directory):
    return [
        p
        for p in sorted(directory.iterdir())
        if p.is_dir() and (p / "__init__.py").is_file()
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("manifest", help="write the manifest")
    make.add_argument("--rank-list", type=Path, help=f"default: {RANK_LIST}")
    make.add_argument("--top", type=int, required=True)
    make.add_argument("--out", type=Path, help="default: corpus/pypi-top-<N>.json")
    pinned = commands.add_parser("pin", help="pin the archives of a manifest")
    pinned.add_argument("--manifest", type=Path, required=True)
    pinned.add_argument("--work", type=Path, required=True)
    get = commands.add_parser("fetch", help="download and unpack the manifest")
    get.add_argument("--manifest", type=Path, required=True)
    get.add_argument("--cache", type=Path, required=True)
    get.add_argument("--work", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "manifest":
        if args.rank_list is None and not RANK_LIST.is_file():
            parser.error(f"no rank list: {RANK_LIST} is absent; give --rank-list")
        args.rank_list = args.rank_list or RANK_LIST
        out = args.out or CORPUS / f"pypi-top-{args.top}.json"
        data = manifest(args.rank_list, args.top, progress=_print)
        out.parent.mkdir(parents=True, exist_ok=True)
        # run.py is loaded here only: the fetch, a step of the build of the
        # inputs image, needs nothing but this file and its manifests.
        run = _load("evaluation_run", COMMON / "run.py")
        out.write_text(run.dumps(data) + "\n", encoding="utf-8")
        return 0
    data = json.loads(args.manifest.read_text(encoding="utf-8"))
    if args.command == "pin":
        found, refused = pin(data, args.work, progress=_print)
        for name, problem in sorted(refused.items(), key=lambda item: str(item[0])):
            print(f"REFUSED {name}: {problem}", file=sys.stderr)
        if refused:
            return 1
        run = _load("evaluation_run", COMMON / "run.py")
        args.manifest.write_text(run.dumps(found) + "\n", encoding="utf-8")
        return 0
    status = fetch(data, args.cache, args.work, progress=_print)
    failed = {name: s for name, s in status.items() if not _done(s)}
    for name, problem in sorted(failed.items()):
        print(f"FAILED {name}: {problem}", file=sys.stderr)
    return 1 if failed else 0


def _done(status):
    """Whether a status of fetch is a success: the package is unpacked, now
    or before, or its archive is refused (recorded: it has no run)."""
    return status in ("unpacked", "unpacked before") or status.startswith(
        "archive refused: "
    )


def _print(text):
    print(text, flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
