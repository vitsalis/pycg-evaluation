#!/usr/bin/env python3
"""Score the stored runs of the prior tools on the micro-benchmark and on
the macro-benchmark: the input of the rows of the prior tools in RQ1.

    python3 -I evaluation/common/prior_tools.py [--tools DIR] [--graphs DIR]
        [--out DIR]

The rq1 service runs it before the table of RQ1 (container/question_entry.py),
after the tools ran in their services:

    docker compose -f evaluation/compose.yaml run --rm TOOL rq1

which write one result file per row, TOOLS/<row>/rq1.json (TOOLS:
evaluation/results/tools; container/tool_job.py): the record of each job
and the graphs of the micro cases; and the graph of each macro project,
GRAPHS/<row>/<project>.json (GRAPHS: evaluation/results/graphs). HeaderGen's
projects with runtime dependencies run in the service headergen-deps, one
file TOOLS/<row>/rq1-NAME.json each, laid over the tool's. Both directories
are committed: this script scores a tool again without running it.

A row without its result file is "not run", with the command that runs it,
so the table never fails for a missing tool. Each job's input is exported
again here and compared with the SHA-256 that the run recorded: a benchmark
whose inputs changed since the run is "not run: needs rerun". A graph is
used only with its record (its SHA-256). The micro cases are those whose
requires-python is at most the oldest Python of the tools' base images
(tool_job.cohort), the same for every tool.

Writes OUT/prior-tools.md and OUT/prior-tools.json (OUT: evaluation/results):
per tool its version, pin, image and limits; per benchmark its directory,
commit and dirty flag, as at this scoring; per tool and benchmark the true
positives, false positives and false negatives of the edges inside the
package and outside it, precision and recall, in the raw and the normalised
mode of score.py, for the macro-benchmark also per project; the failed
runs; and the edges that each adapter dropped or kept, by reason. Both files
are a function of the inputs: no time and no memory figure is in them.
"""

import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVALUATION = HERE.parent
REPO = EVALUATION.parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run = _load("evaluation_run", HERE / "run.py")
score = run.load_sibling("evaluation_score", HERE / "score.py")
tables = run.load_sibling("evaluation_tables", HERE / "tables.py")
tool_job = _load("evaluation_tool_job", EVALUATION / "container" / "tool_job.py")
build = tool_job._build()
PRIOR_TOOLS = build.TOOLS
TOOLS_RESULTS = tables.RESULTS / "tools"
GRAPHS = tables.RESULTS / "graphs"
UNAVAILABLE = tool_job.UNAVAILABLE
SERVICE = "docker compose -f evaluation/compose.yaml run --rm"
STALE = "needs rerun: its inputs changed since the tool ran"


def _counts(totals):
    return {edge_set: totals[edge_set].to_json() for edge_set in score.EDGE_SETS}


def _notes(records):
    notes = {}
    for record in records.values():
        for reason, number in record.get("notes", {}).items():
            notes[reason] = notes.get(reason, 0) + number
    return dict(sorted(notes.items()))


def _failed(records):
    failed = []
    for key, record in sorted(records.items()):
        if record["status"] in ("ok", "skipped", UNAVAILABLE):
            continue
        entry = {"run": key, "status": record["status"]}
        if "error" in record:
            entry["error"] = record["error"]
        failed.append(entry)
    return failed


def _unavailable(records):
    """The jobs whose environment is not there, with the reason."""
    return [
        {"run": key} | ({"reason": record["error"]} if "error" in record else {})
        for key, record in sorted(records.items())
        if record["status"] == UNAVAILABLE
    ]


def _dependencies(records):
    """{statement: number of jobs} of a tool that needs runtime dependencies."""
    found = {}
    for record in records.values():
        if "dependencies" in record:
            found[record["dependencies"]] = found.get(record["dependencies"], 0) + 1
    return dict(sorted(found.items()))


def stored(tool, row, tools_dir, graphs_dir):
    """The result of `row` (a row of `tool`) on rq1, with HeaderGen's runs
    in the images of the projects' dependencies laid over it and the stored
    graph of each macro project that ran: (data, why it is not of the row
    as it is now, or None); (None, None) if it did not run."""
    path = tool_job.result_path(tools_dir, row, "rq1")
    if not path.is_file():
        return None, None
    data = json.loads(path.read_text(encoding="utf-8"))
    problem = tool_job.stored_problem(tool, row, data, "rq1")
    for extra in sorted(path.parent.glob("rq1-*.json")):
        more = json.loads(extra.read_text(encoding="utf-8"))
        problem = problem or tool_job.stored_problem(tool, row, more, "rq1")
        for name, entry in more["sets"].items():
            if name in data["sets"]:
                data["sets"][name]["runs"] |= entry["runs"]
    macro = data["sets"].get("macro")
    if macro is not None:
        macro["graphs"] = {}
        for key in macro["runs"]:
            graph = tool_job.graph_path(graphs_dir, row, key.removeprefix("macro/"))
            if graph.is_file():
                macro["graphs"][key] = json.loads(graph.read_text(encoding="utf-8"))
    return data, problem


JOBS_CHANGED = "needs rerun: the jobs of the benchmark changed since the tool ran"


def entry_problem(entry, jobs, skipped=()):
    """Why the stored runs of a benchmark cannot be scored, or None: they are
    not of its jobs as they are now (each job that ran, each skipped),
    or a finished job has no graph of its own (tool_job.graph_problem)."""
    runs = entry["runs"]
    ran = {k for k, r in runs.items() if r["status"] != "skipped"}
    passed = {k for k, r in runs.items() if r["status"] == "skipped"}
    if ran != {job.key for job in jobs} or passed != set(skipped):
        return JOBS_CHANGED
    graphs = entry.get("graphs", {})
    for key, record in sorted(runs.items()):
        problem = tool_job.graph_problem(record, graphs.get(key))
        if problem:
            return problem
    return None


def changed(adapter, jobs, runs):
    """Whether the input of a job that ran is not what it is now
    (tool_job.input_changed)."""
    return any(
        tool_job.input_changed(adapter, job, runs.get(job.key, {})) for job in jobs
    )


def materialise(tool_dir, benchmark, entry):
    """Write the runs and the graphs of a benchmark in the layout of run.py."""
    for key, graph in entry.get("graphs", {}).items():
        path = run.graph_file(tool_dir, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(graph, sort_keys=True), encoding="utf-8")
    path = Path(tool_dir) / benchmark / "runs.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"runs": entry["runs"]}, sort_keys=True))


def score_micro(adapter, entry, jobs, skipped, snippets, work):
    runs = entry["runs"]
    if problem := entry_problem(entry, jobs, skipped):
        return {"not run": problem}
    if changed(adapter, jobs, runs):
        return {"not run": STALE}
    materialise(work, "micro", entry)
    cases = score.score_micro(work, snippets)
    ran = [k for k, r in runs.items() if r["status"] != "skipped"]
    result = {"runs": len(ran), "failed": _failed(runs), "notes": _notes(runs)}
    for mode in score.MODES:
        total = score.micro_total(score.micro_categories(cases, mode))
        result[mode] = _counts(total["counts"])
    return result


def score_macro(adapter, entry, directory, work):
    runs = entry["runs"]
    jobs, skipped = tool_job.macro_jobs(directory)
    if skipped:
        return {"not run": f"not measured: {len(skipped)} projects have no source"}
    if problem := entry_problem(entry, jobs):
        return {"not run": problem}
    if changed(adapter, jobs, runs):
        return {"not run": STALE}
    materialise(work, "macro", entry)
    scored = score.score_macro(work, Path(directory) / "projects.toml")
    # A project whose environment is not there is not measured: its edges
    # are no false negatives of the tool. The totals are those of the
    # measured projects.
    unavailable = {u["run"].removeprefix("macro/") for u in _unavailable(runs)}
    projects = {n: p for n, p in scored.items() if n not in unavailable}
    result = {
        "runs": len(runs) - len(unavailable),
        "failed": _failed(runs),
        "notes": _notes(runs),
    }
    if unavailable:
        result["unavailable"] = _unavailable(runs)
        result["projects measured"] = [len(projects), len(scored)]
    if _dependencies(runs):
        result["dependencies"] = _dependencies(runs)
        given = {}
        for key, record in sorted(runs.items()):
            if record.get("dependencies", "").startswith("installed"):
                project = key.removeprefix("macro/")
                found = build.deps_tool_versions(directory, project)
                if found:  # from HeaderGen's own environment, not the trace's
                    given[project] = found
        if given:
            result["tool versions"] = given
    for mode in score.MODES:
        result[mode] = {
            edge_set: score.summed(projects, mode, edge_set).to_json()
            for edge_set in score.EDGE_SETS
        }
    result["projects"] = {
        name: {"status": project["status"]}
        | {mode: _counts(project[mode]) for mode in score.MODES}
        for name, project in projects.items()
    } | {name: {"status": UNAVAILABLE} for name in sorted(unavailable)}
    return result


def tool_facts(adapter, data, reason=None):
    facts = (data or {}).get("tool", {})
    memory = facts.get("memory_mib")
    found = {
        "version": f"{adapter.PIN['version']} ({adapter.PIN['commit'][:10]})",
        "python": facts.get("python"),
        "not run": reason,
        "note": getattr(adapter, "NOTE", None),
        "image": {"reference": facts["image"]} if facts.get("image") else None,
        "pin": adapter.PIN,
        "memory_mib": memory,
    }
    if getattr(adapter, "NO_OUTSIDE", None):
        found["outside"] = adapter.NO_OUTSIDE
    return found


def compare(tools_dir, graphs_dir, root=REPO, snippets=score.SNIPPETS):
    """The JSON data of every row of the prior tools, scored from the
    stored results (`root`: the directory that holds macro-benchmark/)."""
    benchmark = Path(root) / "macro-benchmark"
    cohort = tool_job.cohort()
    cohort_text = ".".join(map(str, cohort))
    micro, excluded = tool_job.micro_jobs(cohort, snippets)
    found = {
        "directory": "micro",
        "cases": len(micro),
        "excluded": len(excluded),
        "python": cohort_text,
        # The checkout that holds the micro cases: the submodule PyCG, not
        # this repository.
        **{k: v for k, v in run.git_version(snippets).items() if k != "describe"},
        "inputs": tables.inputs_digest(snippets),
    }
    data = {
        "tools": {},
        "limits": {
            "seconds": tool_job.LIMITS["rq1"][0],
            "memory_mib": tool_job.LIMITS["rq1"][1],
            "hash_seed": 0,
        },
        "scorer": score.scorer(),
        "sets": {"micro": found},
        "not measured": {},
        "results": {},
    }
    if (benchmark / "projects.toml").is_file():
        data["sets"]["macro"] = _header(benchmark)
    else:
        data["not measured"]["macro"] = "no macro-benchmark/projects.toml"
    for tool in PRIOR_TOOLS:
        adapter = build.load_adapter(tool)
        for row, _ in tool_job.rows(tool):
            stored_data, problem = stored(tool, row, tools_dir, graphs_dir)
            if stored_data is None:
                where = tool_job.result_path("results/tools", row, "rq1")
                reason = f"no {where} ({SERVICE} {tool} rq1)"
                data["tools"][row] = tool_facts(adapter, None, reason)
                continue
            if problem:  # of another version, row or limits than today's
                data["tools"][row] = tool_facts(adapter, None, problem)
                continue
            data["tools"][row] = tool_facts(adapter, stored_data)
            results = {}
            with tempfile.TemporaryDirectory(prefix="prior-tools-") as work:
                entry = stored_data["sets"].get("micro")
                if stored_data.get("cohort") != cohort_text:
                    ends = stored_data.get("cohort")
                    results["micro"] = {"not run": f"needs rerun: cohort {ends}"}
                elif entry is not None:
                    results["micro"] = score_micro(
                        adapter, entry, micro, excluded, snippets, Path(work) / "micro"
                    )
                entry = stored_data["sets"].get("macro")
                if "macro" not in data["sets"]:
                    pass
                elif entry is not None:
                    results["macro"] = score_macro(
                        adapter, entry, benchmark, Path(work) / "macro"
                    )
                elif reason := stored_data.get("absent", {}).get("macro"):
                    results["macro"] = {"not run": reason}
            data["results"][row] = results
    return data


# --------------------------------------------------------------------------
# The Markdown table
# --------------------------------------------------------------------------


def _header(directory):
    """The macro-benchmark's directory, commit and projects: those of
    projects.toml that have a graph (ground-truth/<name>.json; the files
    beside it, such as <name>.rejected.json and <name>.added.json, are not
    projects)."""
    git = run.git_version(directory)
    projects = tables.projects.load_projects(Path(directory) / "projects.toml")
    truth = Path(directory) / "ground-truth"
    return {
        "directory": Path(directory).resolve().name,
        "commit": git["commit"],
        "dirty": git["dirty"],
        "inputs": tables.inputs_digest(directory),
        "projects": sorted(
            name for name in projects if (truth / f"{name}.json").is_file()
        ),
    }


def _number(x):
    return "-" if x is None else f"{x:.3f}"


def render(data):
    lines = [
        "# PyCG and prior tools",
        "",
        "Generated by `evaluation/common/prior_tools.py`; do not edit. See",
        '`evaluation/README.md`, section "Prior tools", for the runs, the images,',
        "the conversions of each adapter and what they lose. P: precision, R: recall.",
        "",
        "## Tools",
        "",
        "| Tool | Version | Python | Image | Memory | Run |",
        "|---|---|---|---|---|---|",
    ]
    for name, tool in data["tools"].items():
        state = "yes" if tool["not run"] is None else f"not run: {tool['not run']}"
        image = tool.get("image")
        shown = f"`{image['reference']}`" if image else "-"
        memory = f"{tool.get('memory_mib', '-')} MiB" if tool.get("memory_mib") else "-"
        lines.append(
            f"| {name} | {tool['version']} | {tool['python'] or '-'} | {shown} "
            f"| {memory} | {state} |"
        )
    notes = {}
    for name, tool in data["tools"].items():
        if tool.get("note"):
            notes.setdefault(tool["note"], []).append(name)
    lines.append("")
    for note, names in notes.items():
        lines.append(f"How {', '.join(names)} run: {note}.")
    for name, tool in data["tools"].items():
        if tool.get("outside"):
            lines.append(f"The outside columns of {name}: {tool['outside']}.")
    sets, limits = data["sets"], data["limits"]
    micro = sets["micro"]
    lines += [
        "",
        f"Micro-benchmark: {micro['cases']} cases. {micro['excluded']} cases need a",
        f"Python newer than {micro['python']}, the oldest interpreter of a tool that",
        "runs; they are left out for every tool.",
    ]
    for set_name, bench in sets.items():
        if set_name == "micro":
            continue
        state = ", with changes" if bench["dirty"] else ""
        lines.append(
            f"`{bench['directory']}` at `{(bench['commit'] or '?')[:10]}`{state}: "
            f"{', '.join(bench['projects'])}."
        )
    for set_name, reason in data.get("not measured", {}).items():
        lines.append(f"`{set_name}`: {reason}.")
    lines.append(
        "Each job: a child process in the tool's Compose service, without "
        f"network, hash seed {limits['hash_seed']}, at most "
        f"{limits['seconds']:g} s and the memory of its tool (table above)."
    )
    for name, results in data["results"].items():
        for set_name, result in results.items():
            if result.get("not run"):
                lines.append(f"{name}, `{set_name}`: {result['not run']}.")
    lines.append("")
    for mode, title in (
        ("raw", "Raw names (the headline)"),
        ("normalised", "Normalised names"),
    ):
        lines += [f"## {title}", "", _HEADER, "|" + "---|" * 13]
        for name, results in data["results"].items():
            outside = data["tools"][name].get("outside")
            for set_name in sets:
                result = results.get(set_name)
                if result and not result.get("not run"):
                    lines.append(_row(name, set_name, result, mode, outside))
        for name, tool in data["tools"].items():
            if tool["not run"] is not None:
                lines.append(f"| {name} | all | not run |" + " - |" * 10)
        lines.append("")
    lines += ["## Failed runs", ""]
    failures = [
        (name, set_name, failure)
        for name, results in data["results"].items()
        for set_name, result in results.items()
        for failure in result.get("failed", [])
    ]
    for name, set_name, failure in failures:
        error = f": {failure['error']}" if "error" in failure else ""
        lines.append(
            f"- {name}, {set_name}, `{failure['run']}`: {failure['status']}{error}"
        )
    if not failures:
        lines.append("None.")
    unavailable = [
        (name, set_name, entry)
        for name, results in data["results"].items()
        for set_name, result in results.items()
        for entry in result.get("unavailable", [])
    ]
    if unavailable:
        lines += ["", "## Not measured: the environment is not there", ""]
        for name, set_name, entry in unavailable:
            reason = f": {entry['reason']}" if "reason" in entry else ""
            lines.append(f"- {name}, {set_name}, `{entry['run']}`{reason}")
    lines += ["", "## Edges dropped or kept by the adapters", ""]
    notes = [
        (name, set_name, reason, number)
        for name, results in data["results"].items()
        for set_name, result in results.items()
        for reason, number in result.get("notes", {}).items()
    ]
    if notes:
        lines += ["| Tool | Benchmark | Reason | Edges |", "|---|---|---|---|"]
        lines += [f"| {n} | {s} | {r} | {k} |" for n, s, r, k in notes]
    else:
        lines.append("None.")
    given = [
        (name, set_name, project, entry)
        for name, results in data["results"].items()
        for set_name, result in results.items()
        for project, entries in result.get("tool versions", {}).items()
        for entry in entries
    ]
    if given:
        lines += ["", "## Dependencies that the tool's environment gives", ""]
        lines += [
            "HeaderGen's own environment wins over a project's trace pins:",
            "",
            "| Tool | Benchmark | Project | Distribution | Tool's version "
            "| Trace's version |",
            "|---|---|---|---|---|---|",
        ]
        lines += [
            f"| {n} | {s} | {p} | {e['distribution']} | {e['tool']} | {e['trace']} |"
            for n, s, p, e in given
        ]
    installed = [
        (name, set_name, statement, number)
        for name, results in data["results"].items()
        for set_name, result in results.items()
        for statement, number in result.get("dependencies", {}).items()
    ]
    if installed:
        lines += ["", "## Runtime dependencies of the projects", ""]
        lines += ["| Tool | Benchmark | Dependencies | Projects |", "|---|---|---|---|"]
        lines += [f"| {n} | {s} | {d} | {k} |" for n, s, d, k in installed]
    return "\n".join(lines) + "\n"


_HEADER = (
    "| Tool | Benchmark | Runs | In TP | In FP | In FN | In P | In R "
    "| Out TP | Out FP | Out FN | Out P | Out R |"
)


def _row(name, set_name, result, mode, outside=None):
    failed = len(result["failed"])
    runs = f"{result['runs']}" + (f" ({failed} failed)" if failed else "")
    if result.get("projects measured"):
        measured, total = result["projects measured"]
        runs += f"; {measured} of {total} projects measured"
    cells = [name, set_name, runs]
    for edge_set in ("inside", "outside"):
        if edge_set == "outside" and outside:
            cells += ["-"] * 5
            continue
        c = result[mode][edge_set]
        cells += [
            c["tp"],
            c["fp"],
            c["fn"],
            _number(c["precision"]),
            _number(c["recall"]),
        ]
    return "| " + " | ".join(str(cell) for cell in cells) + " |"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--tools", type=Path, default=TOOLS_RESULTS)
    parser.add_argument("--graphs", type=Path, default=GRAPHS)
    parser.add_argument("--out", type=Path, default=score.RESULTS)
    args = parser.parse_args(argv)
    data = compare(args.tools, args.graphs)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "prior-tools.json").write_text(
        score.dumps(data) + "\n", encoding="utf-8"
    )
    (args.out / "prior-tools.md").write_text(render(data), encoding="utf-8")
    print(args.out / "prior-tools.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
