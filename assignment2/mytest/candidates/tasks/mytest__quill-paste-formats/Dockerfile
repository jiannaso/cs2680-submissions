# mytest task mytest__quill-paste-formats: https://github.com/slab/quill.git at f8bfde4119c2c6a193ae7d435d4f11c6d764e27f (parent of the upstream fix 8fc9bb78)
# Quill's unit tests run in Vitest browser mode (Playwright Chromium), so the image also installs Chromium.
FROM node:20-bookworm
RUN git clone -q --filter=blob:none --no-checkout https://github.com/slab/quill.git /app \
    && git -C /app checkout -q f8bfde4119c2c6a193ae7d435d4f11c6d764e27f \
    # Keep only the base snapshot: no later commits (the fix) are reachable.
    && rm -rf /app/.git \
    && git -C /app init -q \
    && git -C /app add -A \
    && git -C /app -c user.email=mytest@local -c user.name=mytest commit -qm "base f8bfde4119c2c6a193ae7d435d4f11c6d764e27f"
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
