# mytest task mytest__tenacity-643: https://github.com/jd/tenacity.git at a49e9d6dd4e4611238dc277263d1975e1cada21e (parent of the upstream fix 868c60b80cd84273bf15a8b3f5c2026776728e5f)
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone -q --filter=blob:none --no-checkout https://github.com/jd/tenacity.git /app \
    && git -C /app checkout -q a49e9d6dd4e4611238dc277263d1975e1cada21e \
    # Keep only the base snapshot: no later commits (the fix) are reachable.
    && rm -rf /app/.git \
    && git -C /app init -q \
    && git -C /app add -A \
    && git -C /app -c user.email=mytest@local -c user.name=mytest commit -qm "base a49e9d6dd4e4611238dc277263d1975e1cada21e"
WORKDIR /app
RUN pip install --no-cache-dir -e . "pytest" "tornado>=6.0" "typeguard"
