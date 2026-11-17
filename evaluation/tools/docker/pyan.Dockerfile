# The image of pyan3 v2.9.0 (evaluation/tools/pyan.py). It holds CPython
# from the official image and the hash-pinned wheels of
# pyan-requirements.txt in /opt/tool/lib, and the inputs that its jobs
# read in /inputs: no code of ours (the driver is copied per job by
# evaluation/container/tool_job.py, which runs from the repository that the
# service mounts).
#
# evaluation/compose.yaml gives PYTHON_IMAGE (pins.json, by digest) and
# TOOL_PIN; the build fails without them. /opt/tool/image.txt holds the pin,
# which the driver checks. Each job runs in a directory of its own under
# /tmp (container/tool_job.py, work_of). Network at build time only:
# pypi.org and files.pythonhosted.org, for the wheels.
ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}
ARG TOOL_PIN
ARG TREES=""
RUN test -n "${TOOL_PIN}"

COPY tools/pyan-requirements.txt /opt/tool/requirements.txt

# pip runs with no configuration and no variable of the image's environment:
# wheels only, every file by hash, no resolution. The probe imports the tool
# as the driver will (-S -s -P -B). Then pip is removed.
RUN env -i PATH=/usr/local/bin:/usr/bin:/bin PIP_CONFIG_FILE=/dev/null \
        python3 -I -m pip install --require-hashes --only-binary :all: --no-deps \
        --no-cache-dir --disable-pip-version-check --no-input \
        --index-url https://pypi.org/simple --target /opt/tool/lib \
        -r /opt/tool/requirements.txt \
    && env -i PATH=/usr/local/bin:/usr/bin:/bin python3 -S -s -P -B -c \
        "import sys; sys.path[:0] = ['/opt/tool/lib']; import pyan, pyan.analyzer; assert pyan.__version__ == '2.9.0', pyan.__version__" \
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
