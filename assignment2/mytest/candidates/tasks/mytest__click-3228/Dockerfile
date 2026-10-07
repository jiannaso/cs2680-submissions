# mytest task mytest__click-3228: https://github.com/pallets/click.git at 19fd4d6e18bc9fce451f92f422696b11169faa57 (parent of the upstream fix 831c8f0948af519e45b90801d7430ff25451f972)
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone -q --filter=blob:none --no-checkout https://github.com/pallets/click.git /app \
    && git -C /app checkout -q 19fd4d6e18bc9fce451f92f422696b11169faa57 \
    # Keep only the base snapshot: no later commits (the fix) are reachable.
    && rm -rf /app/.git \
    && git -C /app init -q \
    && git -C /app add -A \
    && git -C /app -c user.email=mytest@local -c user.name=mytest commit -qm "base 19fd4d6e18bc9fce451f92f422696b11169faa57"
WORKDIR /app
RUN pip install --no-cache-dir -e . "pytest"
