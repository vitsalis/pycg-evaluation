"""evaluation/compose.yaml: every service is sealed, and the questions, the
trace and the prior tools are services whose images take their inputs from
the inputs image.

The standard library has no YAML parser: compose.yaml keeps to plain YAML
(mappings, lists of scalars, JSON lists, anchors, aliases and merge keys),
and `read` below reads that much. Nothing here runs Docker.
"""

import json
import re
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[1]
EVALUATION = REPOSITORY / "evaluation"
COMPOSE = EVALUATION / "compose.yaml"
DIGEST = re.compile(r"[a-z0-9][a-z0-9._/:-]*@sha256:[0-9a-f]{64}")
QUESTIONS = ("rq1", "rq1-trace", "rq2", "rq3", "rq3-pilot")
TOOLS = ("pyan", "jarvis", "cpygraph", "pyanalyzer", "headergen")
TOOL_SERVICES = (*TOOLS, "headergen-deps")


def _load(name, path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = _load("compose_build", EVALUATION / "tools" / "build.py")


def _scalar(text, anchors):
    text = text.strip()
    if text.startswith("*"):
        return anchors[text[1:]]
    if text.startswith("["):
        return json.loads(text)
    if len(text) > 1 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return {"true": True, "false": False}.get(text, text)


def read(text):
    """The document of plain YAML `text` (see the module docstring)."""
    lines = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped and not stripped.startswith("#"):
            lines.append((len(raw) - len(raw.lstrip(" ")), stripped))
    anchors = {}

    def block(i, indent):
        if lines[i][1].startswith("- "):
            items = []
            while i < len(lines) and lines[i][0] == indent and lines[i][1][:2] == "- ":
                items.append(_scalar(lines[i][1][2:], anchors))
                i += 1
            return items, i
        mapping = {}
        while i < len(lines) and lines[i][0] == indent:
            key, _, rest = lines[i][1].partition(":")
            rest, anchor = rest.strip(), None
            if rest.startswith("&"):
                anchor, _, rest = rest[1:].partition(" ")
            i += 1
            if rest:
                value = _scalar(rest, anchors)
            else:
                value, i = block(i, lines[i][0])
            if anchor:
                anchors[anchor] = value
            if key == "<<":
                mapping = {**value, **mapping}
            else:
                mapping[key] = value
        return mapping, i

    document, end = block(0, 0)
    assert end == len(lines), f"not read: {lines[end:]}"
    return document


@pytest.fixture(scope="module")
def compose():
    return read(COMPOSE.read_text(encoding="utf-8"))


def test_each_python_image_is_pinned_by_digest_in_the_file_once(compose):
    text = COMPOSE.read_text(encoding="utf-8")
    digests = re.findall(r"@sha256:[0-9a-f]{64}", text)
    assert len(digests) == len(set(digests))
    assert DIGEST.fullmatch(compose["x-python-image"])
    # The bases of the tools are those of pins.json, which pin.py writes.
    bases = set(build.read_pins()["base_images"].values())
    images = {value for key, value in compose.items() if key.startswith("x-python")}
    assert images == bases | {compose["x-python-image"]}


def test_the_default_base_of_the_inputs_image_is_the_python_image(compose):
    """Its default lets the target maintain build without arguments; it is
    the one pin of compose.yaml."""
    text = (EVALUATION / "container" / "Dockerfile.inputs").read_text()
    assert f"ARG PYTHON_IMAGE={compose['x-python-image']}\n" in text
    inputs = compose["services"]["inputs"]["build"]["args"]
    assert inputs["PYTHON_IMAGE"] == compose["x-python-image"]


def _dockerfile(service):
    build = service["build"]
    context = EVALUATION / re.sub(
        r"\$\{(\w+)[^}]*\}", "macro-benchmark", build["context"]
    )
    return (context / build["dockerfile"]).resolve()


def test_every_service_is_sealed(compose):
    for name, service in compose["services"].items():
        where = f"service {name}"
        assert service.get("network_mode") == "none", where
        assert service.get("read_only") is True, where
        assert service.get("cap_drop") == ["ALL"], where
        assert "no-new-privileges:true" in service.get("security_opt", []), where
        assert not service.get("privileged"), where
        user = service.get("user", "")
        assert user and not user.startswith(("0", "root")), where
        assert service.get("mem_limit") and service.get("tmpfs"), where
        for volume in service.get("volumes", []):
            assert "docker.sock" not in volume, where
            source, target, *mode = volume.split(":")
            writable = "ro" not in mode
            allowed = (source, target) in (
                ("./results", "/results"),
                ("./results/trace", "/results/trace"),
            )
            if name in TOOL_SERVICES:  # a Docker volume, no path of the host
                allowed = allowed or volume == "tool-graphs:/tool-graphs"
            assert not writable or allowed, f"{where}: {volume} is writable"


def test_every_image_is_built_from_a_pinned_base(compose):
    for name, service in compose["services"].items():
        if "build" not in service:
            assert DIGEST.fullmatch(service["image"]), name
            continue
        dockerfile = _dockerfile(service)
        assert dockerfile.is_relative_to(REPOSITORY), name
        text = dockerfile.read_text(encoding="utf-8")
        assert "FROM ${PYTHON_IMAGE}" in text, name
        assert DIGEST.fullmatch(service["build"]["args"]["PYTHON_IMAGE"]), name


def test_every_prior_tool_is_a_service_built_from_its_pins(compose):
    pins = build.read_pins()
    entry = ["python3", "-I", "-B", "/repo/evaluation/container/tool_job.py"]
    for tool in TOOLS:
        service = compose["services"][tool]
        adapter = build.load_adapter(tool)
        assert service["image"] == f"pycg-tool-{tool}", tool
        assert service["entrypoint"] == [*entry, tool], tool
        assert _dockerfile(service) == build.DOCKER / adapter.IMAGE["dockerfile"]
        args = service["build"]["args"]
        assert args["PYTHON_IMAGE"] == pins["base_images"][tool], tool
        assert args["TOOL_PIN"] == build.pin_label(adapter), tool
        assert args.get("TREES", "") == build.trees_label(tool, pins), tool
        contexts = service["build"]["additional_contexts"]
        assert contexts == {"inputs": "service:inputs"}, tool
        for name in adapter.IMAGE.get("trees", {}):
            assert args[f"TREE_{name.upper()}"] == pins["trees"][f"{tool}/{name}"]
        assert "tool-graphs:/tool-graphs" in service["volumes"], tool
    # The graphs of rq1 are files of results/: no volume holds a result
    # that the tables hide.
    assert list(compose["volumes"]) == ["tool-graphs"]
    assert "held-out" not in COMPOSE.read_text()
    # The question that reads the graphs of rq1-trace reads them only.
    for name in QUESTIONS:
        volumes = compose["services"][name]["volumes"]
        reads = name == "rq1-trace"
        assert ("tool-graphs:/tool-graphs:ro" in volumes) is reads, name
    assert "tool-graphs" in compose["volumes"]


def test_there_are_three_questions(compose):
    """The accuracy on held-out projects is a section of the table of RQ1,
    not a question: the questions are RQ1 to RQ3, each with one directory.
    The trace service takes only NAME."""
    questions = {n for n in compose["services"] if n.startswith("rq")}
    assert questions == set(QUESTIONS)
    directories = sorted(p.name for p in EVALUATION.glob("rq*") if p.is_dir())
    assert [d for d in directories if (EVALUATION / d / "README.md").is_file()] == [
        "rq1-accuracy",
        "rq2-causes",
        "rq3-scale",
    ]
    assert compose["services"]["trace"]["entrypoint"][4:] == ["${NAME:-unset}"]
    assert "SET" not in re.findall(r"\$\{(\w+)", COMPOSE.read_text())


def test_the_trace_service_cannot_write_a_table(compose):
    """The trace service runs a project's test suite: third-party code. It
    gets only its own directory of results, never the tables (found by the
    security scan of 2026-11-14)."""
    writable = [
        volume
        for volume in compose["services"]["trace"]["volumes"]
        if not volume.endswith(":ro")
    ]
    assert writable == ["./results/trace:/results/trace"]
    assert (EVALUATION / "results" / "trace" / ".gitkeep").is_file()


def test_rq3_has_one_memory_limit_for_every_tool(compose):
    """The maintainer's number (2026-11-15): 16 GiB per analysis for PyCG and
    for every prior tool on RQ3 and the traces, each service above it."""
    limit = 16384
    entry = (EVALUATION / "container" / "question_entry.py").read_text()
    assert int(re.search(r"ANALYSIS_MEMORY_GIB = (\d+)", entry)[1]) * 1024 == limit
    tool_job = (EVALUATION / "container" / "tool_job.py").read_text()
    limits = re.findall(r"\(1800, (\d+)\)", tool_job)
    assert limits and {int(found) for found in limits} == {limit}
    questions = ("rq1-trace", "rq3", "rq3-pilot")
    tools = ("pyan", "jarvis", "cpygraph", "pyanalyzer")
    for name in (*questions, *tools):
        # EVAL_MEMORY, for --jobs K; its default holds one analysis.
        memory = compose["services"][name]["mem_limit"]
        assert memory == compose["services"][name]["memswap_limit"], name
        default = re.fullmatch(r"\$\{EVAL_MEMORY:-(\d+)g\}", memory)
        assert default and int(default[1]) * 1024 > limit, name
