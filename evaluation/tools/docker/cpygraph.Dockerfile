# The image of CPyGraph v0.1.0 (evaluation/tools/cpygraph.py). The base is
# the full official Python 3.14 image: it has a C++17 compiler, make and
# CPython's headers and shared library, which pygCG embeds. CMake comes from
# PyPI by hash (cpygraph-requirements.txt) into /opt/cmake. The pinned tree
# (/inputs/tools/cpygraph of the inputs image, the additional context
# `inputs` of evaluation/compose.yaml; checked against pins.json) is built in
# /opt/tool/build with CPYGRAPH_ABLATION=none; image_checks.py (ours) checks
# that CMake chose /usr/local/bin/python3.14 and no ablation, and that this
# interpreter has no startup code (.pth, sitecustomize), as pygCG starts it
# without -S. The inputs that its jobs read are in /inputs; the driver is
# copied per job.
# /opt/tool/image.txt holds the pin and the trees.
#
# Network at build time only: pypi.org and files.pythonhosted.org (CMake).
ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}
ARG TOOL_PIN
ARG TREES
ARG TREE_SOURCE
RUN test -n "${TOOL_PIN}" && test -n "${TREES}" && test -n "${TREE_SOURCE}"

COPY tools/cpygraph-requirements.txt /opt/cmake-requirements.txt
COPY tools/image_checks.py /opt/build/image_checks.py
COPY --from=inputs /inputs/tools/cpygraph /opt/tree/source

RUN python3.14 -I /opt/build/image_checks.py tree /opt/tree/source "${TREE_SOURCE}" \
    && mkdir -p /opt/tool && mv /opt/tree/source /opt/tool/source && rm -r /opt/tree \
    && env -i PATH=/usr/local/bin:/usr/bin:/bin PIP_CONFIG_FILE=/dev/null \
        python3 -I -m pip install --require-hashes --only-binary :all: --no-deps \
        --no-cache-dir --disable-pip-version-check --no-input \
        --index-url https://pypi.org/simple --target /opt/cmake \
        -r /opt/cmake-requirements.txt \
    && env -i PATH=/usr/local/bin:/usr/bin:/bin PIP_CONFIG_FILE=/dev/null \
        python3 -I -m pip uninstall --yes --disable-pip-version-check pip \
    && python3.14 -I /opt/build/image_checks.py site /usr/local/bin/python3.14 \
    && env -i PATH=/usr/local/bin:/usr/bin:/bin /opt/cmake/cmake/data/bin/cmake \
        -S /opt/tool/source -B /opt/tool/build \
        -DCMAKE_BUILD_TYPE=Release -DCPYGRAPH_BUILD_TOOLS=ON -DBUILD_TESTING=OFF \
        -DCPYGRAPH_ABLATION=none -DPython3_EXECUTABLE=/usr/local/bin/python3.14 \
    && python3.14 -I /opt/build/image_checks.py cmake-cache \
        /opt/tool/build/CMakeCache.txt /usr/local/bin/python3.14 \
    && env -i PATH=/usr/local/bin:/usr/bin:/bin /opt/cmake/cmake/data/bin/cmake \
        --build /opt/tool/build --target pygCG -j 4 \
    && test -x /opt/tool/build/tools/pygCG \
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
