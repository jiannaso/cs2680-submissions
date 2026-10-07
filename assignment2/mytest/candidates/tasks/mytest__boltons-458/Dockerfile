# mytest task mytest__boltons-458: https://github.com/mahmoud/boltons.git at d45cc414e33aa4e08a73e0993574df4397e2e9db (parent of the upstream fix a1ea1030ed85137e999c0bd9b11d16368d3ab241)
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone -q --filter=blob:none --no-checkout https://github.com/mahmoud/boltons.git /app \
    && git -C /app checkout -q d45cc414e33aa4e08a73e0993574df4397e2e9db \
    # Keep only the base snapshot: no later commits (the fix) are reachable.
    && rm -rf /app/.git \
    && git -C /app init -q \
    && git -C /app add -A \
    && git -C /app -c user.email=mytest@local -c user.name=mytest commit -qm "base d45cc414e33aa4e08a73e0993574df4397e2e9db"
WORKDIR /app
RUN pip install --no-cache-dir -e . "pytest"
