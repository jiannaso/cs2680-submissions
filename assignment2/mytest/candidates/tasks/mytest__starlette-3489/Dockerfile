# mytest task mytest__starlette-3489: https://github.com/encode/starlette.git at 723de8254c606ac7dbed7b7cf21badb86cc4876c (parent of the upstream fix e4bd5981540ee0003deaae69d7f4881f11714b96)
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone -q --filter=blob:none --no-checkout https://github.com/encode/starlette.git /app \
    && git -C /app checkout -q 723de8254c606ac7dbed7b7cf21badb86cc4876c \
    # Keep only the base snapshot: no later commits (the fix) are reachable.
    && rm -rf /app/.git \
    && git -C /app init -q \
    && git -C /app add -A \
    && git -C /app -c user.email=mytest@local -c user.name=mytest commit -qm "base 723de8254c606ac7dbed7b7cf21badb86cc4876c"
WORKDIR /app
RUN pip install --no-cache-dir -e . "itsdangerous" "jinja2" "opentelemetry-api" "opentelemetry-sdk" "python-multipart>=0.0.18" "pyyaml" "httpx>=0.27.0,<0.29.0" "httpx2[zstd]>=2.0.0" "pytest" "trio" "importlib-metadata" "anyio==4.14.2"
