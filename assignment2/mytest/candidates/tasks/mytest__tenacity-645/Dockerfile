# mytest task mytest__tenacity-645: https://github.com/jd/tenacity.git at 949dcfa6c0a4f0109dbbd019ff58208a580d185b (parent of the upstream fix 26f719dc73d3c5612b9c1b8d18a7883837790ad8)
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone -q --filter=blob:none --no-checkout https://github.com/jd/tenacity.git /app \
    && git -C /app checkout -q 949dcfa6c0a4f0109dbbd019ff58208a580d185b \
    # Keep only the base snapshot: no later commits (the fix) are reachable.
    && rm -rf /app/.git \
    && git -C /app init -q \
    && git -C /app add -A \
    && git -C /app -c user.email=mytest@local -c user.name=mytest commit -qm "base 949dcfa6c0a4f0109dbbd019ff58208a580d185b"
WORKDIR /app
RUN pip install --no-cache-dir -e . "pytest" "tornado>=6.0" "typeguard"
