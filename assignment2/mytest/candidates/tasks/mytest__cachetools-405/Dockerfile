# mytest task mytest__cachetools-405: https://github.com/tkem/cachetools.git at 16e88894ef7d79b25a68f5a3b5411ed881342725 (parent of the upstream fix 39b31bc9b63abe98497409945e9d382d8918c8fb)
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone -q --filter=blob:none --no-checkout https://github.com/tkem/cachetools.git /app \
    && git -C /app checkout -q 16e88894ef7d79b25a68f5a3b5411ed881342725 \
    # Keep only the base snapshot: no later commits (the fix) are reachable.
    && rm -rf /app/.git \
    && git -C /app init -q \
    && git -C /app add -A \
    && git -C /app -c user.email=mytest@local -c user.name=mytest commit -qm "base 16e88894ef7d79b25a68f5a3b5411ed881342725"
WORKDIR /app
RUN pip install --no-cache-dir -e . "pytest"
