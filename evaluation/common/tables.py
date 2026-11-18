"""What the tables of the research questions share.

The benchmarks and the batches of the macro-benchmark, the tools that
every table has a row for, the cells of a table (a measurement, or a
stated reason why there is none), the rows of the prior tools from the
result file of prior_tools.py, and the writing of a table
(evaluation/results/<stem>.md and .json). A table never leaves a tool or a
column out without a reason: an input that is not there gives a cell
"missing" with the reason, in the Markdown and in the JSON.
"""

import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVALUATION = HERE.parent
REPOSITORY = EVALUATION.parent
RESULTS = EVALUATION / "results"
PRIOR_TOOLS_RESULT = RESULTS / "prior-tools.json"
# The one macro-benchmark, and the reader of its projects.toml.
BENCHMARK = REPOSITORY / "macro-benchmark"
PROJECTS_READER = BENCHMARK / "projects.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


projects = _load("macro_projects", PROJECTS_READER)
# The cases of the micro-benchmark: it is versioned with the tool, in the
# submodule PyCG (projects.MICRO_BENCHMARK, the one constant of its place).
SNIPPETS = projects.MICRO_BENCHMARK / "snippets"


def dumps(data):
    """run.dumps, loaded when a table is written (run.py loads this module)."""
    return _load("evaluation_run", HERE / "run.py").dumps(data)


def batch_title(batch):
    """The heading of the column of a batch (projects.BATCHES)."""
    return "ICSE 2021 set" if batch == "icse-2021" else f"batch {batch}"


def batches(found):
    """{batch: [project names]} of the projects `found` (load_projects), in
    the order of projects.BATCHES; a project without a batch (a project of
    the tests) is under None."""
    order = [*projects.BATCHES, None]
    result = {}
    for name, project in found.items():
        result.setdefault(project.batch, []).append(name)
    return {batch: result[batch] for batch in order if batch in result}


# The prior tools that prior_tools.py runs, by the prefixes of the row names
# of its result file: a tool can have more than one row (the variants of its
# adapter). One more tool is one image and one adapter in evaluation/tools/
# (build.TOOLS) and one entry here.
PRIOR_TOOLS = (
    "pyan3",
    "JARVIS",
    "CPyGraph",
    "HeaderGen (hybrid: imports the analysed modules)",
    "PyAnalyzer",
)
# A prior tool that imports the modules it analyses would run third-party
# code: it runs only on code that a security scan covered (the maintainer's
# rule, 2026-11-14), so on any other corpus its row is stated.
IMPORTS_ANALYSED_CODE = ("HeaderGen",)
UNSCANNED = "not run: the tool imports the analysed code, and the corpus is not scanned"


# A result of prior_tools.py that records no image ran the tool on the host,
# before the tools had their images: it is never mixed with a run in them.
ON_THE_HOST = "needs rerun (run on the host, not in its image)"


def missing(reason):
    """A cell without a measurement, and why."""
    return {"status": "missing", "reason": reason}


def measured(counts, **facts):
    """A cell of edge counts (inside, outside, all: the Counts.to_json of
    score.py) and the facts of its run."""
    return {"status": "measured", **facts, **counts}


def prf(counts):
    """`P 0.930 / R 0.895 (1251/94/147)`: precision, recall and TP/FP/FN."""

    def number(x):
        return "-" if x is None else f"{x:.3f}"

    return (
        f"P {number(counts['precision'])} / R {number(counts['recall'])} "
        f"({counts['tp']}/{counts['fp']}/{counts['fn']})"
    )


def cell_text(cell, edge_set="inside"):
    """The Markdown text of a cell: its counts of one edge set, or its reason."""
    if cell["status"] == "missing":
        return cell["reason"]
    if edge_set != "inside" and cell.get("no outside"):
        return cell["no outside"]  # a tool that names no outside callee
    text = prf(cell[edge_set])
    if cell.get("projects measured"):
        measured, total = cell["projects measured"]
        text += f", {measured} of {total} projects measured"
    if cell.get("failed"):
        text += f", {len(cell['failed'])} failed runs"
    return text


def table(header, rows):
    """A Markdown table; `|` in a cell is escaped."""
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for row in rows:
        cells = [str(cell).replace("|", "\\|") for cell in row]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def write(results, stem, data, markdown):
    """Write RESULTS/<stem>.json and RESULTS/<stem>.md; return their paths."""
    results = Path(results)
    results.mkdir(parents=True, exist_ok=True)
    json_path, md_path = results / f"{stem}.json", results / f"{stem}.md"
    json_path.write_text(dumps(data) + "\n", encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")
    return json_path, md_path


def inputs_digest(directory):
    """The SHA-256 of the inputs of a benchmark (the macro-benchmark, or the
    micro cases): the paths and bytes of its files, without what Python
    writes (__pycache__) and without projects/ at its top (a copy of the
    sources for reading, which projects.toml pins). A stored measurement is
    of these inputs, whatever the state of the rest of the repository
    (results/ is written by the services themselves)."""
    directory = Path(directory)
    digest = hashlib.sha256()
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        relative = path.relative_to(directory)
        if "__pycache__" in relative.parts or relative.parts[0] == "projects":
            continue
        digest.update(relative.as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def current_inputs(snippets=SNIPPETS, benchmark=BENCHMARK):
    """{"micro": the inputs of the micro cases, "macro": the inputs of the
    macro-benchmark} of this checkout."""
    return {"micro": inputs_digest(snippets), "macro": inputs_digest(benchmark)}


def prior_tool_cells(path, current, table):
    """The cells of the prior tools from a result file of prior_tools.py:
    {row name: {benchmark: cell}}, where a benchmark is "micro" or "macro".
    Each cell keeps the benchmark of its measurement (directory,
    commit and dirty flag, as facts; its inputs, inputs_digest; for the
    micro-benchmark, the newest Python of its cases), the image of the tool
    and the table that scored it. A measurement whose inputs are not those
    of `current` (current_inputs: {benchmark: inputs} of this run) is a cell
    "needs rerun (its inputs changed since it was scored)", one that
    records none "needs rerun (the result records no inputs)"; a row that
    records no image ran on the host and is a cell ON_THE_HOST.
    Nor is a measurement scored with another table of names of one function
    than `table` (the SHA-256 of the table that scored the measurements it
    is shown beside, or None for a measurement scored with no table), or
    with none recorded: a cell "scored with another table (<its table>;
    here <table>)". A result that records no table matches nothing, not
    even `table` None: its absence proves nothing about how it was scored.
    None of these is ever mixed with this run's. The cell of the
    macro-benchmark keeps the counts of each project (`projects`), from
    which the table of RQ1 makes its columns. A benchmark that the file did
    not run is not in a row (the caller states it); a tool of PRIOR_TOOLS
    without a row, or a row that the file records as not run, has the
    reason under the key None. Raises FileNotFoundError, naming the file,
    if it is not there."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"no result of prior_tools.py: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    benchmarks = {}
    for key, found in data.get("sets", {}).items():
        benchmarks[key] = {
            "directory": found.get("directory", key),
            "commit": found.get("commit"),
            "dirty": found.get("dirty"),
            "inputs": found.get("inputs"),
        }
        if key == "micro":
            benchmarks[key] |= {"python": found.get("python")}
            benchmarks[key] |= {"cases": found.get("cases")}
    source = path.name
    scored_with = (data.get("scorer") or {}).get("table")
    rows = {}
    for name, tool in data["tools"].items():
        if not any(name.startswith(prior) for prior in PRIOR_TOOLS):
            continue  # a PyCG checkout: the tables measure PyCG themselves
        if tool.get("not run"):
            rows[name] = {None: missing(f"not run: {tool['not run']}")}
            continue
        cells = {}
        for key, result in data["results"].get(name, {}).items():
            if key not in benchmarks:
                continue
            benchmark = benchmarks[key]
            facts = {"version": tool.get("version"), "source": source}
            facts |= {"benchmark": benchmark, "image": tool.get("image")}
            facts["table"] = scored_with
            if tool.get("outside"):
                facts["no outside"] = f"not measured: {tool['outside']}"
            same_table = scored_with is not None and scored_with == table
            recorded = benchmark.get("inputs")
            fresh = recorded is not None and recorded == current.get(key)
            if result.get("not run"):  # a benchmark that the row has no run of
                cell = missing(result["not run"]) | facts
            elif not tool.get("image"):
                cell = missing(ON_THE_HOST) | facts
            elif fresh and same_table:
                cell = measured(
                    result["raw"], runs=result["runs"], failed=result["failed"], **facts
                )
                # Which projects were not measured, and which image each
                # job ran, stay with the cell.
                kept = ("projects measured", "unavailable", "images", "tool versions")
                kept += ("projects",)
                for fact in kept:
                    if result.get(fact):
                        cell[fact] = result[fact]
            elif fresh:
                which = scored_with[:10] if scored_with else "none recorded"
                here = table[:10] if table else "none"
                reason = f"scored with another table ({which}; here {here})"
                cell = missing(reason) | facts
            elif benchmark.get("inputs") is None:
                cell = missing("needs rerun (the result records no inputs)") | facts
            else:
                reason = "needs rerun (its inputs changed since it was scored)"
                cell = missing(reason) | facts
            cells[key] = cell
        rows[name] = cells
    for prior in PRIOR_TOOLS:
        if not any(name.startswith(prior) for name in rows):
            rows[prior] = {None: missing(f"not yet run: no row in {source}")}
    return rows
