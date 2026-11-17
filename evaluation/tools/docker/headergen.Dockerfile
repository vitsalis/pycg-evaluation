# The image of HeaderGen 1ea5226 (evaluation/tools/headergen.py), a hybrid
# tool: it imports the modules that the analysed code imports. It holds
# CPython 3.12 from the official image, and:
# - /opt/tool/lib: the distributions of headergen-requirements.txt, by hash
#   (written by pin.py with an upload cutoff of 2024-10-19);
# - /opt/tool/headergen: pycg_extended, beniget and typestub-database of the
#   pinned commit, with stdlib/ and stubs/ of its typeshed submodule at
#   be0ef21 (/inputs/tools/headergen and /inputs/tools/typeshed of the
#   inputs image, the additional context `inputs` of evaluation/compose.yaml;
#   each checked against pins.json); NOT its framework_models, its server, setup.sh or
#   Dockerfile;
# - /opt/tool/shim/framework_models: our shim (headergen-shim/), with the
#   pinned aliases/*.json;
# - the stub cache that HeaderGen writes at its first import
#   (pycg_extended/machinery/pytd_cache.pickle), built here, read-only at
#   run time (owned by root, and the root file system is read-only);
# - /opt/tool/image.txt, the pin and the trees.
# The driver is copied per job. Network at build time only: pypi.org and
# files.pythonhosted.org.
ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}
ARG TOOL_PIN
ARG TREES
ARG TREE_HEADERGEN
ARG TREE_TYPESHED
RUN test -n "${TOOL_PIN}" && test -n "${TREES}" \
    && test -n "${TREE_HEADERGEN}" && test -n "${TREE_TYPESHED}"

COPY tools/headergen-requirements.txt /opt/tool/requirements.txt
COPY tools/image_checks.py /opt/build/image_checks.py
COPY --from=inputs /inputs/tools/headergen/pycg_extended /opt/tree/headergen/pycg_extended
COPY --from=inputs /inputs/tools/headergen/beniget /opt/tree/headergen/beniget
COPY --from=inputs /inputs/tools/headergen/typestub-database \
    /opt/tree/headergen/typestub-database
COPY --from=inputs /inputs/tools/headergen/framework_models/aliases \
    /opt/tree/headergen/framework_models/aliases
COPY --from=inputs /inputs/tools/typeshed/stdlib /opt/tree/typeshed/stdlib
COPY --from=inputs /inputs/tools/typeshed/stubs /opt/tree/typeshed/stubs
COPY tools/headergen-shim /opt/tool/shim

# The cache build imports the analysis as the driver will: our shim first,
# then the tree, the dependencies as a site directory.
RUN python3 -I /opt/build/image_checks.py tree /opt/tree/headergen "${TREE_HEADERGEN}" \
    && python3 -I /opt/build/image_checks.py tree /opt/tree/typeshed "${TREE_TYPESHED}" \
    && mkdir -p /opt/tool/headergen /opt/tool/shim/framework_models \
    && mv /opt/tree/headergen/pycg_extended /opt/tree/headergen/beniget \
        /opt/tree/headergen/typestub-database /opt/tool/headergen/ \
    && mv /opt/tree/typeshed /opt/tool/headergen/typestub-database/typeshed \
    && mv /opt/tree/headergen/framework_models/aliases \
        /opt/tool/shim/framework_models/aliases \
    && rm -r /opt/tree /opt/build \
    && env -i PATH=/usr/local/bin:/usr/bin:/bin PIP_CONFIG_FILE=/dev/null \
        python3 -I -m pip install --require-hashes --only-binary :all: --no-deps \
        --no-cache-dir --disable-pip-version-check --no-input \
        --index-url https://pypi.org/simple --target /opt/tool/lib \
        -r /opt/tool/requirements.txt \
    && env -i PATH=/usr/local/bin:/usr/bin:/bin PIP_CONFIG_FILE=/dev/null \
        python3 -I -m pip uninstall --yes --disable-pip-version-check pip \
    && test ! -e /opt/tool/headergen/framework_models \
    && env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp PYTHONHASHSEED=0 \
        python3 -S -s -P -B -c "import site, sys; site.addsitedir('/opt/tool/lib'); sys.path[:0] = ['/opt/tool/shim', '/opt/tool/headergen']; import framework_models; assert framework_models.__file__.startswith('/opt/tool/shim/'), framework_models.__file__; import pycg_extended.machinery.typestubs; from pycg_extended import formats; from pycg_extended.pycg import CallGraphGenerator" \
        > /dev/null \
    && test -s /opt/tool/headergen/pycg_extended/machinery/pytd_cache.pickle \
    && printf '%s\n%s\n' "${TOOL_PIN}" "${TREES}" > /opt/tool/image.txt \
    && mkdir -m 1777 /tool-graphs \
    && chmod -R a+rX,go-w /opt/tool \
    && groupadd --gid 10001 runner \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp \
        --shell /usr/sbin/nologin runner

# The sources of the macro-benchmark, from the inputs image: HeaderGen runs
# on rq1 only. Last, so that a change of them does not build the tool again.
COPY --from=inputs /inputs/macro-benchmark /inputs/macro-benchmark

USER 10001:10001
WORKDIR /tmp
