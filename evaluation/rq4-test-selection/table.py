#!/usr/bin/env python3
"""RQ4, a client: regression test selection with each tool's call graph.

    python -I evaluation/rq4-test-selection/table.py --checkout PYCG \\
        --work WORK [--export DIR] [--original ORIGINAL_PYCG] [--timeout S] \\
        [--memory-limit MIB] [--pilot] [--jobs K]

WORK holds the clones that `rts.py fetch` made (WORK/NameRTS and
WORK/repos/<project>); a clone that is not there is an error that names
it. With --export DIR (the output of `rts.py export`, which the inputs
image holds), the clones are not read: every input comes
from DIR as plain files, so the plain image runs this without git, and
WORK only receives the output (snapshots of the tools, graphs, selections,
the report). A project that DIR lacks is an error that names it. For each
PyCG checkout and each project of the manifest (corpus/rts.json), rts.py
selects the test files of each target commit; then rts.py scores every
selection (its report goes to WORK/report/). The table,
evaluation/results/rq4-test-selection.md and .json, has a row per tool:
NameRTS's metrics with all the client's rules, the mean of the projects'
values (safe rate, safe by analysis and by fallback, test reduction,
precision), and the rows that the same code computes from the published
selections of NameRTS and BabelRTS.

--jobs K runs K selections at a time (one `rts.py select` per tool and
project), each analysis with its own limits; each writes its own files, so
the records do not depend on K, but the measured times can: a run beside
others is slower, as in RQ3.

--pilot selects the first target of each project only, prints the time
and the peak memory of each analysis, and writes
evaluation/results/rq4-pilot.md and .json instead of the table: a
measurement to make before a full run. On the host its graphs stay in
WORK, so a full run with the same WORK reuses them; in the container
(`make rq4-pilot`) WORK is temporary and only the pilot's tables come out,
so the full run analyses again.

The rows of pyan3, JARVIS, CPyGraph and PyAnalyzer select with the graphs
that each tool's service stored (`docker compose run --rm TOOL rq4`, or
`rq4-pilot`: container/tool_job.py, the volume tool-graphs): rts.py selects
and scores them as PyCG's, and the time of an analysis is the tool's. A
row without its graphs is "not run" with the command; a graph of another
input is "needs rerun". HeaderGen imports the modules it analyses, so it
does not run on a dataset that no security scan covered; its row says so.
"""

import argparse
import importlib.util
import json
import platform
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVALUATION = HERE.parent
COMMON = EVALUATION / "common"
STEM = "rq4-test-selection"
PILOT = "rq4-pilot"
METRICS = ("safe_rate", "safe_by_analysis", "safe_by_fallback")
METRICS += ("test_reduction", "precision")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rts = _load("evaluation_rts", HERE / "rts.py")
tables = rts.run.load_sibling("evaluation_tables", COMMON / "tables.py")
tool_job = rts.run.load_sibling(
    "evaluation_tool_job", EVALUATION / "container" / "tool_job.py"
)
TOOL_GRAPHS = tool_job.GRAPHS  # the volume tool-graphs of compose.yaml


def absent_clones(work, manifest):
    """The clones of WORK that `rts.py fetch` makes and that are not there."""
    work = Path(work).resolve()
    wanted = [work / "NameRTS"] + [work / "repos" / n for n in manifest["projects"]]
    return [str(path) for path in wanted if not path.is_dir()]


def means(report):
    """{row label: {"projects": n, metric: {"mean": the mean over the
    projects where it is defined, unrounded, "projects": how many}}} of a
    report of `rts.py score`: each tool, then the published selections."""
    values = {}

    def add(label, m, analysed=None, fallback=None):
        row = m | {"safe_by_analysis": analysed, "safe_by_fallback": fallback}
        values.setdefault(label, []).append(row)

    for project in report["projects"].values():
        for tool, t in project["tools"].items():
            add(
                tool,
                t["variants"]["client"],
                t["safe_by_analysis"],
                t["safe_by_fallback"],
            )
        for label, m in project["published"].items():
            add(f"{label} (published)", m)
    result = {}
    for label, rows in values.items():
        result[label] = {"projects": len(rows)}
        for key in METRICS:
            defined = [r[key] for r in rows if r[key] is not None]
            result[label][key] = {
                "mean": sum(defined) / len(defined) if defined else None,
                "projects": len(defined),
            }
    return result


def build(report, rows, run=None):
    """The JSON data and the Markdown of the table. `rows`: [(row name, a
    label of the report, or a missing cell)]; the published rows follow.
    `run`: the platform and the selections at a time (--jobs)."""
    found = means(report) if report else {}
    versions = report["versions"] if report else {}
    filled = []
    for name, label in rows:
        if isinstance(label, dict):
            filled.append({"tool": name, "version": "-", "client": label})
            continue
        provenance = versions.get(label, {})
        commit = (provenance.get("tool", {}).get("commit") or "?")[:10]
        filled.append(
            {
                "tool": name,
                "version": commit,
                "provenance": provenance,
                "client": tables.measured({}, **found[label]),
            }
        )
    for label, cell in found.items():
        if label.endswith("(published)"):
            client = tables.measured({}, **cell)
            filled.append({"tool": label, "version": "-", "client": client})
    lines = [
        "# RQ4: regression test selection",
        "",
        "Generated by `evaluation/rq4-test-selection/table.py`; do not edit.",
        'See `evaluation/README.md`, section "RQ4: regression test',
        "selection\". NameRTS's metrics with all the client's rules, the mean",
        "of the projects' values; a metric that is not defined on every",
        "project gives over how many projects its mean is.",
        "",
    ]
    if run:
        lines += [f"Run on {run['platform']}, {run['jobs']} selections at a time.", ""]
    header = ["Tool", "Version", "Projects", "Safe", "Safe by analysis"]
    header += ["Safe by fallback"]
    header += ["Test reduction", "Precision"]
    body = []
    for row in filled:
        cell = row["client"]
        if cell["status"] == "missing":
            body.append([row["tool"], row["version"], cell["reason"], *[""] * 5])
            continue
        texts = [_mean(cell[k], cell["projects"]) for k in METRICS]
        body.append([row["tool"], row["version"], cell["projects"], *texts])
    lines += tables.table(header, body)
    data = {"rows": filled}
    if report:
        data["namerts"] = report["namerts"]
        # A target whose files the export could not give (a blob that the
        # remote refused) is left out of every row of its project.
        data["excluded targets"] = [
            {"project": name, "target": e["current"], "reason": e["reason"]}
            for name, project in report["projects"].items()
            for e in project.get("excluded", [])
        ]
        if data["excluded targets"]:
            lines += ["", "Targets left out of every row, each with its reason:", ""]
            body = [
                [e["project"], f"`{e['target'][:10]}`", e["reason"]]
                for e in data["excluded targets"]
            ]
            lines += tables.table(["Project", "Target", "Reason"], body)
    return data, "\n".join(lines) + "\n"


def _mean(metric, projects):
    """The mean as a percentage, rounded for display only, with over how
    many projects when that is not all of them."""
    if metric["mean"] is None:
        return "-"
    text = f"{100 * metric['mean']:.1f}%"
    if metric["projects"] != projects:
        text += f" (over {metric['projects']} of {projects})"
    return text


def prior_rows(manifest, work, export, pilot, graphs=None, progress=print):
    """[(row name, label of its selections, or a missing cell)] of the prior
    tools: rts.py selects with the graphs that each row's service stored
    (GRAPHS/<row>/<rq4|rq4-pilot>/<project>.json)."""
    graphs = Path(graphs or TOOL_GRAPHS)
    question = PILOT if pilot else "rq4"
    found = []
    for tool in tool_job._build().TOOLS:
        for row, _ in tool_job.headline(tool):
            if row.startswith(tables.IMPORTS_ANALYSED_CODE):
                found.append((row, tables.missing(tables.UNSCANNED)))
                continue
            label = tool_job.slug(row)
            directory = graphs / label / question
            absent = [
                n
                for n in manifest["projects"]
                if not (directory / f"{n}.json").is_file()
            ]
            if absent:
                command = f"docker compose -f evaluation/compose.yaml run --rm {tool}"
                reason = f"not run: no graphs of {', '.join(absent)}"
                found.append((row, tables.missing(f"{reason} ({command} {question})")))
                continue
            name = rts.run.Tool(label, "prior", None, None)
            try:
                for project in manifest["projects"]:
                    path = directory / f"{project}.json"
                    stored = json.loads(path.read_text(encoding="utf-8"))
                    if stored["dataset"] != rts.dataset_fingerprint(manifest, project):
                        raise rts.StaleGraph(f"{project}: another dataset")
                    problem = tool_job.stored_problem(tool, row, stored, question)
                    for entry in stored["analyses"].values():
                        problem = problem or tool_job.graph_problem(
                            entry["record"], entry.get("graph")
                        )
                    if problem:
                        raise rts.StaleGraph(problem.removeprefix("needs rerun: "))
                    rts.select(
                        work,
                        manifest,
                        project,
                        name,
                        None,
                        limit=1 if pilot else None,
                        progress=progress,
                        export=export,
                        stored=stored,
                    )
            except rts.StaleGraph as error:
                found.append((row, tables.missing(f"needs rerun: {error}")))
                continue
            found.append((row, label))
    return found


def absent_exports(export, manifest):
    """The projects of the manifest that the export does not hold."""
    return [
        n for n in manifest["projects"] if not (Path(export) / "trees" / n).is_dir()
    ]


def pilot_rows(work, labels, manifest):
    """The analysis of the first target of each project and tool, from the
    selections that the pilot wrote. `labels`: [(label, row name)]."""
    rows = []
    for label, shown in labels:
        for project in manifest["projects"]:
            path = Path(work) / "selections" / label / f"{project}.json"
            commit = json.loads(path.read_text(encoding="utf-8"))["commits"][0]
            analysis = commit.get("analysis", {})  # none for a target not exported
            rows.append(
                {
                    "tool": shown,
                    "project": project,
                    "target": commit["current"],
                    "status": commit["status"],
                    "wall_seconds": analysis.get("wall_seconds"),
                    "peak_rss_mib": analysis.get("peak_rss_mib"),
                    "cached": analysis.get("cached"),
                }
            )
    return rows


def pilot_markdown(rows, limits, run):
    lines = [
        "# RQ4 pilot: one target per project",
        "",
        "Generated by `evaluation/rq4-test-selection/table.py --pilot`; do not edit.",
        f"Limits per analysis: {limits['seconds']:g} s and {limits['memory_mib']} MiB;",
        f"run on {run['platform']}, {run['jobs']} selections at a time.",
        "",
    ]
    body = [
        [
            r["tool"],
            r["project"],
            f"`{r['target'][:10]}`",
            r["status"] + (" (cached)" if r["cached"] else ""),
            "-" if r["wall_seconds"] is None else f"{r['wall_seconds']:g}",
            "-" if r["peak_rss_mib"] is None else f"{r['peak_rss_mib']:g}",
        ]
        for r in rows
    ]
    header = ["Tool", "Project", "Target", "Analysis", "Seconds", "Peak MiB"]
    return "\n".join(lines + tables.table(header, body)) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--checkout", type=Path, required=True, help="PyCG under test")
    parser.add_argument("--original", type=Path, help="a checkout of the 2021 PyCG")
    parser.add_argument("--work", type=Path, required=True, help="of rts.py fetch")
    parser.add_argument("--manifest", type=Path, default=rts.MANIFEST)
    parser.add_argument("--timeout", type=float, default=rts.DEFAULT_TIMEOUT)
    parser.add_argument(
        "--memory-limit", type=int, default=rts.DEFAULT_MEMORY_LIMIT_MIB, help="MiB"
    )
    parser.add_argument("--results", type=Path, default=tables.RESULTS)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--export", type=Path, help="the output of rts.py export")
    parser.add_argument(
        "--pilot", action="store_true", help="the first target of each project only"
    )
    parser.add_argument(
        "--jobs", type=int, default=1, help="K analyses at a time, each with its limits"
    )
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs is at least 1")
    if not args.manifest.is_file():
        parser.error(f"no manifest {args.manifest}; run rts.py manifest")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if args.export is not None:
        absent = absent_exports(args.export, manifest)
        if absent:
            parser.error(f"not exported: {', '.join(absent)}; build the inputs image")
    else:
        absent = absent_clones(args.work, manifest)
        if absent:
            parser.error(f"not fetched: {', '.join(absent)}; run rts.py fetch")
    tools = [("PyCG", "new", args.checkout)]
    if args.original:
        tools.append(("original PyCG", "original", args.original))
    common = ["--work", str(args.work), "--manifest", str(args.manifest)]
    if args.export is not None:
        common += ["--export", str(args.export)]
    labels, selects = [], []
    for name, flavour, checkout in tools:
        label = name.replace(" ", "-")
        labels.append((label, name))
        for project in manifest["projects"]:
            argv = ["select", *common, "--project", project]
            argv += ["--tool", f"{label}:{flavour}:{checkout}"]
            argv += ["--timeout", str(args.timeout)]
            argv += ["--memory-limit", str(args.memory_limit), "--python", args.python]
            if args.pilot:
                argv += ["--limit", "1"]
            selects.append(argv)
    # Each tool on each project is one `rts.py select`, `--jobs` at a time;
    # each writes its own files, so the outputs do not depend on K.
    rts.run.run_tasks(selects, rts.main, args.jobs)
    facts = {"platform": f"{sys.platform} {platform.machine()}", "jobs": args.jobs}
    prior = prior_rows(manifest, args.work, args.export, args.pilot)
    labels += [(label, name) for name, label in prior if isinstance(label, str)]
    if args.pilot:
        rows = pilot_rows(args.work, labels, manifest)
        for row in rows:
            print(
                f"{row['tool']} {row['project']} {row['target'][:10]}: "
                f"{row['status']}, {row['wall_seconds']} s, {row['peak_rss_mib']} MiB"
            )
        limits = {"seconds": args.timeout, "memory_mib": args.memory_limit}
        data = {"limits": limits, "run": facts, "rows": rows}
        for path in tables.write(
            args.results, PILOT, data, pilot_markdown(rows, limits, facts)
        ):
            print(path)
        return 0
    report_dir = args.work / "report"
    argv = ["score", *common, "--out", str(report_dir)]
    for label, _ in labels:
        argv += ["--tool", label]
    rts.main(argv)
    report = json.loads((report_dir / "rts.json").read_text(encoding="utf-8"))
    rows = [(name, name.replace(" ", "-")) for name, _, _ in tools]
    if not args.original:
        rows.append(
            ("original PyCG", tables.missing("not run: no --original checkout given"))
        )
    rows += prior
    data, markdown = build(report, rows, facts)
    data["run"] = facts
    for path in tables.write(args.results, STEM, data, markdown):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
