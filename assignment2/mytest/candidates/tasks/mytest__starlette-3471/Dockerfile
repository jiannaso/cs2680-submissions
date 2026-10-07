# mytest task mytest__starlette-3471: https://github.com/encode/starlette.git at 04d7869c1ec3016fa11f58382a9ac7bc14b46662 (parent of the upstream fix 6ad24bb7809c2d833445d5b1cfb9e95c26260e56)
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone -q --filter=blob:none --no-checkout https://github.com/encode/starlette.git /app \
    && git -C /app checkout -q 04d7869c1ec3016fa11f58382a9ac7bc14b46662 \
    # Keep only the base snapshot: no later commits (the fix) are reachable.
    && rm -rf /app/.git \
    && git -C /app init -q \
    && git -C /app add -A \
    && git -C /app -c user.email=mytest@local -c user.name=mytest commit -qm "base 04d7869c1ec3016fa11f58382a9ac7bc14b46662"
WORKDIR /app
RUN pip install --no-cache-dir -e . "itsdangerous" "jinja2" "opentelemetry-api" "opentelemetry-sdk" "python-multipart>=0.0.18" "pyyaml" "httpx>=0.27.0,<0.29.0" "httpx2[zstd]>=2.0.0" "pytest" "trio" "importlib-metadata" "anyio==4.14.2"
