# mytest task mytest__tenacity-597: https://github.com/jd/tenacity.git at 213446e8f5f73ca0d20e22ec8ee38b1f75a45434 (parent of the upstream fix 70083b802cd37f15ad5a7bece152fc2d8e7ed4ec)
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone -q --filter=blob:none --no-checkout https://github.com/jd/tenacity.git /app \
    && git -C /app checkout -q 213446e8f5f73ca0d20e22ec8ee38b1f75a45434 \
    # Keep only the base snapshot: no later commits (the fix) are reachable.
    && rm -rf /app/.git \
    && git -C /app init -q \
    && git -C /app add -A \
    && git -C /app -c user.email=mytest@local -c user.name=mytest commit -qm "base 213446e8f5f73ca0d20e22ec8ee38b1f75a45434"
WORKDIR /app
RUN pip install --no-cache-dir -e . "pytest" "tornado>=6.0" "typeguard"
