"""The engine that a table measures: a clean PyCG checkout at one
commit, exported to a directory that holds its `pycg` package and nothing
else.

A checkout can hold more than the engine (tests, data). The export holds
only the blobs of the commit's `pycg` tree, written as git stores them:
`git ls-tree` and `git cat-file` apply no attribute (no export-ignore, no
export-subst, no filter), and git runs
without the user's or the system's configuration and, for the export and
the version probe alike, without replacement objects
(`--no-replace-objects`), so the commit names the tree that is written.
A gitlink or a symbolic link in the tree is refused. Each written file is
checked against the blob id of the tree. So what an analysis can reach is
the engine of that commit, and the commit is the version that every result
records. A checkout with
changes, or not under git, is refused: its commit would not say which code
ran. Directories are mode 0755 and files 0644, whatever the umask, so that
the user of a container can read them.
"""

import hashlib
import os
import subprocess
from pathlib import Path, PurePosixPath


class Refused(Exception):
    """A checkout that cannot be measured, with the reason."""


# git reads a repository owned by another user (the read-only mount of a
# Compose service) only as a safe directory; with --no-optional-locks,
# `status` writes no lock into it.
GIT = ("git", "--no-replace-objects", "--no-optional-locks", "-c", "safe.directory=*")


def git_version(path):
    """The commit of a checkout, its description and whether it has changes."""

    def git(*args):
        done = subprocess.run(
            [*GIT, "-C", str(path), *args],
            capture_output=True,
            text=True,
        )
        return done.stdout.strip() if done.returncode == 0 else None

    status = git("status", "--porcelain")
    return {
        "commit": git("rev-parse", "HEAD"),
        "describe": git("describe", "--tags", "--always"),
        "dirty": bool(status) if status is not None else None,
    }


def clean_version(checkout):
    """The version of a PyCG checkout that is clean and under git; raises
    Refused otherwise."""
    checkout = Path(checkout).resolve()
    if not (checkout / "pycg").is_dir():
        raise Refused(f"{checkout} has no pycg package")
    version = git_version(checkout)
    if version["commit"] is None:
        raise Refused(f"{checkout} is not a git checkout")
    if version["dirty"]:
        raise Refused(f"{checkout} has changes; commit them")
    return version


def package_version(checkout):
    """The version of a PyCG checkout under git whose `pycg` package has no
    change against its commit; raises Refused otherwise. For a caller that
    measures the export of the commit (`export`): a change of the package
    would not be measured, so it is refused; a change outside the package
    (a table that a question wrote) is not in the export and does not
    matter."""
    checkout = Path(checkout).resolve()
    if not (checkout / "pycg").is_dir():
        raise Refused(f"{checkout} has no pycg package")
    version = git_version(checkout)
    if version["commit"] is None:
        raise Refused(f"{checkout} is not a git checkout")
    if _git(checkout, "status", "--porcelain", "--", "pycg").strip():
        raise Refused(f"{checkout}/pycg has changes; commit them")
    return version | {"dirty": False}


def _git(checkout, *args, data=None):
    """Run git in `checkout` without any configuration but the repository's."""
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    environment |= {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    return subprocess.run(
        [*GIT, "-C", str(checkout), *args],
        input=data,
        capture_output=True,
        check=True,
        env=environment,
    ).stdout


def revision(checkout, name):
    """The commit that the revision `name` (a tag, say) names in `checkout`."""
    return (
        _git(checkout, "rev-parse", "--verify", f"{name}^{{commit}}").decode().strip()
    )


def in_history(checkout, commit, head):
    """Whether `commit` is `head` or an ancestor of it in `checkout`."""
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    environment |= {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    done = subprocess.run(
        [*GIT, "-C", str(checkout), "merge-base", "--is-ancestor", commit, head],
        capture_output=True,
        env=environment,
    )
    return done.returncode == 0


def blob_id(data, length):
    """The git id of a blob with these bytes (SHA-1, or SHA-256 for a
    64-digit id)."""
    digest = hashlib.sha1 if length == 40 else hashlib.sha256
    return digest(b"blob %d\0" % len(data) + data).hexdigest()


def export(checkout, commit, destination):
    """Write the `pycg` tree of `commit` to the new directory `destination`;
    return it. Raises Refused for a gitlink, a link or an unsafe path."""
    destination = Path(destination)
    entries = []
    listing = _git(checkout, "ls-tree", "-r", "-z", "--full-tree", commit, "--", "pycg")
    for line in listing.split(b"\0"):
        if not line:
            continue
        meta, path = line.split(b"\t", 1)
        mode, kind, sha = meta.decode().split()
        path = PurePosixPath(path.decode())
        if kind != "blob" or mode not in ("100644", "100755"):
            raise Refused(f"{path}: a {kind} of mode {mode} is not exported")
        if path.is_absolute() or ".." in path.parts or path.parts[0] != "pycg":
            raise Refused(f"{path}: not a path inside pycg/")
        entries.append((path, sha))
    if not entries:
        raise Refused(f"{commit} has no pycg package")
    batch = _git(
        checkout,
        "cat-file",
        "--batch",
        data=b"".join(sha.encode() + b"\n" for _, sha in entries),
    )
    _make_directory(destination)
    offset = 0
    for path, sha in entries:
        header_end = batch.index(b"\n", offset)
        size = int(batch[offset:header_end].split()[2])
        data = batch[header_end + 1 : header_end + 1 + size]
        offset = header_end + 2 + size
        if blob_id(data, len(sha)) != sha:
            raise Refused(f"{path}: its bytes are not those of the tree")
        target = destination.joinpath(*path.parts)
        for parent in reversed(target.relative_to(destination).parents[:-1]):
            _make_directory(destination / parent)
        target.write_bytes(data)
        os.chmod(target, 0o644)
    written = {
        PurePosixPath(p.relative_to(destination).as_posix())
        for p in destination.rglob("*")
        if p.is_file()
    }
    if written != {path for path, _ in entries}:
        raise Refused("the export does not hold exactly the files of the tree")
    return destination


def _make_directory(path):
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o755)


def content_hash(directory):
    """The SHA-256 of the files under `directory`: their paths and bytes."""
    digest = hashlib.sha256()
    directory = Path(directory)
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        digest.update(path.relative_to(directory).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()
