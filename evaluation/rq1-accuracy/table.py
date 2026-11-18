#!/usr/bin/env python3
"""RQ1, accuracy: precision and recall of each tool on the two benchmarks.

    python -I evaluation/rq1-accuracy/table.py --checkout PYCG \\
        [--original ORIGINAL_PYCG] [--prior-tools FILE] [--work DIR]

Writes evaluation/results/rq1-accuracy.md and .json: a row per tool, a
column for the micro-benchmark, one per batch of the macro-benchmark
(projects.toml `batch`) and one for all its projects; the edges inside the
analysed package (the headline) and outside it apart, raw names (score.py).
The PyCG rows are measured here: the checkout under test (`--checkout`, the
new API) and the original PyCG (`--original`, the 2021 API), each run by
run.py and scored by score.py. The rows of the prior tools come from the
result file of prior_tools.py (`--prior-tools`), which scores their stored
runs. A benchmark that a tool did not run, a benchmark whose sources are
not there and an absent `--original` are cells with the reason. The
recall against traces of real executions is the table of trace_recall.py
(results/rq1-trace-recall.md).

The last section of the table shows the first measurement of each batch
(first-measurements.json, beside this script): a frozen engine, run once on
the batch before anyone read its false positives and false negatives, with
the gap to the batches known then. Those numbers are data: they were
recorded then and are not measured again.

Every row of the column "micro" scores the same cases: those whose
requires-python is at most the cohort, the oldest Python of the tools of
the table (that of PyCG's interpreter, and the bound of the micro cases of
the prior-tool result). The cases that only newer interpreters parse are a
column of their own, "micro, newer Python", for the tools that run them.
A cached prior-tool measurement of another benchmark commit, or of another
cohort, is a cell "needs rerun", never mixed with this run's.
"""

import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVALUATION = HERE.parent
COMMON = EVALUATION / "common"
REPO = EVALUATION.parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run = _load("evaluation_run", COMMON / "run.py")
score = run.load_sibling("evaluation_score", COMMON / "score.py")
tables = run.load_sibling("evaluation_tables", COMMON / "tables.py")
tool_job = run.load_sibling(
    "evaluation_tool_job", EVALUATION / "container" / "tool_job.py"
)
# The budget of one job of RQ1 (seconds, MiB of the resident memory of its
# process group), the same for PyCG and for the prior tools
# (tool_job.LIMITS: one limit for every run of every question).
LIMITS = tool_job.LIMITS["rq1"]

NEWER = "micro, newer Python"
ALL = "all projects"
FIRST_MEASUREMENTS = HERE / "first-measurements.json"
DEFAULT_WORK = Path(tempfile.gettempdir()) / "pycg-evaluation" / "rq1-accuracy"
STEM = "rq1-accuracy"


def version_text(checkout):
    """The commit of a checkout as the tables show it."""
    git = run.git_version(checkout)
    text = f"{(git['commit'] or '?')[:10]} ({git['describe']})"
    return text + (", with changes" if git["dirty"] else "")


def _python(version):
    return ".".join(map(str, version))


def required(case):
    """The requires-python of a micro case, as a tuple; () if it has none."""
    path = Path(case) / "requires-python"
    if not path.is_file():
        return ()
    return tuple(int(p) for p in path.read_text().strip().split("."))


def cohort(python, prior_tools):
    """The newest Python of the micro cases that every row scores: the
    oldest of PyCG's interpreter and of the cases of the prior-tool result."""
    versions = [tuple(run.python_version(python)[:2])]
    try:
        cells = tables.prior_tool_cells(prior_tools, {}, None)
    except FileNotFoundError:
        return versions[0]
    for row in cells.values():
        for cell in row.values():
            bound = cell.get("benchmark", {}).get("python")
            if bound:
                versions.append(tuple(int(p) for p in bound.split(".")))
    return min(versions)


def _micro_cell(cases, keys):
    """The cell of the micro cases `keys`, raw names."""
    subset = {k: cases[k] for k in keys}
    total = score.micro_total(score.micro_categories(subset, "raw"))
    counts = {s: total["counts"][s].to_json() for s in score.EDGE_SETS}
    failed = [
        {"run": f"micro/{k}", "status": cases[k]["status"]}
        for k in keys
        if cases[k]["status"] not in ("ok", "skipped")
    ]
    facts = {k: total[k] for k in ("cases", "skipped", "exact")}
    return tables.measured(counts, runs=total["cases"], failed=failed, **facts)


def measure_micro(
    tool, work, snippets, timeout, allow_dirty, progress, bound, memory_limit_mib=None
):
    """The cells of the micro-benchmark: the cases up to Python `bound`
    (the cohort), and the newer cases that the interpreter meets."""
    version = run.python_version(tool.python)
    jobs, skipped = run.micro_jobs(snippets, version)
    out = Path(work) / "micro"
    run.run(
        tool,
        "micro",
        jobs,
        out,
        timeout,
        skipped,
        progress,
        allow_dirty,
        memory_limit_mib=memory_limit_mib,
    )
    cases = score.score_micro(out / tool.name, snippets)
    run_keys = [k for k, c in cases.items() if c["status"] != "skipped"]
    cohort_keys = [k for k in run_keys if required(Path(snippets) / k)[:2] <= bound]
    newer = [k for k in run_keys if required(Path(snippets) / k)[:2] > bound]
    cells = {"micro": _micro_cell(cases, cohort_keys) | {"cohort": _python(bound)}}
    if newer:
        cells[NEWER] = _micro_cell(cases, newer)
    else:
        cells[NEWER] = tables.missing(
            f"no case: the interpreter is Python {_python(version[:2])}"
        )
    return cells


def measure_macro(
    tool, benchmark_dir, work, timeout, allow_dirty, progress, memory_limit_mib=None
):
    """The cell of the macro-benchmark, with the counts of each project
    (`projects`), or the reason why it is not measured."""
    benchmark_dir = Path(benchmark_dir)
    config = benchmark_dir / "projects.toml"
    if not config.is_file():
        return tables.missing(f"not measured: no {benchmark_dir.name}/projects.toml")
    jobs, skipped = run.macro_jobs(config)
    if skipped:
        names = ", ".join(key.split("/", 1)[1] for key in skipped)
        return tables.missing(
            f"not measured: the source of {names} is not there "
            "(the images hold it: build the inputs image)"
        )
    out = Path(work) / "macro"
    run.run(
        tool,
        "macro",
        jobs,
        out,
        timeout,
        (),
        progress,
        allow_dirty,
        memory_limit_mib=memory_limit_mib,
    )
    data = score.tool_results(out / tool.name, config)
    projects = {
        name: {"status": project["status"]}
        | {"raw": {s: project["raw"][s] for s in score.EDGE_SETS}}
        for name, project in data["macro"]["projects"].items()
    }
    summed = data["macro"]["summed"]["raw"]
    return tables.measured(
        summed, runs=len(jobs), failed=data["failed"], projects=projects
    )


def by_batch(cell, batches):
    """{column: cell}: the cell of the macro-benchmark as one cell per batch
    (`batches`: {batch: [projects]}; the sum over its projects) and one of
    all projects. A project whose environment was not there is not
    measured: the cell says how many were. A cell without a measurement is
    the cell of every column."""
    columns = [tables.batch_title(batch) for batch in batches]
    if cell["status"] == "missing":
        return dict.fromkeys((*columns, ALL), cell)
    facts = {k: v for k, v in cell.items() if k in FACTS}
    cells = {}
    for batch, names in batches.items():
        scored = [
            cell["projects"][n]
            for n in names
            if cell["projects"].get(n, {}).get("raw") is not None
        ]
        counts = {
            s: score.summed_json(p["raw"][s] for p in scored) for s in score.EDGE_SETS
        }
        keys = {f"macro/{name}" for name in names}
        failed = [f for f in cell["failed"] if f["run"] in keys]
        found = tables.measured(counts, runs=len(scored), failed=failed, **facts)
        if len(scored) < len(names):
            found["projects measured"] = [len(scored), len(names)]
        cells[tables.batch_title(batch)] = found
    cells[ALL] = {k: v for k, v in cell.items() if k != "projects"}
    return cells


# The facts of a cell of the macro-benchmark that the cell of a batch keeps.
FACTS = ("version", "source", "benchmark", "image", "table", "no outside")


def measure_pycg(
    tool,
    work,
    root=REPO,
    snippets=score.SNIPPETS,
    timeout=LIMITS[0],
    allow_dirty=False,
    progress=None,
    bound=None,
    memory_limit_mib=LIMITS[1],
):
    """The cells of a PyCG checkout: {"micro", NEWER, "macro": cell}.
    `bound`: the cohort of the micro cases (default: the interpreter's
    Python); `root`: the directory that holds macro-benchmark/."""
    work = Path(work) / tool.name
    bound = bound or tuple(run.python_version(tool.python)[:2])
    cells = measure_micro(
        tool, work, snippets, timeout, allow_dirty, progress, bound, memory_limit_mib
    )
    cells["macro"] = measure_macro(
        tool,
        Path(root) / "macro-benchmark",
        work,
        timeout,
        allow_dirty,
        progress,
        memory_limit_mib,
    )
    return cells


def prior_rows(path, current, bound):
    """The rows of the prior tools: [(name, version, cells)]. `current`: the
    inputs of the benchmarks of this run (tables.current_inputs); `bound`:
    the micro cohort."""
    try:
        found = tables.prior_tool_cells(path, current, score.SAME_FUNCTION_SHA256)
    except FileNotFoundError as error:
        reason = tables.missing(f"not yet run: {error}")
        return [(name, "-", {None: reason}) for name in tables.PRIOR_TOOLS]
    rows = []
    for name, cells in found.items():
        version = next((c["version"] for c in cells.values() if c.get("version")), "-")
        micro = cells.get("micro")
        if micro is not None:
            ends = micro["benchmark"].get("python")
            cells[NEWER] = tables.missing(
                f"not run: its micro cases end at Python {ends}"
            )
            if micro["status"] == "measured" and ends != _python(bound):
                cells["micro"] = tables.missing(
                    f"needs rerun: its micro cases end at Python {ends}, "
                    f"the cohort at {_python(bound)}"
                ) | {k: micro[k] for k in ("version", "source", "benchmark")}
        for benchmark in ("micro", NEWER, "macro"):
            if benchmark not in cells and None not in cells:
                cells[benchmark] = tables.missing(
                    f"not yet run: {Path(path).name} has no run on {benchmark}"
                )
        rows.append((name, version, cells))
    return rows


def _prf(counts):
    """`P 0.930 / R 0.895 (1251/94/147)` of [TP, FP, FN]; `-` for None."""
    if counts is None:
        return "-"
    tp, fp, fn = counts
    found = {"tp": tp, "fp": fp, "fn": fn}
    found["precision"] = tp / (tp + fp) if tp + fp else None
    found["recall"] = tp / (tp + fn) if tp + fn else None
    return tables.prf(found)


def _gap(batch, known):
    """(precision, recall) of the batch minus those of the known batches,
    each `+0.011`; `-` where one side has none."""
    if batch is None or known is None:
        return "-", "-"

    def ratio(part, whole):
        return part / whole if whole else None

    gaps = []
    for other in (1, 2):  # FP for precision, FN for recall
        new = ratio(batch[0], batch[0] + batch[other])
        old = ratio(known[0], known[0] + known[other])
        gaps.append("-" if new is None or old is None else f"{new - old:+.3f}")
    return tuple(gaps)


def first_measurements(path=FIRST_MEASUREMENTS):
    """(the data of first-measurements.json, the lines of its section)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    lines = [
        "## Accuracy on each batch at its first measurement",
        "",
        "From `evaluation/rq1-accuracy/first-measurements.json`: recorded then,",
        "not measured again. The benchmark grew in batches of five projects.",
        "Each batch after the first was measured once, by a frozen engine,",
        "before anyone read its false positives and false negatives. *Known*",
        "is the sum over the batches that the engine was developed on until",
        "then; the gaps are the batch minus known, inside the package. P / R",
        "(TP/FP/FN), raw names. The ground truths and the engine changed",
        "afterwards, so these numbers differ from the tables above.",
        "",
    ]
    header = ["Batch", "Date", "Tool", "Version", "Known batches", "Known, inside"]
    header += ["Batch, inside", "Gap P", "Gap R", "Batch, outside", "Runs"]
    body, notes = [], []
    for batch in data["batches"]:
        title = tables.batch_title(batch["batch"])
        known = ", ".join(tables.batch_title(b) for b in batch["known"])
        for row in batch["rows"]:
            engine = data["original"] if row["tool"] == "original PyCG" else None
            engine = batch["engine"] if row["tool"] == "PyCG" else engine
            version = row.get("version")
            if engine:
                version = f"{engine['commit'][:10]} ({engine['tag'] or 'no tag'})"
            inside = _prf(row["batch"]["inside"])
            outside = _prf(row["batch"]["outside"])
            if row.get("projects measured"):
                measured, total = row["projects measured"]
                inside += f", {measured} of {total} projects measured"
                outside += f", {measured} of {total} projects measured"
            if row.get("no outside"):
                outside = row["no outside"]
            known_inside = _prf(row["known"]["inside"]) if row["known"] else "-"
            if row.get("known projects measured"):
                measured, total = row["known projects measured"]
                known_inside += f", {measured} of {total} projects measured"
            gap = _gap(row["batch"]["inside"], (row["known"] or {}).get("inside"))
            runs = row.get("runs") or "-"
            body.append([title, batch["date"], row["tool"], version, known])
            body[-1] += [known_inside, inside, *gap, outside, runs]
        notes.append(f"- {title}: names matched by {batch['names']}")
        for change in batch.get("corrected after", []):
            notes.append(
                f"- {title}, {change['project']}, corrected on {change['date']} "
                f"(the first measurement stays as it was): {change['what']}"
            )
    lines += tables.table(header, body) + ["", *notes, ""]
    return data, lines


def build(rows, bound, batches, first=FIRST_MEASUREMENTS):
    """The JSON data and the Markdown of the table. `rows`: [(tool, version,
    cells)], where cells maps "micro", NEWER and "macro" to a cell, or None
    to the one reason of a tool that has no cell at all; `bound`: the micro
    cohort; `batches`: {batch: [projects]} of the macro-benchmark
    (tables.batches); `first`: the file of the first measurements."""
    cohort_text = f"the cases up to Python {_python(bound)}"
    columns = ["micro", NEWER, *(tables.batch_title(b) for b in batches), ALL]
    filled = []
    for name, version, cells in rows:
        if None in cells:
            cells = dict.fromkeys(("micro", NEWER, "macro"), cells[None])
        cells = {
            "micro": cells["micro"],
            NEWER: cells[NEWER],
            **by_batch(cells["macro"], batches),
        }
        filled.append({"tool": name, "version": version, "columns": cells})
    data = {"columns": columns, "cohort": cohort_text, "rows": filled}
    data["batches"] = {tables.batch_title(b): names for b, names in batches.items()}
    data["scorer"] = score.scorer()
    data["first measurements"], section = first_measurements(first)
    return data, rendered(data, section)


def rendered(data, section):
    """The Markdown of the table from its data and the lines of the section
    of the first measurements."""
    columns, filled = data["columns"], data["rows"]
    total = sum(len(names) for names in data["batches"].values())
    lines = [
        "# RQ1: accuracy",
        "",
        "Generated by `evaluation/rq1-accuracy/table.py`; do not edit. See",
        '`evaluation/README.md`, section "RQ1: accuracy". Precision, recall',
        "and TP/FP/FN of the edges, raw names. A cell without a measurement",
        f"says why. Every row of the column micro scores {data['cohort']};",
        f"{NEWER} holds the newer cases, for the tools that run them. The",
        "macro-benchmark has a column per batch of five projects",
        f"(`macro-benchmark/projects.toml`, `batch`) and one for all {total}.",
        "The recall against traces of real executions is in",
        "`results/rq1-trace-recall.md`.",
        "",
    ]
    header = ["Tool", "Version", *columns]
    for edge_set, title in (
        ("inside", "Edges inside the analysed package (the headline)"),
        ("outside", "Edges to code outside the package"),
    ):
        lines += [f"## {title}", ""]
        body = []
        for row in filled:
            texts = []
            for column in columns:
                cell = row["columns"][column]
                text = tables.cell_text(cell, edge_set)
                if column in ("micro", NEWER) and cell["status"] == "measured":
                    if "exact" in cell:
                        text += f"; {cell['exact']} of {cell['cases']} cases exact"
                    elif "runs" in cell:
                        text += f"; {cell['runs']} cases"
                texts.append(text)
            body.append([row["tool"], row["version"], *texts])
        lines += tables.table(header, body) + [""]
    return "\n".join(lines + section)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--checkout", type=Path, required=True, help="PyCG under test")
    parser.add_argument("--original", type=Path, help="a checkout of the 2021 PyCG")
    parser.add_argument("--prior-tools", type=Path, default=tables.PRIOR_TOOLS_RESULT)
    parser.add_argument("--work", type=Path, default=DEFAULT_WORK)
    parser.add_argument("--results", type=Path, default=tables.RESULTS)
    parser.add_argument("--timeout", type=float, default=LIMITS[0], help="seconds")
    parser.add_argument(
        "--memory-limit", type=float, default=LIMITS[1], help="MiB per job"
    )
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args(argv)
    pycg = [("PyCG", "new", args.checkout)]
    if args.original:
        pycg.append(("original PyCG", "original", args.original))
    for _, _, checkout in pycg:
        if not (checkout / "pycg").is_dir():
            parser.error(f"{checkout} has no pycg package")
    bound = cohort(args.python, args.prior_tools)
    rows = []
    try:
        for name, flavour, checkout in pycg:
            tool = run.Tool(
                name.replace(" ", "-"), flavour, checkout.resolve(), args.python
            )
            cells = measure_pycg(
                tool,
                args.work,
                timeout=args.timeout,
                memory_limit_mib=args.memory_limit,
                progress=lambda line: print(line, file=sys.stderr, flush=True),
                bound=bound,
            )
            rows.append((name, version_text(checkout), cells))
    except run.DirtyCheckout as error:
        parser.error(f"{error}; commit it")
    if not args.original:
        reason = tables.missing("not run: no --original checkout given")
        rows.append(("original PyCG", "-", {None: reason}))
    rows += prior_rows(args.prior_tools, tables.current_inputs(), bound)
    batches = tables.batches(tables.projects.load_projects())
    data, markdown = build(rows, bound, batches)
    for path in tables.write(args.results, STEM, data, markdown):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
