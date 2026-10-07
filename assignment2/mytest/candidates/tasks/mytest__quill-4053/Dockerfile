# mytest task mytest__quill-4053: https://github.com/slab/quill.git at db7455a4a56e44f5426f59339612794cb94c6cdb (parent of the upstream fix c7d4d81d)
# Quill's unit tests run in Vitest browser mode (Playwright Chromium), so the image also installs Chromium.
FROM node:20-bookworm
RUN git clone -q --filter=blob:none --no-checkout https://github.com/slab/quill.git /app \
    && git -C /app checkout -q db7455a4a56e44f5426f59339612794cb94c6cdb \
    # Keep only the base snapshot: no later commits (the fix) are reachable.
    && rm -rf /app/.git \
    && git -C /app init -q \
    && git -C /app add -A \
    && git -C /app -c user.email=mytest@local -c user.name=mytest commit -qm "base db7455a4a56e44f5426f59339612794cb94c6cdb"
WORKDIR /app
# Vitest browser mode runs Chromium headless only when CI is set (there is no display).
ENV CI=true
# Only the quill workspace (the website workspace is not needed for its tests).
RUN npm ci --workspace quill --include-workspace-root --ignore-scripts --no-audit --no-fund \
    && npx --workspace quill playwright install --with-deps chromium \
    && npm cache clean --force
# The harness (madsLoop) needs Python >= 3.10.
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh \
    && UV_PYTHON_INSTALL_DIR=/opt/uv-python uv python install 3.12 \
    && ln -s "$(UV_PYTHON_INSTALL_DIR=/opt/uv-python uv python find 3.12)" /usr/local/bin/python3.12
