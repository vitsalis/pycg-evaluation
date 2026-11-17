# The image of JARVIS 8871b86 (evaluation/tools/jarvis.py). It holds CPython
# 3.13 from the official image (JARVIS uses ast.Num, gone in 3.14), the tree
# Jarvis/tool/Jarvis of the pinned commit in /opt/tool/src (from the inputs
# image, the additional context `inputs` of evaluation/compose.yaml; checked
# against its SHA-256 in pins.json) and setuptools 80.9.0
# (pkg_resources) by hash in /opt/tool/lib; the inputs that its jobs
# read in /inputs. No code of ours (the driver is copied per job). /opt/tool/image.txt holds the pin and the
# trees.
#
# Network at build time only: pypi.org and files.pythonhosted.org.
ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}
ARG TOOL_PIN
ARG TREES
ARG TREE_SOURCE
RUN test -n "${TOOL_PIN}" && test -n "${TREES}" && test -n "${TREE_SOURCE}"

COPY tools/jarvis-requirements.txt /opt/tool/requirements.txt
COPY tools/image_checks.py /opt/build/image_checks.py
COPY --from=inputs /inputs/tools/jarvis/Jarvis/tool/Jarvis /opt/tree/source

# The probe imports the tool's modules as the driver will (-S -s -P -B), so
# their module-level code runs here, in the build container.
RUN python3 -I /opt/build/image_checks.py tree /opt/tree/source "${TREE_SOURCE}" \
    && mv /opt/tree/source /opt/tool/src && rm -r /opt/tree /opt/build \
    && env -i PATH=/usr/local/bin:/usr/bin:/bin PIP_CONFIG_FILE=/dev/null \
        python3 -I -m pip install --require-hashes --only-binary :all: --no-deps \
        --no-cache-dir --disable-pip-version-check --no-input \
        --index-url https://pypi.org/simple --target /opt/tool/lib \
        -r /opt/tool/requirements.txt \
    && env -i PATH=/usr/local/bin:/usr/bin:/bin python3 -S -s -P -B -c \
        "import sys; sys.path[:0] = ['/opt/tool/src', '/opt/tool/lib']; import pkg_resources, jarvis, jarvis_cli, formats" \
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
# trees; rq4 and rq4-pilot the file lists and blobs of the export. Not
# DyPyBench's traces nor NameRTS's files: only the questions read them.
# Last, so that a change of them does not build the tool again.
COPY --from=inputs /inputs/macro-benchmark /inputs/macro-benchmark
COPY --from=inputs /inputs/corpus/cache /inputs/corpus/cache
COPY --from=inputs /inputs/corpus/work /inputs/corpus/work
COPY --from=inputs /inputs/corpus/dypybench /inputs/corpus/dypybench
COPY --from=inputs /inputs/corpus/large /inputs/corpus/large
COPY --from=inputs /inputs/rts/export/trees /inputs/rts/export/trees
COPY --from=inputs /inputs/rts/export/blobs /inputs/rts/export/blobs

USER 10001:10001
WORKDIR /tmp
