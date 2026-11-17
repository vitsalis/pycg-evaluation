"""Behaviour tests for evaluation/rq3-scale/corpus.py: the manifest of a pinned corpus
of PyPI packages, the download, the unpacking as data, and the analysis
target of a package.

The network is replaced at its boundary (`corpus.get_json` and the
`download` argument of `corpus.fetch`). They run on every supported Python
version.
"""

import hashlib
import io
import json
import stat
import tarfile
import warnings
import zipfile
from pathlib import Path

import pytest
from evaluation_fakes import load

corpus = load("rq3-scale/corpus")

HOST = "https://files.pythonhosted.org/packages/ab/cd/"


# --------------------------------------------------------------------------
# The release
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# The file of the release
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# The manifest
# --------------------------------------------------------------------------


def test_the_published_manifest_names_the_frozen_rank_list():
    manifest = json.loads((corpus.CORPUS / "pypi-top-100.json").read_text())
    assert manifest["rank_list"]["file"] == (
        "macro-benchmark/selection/top-pypi-packages-2026-11-01.json"
    )
    assert corpus.RANK_LIST == corpus.REPO / manifest["rank_list"]["file"]
    data = corpus.RANK_LIST.read_bytes()
    assert manifest["rank_list"]["sha256"] == hashlib.sha256(data).hexdigest()
    ranks = json.loads(data)
    assert manifest["rank_list"]["last_update"] == ranks["last_update"]
    top = [row["project"] for row in ranks["rows"][: manifest["top"]]]
    assert [row["name"] for row in manifest["rows"]] == top
    assert [row["rank"] for row in manifest["rows"]] == list(range(1, len(top) + 1))


# --------------------------------------------------------------------------
# Fetching and unpacking
# --------------------------------------------------------------------------


def zip_bytes(members):
    """A zip archive of (name, content, mode) members."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive, warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # a duplicate name
        for name, content, mode in members:
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            archive.writestr(info, content)
    return buffer.getvalue()


def tar_bytes(members):
    """A tar.gz archive of (name, type, content, linkname) members."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, kind, content, link in members:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.linkname = link
            info.size = len(content) if kind == tarfile.REGTYPE else 0
            archive.addfile(info, io.BytesIO(content) if info.size else None)
    return buffer.getvalue()


def row(name, filename, data, kind):
    return {
        "name": name,
        "rank": 1,
        "version": "1.0",
        "kind": kind,
        "file": filename,
        "url": HOST + filename,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "uploaded": "2026-10-01T12:00:00.000000Z",
    }


def serve(files):
    """A download function that serves `files` ({url: bytes}) and records
    the URLs asked."""
    asked = []

    def download(url, path, size):
        asked.append(url)
        Path(path).write_bytes(files[url])

    download.asked = asked
    return download


WHEEL = zip_bytes(
    [
        ("demo/__init__.py", b"", stat.S_IFREG | 0o755),
        ("demo/core.py", b"def f():\n    pass\n", stat.S_IFREG | 0o644),
        ("demo-1.0.dist-info/METADATA", b"", stat.S_IFREG | 0o644),
    ]
)


@pytest.mark.parametrize("tampered", [WHEEL + b"longer", WHEEL[:-1] + b"?"])
def test_a_hash_mismatch_is_refused_and_nothing_is_kept(tmp_path, tampered):
    manifest = {"rows": [row("demo", "demo-1.0-py3-none-any.whl", WHEEL, "pure")]}
    download = serve({HOST + "demo-1.0-py3-none-any.whl": tampered})
    cache, work = tmp_path / "cache", tmp_path / "work"
    status = corpus.fetch(manifest, cache, work, download)
    assert status == {"demo": "hash mismatch"}
    assert list(cache.iterdir()) == []
    assert not (work / "demo").exists()
    assert json.loads((work / "fetch.json").read_text()) == {}


# --------------------------------------------------------------------------
# The analysis target
# --------------------------------------------------------------------------


def tree(root, files):
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n")
    return root


def test_the_target_of_a_wheel_is_its_top_level_modules(tmp_path):
    root = tree(
        tmp_path / "demo",
        [
            "demo/__init__.py",
            "demo/sub/mod.py",
            "google/protobuf/x.py",  # a namespace package
            "six.py",
            "demo.libs/libfoo.so",  # no Python module
            "demo-1.0.dist-info/METADATA",
            "demo-1.0.data/purelib/extra.py",
            "demo-1.0.dist-info/tool.py",
            "stub.pyi",
        ],
    )
    target = corpus.target(root, "pure wheel")
    assert target.root == root
    assert target.modules == ["demo", "google", "six"]
    assert [p.relative_to(root).as_posix() for p in target.files] == [
        "demo/__init__.py",
        "demo/sub/mod.py",
        "google/protobuf/x.py",
        "six.py",
    ]


@pytest.mark.parametrize(
    ("files", "root", "modules"),
    [
        (
            ["demo-1.0/src/demo/__init__.py", "demo-1.0/tests/t.py"],
            "demo-1.0/src",
            ["demo"],
        ),
        (
            [
                "demo-1.0/demo/__init__.py",
                "demo-1.0/b/__init__.py",
                "demo-1.0/setup.py",
            ],
            "demo-1.0",
            ["b", "demo"],
        ),
        # Not one directory at the top: the unpack directory is the base.
        (["demo/__init__.py", "PKG-INFO"], ".", ["demo"]),
    ],
)
def test_the_target_of_an_sdist_is_its_packages(tmp_path, files, root, modules):
    unpacked = tree(tmp_path / "demo", files)
    target = corpus.target(unpacked, "sdist")
    assert target.root == unpacked / root
    assert target.modules == modules


def test_an_sdist_that_the_rule_cannot_handle_is_a_reason(tmp_path):
    unpacked = tree(tmp_path / "demo", ["demo-1.0/demo.py", "demo-1.0/setup.py"])
    assert corpus.target(unpacked, "sdist") == (
        "no package directory (with __init__.py) under the root or src/"
    )
    empty = tree(tmp_path / "wheel", ["demo-1.0.dist-info/METADATA"])
    assert corpus.target(empty, "pure wheel") == "no Python module at the top"


# --------------------------------------------------------------------------
# A project pinned by its commit: the archive of GitHub (DyPyBench)
# --------------------------------------------------------------------------

COMMIT = "c0ffee" + "0" * 34
ARCHIVE = tar_bytes(
    [
        (f"demo-{COMMIT}/src/demo/__init__.py", tarfile.REGTYPE, b"", ""),
        (f"demo-{COMMIT}/tests/test_demo.py", tarfile.REGTYPE, b"x = 1\n", ""),
    ]
)


def git_row(data=ARCHIVE, commit=COMMIT, url=None):
    return {
        "name": "demo",
        "rank": 1,
        "version": commit,
        "kind": "git archive",
        "repository": "https://github.com/owner/demo",
        "commit": commit,
        "file": f"demo-{commit}.tar.gz",
        "url": url or f"https://codeload.github.com/owner/demo/tar.gz/{commit}",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "root": "src",
        "modules": ["demo"],
    }


@pytest.mark.parametrize(
    "url",
    [
        f"https://codeload.github.com/owner/demo/tar.gz/{'1' * 40}",  # another commit
        f"https://codeload.github.com/other/demo/tar.gz/{COMMIT}",  # another repository
        f"http://codeload.github.com/owner/demo/tar.gz/{COMMIT}",
        f"https://example.com/owner/demo/tar.gz/{COMMIT}",
        f"https://files.pythonhosted.org/owner/demo/tar.gz/{COMMIT}",
    ],
)
def test_the_archive_url_names_the_repository_and_commit_of_the_row(tmp_path, url):
    download = serve({})
    entry = git_row(url=url)
    status = corpus.fetch({"rows": [entry]}, tmp_path / "c", tmp_path / "w", download)
    assert status == {"demo": "refused URL"}
    assert download.asked == []


@pytest.mark.parametrize("commit", ["c0ffee", "C0FFEE" + "0" * 34, "main"])
def test_a_git_archive_is_pinned_by_a_full_commit_id(tmp_path, commit):
    entry = git_row(commit=commit)
    download = serve({})
    status = corpus.fetch({"rows": [entry]}, tmp_path / "c", tmp_path / "w", download)
    assert status == {"demo": "refused URL"}
    assert download.asked == []


def test_the_target_of_a_git_archive_is_its_stated_root_and_modules(tmp_path):
    unpacked = tree(
        tmp_path / "demo",
        [
            "demo-1/src/demo/__init__.py",
            "demo-1/src/demo/sub/mod.py",
            "demo-1/src/_tool.py",
            "demo-1/src/other/__init__.py",  # not a stated module
            "demo-1/tests/test_demo.py",
        ],
    )
    target = corpus.target(unpacked, "git archive", "src", ["demo", "_tool"])
    assert target.root == unpacked / "demo-1" / "src"
    assert target.modules == ["demo", "_tool"]
    assert [p.relative_to(target.root).as_posix() for p in target.files] == [
        "demo/__init__.py",
        "demo/sub/mod.py",
        "_tool.py",
    ]
    # A directory of scripts without __init__.py is a module too.
    scripts = tree(tmp_path / "scripts", ["r-1/Mapping/grid.py", "r-1/README.md"])
    target = corpus.target(scripts, "git archive", "", ["Mapping"])
    assert target.root == scripts / "r-1"
    assert [p.name for p in target.files] == ["grid.py"]


@pytest.mark.parametrize(
    ("root", "modules", "reason"),
    [
        ("src", ["absent"], "no module absent under the root src"),
        ("lib", ["demo"], "no module demo under the root lib"),
        ("../x", ["demo"], "not a relative path in the archive: ../x"),
        ("/src", ["demo"], "not a relative path in the archive: /src"),
        ("src", ["demo/sub"], "not a module name: demo/sub"),
        ("src", ["demo.py"], "not a module name: demo.py"),
        ("src", [], "no module stated"),
    ],
)
def test_a_git_archive_target_that_does_not_hold_is_a_reason(
    tmp_path, root, modules, reason
):
    unpacked = tree(tmp_path / "demo", ["demo-1/src/demo/__init__.py"])
    assert corpus.target(unpacked, "git archive", root, modules) == reason


def test_a_git_archive_must_have_one_directory_at_the_top(tmp_path):
    unpacked = tree(tmp_path / "demo", ["a/demo/__init__.py", "b/x.py"])
    assert corpus.target(unpacked, "git archive", "", ["demo"]) == (
        "not one directory at the top of the archive"
    )


def test_the_published_dypybench_manifest_pins_every_project():
    data = json.loads((corpus.CORPUS / "dypybench.json").read_text())
    source = data["source"]
    assert source["repository"] == "https://github.com/sola-st/DyPyBench"
    assert len(source["commit"]) == 40
    rows = data["rows"]
    assert len(rows) == 50
    assert [entry["rank"] for entry in rows] == list(range(1, 51))
    assert len({entry["name"] for entry in rows}) == 50
    for entry in rows:
        assert entry["repository"].startswith("https://github.com/"), entry["name"]
        if "file" not in entry:  # a project whose checkout cannot be recovered
            assert entry["reason"], entry["name"]
            continue
        assert entry["kind"] == "git archive", entry["name"]
        assert set(entry["sha256"]) <= set("0123456789abcdef"), entry["name"]
        assert len(entry["sha256"]) == 64 and entry["size"] > 0
        assert entry["modules"], entry["name"]
        assert entry["url"] == corpus.git_archive_url(
            entry["repository"], entry["commit"]
        )
        assert entry["version"] == entry["commit"]
        assert entry["committed"] < "2023-01-19", entry["name"]


# --- review round 1: the whole row is checked, cached or not -----------------


def test_a_cached_archive_with_another_commit_in_its_row_is_refused(tmp_path):
    entry = git_row()
    download = serve({entry["url"]: ARCHIVE})
    cache, work = tmp_path / "cache", tmp_path / "work"
    assert corpus.fetch({"rows": [entry]}, cache, work, download) == {
        "demo": "unpacked"
    }
    # Another commit and version, with the first commit's URL and hash.
    changed = dict(entry, commit="b" * 40, version="b" * 40)
    status = corpus.fetch({"rows": [changed]}, cache, work, download)
    assert status == {"demo": "refused URL"}
    assert len(download.asked) == 1


def test_a_git_archive_whose_version_is_not_its_commit_is_refused(tmp_path):
    entry = dict(git_row(), version="1.0")
    download = serve({entry["url"]: ARCHIVE})
    status = corpus.fetch({"rows": [entry]}, tmp_path / "c", tmp_path / "w", download)
    assert status == {"demo": "refused version: not the commit"}
    assert download.asked == []


@pytest.mark.parametrize(
    ("field", "value", "problem"),
    [
        ("sha256", "abc", "refused SHA-256"),
        ("url", HOST + "other-1.0-py3-none-any.whl", "refused URL"),
    ],
)
def test_a_pypi_row_must_name_its_file_and_a_full_hash(field, value, problem):
    entry = row("demo", "demo-1.0-py3-none-any.whl", WHEEL, "pure wheel")
    assert corpus.row_problem(entry) is None
    assert corpus.row_problem(dict(entry, **{field: value})) == problem


@pytest.mark.parametrize("manifest", ["pypi-top-100.json", "dypybench.json"])
def test_every_published_row_passes_the_check_of_fetch(manifest):
    rows = json.loads((corpus.CORPUS / manifest).read_text())["rows"]
    assert [r["name"] for r in rows if "file" in r and corpus.row_problem(r)] == []


# --- review round 2: the unpacked tree is its archive; sizes ------------------


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ("edit", "demo/core.py: changed"),
        ("add", "demo/added.py: not in the archive"),
        ("remove", "demo/core.py: missing"),
        # Codex, round 3: an empty directory changes what the target is.
        ("add directory", "demo/scratch: not in the archive"),
        ("add top directory", "scratch: not in the archive"),
    ],
)
def test_an_unpacked_tree_that_is_not_its_archive_is_refused(tmp_path, change, problem):
    entry = row("demo", "demo-1.0-py3-none-any.whl", WHEEL, "pure wheel")
    download = serve({entry["url"]: WHEEL})
    cache, work = tmp_path / "cache", tmp_path / "work"
    assert corpus.fetch({"rows": [entry]}, cache, work, download) == {
        "demo": "unpacked"
    }
    archive = cache / entry["file"]
    assert corpus.verify_unpacked(archive, work / "demo") is None
    core = work / "demo" / "demo" / "core.py"
    if change == "edit":
        core.write_text("def f():\n    return 2\n")
    elif change == "add":
        (core.parent / "added.py").write_text("x = 1\n")
    elif change == "add directory":
        (core.parent / "scratch").mkdir()
    elif change == "add top directory":
        (work / "demo" / "scratch").mkdir()
    else:
        core.unlink()
    assert corpus.verify_unpacked(archive, work / "demo") == problem
    # Not repaired: fetch refuses it, with the path.
    status = corpus.fetch({"rows": [entry]}, cache, work, download)
    assert status == {"demo": f"unpacked tree changed: {problem}"}


# --- review round 3: directories; the CLI; a hash of the same length ---------

WITH_DIRECTORY = zip_bytes(
    [
        ("demo/__init__.py", b"", stat.S_IFREG | 0o644),
        ("demo/data/", b"", stat.S_IFDIR | 0o755),
        ("demo/data/empty/", b"", stat.S_IFDIR | 0o755),
    ]
)


def test_a_directory_of_the_archive_that_is_missing_is_refused(tmp_path):
    entry = row("demo", "demo-1.0-py3-none-any.whl", WITH_DIRECTORY, "pure wheel")
    cache, work = tmp_path / "cache", tmp_path / "work"
    corpus.fetch({"rows": [entry]}, cache, work, serve({entry["url"]: WITH_DIRECTORY}))
    archive = cache / entry["file"]
    # Its directories, written ("demo/data/") or implied ("demo/"), are there.
    assert corpus.verify_unpacked(archive, work / "demo") is None
    (work / "demo" / "demo" / "data" / "empty").rmdir()
    problem = "demo/data/empty: missing"
    assert corpus.verify_unpacked(archive, work / "demo") == problem
    status = corpus.fetch({"rows": [entry]}, cache, work, serve({}))
    assert status == {"demo": f"unpacked tree changed: {problem}"}


def test_the_fetch_command_fails_on_a_changed_tree(tmp_path, capsys):
    entry = row("demo", "demo-1.0-py3-none-any.whl", WHEEL, "pure wheel")
    cache, work = tmp_path / "cache", tmp_path / "work"
    corpus.fetch({"rows": [entry]}, cache, work, serve({entry["url"]: WHEEL}))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"rows": [entry]}))
    argv = ["fetch", "--manifest", str(manifest), "--cache", str(cache)]
    argv += ["--work", str(work)]
    assert corpus.main(argv) == 0  # unpacked before: nothing is downloaded
    (work / "demo" / "scratch").mkdir()
    assert corpus.main(argv) == 1
    assert "FAILED demo: unpacked tree changed: scratch: not in the archive" in (
        capsys.readouterr().err
    )


SAME_LENGTH = zip_bytes(
    [
        ("demo/__init__.py", b"", stat.S_IFREG | 0o755),
        ("demo/core.py", b"def g():\n    pass\n", stat.S_IFREG | 0o644),
        ("demo-1.0.dist-info/METADATA", b"", stat.S_IFREG | 0o644),
    ]
)


def test_a_cached_archive_of_the_same_length_but_another_hash_is_not_reused(
    tmp_path,
):
    entry = row("demo", "demo-1.0-py3-none-any.whl", WHEEL, "pure wheel")
    cache, work = tmp_path / "cache", tmp_path / "work"
    download = serve({entry["url"]: WHEEL})
    corpus.fetch({"rows": [entry]}, cache, work, download)
    # Another valid wheel of the same length, with its own unpacked tree.
    assert len(SAME_LENGTH) == len(WHEEL) and SAME_LENGTH != WHEEL
    archive = cache / entry["file"]
    archive.write_bytes(SAME_LENGTH)
    (work / "demo" / "demo" / "core.py").write_text("def g():\n    pass\n")
    assert corpus.verify_unpacked(archive, work / "demo") is None
    assert not corpus.cached(archive, entry)
    # Downloaded again by its hash, and the tree checked against the pinned one.
    status = corpus.fetch({"rows": [entry]}, cache, work, download)
    assert len(download.asked) == 2
    assert status == {"demo": "unpacked tree changed: demo/core.py: changed"}
