#!/usr/bin/env python3
"""Propose groups for the table common/same_function.json.

    python -I evaluation/tools/same_function.py candidates TOOL_DIR [TOOL_DIR ...]

Not part of the scoring. For each project of the macro-benchmark and each
tool directory (graphs in the layout of run.py, any tool alike), it takes
every caller and the callee names outside the package that the tool's graph
and the ground truth give it, and lists each pair of different names at
one caller that resolve to one object of the standard library
(common/lookup.py, static inspection in this interpreter) and are not yet
one group of the table. A person reviews each pair, checks it on every
supported CPython, and adds it to the table with its source; the scoring
uses only the table.
"""

import argparse
import importlib.util
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[1] / "common"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


score = _load("evaluation_score", COMMON / "score.py")
lookup = _load("evaluation_lookup", COMMON / "lookup.py")


def candidates(tool_dirs, config=None):
    """{(name, name): [(tool, project, caller, sides)]}: the pairs of names
    at one caller that are one object and not yet one group."""
    found = {}
    for tool_dir in tool_dirs:
        tool_dir = Path(tool_dir)
        runs = score._runs(tool_dir, "macro")
        for name, project, truth, trace, _ in score.macro_projects(config):
            _, tool = score._graph(tool_dir, f"macro/{name}", runs)
            packages = project.package_names
            # The names as the scorer matches them before the table: after
            # the aliases of the trace, which already join some names.
            matched = score.Names(score.identities(trace) if trace else {})
            for caller in sorted(set(tool or {}) | set(truth)):
                sides = {}
                for side, graph in (("tool", tool or {}), ("truth", truth)):
                    for callee in graph.get(caller, ()):
                        if not score.check.ledger.in_package(callee, packages):
                            sides.setdefault(matched.callee(callee), set()).add(side)
                names = sorted(sides)
                for i, first in enumerate(names):
                    for second in names[i + 1 :]:
                        if _grouped(first, second):
                            continue
                        if lookup.same_object(first, second):
                            where = "+".join(sorted(sides[first] | sides[second]))
                            found.setdefault((first, second), []).append(
                                (tool_dir.name, name, caller, where)
                            )
    return found


def _grouped(first, second):
    table = score.SAME_FUNCTION
    return first in table and table.get(first) == table.get(second)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    sub = commands.add_parser("candidates")
    sub.add_argument("tool_dirs", nargs="+", type=Path)
    args = parser.parse_args(argv)
    found = candidates(args.tool_dirs)
    for (first, second), places in sorted(found.items()):
        tool, project, caller, where = places[0]
        print(
            f"{first} = {second}  ({len(places)} callers; first: {tool} "
            f"{project} {caller}, {where})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
