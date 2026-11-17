# The image of PyAnalyzer ea143ef (evaluation/tools/pyanalyzer.py), a static
# tool with no dependency. It holds CPython 3.13 from the official image and
# the directory SourceCode/pyanalyzer of the pinned commit in
# /opt/tool/pyanalyzer (from the inputs image, the additional context
# `inputs` of evaluation/compose.yaml; checked against pins.json): never its .exe, its .jar files, Scripts/ or Data/. No PyPI:
# the build fetches nothing but the base image. The driver is copied per
# job. /opt/tool/image.txt holds the pin and the trees.
ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}
ARG TOOL_PIN
ARG TREES
ARG TREE_SOURCE
RUN test -n "${TOOL_PIN}" && test -n "${TREES}" && test -n "${TREE_SOURCE}"

COPY tools/image_checks.py /opt/build/image_checks.py
COPY --from=inputs /inputs/tools/pyanalyzer/SourceCode/pyanalyzer \
    /opt/tree/source/SourceCode/pyanalyzer

# The probe imports the tool's command line as the driver will.
RUN python3 -I /opt/build/image_checks.py tree /opt/tree/source "${TREE_SOURCE}" \
    && mkdir -p /opt/tool \
    && mv /opt/tree/source/SourceCode/pyanalyzer /opt/tool/pyanalyzer \
    && rm -r /opt/tree /opt/build \
    && env -i PATH=/usr/local/bin:/usr/bin:/bin python3 -S -s -P -B -c \
        "import sys; sys.path[:0] = ['/opt/tool']; import pyanalyzer.__main__" \
    && env -i PATH=/usr/local/bin:/usr/bin:/bin PIP_CONFIG_FILE=/dev/null \
        python3 -I -m pip uninstall --yes --disable-pip-version-check pip \
    && printf '%s\n%s\n' "${TOOL_PIN}" "${TREES}" > /opt/tool/image.txt \
    && mkdir -m 1777 /tool-graphs \
    && chmod -R a+rX,go-w /opt/tool \
    && groupadd --gid 10001 runner \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp \
        --shell /usr/sbin/nologin runner

# From the inputs image, what the jobs of the tool read (container/tool_job.py):
# rq1 the sources of the macro-benchmark; rq3, rq3-pilot and rq1-trace the
# corpus, its archives (which robustness.plan checks) and its unpacked
# trees. Not DyPyBench's traces: only the question reads them.
# Last, so that a change of them does not build the tool again.
COPY --from=inputs /inputs/macro-benchmark /inputs/macro-benchmark
COPY --from=inputs /inputs/corpus/cache /inputs/corpus/cache
COPY --from=inputs /inputs/corpus/work /inputs/corpus/work
COPY --from=inputs /inputs/corpus/dypybench /inputs/corpus/dypybench
COPY --from=inputs /inputs/corpus/large /inputs/corpus/large

USER 10001:10001
WORKDIR /tmp
