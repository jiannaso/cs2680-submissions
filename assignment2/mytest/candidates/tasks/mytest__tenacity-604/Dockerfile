# mytest task mytest__tenacity-604: https://github.com/jd/tenacity.git at 124e3a67c7a9b68f46df39d2a7b1a5e8d15270c1 (parent of the upstream fix 39eb157a686a8642646807c64d0791de35a0910a)
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone -q --filter=blob:none --no-checkout https://github.com/jd/tenacity.git /app \
    && git -C /app checkout -q 124e3a67c7a9b68f46df39d2a7b1a5e8d15270c1 \
    # Keep only the base snapshot: no later commits (the fix) are reachable.
    && rm -rf /app/.git \
    && git -C /app init -q \
    && git -C /app add -A \
    && git -C /app -c user.email=mytest@local -c user.name=mytest commit -qm "base 124e3a67c7a9b68f46df39d2a7b1a5e8d15270c1"
WORKDIR /app
RUN pip install --no-cache-dir -e . "pytest" "tornado>=6.0" "typeguard"
