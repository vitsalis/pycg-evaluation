#!/usr/bin/env python3
"""RQ2, causes: why each false positive and each false negative is there.

    python -I evaluation/rq2-causes/table.py

Reads the label data of the final engine, beside this file:

- labels.json: every false negative (a missed edge) and every false
  positive (a wrong edge) of PyCG on the packages of the macro-benchmark.
  `batches` maps each batch (its heading in the tables: "batch 1", ...,
  "ICSE 2021 set") to its rows, one per edge, sorted:
  [package, caller, callee, "fn" or "fp", "inside" or "outside", label,
  note]. Inside: the callee is in the analysed package. The label is the
  mechanism that explains the edge; the note is the evidence. `engine` is
  the version of PyCG that the labels are of: its `commit`, and optionally
  its `tag` and the state of the ground truths that it was scored against
  (`ground_truth`, a phrase: "the ground truths after the audit of ...").
- limits.json: for each label its `group` (GROUPS) and its `name`; for a
  stated limit also its `family`, where the label data names it the section
  of docs/design.md that states it (`design`), and the date at which the
  maintainer left its measured fix out (`left_out`).

The groups: a stated limit (a kind of edge that the design leaves out on
purpose, docs/design.md); a scorer artefact (the scorer does not match two
names of one function); a ground-truth doubt; a measured rule that is not
built (the rule was measured edge by edge, it removes the edge, and the
engine does not have it); a gap (a mechanism that the design does not
state as a limit: PyCG lacks a rule, or has a wrong one).

Writes evaluation/results/rq2-causes.md and .json: the counts of each
group and the share under stated limits, one row per stated limit, the
rows of the other groups, and the counts per batch. Counts only: the edges
stay in labels.json.

A label without a group, a group that is none of GROUPS, an edge that is
labelled twice and a row that is no false positive or false negative,
inside or outside, and a batch that projects.toml does not have are errors
that name the label, the edge or the batch.
"""

import argparse
import collections
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVALUATION = HERE.parent
STEM = "rq2-causes"
LABELS = HERE / "labels.json"
LIMITS = HERE / "limits.json"
KINDS = {"fn": "false negative", "fp": "false positive"}
SIDES = ("inside", "outside")
CELLS = ("inside_fn", "inside_fp", "outside_fn", "outside_fp")
CELL_TITLES = ["Inside FN", "Inside FP", "Outside FN", "Outside FP"]
GROUPS = LIMIT, SCORER, TRUTH, RULE, GAP = (
    "stated limit",
    "scorer artefact",
    "ground-truth doubt",
    "measured rule, not built",
    "gap",
)
TITLES = {
    LIMIT: "Under stated limits",
    SCORER: "Scorer artefacts",
    TRUTH: "Ground-truth doubts",
    RULE: "Measured rule, not built",
    GAP: "Gaps",
}

Edge = collections.namedtuple("Edge", "batch package caller callee kind side label")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tables = _load("evaluation_tables", EVALUATION / "common" / "tables.py")


def read(labels, limits):
    """(engine, edges, {label: its entry of the limits file}) of a label
    file and a limits file, both checked (the module docstring has the
    rules). The edges are in the order of the batches of the tables."""
    labels, limits = Path(labels), Path(limits)
    mapping = json.loads(limits.read_text(encoding="utf-8"))
    for label, entry in mapping.items():
        if entry.get("group") not in GROUPS:
            raise ValueError(
                f"{limits.name}: the label {label} has the group "
                f"{entry.get('group')!r}, which is none of {', '.join(GROUPS)}"
            )
    data = json.loads(labels.read_text(encoding="utf-8"))
    titles = [tables.batch_title(batch) for batch in tables.projects.BATCHES]
    edges, seen = [], set()
    for name in data["batches"]:
        if name not in titles:
            raise ValueError(f"{labels.name}: {name} is no batch of the benchmark")
    for name in (n for n in titles if n in data["batches"]):
        for package, caller, callee, kind, side, label, _note in data["batches"][name]:
            where = f"{labels.name}: {name}, {package}, {caller} -> {callee}"
            if kind not in KINDS:
                raise ValueError(
                    f"{where} is neither a false positive nor a false negative"
                )
            if side not in SIDES:
                raise ValueError(f"{where} is neither inside nor outside")
            if label not in mapping:
                raise ValueError(
                    f"{where}: the label {label} has no group in {limits.name}"
                )
            if (name, package, caller, callee) in seen:
                raise ValueError(f"{where} is labelled twice")
            seen.add((name, package, caller, callee))
            edges.append(Edge(name, package, caller, callee, kind, side, label))
    return data["engine"], edges, mapping


def cells(edges):
    """{cell: the number of `edges` in it, "total": their number}."""
    found = collections.Counter(f"{edge.side}_{edge.kind}" for edge in edges)
    return {**{cell: found[cell] for cell in CELLS}, "total": len(edges)}


def share(part, whole):
    """The share of `part` in `whole`, or None of nothing."""
    return round(part / whole, 4) if whole else None


def counted(edges, engine, mapping):
    """The JSON data of the table."""

    def row(of, /, **facts):
        own = [edge for edge in edges if edge.label in of]
        packages = sorted({edge.package for edge in own})
        return {**facts, **cells(own), "packages": packages}

    def of_group(group):
        return [edge for edge in edges if mapping[edge.label]["group"] == group]

    total = cells(edges)
    groups = []
    for group in GROUPS:
        found = cells(of_group(group))
        found["share"] = share(found["total"], total["total"])
        groups.append({"group": group, **found})
    stated = [
        row([label], label=label, **{k: v for k, v in entry.items() if k != "group"})
        for label, entry in mapping.items()
        if entry["group"] == LIMIT
    ]
    stated.sort(key=lambda r: (-r["total"], r["label"]))
    others = []
    for group in GROUPS[1:]:
        names = {}  # a rule of several labels is one row
        for label, entry in mapping.items():
            if entry["group"] == group:
                names.setdefault(entry["name"], []).append(label)
        rows = [
            row(labels, group=group, name=name, labels=sorted(labels))
            for name, labels in names.items()
        ]
        others += sorted(rows, key=lambda r: (-r["total"], r["name"]))
    batches = []
    for name in dict.fromkeys(edge.batch for edge in edges):
        own = [edge for edge in edges if edge.batch == name]
        found = cells(own)
        by_group = collections.Counter(mapping[edge.label]["group"] for edge in own)
        found["groups"] = {group: by_group[group] for group in GROUPS}
        found["share"] = share(by_group[LIMIT], found["total"])
        batches.append({"batch": name, **found})
    return {
        "engine": engine,
        "edges": total,
        "groups": groups,
        "stated_limits": stated,
        "others": others,
        "batches": batches,
    }


def percent(value):
    return "-" if value is None else f"{100 * value:.1f}%"


def numbers(row):
    """The cells of a row and its total, as the columns of a table."""
    return [row[cell] for cell in CELLS] + [row["total"]]


def engine_text(engine):
    """PyCG and what it was scored against, as the table names them:
    "PyCG at `tag` (`commit`)", or "PyCG at `c5c6a89`" without a tag, and
    the state of the ground truths if the label data gives it."""
    if engine.get("tag"):
        text = f"PyCG at `{engine['tag']}` (`{engine['commit']}`)"
    else:
        text = f"PyCG at `{engine['commit'][:7]}`"
    if engine.get("ground_truth"):
        text += f", on {engine['ground_truth']},"
    return text


def rendered(data, edges):
    """The Markdown of the table."""
    total = data["edges"]["total"]
    under = data["groups"][0]
    packages = len({(edge.batch, edge.package) for edge in edges})
    lines = [
        "# RQ2: causes of the false positives and the false negatives",
        "",
        "Generated by `evaluation/rq2-causes/table.py` from",
        "`evaluation/rq2-causes/labels.json` and `limits.json`; do not edit. See",
        '`evaluation/README.md`, section "RQ2: causes".',
        "",
        f"{engine_text(data['engine'])} has {total:,} false",
        "negatives (missed edges: Python makes the call, PyCG does not report",
        "it) and false positives (wrong edges: PyCG reports a call that Python",
        f"does not make) on the {packages} packages of the macro-benchmark, in",
        f"{len(data['batches'])} batches. Each has one label, the mechanism that",
        "explains it. Inside: the callee is in the analysed package; outside: it",
        "is not. FN: false negatives; FP: false positives.",
        "",
        f"**Under stated limits: {under['total']:,} of {total:,} "
        f"({percent(under['share'])}).** A stated limit is a",
        "kind of edge that the design leaves out on purpose (`docs/design.md`).",
        "",
        "## The groups",
        "",
    ]
    body = [
        [TITLES[group["group"]], *numbers(group), percent(group["share"])]
        for group in data["groups"]
    ]
    body.append(["All", *numbers(data["edges"]), percent(share(total, total))])
    lines += tables.table(["Group", *CELL_TITLES, "All", "Share"], body)
    lines += [
        "",
        "- Scorer artefacts: a naming or matching artefact of the scorer (two",
        "  names of one function, a convention of the ground truth).",
        "- Ground-truth doubts: the label doubts the ground truth, not PyCG.",
        "- Measured rule, not built: a rule that was measured edge by edge",
        "  removes the edge, and the engine does not have the rule.",
        "- Gaps: a mechanism that the design does not state as a limit: PyCG",
        "  lacks a rule, or has a wrong one.",
        "",
        "## The stated limits",
        "",
    ]
    stated = data["stated_limits"]
    left = [limit for limit in stated if "left_out" in limit]
    if left:
        dates = " and ".join(sorted({limit["left_out"] for limit in left}))
        names = ", ".join(f"`{limit['label']}`" for limit in left)
        gone = sum(limit["total"] for limit in left)
        rest = under["total"] - gone
        lines += [
            f"{len(left)} of the {len(stated)} limits ({names}) are defects whose",
            f"measured fix the maintainer left out on {dates}: {gone:,} false",
            "positives and false negatives. They count as stated limits, because",
            "`docs/design.md` states them. Without them, the other",
            f"{len(stated) - len(left)} limits hold {rest:,} of {total:,} "
            f"({percent(share(rest, total))}).",
            "",
        ]
    body = [
        [
            number,
            limit["name"],
            limit["label"],
            limit.get("family", ""),
            *numbers(limit),
            len(limit["packages"]),
        ]
        for number, limit in enumerate(stated, 1)
    ]
    body.append(["", "All stated limits", "", "", *numbers(under), ""])
    header = ["#", "Stated limit", "Label", "Family", *CELL_TITLES, "All", "Packages"]
    lines += tables.table(header, body)
    lines += [
        "",
        "Label: the label of the limit's edges in `labels.json`. Family: what",
        "the limit is about (`F-callback`: library code that calls back;",
        "`F-client`: what only a client of the package supplies; `F-names`:",
        "names and attributes; `F-runtime`: what exists only at run time;",
        "`F-types`: types and class tests).",
        "",
    ]
    where = [limit for limit in stated if "design" in limit]
    if where:
        lines += ["Where `docs/design.md` states a limit, for the limits whose"]
        lines += ["label data names the section:", ""]
        lines += [f"- `{limit['label']}`: {limit['design']}" for limit in where]
        lines += [""]
    lines += ["## Under no stated limit", ""]
    body = [
        [
            TITLES[other["group"]],
            other["name"],
            ", ".join(other["labels"]),
            *numbers(other),
            len(other["packages"]),
        ]
        for other in data["others"]
    ]
    header = ["Group", "Cause", "Labels", *CELL_TITLES, "All", "Packages"]
    lines += tables.table(header, body)
    lines += [
        "",
        "## Per batch",
        "",
        "The four counts of a batch are those of PyCG in",
        "`results/rq1-accuracy.md` (`tests/test_evaluation_questions.py` checks",
        "it).",
        "",
    ]
    body = [
        [
            row["batch"],
            *numbers(row),
            row["groups"][LIMIT],
            percent(row["share"]),
            *(row["groups"][group] for group in GROUPS[1:]),
        ]
        for row in data["batches"]
    ]
    header = ["Batch", *CELL_TITLES, "All", TITLES[LIMIT], "Share"]
    lines += tables.table(header + [TITLES[group] for group in GROUPS[1:]], body)
    lines += [""]
    return "\n".join(lines)


def build(labels=None, limits=None):
    """The JSON data and the Markdown of the table, from a label file and a
    limits file (default: those beside this file)."""
    engine, edges, mapping = read(labels or LABELS, limits or LIMITS)
    data = counted(edges, engine, mapping)
    return data, rendered(data, edges)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--results", type=Path, default=tables.RESULTS)
    args = parser.parse_args(argv)
    try:
        data, markdown = build()
    except (OSError, ValueError) as error:
        parser.error(f"the label data of RQ2: {error}")
    for path in tables.write(args.results, STEM, data, markdown):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
