"""Behaviour tests for evaluation/rq3-scale/robustness.py: one run per package of a
corpus and tool under a time and a memory limit, and its report.

The corpus is written to tmp_path as corpus.py's `fetch` leaves it; the
tool is a fake PyCG checkout run in real child processes (evaluation_fakes).
They run on every supported Python version.
"""

import json
import shutil

import pytest
from evaluation_fakes import fake_checkout, load, pinned_archive

robustness = load("rq3-scale/robustness")


def make_corpus(tmp_path, behaviours):
    """A manifest and a work directory with one pure wheel per package,
    whose BEHAVIOUR file tells the fake tool what to do."""
    work, cache = tmp_path / "work", tmp_path / "cache"
    rows, recorded = [], {}
    for rank, (name, behaviour) in enumerate(behaviours.items(), start=1):
        if behaviour is None:  # a wheel without a Python module
            files = {f"{name}-1.0.dist-info/METADATA": ""}
        else:
            files = {
                f"{name}/__init__.py": "x = 1\n# note\n\n",
                f"{name}/sub/mod.py": "y = 2\n",
                "BEHAVIOUR": behaviour,
            }
        file = f"{name}-1.0-py3-none-any.whl"
        fields, recorded[name] = pinned_archive(cache, work, name, files, file)
        rows.append(
            {
                "name": name,
                "rank": rank,
                "version": "1.0",
                "kind": "pure wheel",
                "url": "https://files.pythonhosted.org/packages/" + file,
                "uploaded": "2026-10-01T12:00:00.000000Z",
                **fields,
            }
        )
    rows.append({"name": "gone", "rank": len(rows) + 1, "reason": "not on PyPI"})
    manifest = tmp_path / "pypi-top-3.json"
    manifest.write_text(
        json.dumps(
            {
                "rank_list": {
                    "file": "top.json",
                    "sha256": "0" * 64,
                    "last_update": "x",
                },
                "cutoff": "2026-11-01",
                "top": len(rows),
                "rows": rows,
            }
        )
    )
    (work / "fetch.json").write_text(json.dumps(recorded))
    return manifest, work


def run_main(tmp_path, manifest, work, *extra, results="results"):
    checkout = fake_checkout(tmp_path / results / "tool")
    argv = ["--manifest", str(manifest), "--work", str(work)]
    argv += ["--tool", f"fake:new:{checkout}", "--allow-dirty"]
    argv += ["--results", str(tmp_path / results), *extra]
    assert robustness.main(argv) == 0
    path = tmp_path / results / f"robustness-{manifest.stem}.json"
    return json.loads(path.read_text()), path.with_suffix(".md").read_text()


def unmeasured(data):
    """The data without its measured times and memory."""
    if isinstance(data, dict):
        return {k: unmeasured(v) for k, v in data.items() if k != "measured"}
    return data


def test_a_refused_archive_is_a_package_without_a_run(tmp_path):
    manifest, work = make_corpus(tmp_path, {"fine": "ok", "bomb": "ok"})
    recorded = json.loads((work / "fetch.json").read_text())
    recorded["bomb"] = {
        "file": recorded["bomb"]["file"],
        "sha256": recorded["bomb"]["sha256"],
        "archive refused": "more than 4294967296 bytes in all",
    }
    (work / "fetch.json").write_text(json.dumps(recorded))
    shutil.rmtree(work / "bomb")
    data, report = run_main(tmp_path, manifest, work)
    assert data["packages"]["bomb"] == {
        "rank": 2,
        "version": "1.0",
        "kind": "pure wheel",
        "reason": "archive refused: more than 4294967296 bytes in all",
    }
    assert data["packages"]["fine"]["runs"]["fake"]["status"] == "ok"


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------


def run_record(status, seconds=None, mib=None, **extra):
    measured = {"wall_seconds": seconds, "peak_rss_mib": mib}
    return {"status": status, **extra, "measured": measured}


def package(rank, code_lines, run):
    return {
        "rank": rank,
        "version": "1",
        "kind": "pure wheel",
        "modules": ["m"],
        "files": 1,
        "lines": code_lines,
        "code_lines": code_lines,
        "runs": {"new": run},
    }


def report_data(packages, seconds=7200, memory_mib=16 * 1024):
    return {
        "manifest": {
            "file": "pypi-top-5.json",
            "sha256": "a" * 64,
            "top": 5,
            "rank_list": {"file": "top.json", "last_update": "2026-11-01 06:41:05"},
        },
        "python": "3.15.2",
        "platform": "darwin arm64",
        "hash_seed": 0,
        "limits": {"seconds": seconds, "memory_mib": memory_mib},
        "tools": {
            "new": {
                "name": "new",
                "flavour": "new",
                "commit": "e5a3b10" + "0" * 33,
                "describe": "e5a3b10",
                "dirty": False,
            }
        },
        "packages": packages,
        "measured": {"jobs": 4, "watch_interval": 0.2},
    }


GIB = 1024


COMMIT = "c0ffee" + "0" * 34


def pinned_corpus(tmp_path, extra=None, content="x = 1\n"):
    """A manifest of one project pinned by its commit (as DyPyBench's are)
    and its unpacked archive: three files of `content`, and `extra`."""
    work, cache = tmp_path / "work", tmp_path / "cache"
    top = f"demo-{COMMIT}"
    files = {
        f"{top}/{name}": content
        for name in ("src/demo/__init__.py", "src/demo/core.py", "tests/test_demo.py")
    }
    files.update(extra or {})
    fields, record = pinned_archive(cache, work, "demo", files, f"demo-{COMMIT}.tar.gz")
    row = {
        "name": "demo",
        "rank": 1,
        "version": COMMIT,
        "kind": "git archive",
        "repository": "https://github.com/owner/demo",
        "commit": COMMIT,
        "committed": "2023-01-02T00:00:00Z",
        "file": f"demo-{COMMIT}.tar.gz",
        "url": f"https://codeload.github.com/owner/demo/tar.gz/{COMMIT}",
        **fields,
        "root": "src",
        "modules": ["demo"],
    }
    gone = {"name": "gone", "rank": 2, "reason": "not recoverable"}
    source = {"repository": "https://github.com/sola-st/DyPyBench", "commit": "d" * 40}
    manifest = {"source": source, "rows": [row, gone]}
    (work / "fetch.json").write_text(json.dumps({"demo": record}))
    return manifest, work


@pytest.mark.parametrize("change", ["edit", "add", "remove"])
def test_planning_refuses_an_unpacked_tree_that_is_not_its_archive(tmp_path, change):
    manifest, work = pinned_corpus(tmp_path)
    core = work / "demo" / f"demo-{COMMIT}" / "src" / "demo" / "core.py"
    if change == "edit":
        core.write_text("x = 2\n")
    elif change == "add":
        (core.parent / "added.py").write_text("x = 1\n")
    else:
        core.unlink()
    with pytest.raises(robustness.TreeChanged, match="src/demo/"):
        robustness.plan(manifest, work)


def test_planning_refuses_an_archive_of_the_same_length_with_another_hash(
    tmp_path,
):
    manifest, work = pinned_corpus(tmp_path)
    pinned = manifest["rows"][0]
    # Another valid archive of the same length, with its own unpacked tree.
    other, _ = pinned_corpus(tmp_path / "other", content="x = 2\n")
    fields = other["rows"][0]
    assert fields["size"] == pinned["size"] and fields["sha256"] != pinned["sha256"]
    archive = tmp_path / "cache" / pinned["file"]
    archive.write_bytes((tmp_path / "other" / "cache" / fields["file"]).read_bytes())
    (work / "demo").rename(tmp_path / "pinned-tree")
    (tmp_path / "other" / "work" / "demo").rename(work / "demo")
    assert robustness.corpus.verify_unpacked(archive, work / "demo") is None
    with pytest.raises(robustness.InvalidRow, match="not the pinned one"):
        robustness.plan(manifest, work)


def test_planning_needs_the_pinned_archive_in_the_cache(tmp_path):
    manifest, work = pinned_corpus(tmp_path)
    archive = tmp_path / "cache" / manifest["rows"][0]["file"]
    archive.write_bytes(archive.read_bytes() + b"x")
    with pytest.raises(robustness.InvalidRow, match="not the pinned one"):
        robustness.plan(manifest, work)
    archive.unlink()
    with pytest.raises(robustness.NotFetched, match="demo"):
        robustness.plan(manifest, work)


def test_the_success_rate_is_computed_at_each_threshold_under_the_limits():
    packages = {
        "a": package(1, 10, run_record("ok", 100, 1 * GIB)),
        "b": package(2, 10, run_record("ok", 2000, 2 * GIB)),  # over 30 min
        "c": package(3, 10, run_record("ok", 100, 10 * GIB)),  # over 8 GiB
        "d": package(4, 10, run_record("timeout", 7200, 3 * GIB)),
        "e": package(5, 10, run_record("crash", 5, 1, error="E: x", where="w:1 in f")),
        "f": {"rank": 6, "reason": "not on PyPI"},
    }
    report = robustness.render(report_data(packages))
    assert "| new | 5 | 30 min | 1 (20.0%) | 2 (40.0%) |" in report
    assert "| new | 5 | 1 h | 2 (40.0%) | 3 (60.0%) |" in report
    assert "| new | 5 | 2 h | 2 (40.0%) | 3 (60.0%) |" in report
    # Thresholds over the limits of the run are not shown.
    assert "6 h" not in report and "60 GiB" not in report
    assert "| new | crash | E at w:1 in f | 1 | e |" in report
    assert "| new | timeout | - | 1 | d |" in report
    assert "| 6 | f | not on PyPI |" in report


def test_failures_are_grouped_by_a_stable_cause():
    unread = "a process of the group cannot be read"
    packages = {
        "a": package(1, 10, run_record("unmeasured", 1, None, error=unread)),
        "b": package(2, 10, run_record("unmeasured", 1, None, error=unread)),
    }
    packages["a"]["runs"]["new"]["detail"] = "process 101"
    packages["b"]["runs"]["new"]["detail"] = "process 202"
    report = robustness.render(report_data(packages))
    assert f"| new | unmeasured | {unread} | 2 | a, b |" in report


def test_a_finished_run_over_a_threshold_fails_there():
    # The watchdog samples: a run can finish with a peak over the limit.
    packages = {"a": package(1, 10, run_record("ok", 10, 9 * GIB))}
    report = robustness.render(report_data(packages, 1800, 8 * GIB))
    assert "| new | 1 | 30 min | 0 (0.0%) |" in report


def test_time_and_memory_are_summarised_by_size_class():
    packages = {
        f"p{i}": package(i, 5_000, run_record("ok", float(i), float(10 * i)))
        for i in range(1, 11)
    }
    packages["big"] = package(11, 200_000, run_record("timeout", 7200, 100))
    packages["empty"] = package(12, 0, run_record("ok", 0.5, 30))
    report = robustness.render(report_data(packages))
    # Median, 90th percentile (nearest rank) and maximum of the ok runs.
    assert "| new | 1k-10k | 10 | 10 | 5.5 / 9.0 / 10.0 | 55 / 90 / 100 |" in report
    assert "| new | < 1k | 1 | 1 | 0.5 / 0.5 / 0.5 | 30 / 30 / 30 |" in report
    assert "| new | >= 100k | 1 | 0 | - | - |" in report
    assert "10k-100k" not in report  # no package of that size


def test_lines_are_counted_physical_and_code(tmp_path):
    path = tmp_path / "m.py"
    path.write_text("# a comment\n\nimport os\n    # indented comment\nx = 1  # c\n")
    assert robustness.count_lines([path]) == (5, 2)
