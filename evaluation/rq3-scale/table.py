#!/usr/bin/env python3
"""RQ3, scale and robustness: does each tool finish on a corpus of real
packages, and in what time and memory?

    python -I evaluation/rq3-scale/table.py --checkout PYCG --work CORPUS_WORK \\
        [--original ORIGINAL_PYCG] [--timeout S] [--memory-limit GIB] [--jobs K]

The corpus has three parts (PARTS, the maintainer's decisions of 2026-11-14
and 2026-11-17): the top 100 packages of PyPI (corpus/pypi-top-100.json,
fetched into CORPUS_WORK/work), DyPyBench's 50 projects
(corpus/dypybench.json, into CORPUS_WORK/dypybench) and ten large projects
(corpus/large-projects.json, into CORPUS_WORK/large), each by `corpus.py
fetch` (in the build of the inputs image). A part that is not fetched is an
error that names it and the packages that are not there. The PyCG rows are
run here by robustness.py (one run per package and tool, under a time and a
memory limit), once per part; it writes its own report beside the table
(robustness-<manifest>.md and .json; CORPUS_WORK is only read). The table,
evaluation/results/rq3-scale.md and .json, has for each tool a row for the
whole corpus ("all") and one per part: the packages, those with an
analysis target, the runs that finished (status ok) and their share of the
packages with a target, the failures by status, and the median, 90th
percentile and maximum of the wall time and the peak memory of the runs
that finished. Every package without a run (akshare, a package without a
usable file or target) is a stated row with its reason. The prior tools
have no runner on the corpus yet. HeaderGen imports the modules it
analyses, so it does not run on a corpus that no security scan covered.
Their rows say so.

`--pilot` is the same run on a fixed sample of ten packages, PILOT_SIZE of
each of the first two parts (PILOT_PARTS): to check that the question runs
before the full run on a Linux host. The sample is drawn with
random.Random(PILOT_SEED) from the sorted names of the packages with a file;
in that order, a package is taken if it has an analysis target (one without
is skipped and recorded), until a part has PILOT_SIZE. Each sampled package
is checked as in the full run (its row, archive and unpacked tree), and one
that is not fetched is an error that names it. It writes
evaluation/results/rq3-pilot.md and .json (per package and tool: status,
seconds, peak memory; the sample and the seed; then the summary of the
table), never rq3-scale.* nor robustness-*.
"""

import argparse
import collections
import importlib.util
import json
import platform
import random
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVALUATION = HERE.parent
COMMON = EVALUATION / "common"
STEM = "rq3-scale"
PILOT_STEM = "rq3-pilot"
PILOT_SIZE = 5  # per part of the pilot
PILOT_SEED = 20261114
# The parts of the corpus: (label, manifest, directory under CORPUS_WORK
# that the build of the inputs image unpacks it into). The JSON of a result
# keeps the key "halves" for them, as the stored runs have it.
PARTS = (
    ("PyPI top 100", HERE / "corpus" / "pypi-top-100.json", "work"),
    ("DyPyBench", HERE / "corpus" / "dypybench.json", "dypybench"),
    ("Large projects", HERE / "corpus" / "large-projects.json", "large"),
)
PILOT_PARTS = PARTS[:2]  # the pilot's sample: five of each of the first two
ALL = "all"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


robustness = _load("evaluation_robustness", HERE / "robustness.py")
run = robustness.run  # one module, so that its exceptions are the ones raised
tables = run.load_sibling("evaluation_tables", COMMON / "tables.py")
tool_job = run.load_sibling(
    "evaluation_tool_job", EVALUATION / "container" / "tool_job.py"
)


def summary(results, tool):
    """The cell of one tool from results of robustness.py (one per part of
    the corpus, or one)."""
    packages = [p for data in results for p in data["packages"].values()]
    runs = [p["runs"][tool] for p in packages if tool in p.get("runs", {})]
    finished = [r["measured"] for r in runs if r["status"] == "ok"]
    seconds = sorted(m["wall_seconds"] for m in finished)
    memory = sorted(m["peak_rss_mib"] for m in finished)

    def spread(values):
        if not values:
            return None
        return {
            "median": statistics.median(values),
            "p90": robustness.nearest_rank(values, 0.9),
            "max": values[-1],
        }

    failures = collections.Counter(r["status"] for r in runs if r["status"] != "ok")
    return {
        "status": "measured",
        "packages": len(packages),
        "with_target": len(runs),
        "finished": len(finished),
        "share": round(len(finished) / len(runs), 4) if runs else None,
        "failures": dict(sorted(failures.items())),
        "wall_seconds": spread(seconds),
        "peak_rss_mib": spread(memory),
    }


def cleanup_failed(data):
    """Whether a run of a result of robustness.py could not be cleaned up."""
    return any(
        result["status"] == "cleanup failed"
        for package in data["packages"].values()
        for result in package.get("runs", {}).values()
    )


def build(parts, rows, stopped=False):
    """The JSON data and the Markdown of the table. `parts`: {label: a
    result of robustness.py} in the order of PARTS; `rows`: [(row name,
    tool name in the results, or a missing cell)]; `stopped`: a cleanup
    failed, so no later run started."""
    results = list(parts.values())
    filled = []
    for name, tool in rows:
        if isinstance(tool, dict):
            filled.append({"tool": name, "version": "-", "corpus": {ALL: tool}})
            continue
        corpus = {ALL: summary(results, tool)}
        corpus |= {label: summary([data], tool) for label, data in parts.items()}
        version = (results[0]["tools"][tool]["commit"] or "?")[:10]
        filled.append({"tool": name, "version": version, "corpus": corpus})
    stated = []
    for label, data in parts.items():
        for name, package in data["packages"].items():
            if "reason" in package:
                stated.append(
                    {"part": label, "name": name, "reason": package["reason"]}
                )
            # A run that did not start (after a failed cleanup), with its tool.
            for tool, result in package.get("runs", {}).items():
                if result["status"] == "not run":
                    reason = f"{tool}: {result['error']}"
                    stated.append({"part": label, "name": name, "reason": reason})
    first = results[0]
    result = {
        "rows": filled,
        "stated packages": stated,
        "corpus": {label: data["manifest"] for label, data in parts.items()},
    }
    result |= {k: first[k] for k in ("python", "platform", "limits")}
    result["jobs"] = jobs_of(first)
    if stopped:
        result["stopped"] = robustness.NOT_STARTED
    limits = first["limits"]
    lines = [
        "# RQ3: scale and robustness",
        "",
        "Generated by `evaluation/rq3-scale/table.py`; do not edit. See",
        '`evaluation/README.md`, section "RQ3: scale and robustness".',
        "",
        "Corpus: "
        + "; ".join(
            f"{label}, `{data['manifest']['file']}`" for label, data in parts.items()
        )
        + f". Python {first['python']} on {first['platform']}, {jobs_of(first)}"
        f" runs at a time; limits {limits['seconds']:g} s and"
        f" {limits['memory_mib']:g} MiB per run.",
        "",
    ]
    if stopped:
        lines += [f"Stopped after a failed cleanup: {robustness.NOT_STARTED}.", ""]
    header = ["Tool", "Version", "Corpus", "Packages", "With a target", "Finished"]
    header += [
        "Share",
        "Failures",
        "Time (s): median / p90 / max",
        "Memory (MiB): median / p90 / max",
    ]
    body = []
    for row in filled:
        for label, cell in row["corpus"].items():
            if cell["status"] == "missing":
                body.append([row["tool"], row["version"], label, cell["reason"]])
                body[-1] += [""] * 6
                continue
            failures = ", ".join(f"{k} {n}" for k, n in cell["failures"].items())
            share = "-" if cell["share"] is None else f"{100 * cell['share']:.1f}%"
            body.append(
                [
                    row["tool"],
                    row["version"],
                    label,
                    cell["packages"],
                    cell["with_target"],
                    cell["finished"],
                    share,
                    failures or "-",
                    _spread(cell["wall_seconds"]),
                    _spread(cell["peak_rss_mib"]),
                ]
            )
    lines += tables.table(header, body)
    lines += ["", "Packages without a run, each with its reason:", ""]
    stated_body = [[p["part"], p["name"], p["reason"]] for p in stated]
    lines += tables.table(["Corpus", "Package", "Reason"], stated_body)
    return result, "\n".join(lines) + "\n"


def pilot_sample(manifest, work, size=PILOT_SIZE, seed=PILOT_SEED):
    """(names, skipped) of the pilot of one part: `size` packages with an
    analysis target, in the order of random.Random(seed) over the sorted
    names of the packages with a file; skipped: [{name, reason}] of those
    passed over for having no target. Each package is checked as in the
    full run (robustness.plan); one that is not fetched raises NotFetched,
    naming it."""
    rows = {row["name"]: row for row in manifest["rows"] if "file" in row}
    order = sorted(rows)
    random.Random(seed).shuffle(order)
    names, skipped = [], []
    for name in order:
        if len(names) == size:
            break
        entry, job = robustness.plan(manifest | {"rows": [rows[name]]}, work)[name]
        if job is None:
            skipped.append({"name": name, "reason": entry["reason"]})
        else:
            names.append(name)
    return names, skipped


def pilot_plan(work):
    """{label: (manifest path, its bytes, manifest, sample, skipped, entries)}
    of the pilot: each part's sample (PILOT_PARTS), checked and planned as
    in the full run, before anything runs."""
    planned = {}
    for label, path, directory in PILOT_PARTS:
        text = Path(path).read_bytes()
        manifest = json.loads(text)
        sample, skipped = pilot_sample(manifest, work / directory)
        chosen = [r for r in manifest["rows"] if r.get("name") in sample]
        entries = robustness.plan(manifest | {"rows": chosen}, work / directory)
        planned[label] = (path, text, manifest, sample, skipped, entries)
    return planned


def pilot(
    tools, rows, planned, timeout, memory_limit_mib, jobs, python, tools_dir=None
):
    """The data and the Markdown of the pilot (see the docstring). `tools`:
    [run.Tool], each clean (refused before); `rows`: as for build, to which
    the prior tools' rows are added (tool_rows); `planned`: of
    pilot_plan."""
    versions = {
        tool.name: {"name": tool.name, "flavour": tool.flavour}
        | run.git_version(tool.checkout)
        for tool in tools
    }
    parts, sample, skipped, runs = {}, {}, {}, []
    stopped = False
    for label, (path, text, manifest, chosen, passed, entries) in planned.items():
        sample[label], skipped[label] = chosen, passed
        packages = robustness.run_corpus(
            tools,
            entries,
            timeout,
            memory_limit_mib,
            jobs,
            progress=print,
            stopped=stopped,
        )
        parts[label] = {
            "manifest": robustness.manifest_facts(path.name, text, manifest),
            "python": ".".join(map(str, run.python_version(python))),
            "platform": f"{sys.platform} {platform.machine()}",
            "limits": {"seconds": timeout, "memory_mib": memory_limit_mib},
            "measured": {"jobs": jobs},
            "tools": versions,
            "packages": packages,
        }
        stopped = stopped or cleanup_failed(parts[label])
    rows = [*rows, *tool_rows(parts, PILOT_STEM, tools_dir)]
    for label, part in parts.items():
        for name, package in part["packages"].items():
            for tool, result in package.get("runs", {}).items():
                measured = result.get("measured", {})
                runs.append(
                    {
                        "part": label,
                        "package": name,
                        "tool": tool,
                        "status": result["status"],
                        "seconds": measured.get("wall_seconds"),
                        "peak_rss_mib": measured.get("peak_rss_mib"),
                    }
                )
    data, markdown = build(parts, rows, stopped)
    data["pilot"] = {
        "seed": PILOT_SEED,
        "size per part": PILOT_SIZE,
        "sample": sample,
        "skipped": skipped,
        "runs": runs,
    }
    lines = [
        "# RQ3 pilot: ten packages of the corpus",
        "",
        f"A fixed sample, {PILOT_SIZE} packages of each part, drawn with"
        f" random.Random({PILOT_SEED}); it checks that the question runs"
        " before the full run (`table.py` without `--pilot`).",
        "",
    ]
    body = [
        [r["part"], r["package"], r["tool"], r["status"]]
        + ["-" if r[k] is None else f"{r[k]:g}" for k in ("seconds", "peak_rss_mib")]
        for r in runs
    ]
    lines += tables.table(
        ["Corpus", "Package", "Tool", "Status", "Time (s)", "Memory (MiB)"], body
    )
    summary_lines = markdown.splitlines()[1:]  # the table's, without its title
    return data, "\n".join(lines + ["", "## Summary", *summary_lines]) + "\n"


def _spread(values):
    if values is None:
        return "-"
    return " / ".join(f"{values[k]:g}" for k in ("median", "p90", "max"))


def jobs_of(part):
    """How many runs ran at a time in a part (robustness.py: `measured`)."""
    return part.get("measured", {}).get("jobs", 1)


def tool_rows(parts, question, tools_dir=None):
    """The rows of the prior tools, [(row, its name in the results, or a
    missing cell)]: the stored runs of `question` (rq3-scale or rq3-pilot;
    results/tools/<row>/<rq3|rq3-pilot>.json of container/tool_job.py) are
    added to `parts`, the results of robustness.py. A row without its file,
    or of another manifest, is a missing cell; a tool that imports the
    analysed code is stated (tables.UNSCANNED)."""
    tools_dir = Path(tools_dir or tables.RESULTS / "tools")
    name = "rq3" if question == STEM else "rq3-pilot"
    found = []
    for tool in tool_job._build().TOOLS:
        for row, _ in tool_job.headline(tool):
            if row.startswith(tables.IMPORTS_ANALYSED_CODE):
                found.append((row, tables.missing(tables.UNSCANNED)))
                continue
            path = tool_job.result_path(tools_dir, row, name)
            if not path.is_file():
                command = f"docker compose -f evaluation/compose.yaml run --rm {tool}"
                reason = f"not run: no results/tools/{path.parent.name}/{path.name}"
                found.append((row, tables.missing(f"{reason} ({command} {name})")))
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            stored = data["halves"]
            problem = tool_job.stored_problem(tool, row, data, name)
            jobs = {jobs_of(part) for part in parts.values()}
            if not problem and jobs and data.get("jobs", 1) not in jobs:
                # The times compare only at one K for every tool.
                problem = (
                    f"needs rerun: it ran {data.get('jobs', 1)} jobs at a time, "
                    f"PyCG {min(jobs)} (--jobs)"
                )
            if problem:
                found.append((row, tables.missing(problem)))
                continue
            if any(
                stored.get(label, {}).get("sha256") != part["manifest"]["sha256"]
                for label, part in parts.items()
            ):
                reason = "needs rerun: it ran on another manifest of the corpus"
                found.append((row, tables.missing(reason)))
                continue
            if any(  # each package with a target of PyCG's run has a run
                {p for p, e in part["packages"].items() if "reason" not in e}
                != {
                    p
                    for p, e in stored[label]["packages"].items()
                    if row in e.get("runs", {})
                }
                for label, part in parts.items()
            ):
                reason = "needs rerun: it ran on other packages of the corpus"
                found.append((row, tables.missing(reason)))
                continue
            for label, part in parts.items():
                packages = part["packages"]
                for package, entry in stored[label]["packages"].items():
                    if package in packages and "runs" in entry:
                        packages[package].setdefault("runs", {}).update(entry["runs"])
                part["tools"][row] = {"commit": data["tool"]["pin"]["commit"]}
            found.append((row, row))
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--checkout", type=Path, required=True, help="PyCG under test")
    parser.add_argument("--original", type=Path, help="a checkout of the 2021 PyCG")
    parser.add_argument(
        "--work", type=Path, required=True, help="CORPUS_WORK of the inputs image"
    )
    parser.add_argument("--timeout", type=float, default=1800, help="seconds")
    parser.add_argument("--memory-limit", type=float, default=16, help="GiB")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--results", type=Path, default=tables.RESULTS)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--pilot", action="store_true", help="ten packages of the corpus only"
    )
    args = parser.parse_args(argv)
    if args.pilot:
        return _pilot(parser, args)
    for label, path, directory in PARTS:
        if not path.is_file():
            parser.error(f"no manifest {path} of {label}")
        manifest = json.loads(Path(path).read_text(encoding="utf-8"))
        try:
            robustness.plan(manifest, args.work / directory)
        except robustness.NotFetched as error:
            parser.error(
                f"the corpus {label} in {args.work / directory} is {error}; "
                "build the inputs image"
            )
        except robustness.InvalidRow as error:
            parser.error(f"the corpus {label}: {error}")
    tools = [("PyCG", "new", args.checkout)]
    if args.original:
        tools.append(("original PyCG", "original", args.original))
    out = args.results
    parts = {}
    stopped = False  # a cleanup failed: no run starts in a later part
    for label, path, directory in PARTS:
        command = ["--manifest", str(path), "--work", str(args.work / directory)]
        if stopped:
            command.append("--stopped")
        for name, flavour, checkout in tools:
            command += ["--tool", f"{name.replace(' ', '-')}:{flavour}:{checkout}"]
        command += ["--timeout", str(args.timeout)]
        command += ["--memory-limit", str(args.memory_limit), "--jobs", str(args.jobs)]
        command += ["--results", str(out), "--python", args.python]
        robustness.main(command)
        parts[label] = json.loads(
            (out / f"robustness-{path.stem}.json").read_text(encoding="utf-8")
        )
        stopped = stopped or cleanup_failed(parts[label])
    rows = [(name, name.replace(" ", "-")) for name, _, _ in tools]
    if not args.original:
        rows.append(
            ("original PyCG", tables.missing("not run: no --original checkout given"))
        )
    rows += tool_rows(parts, STEM)
    result, markdown = build(parts, rows, stopped)
    for path in tables.write(args.results, STEM, result, markdown):
        print(path)
    return 0


def _pilot(parser, args):
    try:  # the inputs first, as the full run reads them
        planned = pilot_plan(args.work)
    except robustness.NotFetched as error:
        parser.error(f"the pilot's sample is {error}; build the inputs image")
    except robustness.InvalidRow as error:
        parser.error(str(error))
    tools = [("PyCG", "new", args.checkout)]
    if args.original:
        tools.append(("original PyCG", "original", args.original))
    found, rows = [], []
    for name, flavour, checkout in tools:
        tool = run.Tool(
            name.replace(" ", "-"), flavour, checkout.resolve(), args.python
        )
        info = run.git_version(tool.checkout)
        if info["dirty"] or not info["commit"]:
            parser.error(f"{checkout} has changes or is not a git checkout; commit it")
        found.append(tool)
        rows.append((name, tool.name))
    if not args.original:
        rows.append(
            ("original PyCG", tables.missing("not run: no --original checkout given"))
        )
    memory_limit_mib = round(args.memory_limit * 1024, 1)
    data, markdown = pilot(
        found,
        rows,
        planned,
        args.timeout,
        memory_limit_mib,
        args.jobs,
        args.python,
    )
    for path in tables.write(args.results, PILOT_STEM, data, markdown):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
