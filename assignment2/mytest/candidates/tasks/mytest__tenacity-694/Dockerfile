# mytest task mytest__tenacity-694: https://github.com/jd/tenacity.git at a09999688c8d3bb3d4ca1716748009954cc7c4d0 (parent of the upstream fix 389aab8b2688f7a1df41c2dd7797eeb18c8baff9)
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone -q --filter=blob:none --no-checkout https://github.com/jd/tenacity.git /app \
    && git -C /app checkout -q a09999688c8d3bb3d4ca1716748009954cc7c4d0 \
    # Keep only the base snapshot: no later commits (the fix) are reachable.
    && rm -rf /app/.git \
    && git -C /app init -q \
    && git -C /app add -A \
    && git -C /app -c user.email=mytest@local -c user.name=mytest commit -qm "base a09999688c8d3bb3d4ca1716748009954cc7c4d0"
WORKDIR /app
RUN pip install --no-cache-dir -e . "pytest" "tornado>=6.0" "typeguard"
