#!/usr/bin/env python3
"""Checks that an image build runs inside its build container (our code).

    python3 -I image_checks.py site PYTHON
    python3 -I image_checks.py cmake-cache CACHE PYTHON
    python3 -I image_checks.py tree DIRECTORY SHA256

site fails if the site-packages or the standard library of PYTHON hold
startup code that `site` runs (.pth files, sitecustomize, usercustomize):
pygCG starts that interpreter, and embeds its library, without -S.
cmake-cache fails unless CMake chose PYTHON and no ablation. tree fails
unless the files under DIRECTORY have the SHA-256 SHA256 (their paths and
bytes, as tools/build.py tree_hash, which pins.json records). Standard
library only; the exit status is 1 with the reason on a failure.
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path


class CheckError(Exception):
    """A check failed; the message says why."""


def startup_files(directories):
    """The entries of `directories` that `site` would run at startup: .pth
    files, sitecustomize and usercustomize (module or package)."""
    found = []
    for directory in directories:
        for entry in sorted(Path(directory).iterdir()):
            stem = entry.name.removesuffix(".py")
            if entry.suffix == ".pth" or stem in ("sitecustomize", "usercustomize"):
                found.append(str(entry))
    return found


_SITE_DIRECTORIES = (
    "import json, site, sysconfig; paths = sysconfig.get_paths(); "
    "print(json.dumps(sorted({*site.getsitepackages(), paths['purelib'], "
    "paths['platlib'], paths['stdlib'], paths['platstdlib']})))"
)


def check_site(python):
    """CheckError if the site-packages (or the standard library) of
    `python` hold startup code."""
    done = subprocess.run(
        [python, "-I", "-S", "-c", _SITE_DIRECTORIES],
        capture_output=True,
        text=True,
        check=True,
    )
    directories = [d for d in json.loads(done.stdout) if Path(d).is_dir()]
    found = startup_files(directories)
    if found:
        raise CheckError(
            f"{python} has startup code that pygCG would run: {', '.join(found)}"
        )


def check_cmake_cache(cache, python):
    """CheckError unless CMake chose `python` and no ablation. The choice of
    FindPython is `_Python3_EXECUTABLE`; `Python3_EXECUTABLE` is only what
    the command line asked for."""
    values = {}
    for line in Path(cache).read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        values[key.split(":")[0]] = value
    chosen = values.get("_Python3_EXECUTABLE")
    if chosen != python:
        raise CheckError(f"CMake chose {chosen}, not {python}")
    ablation = values.get("CPYGRAPH_ABLATION")
    if ablation != "none":
        raise CheckError(f"the CMake cache selects the ablation {ablation}")


def tree_hash(directory):
    """The SHA-256 of the files under `directory`: their paths and bytes."""
    digest = hashlib.sha256()
    directory = Path(directory)
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        digest.update(path.relative_to(directory).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def check_tree(directory, pinned):
    """CheckError unless the tree at `directory` has the hash `pinned`."""
    found = tree_hash(directory)
    if found != pinned:
        raise CheckError(f"the tree {directory} is {found}, pins.json {pinned}")


def main(argv):
    try:
        if argv[:1] == ["site"] and len(argv) == 2:
            check_site(argv[1])
        elif argv[:1] == ["cmake-cache"] and len(argv) == 3:
            check_cmake_cache(argv[1], argv[2])
        elif argv[:1] == ["tree"] and len(argv) == 3:
            check_tree(argv[1], argv[2])
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except CheckError as error:
        print(f"image_checks: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
