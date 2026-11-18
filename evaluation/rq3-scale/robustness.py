#!/usr/bin/env python3
"""Does PyCG finish on real packages? One run per package of a corpus and
tool, under a time and a memory limit.

    python -I evaluation/rq3-scale/robustness.py \\
        --manifest evaluation/rq3-scale/corpus/pypi-top-100.json --work WORK \\
        --tool new-engine:new:CHECKOUT --timeout 1800 --memory-limit 16 --jobs 4

The corpus is a manifest of corpus.py, fetched and unpacked into WORK by
`corpus.py fetch`. For each row with an analysis target (corpus.target) and
each tool, one run in a child process of run.py, with the package alone:
the analysis root is the package's root, its dependencies are outside it,
and the entry points are all modules of the target. A run's status:
- ok: the tool finished; the record has the counts of its output
  (analyse.py: for the new engine the modules by status, definitions, call
  sites, edges and unresolved sites by cause);
- timeout: killed at --timeout seconds of wall time;
- memory: killed by the watchdog of run.py, which samples the resident
  set size of the child's process group (the child and the processes it
  starts) every run.WATCH_INTERVAL seconds and kills the group at the
  first sample over --memory-limit (GiB);
- crash: the tool raised, with the exception type, its message and the
  innermost frame inside the tool; or the child died (exit status);
- unmeasured: the memory of the group could not be read (not a process
  that has gone: an error), so the run was stopped, with the error.
A run is not repeated. --jobs K runs K children at a time. K changes
neither the order of the results nor which result belongs to which package
and tool; the measured times and memory, and the details of an error that
change from run to run (a pid), are kept apart, under `measured`. A failure
is reported by a cause that is the same in every run.
An outcome that depends on the resources (a run near the time limit) can
change with K or with the load of the machine.

Writes RESULTS/robustness-<manifest name>.json and .md. The report gives,
per tool, the success rate at each published threshold (30 min, 1 h, 2 h,
6 h; 8, 16, 60 GiB) at or under the limits of the run, from the wall time
and the peak memory of each run; the failures by status and cause; and the
median, 90th percentile (nearest rank) and maximum of time and memory of
the runs that finished, by size of the target in code lines.
"""

import argparse
import hashlib
import importlib.util
import json
import math
import platform
import statistics
import sys
import threading
from pathlib import Path

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
corpus = _load("evaluation_corpus", HERE / "corpus.py")
tables = _load("evaluation_tables", COMMON / "tables.py")


def count_lines(files):
    """(physical lines, code lines) of the files; a code line is neither
    blank nor only a comment."""
    physical = code = 0
    for path in files:
        for line in (
            Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        ):
            physical += 1
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                code += 1
    return physical, code


def _table(header, rows):
    return tables.table(header, rows) + [""]


# The thresholds of earlier work: DyPyBench 6 h and 60 GB, PoTo 2 h, PyXray
# 30 min, JARVIS 8 GB, PyCG issue #35 16 GB. Memory in GiB.
TIME_THRESHOLDS = ((1800, "30 min"), (3600, "1 h"), (7200, "2 h"), (21600, "6 h"))
MEMORY_THRESHOLDS = (8, 16, 60)
# Size classes of a target, by code lines: [low, high).
SIZE_CLASSES = (
    (0, 1_000, "< 1k"),
    (1_000, 10_000, "1k-10k"),
    (10_000, 100_000, "10k-100k"),
    (100_000, math.inf, ">= 100k"),
)
# What changes from one run to the next: times, memory, and the details of
# an error (a pid).
MEASURED = ("wall_seconds", "seconds", "peak_rss_mib", "detail")


# After a run whose process group could not be killed, no other run starts.
NOT_STARTED = (
    "not started after a cleanup failure: the processes left by that run "
    "would disturb the measurements of later runs"
)


class NotFetched(Exception):
    """Rows of the manifest that are not unpacked from their file."""


class InvalidRow(Exception):
    """A row of the manifest that fetch would refuse (corpus.row_problem):
    a corpus fetched under another row must not be analysed under this one."""


class TreeChanged(InvalidRow):
    """An unpacked tree that is not the archive that the manifest pins: a
    file changed, added or removed since it was unpacked."""


def plan(manifest, work, cache=None):
    """{name: (entry, job)} in the manifest's order: the facts of each
    package for the results, and its run.Job, or None with the reason in
    the entry. Raises NotFetched if a row with a file is not unpacked (by
    `corpus.py fetch`) from the file of the manifest, or its archive is not
    in CACHE (default: `cache` beside WORK, as the inputs image lays them out);
    InvalidRow if a row is one that fetch refuses or its cached archive is
    not the pinned one; TreeChanged if an unpacked tree is not exactly the
    files of that archive. fetch.json says only which archive a tree came
    from; the content is checked against the archive itself."""
    for row in manifest["rows"]:
        problem = "file" in row and corpus.row_problem(row)
        if problem:
            raise InvalidRow(
                f"an inconsistent row of the manifest: {row['name']}: {problem}"
            )
    work = Path(work).resolve()  # `link/..` as the file system reads it
    fetch_record = work / "fetch.json"
    recorded = json.loads(fetch_record.read_text()) if fetch_record.exists() else {}
    missing = [
        row["name"]
        for row in manifest["rows"]
        if "file" in row
        and (
            recorded.get(row["name"], {}).get("sha256") != row["sha256"]
            or (
                "archive refused" not in recorded[row["name"]]
                and not (work / row["name"]).is_dir()
            )
        )
    ]
    if missing:
        raise NotFetched(f"not unpacked from the manifest's file: {', '.join(missing)}")
    cache = Path(cache if cache is not None else work.parent / "cache")
    for row in manifest["rows"]:
        if "file" not in row or "archive refused" in recorded[row["name"]]:
            continue
        archive = cache / row["file"]
        if not archive.is_file():
            raise NotFetched(f"no archive of {row['name']} in {cache}")
        if not corpus.cached(archive, row):
            raise InvalidRow(
                f"{row['name']}: the archive in {cache} is not the pinned one"
            )
        changed = corpus.verify_unpacked(archive, work / row["name"])
        if changed:
            raise TreeChanged(
                f"the unpacked tree of {row['name']} is not its archive: {changed}"
            )
    entries = {}
    for row in manifest["rows"]:
        entry = {"rank": row["rank"]}
        if "file" not in row:
            entries[row["name"]] = (entry | {"reason": row["reason"]}, None)
            continue
        entry |= {"version": row["version"], "kind": row["kind"]}
        refusal = recorded[row["name"]].get("archive refused")
        if refusal:
            entries[row["name"]] = (
                entry | {"reason": f"archive refused: {refusal}"},
                None,
            )
            continue
        target = corpus.target(
            work / row["name"], row["kind"], row.get("root"), row.get("modules")
        )
        if isinstance(target, str):
            entries[row["name"]] = (entry | {"reason": target}, None)
            continue
        physical, code = count_lines(target.files)
        entry |= {
            "modules": target.modules,
            "files": len(target.files),
            "lines": physical,
            "code_lines": code,
        }
        job = run.Job(f"corpus/{row['name']}", tuple(target.files), target.root)
        entries[row["name"]] = (entry, job)
    return entries


def run_one(tool, job, timeout, memory_limit_mib):
    """One run; what does not depend on the machine's speed first, the
    measured times and memory under `measured`."""
    record = run.run_job(tool, job, None, timeout, memory_limit_mib)
    result = {
        k: record[k] for k in ("status", "error", "where", "counts") if k in record
    }
    result["measured"] = {k: record[k] for k in MEASURED if k in record}
    return result


def run_corpus(
    tools, entries, timeout, memory_limit_mib, jobs, progress=None, stopped=False
):
    """Run each tool on each package with a job, `jobs` children at a time;
    return the packages' entries with their runs, in the manifest's order.
    With `stopped` (a cleanup failed in an earlier corpus of the same
    command), no run starts: each is "not run" with NOT_STARTED."""
    tasks = [
        (name, tool, job)
        for name, (_, job) in entries.items()
        if job is not None
        for tool in tools
    ]

    cleanup_failed = threading.Event()
    if stopped:
        cleanup_failed.set()

    def run_task(item):
        name, tool, job = item
        if cleanup_failed.is_set():
            return {"status": "not run", "error": NOT_STARTED, "measured": {}}
        result = run_one(tool, job, timeout, memory_limit_mib)
        if result["status"] == "cleanup failed":
            cleanup_failed.set()
        if progress:
            measured = result["measured"]
            progress(
                f"{name} {tool.name}: {result['status']} "
                f"{measured.get('wall_seconds')} s {measured.get('peak_rss_mib')} MiB"
            )
        return result

    results = run.run_tasks(tasks, run_task, jobs)
    packages = {name: dict(entry) for name, (entry, _) in entries.items()}
    for (name, tool, _), result in zip(tasks, results):
        packages[name].setdefault("runs", {})[tool.name] = result
    return packages


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------


def succeeded(result, seconds, memory_mib):
    """Whether a run finished within `seconds` of wall time and
    `memory_mib` of peak memory."""
    measured = result["measured"]
    return (
        result["status"] == "ok"
        and measured["wall_seconds"] <= seconds
        and measured["peak_rss_mib"] <= memory_mib
    )


def nearest_rank(values, share):
    """The nearest-rank percentile of sorted `values`."""
    return values[max(math.ceil(share * len(values)), 1) - 1]


def manifest_facts(name, text, manifest):
    """What the results record of the manifest `name` (its bytes `text`,
    read as `manifest`): the top N of a rank list, or the projects of a
    source (DyPyBench's, pinned by their commits)."""
    facts = {"file": name, "sha256": hashlib.sha256(text).hexdigest()}
    if "rank_list" in manifest:
        return facts | {"top": manifest["top"], "rank_list": manifest["rank_list"]}
    return facts | {"source": manifest["source"], "projects": len(manifest["rows"])}


def _corpus_text(manifest):
    """The corpus in a sentence of the report."""
    if "rank_list" in manifest:
        rank_list = manifest["rank_list"]
        return (
            f"`{manifest['file']}`, the top {manifest['top']} of the rank list "
            f"`{rank_list['file']}` of {rank_list['last_update']}"
        )
    source = manifest["source"]
    return (
        f"`{manifest['file']}`, the {manifest['projects']} projects of "
        f"{source['repository']} at {source['commit'][:10]}"
    )


def render(data):
    limits = data["limits"]
    manifest = data["manifest"]
    measured = data["measured"]
    runs = {
        tool: {
            name: package["runs"][tool]
            for name, package in data["packages"].items()
            if package.get("runs", {}).get(tool, {}).get("status", "not run")
            != "not run"
        }
        for tool in data["tools"]
    }
    lines = [
        "# Robustness on a corpus",
        "",
        "Generated by `evaluation/rq3-scale/robustness.py`; do not edit. Corpus:",
        _corpus_text(manifest),
        f"(manifest SHA-256 `{manifest['sha256'][:16]}`). Python {data['python']}",
        f"on {data['platform']}, hash seed {data['hash_seed']}. One run per package",
        f"and tool, {measured['jobs']} at a time, under a limit of",
        f"{limits['seconds']:g} s of wall time and {limits['memory_mib']:g} MiB of",
        "resident memory; a watchdog samples the summed resident memory of the",
        "child's process group (the child and the processes it starts) every",
        f"{measured['watch_interval']:g} s and kills the group at the first sample",
        "over the limit.",
        "",
    ]
    rows = [
        [name, t["flavour"], f"`{(t['commit'] or '?')[:10]}`", t.get("describe")]
        for name, t in data["tools"].items()
    ]
    lines += _table(["Tool", "Flavour", "Commit", "git describe"], rows)
    lines += _success(runs, limits)
    lines += _failures(runs)
    lines += _by_size(runs, data["packages"])
    lines += _outputs(runs)
    lines += ["## Packages without a run", ""]
    rows = []
    for name, package in data["packages"].items():
        if "reason" in package:
            rows.append([package["rank"], name, package["reason"]])
        for tool, result in package.get("runs", {}).items():
            if result["status"] == "not run":
                rows.append([package["rank"], name, f"{tool}: {result['error']}"])
    lines += _table(["Rank", "Package", "Reason"], rows)
    return "\n".join(lines).rstrip() + "\n"


def _success(runs, limits):
    times = [(s, label) for s, label in TIME_THRESHOLDS if s <= limits["seconds"]]
    memories = [g for g in MEMORY_THRESHOLDS if g * 1024 <= limits["memory_mib"]]
    lines = [
        "## Success rate",
        "",
        "A run succeeds at a threshold if it finished (status ok) within that",
        "wall time and peak memory (GiB). Only the thresholds at or under the",
        "limits of the run are shown. The rate is over the packages with a",
        "run.",
        "",
    ]
    rows = []
    for tool, results in runs.items():
        for seconds, label in times:
            row = [tool, len(results), label]
            for gib in memories:
                count = sum(succeeded(r, seconds, gib * 1024) for r in results.values())
                row.append(f"{count} ({_share(count, len(results))})")
            rows.append(row)
    return lines + _table(
        ["Tool", "Packages", "Time"] + [f"{g} GiB" for g in memories], rows
    )


def _share(count, total):
    return f"{100 * count / total:.1f}%" if total else "-"


def _failures(runs):
    lines = [
        "## Failures",
        "",
        "Runs that did not finish, by status and cause: for a crash, the",
        "exception type and the innermost frame inside the tool (or the exit",
        "status of a child that died).",
        "",
    ]
    rows = []
    for tool, results in runs.items():
        groups = {}
        for name, result in results.items():
            if result["status"] != "ok":
                groups.setdefault((result["status"], _cause(result)), []).append(name)
        for (status, cause), names in sorted(
            groups.items(), key=lambda item: (-len(item[1]), item[0])
        ):
            rows.append([tool, status, cause, len(names), ", ".join(sorted(names))])
    return lines + _table(["Tool", "Status", "Cause", "Runs", "Packages"], rows)


def _cause(result):
    if result["status"] in ("unmeasured", "cleanup failed"):
        return result["error"]
    if result["status"] != "crash":
        return "-"
    error = result.get("error", "")
    if error.startswith("exit status"):
        return error.split(":", 1)[0]
    kind = error.split(":", 1)[0]
    return f"{kind} at {result['where']}" if result.get("where") else kind


def _by_size(runs, packages):
    lines = [
        "## Time and memory by size",
        "",
        "The size of a target is its code lines (neither blank nor only a",
        "comment). Median, 90th percentile (nearest rank) and maximum of the",
        "wall time and the peak memory of the runs that finished (ok).",
        "",
    ]
    rows = []
    for tool, results in runs.items():
        for low, high, label in SIZE_CLASSES:
            members = [
                result
                for name, result in results.items()
                if low <= packages[name]["code_lines"] < high
            ]
            if not members:
                continue
            ok = [r["measured"] for r in members if r["status"] == "ok"]
            seconds = sorted(m["wall_seconds"] for m in ok)
            memory = sorted(m["peak_rss_mib"] for m in ok)
            rows.append(
                [
                    tool,
                    label,
                    len(members),
                    len(ok),
                    _spread(seconds, 1),
                    _spread(memory, 0),
                ]
            )
    return lines + _table(
        [
            "Tool",
            "Code lines",
            "Packages",
            "OK",
            "Seconds: median / p90 / max",
            "Peak MiB: median / p90 / max",
        ],
        rows,
    )


def _spread(values, digits):
    if not values:
        return "-"
    parts = (statistics.median(values), nearest_rank(values, 0.9), values[-1])
    return " / ".join(f"{v:,.{digits}f}" for v in parts)


def _outputs(runs):
    lines = [
        "## Output of the runs that finished",
        "",
        "Sums over the runs that finished: the modules by status, the",
        "definitions, call sites and edges, and the unresolved sites by cause",
        "(docs/format.md). The original analysis gives a call graph only.",
        "",
    ]
    rows = []
    for tool, results in runs.items():
        sums = {}
        ok = [r.get("counts", {}) for r in results.values() if r["status"] == "ok"]
        for counts in ok:
            _add(sums, counts)
        rows.append(
            [
                tool,
                len(ok),
                _breakdown(sums.get("modules")),
                *(
                    f"{sums[k]:,}" if k in sums else "-"
                    for k in ("definitions", "sites")
                ),
                f"{sums.get('edges', 0):,}",
                _breakdown(sums.get("unresolved")),
            ]
        )
    return lines + _table(
        ["Tool", "Runs", "Modules", "Definitions", "Sites", "Edges", "Unresolved"],
        rows,
    )


def _add(sums, counts):
    for key, value in counts.items():
        if isinstance(value, dict):
            _add(sums.setdefault(key, {}), value)
        else:
            sums[key] = sums.get(key, 0) + value


def _breakdown(counts):
    if counts is None:
        return "-"
    return ", ".join(f"{k} {v:,}" for k, v in sorted(counts.items())) or "0"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True, help="of corpus.py fetch")
    parser.add_argument(
        "--cache",
        type=Path,
        help="the archives of corpus.py fetch (default: WORK/../cache)",
    )
    parser.add_argument(
        "--tool", action="append", required=True, metavar="NAME:FLAVOUR:CHECKOUT"
    )
    parser.add_argument("--timeout", type=float, default=1800, help="seconds")
    parser.add_argument("--memory-limit", type=float, default=16, help="GiB")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--results", type=Path, default=EVALUATION / "results")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--hash-seed", type=int, default=0)
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument(
        "--stopped",
        action="store_true",
        help="a cleanup failed earlier in the same command: start no run",
    )
    args = parser.parse_args(argv)
    tools, versions = [], {}
    for spec in args.tool:
        name, flavour, checkout = spec.split(":", 2)
        if name in versions:
            parser.error(f"two tools are named {name}; give each its own name")
        tool = run.Tool(
            name, flavour, Path(checkout).resolve(), args.python, args.hash_seed
        )
        info = run.git_version(tool.checkout)
        if not args.allow_dirty and (info["dirty"] or not info["commit"]):
            problem = "has changes" if info["commit"] else "is not a git checkout"
            parser.error(f"{checkout} {problem}; commit it, or pass --allow-dirty")
        tools.append(tool)
        versions[name] = {"name": name, "flavour": flavour, **info}
    text = args.manifest.read_bytes()
    manifest = json.loads(text)
    try:
        entries = plan(manifest, args.work, args.cache)
    except NotFetched as error:
        parser.error(f"{error}; run corpus.py fetch")
    except InvalidRow as error:
        parser.error(str(error))
    memory_limit_mib = round(args.memory_limit * 1024, 1)
    packages = run_corpus(
        tools,
        entries,
        args.timeout,
        memory_limit_mib,
        args.jobs,
        progress=_print,
        stopped=args.stopped,
    )
    data = {
        "manifest": manifest_facts(args.manifest.name, text, manifest),
        "python": ".".join(map(str, run.python_version(args.python))),
        "platform": f"{sys.platform} {platform.machine()}",
        "hash_seed": args.hash_seed,
        "limits": {"seconds": args.timeout, "memory_mib": memory_limit_mib},
        "tools": versions,
        "packages": packages,
        "measured": {"jobs": args.jobs, "watch_interval": run.WATCH_INTERVAL},
    }
    args.results.mkdir(parents=True, exist_ok=True)
    stem = f"robustness-{args.manifest.stem}"
    (args.results / f"{stem}.json").write_text(run.dumps(data) + "\n", encoding="utf-8")
    (args.results / f"{stem}.md").write_text(render(data), encoding="utf-8")
    return 0


def _print(text):
    print(text, flush=True)  # the log of a long run, as it goes


if __name__ == "__main__":
    sys.exit(main())
