# mytest task mytest__react-use-uselatest: https://github.com/streamich/react-use.git at b3d14e2c71852e95ba6b40414caf5bf5131fef2e (parent of the upstream fix d6fe2676153f19302ce170b03ceadc3bab5a945a)
FROM node:14-bullseye
RUN git clone -q --filter=blob:none --no-checkout https://github.com/streamich/react-use.git /app \
    && git -C /app checkout -q b3d14e2c71852e95ba6b40414caf5bf5131fef2e \
    # Keep only the base snapshot: no later commits (the fix) are reachable.
    && rm -rf /app/.git \
    && git -C /app init -q \
    && git -C /app add -A \
    && git -C /app -c user.email=mytest@local -c user.name=mytest commit -qm "base b3d14e2c71852e95ba6b40414caf5bf5131fef2e"
WORKDIR /app
RUN yarn install --frozen-lockfile --ignore-scripts --network-timeout 600000 && yarn cache clean
# The harness (madsLoop) needs Python >= 3.10; Debian bullseye ships 3.9 without pip (and
# its apt archive is end-of-life), so install a standalone CPython with uv instead.
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh \
    && UV_PYTHON_INSTALL_DIR=/opt/uv-python uv python install 3.12 \
    && ln -s "$(UV_PYTHON_INSTALL_DIR=/opt/uv-python uv python find 3.12)" /usr/local/bin/python3.12
