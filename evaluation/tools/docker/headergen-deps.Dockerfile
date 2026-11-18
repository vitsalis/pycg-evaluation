# The image of HeaderGen for one macro project with runtime dependencies
# (projects.toml `dependencies`): the HeaderGen image (TOOL_IMAGE, the image
# of the service headergen, built first) with that project's pinned
# dependencies (tools/headergen-deps/<project>.txt, the build context
# and REQUIREMENTS of the service headergen-deps; from pin.py deps: the
# versions of its trace environment, resolved for CPython 3.12 against
# HeaderGen's own pins, without the distributions that HeaderGen's image
# has) in /opt/deps, which the driver adds as a site directory after
# HeaderGen's own: the tool's environment wins. HeaderGen imports the
# project's modules, and a module whose import fails is outside code for
# it, so the job needs them. pip runs in a first stage on the plain Python
# image (PYTHON_IMAGE, by digest, the base of the HeaderGen image), so the
# run image has no pip. /opt/deps/requirements.txt is the pinned file, which
# the driver compares with the repository's.
#
# Network at build time only: pypi.org and files.pythonhosted.org.
ARG PYTHON_IMAGE
ARG TOOL_IMAGE
FROM ${PYTHON_IMAGE} AS deps
ARG REQUIREMENTS
COPY ${REQUIREMENTS} /opt/deps-requirements.txt
RUN env -i PATH=/usr/local/bin:/usr/bin:/bin PIP_CONFIG_FILE=/dev/null \
        python3 -I -m pip install --require-hashes --only-binary :all: --no-deps \
        --no-cache-dir --disable-pip-version-check --no-input \
        --index-url https://pypi.org/simple --target /opt/deps \
        -r /opt/deps-requirements.txt \
    && cp /opt/deps-requirements.txt /opt/deps/requirements.txt \
    && chmod -R a+rX,go-w /opt/deps

FROM ${TOOL_IMAGE}
COPY --from=deps /opt/deps /opt/deps
