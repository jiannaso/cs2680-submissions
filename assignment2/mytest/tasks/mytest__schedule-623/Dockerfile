# mytest task mytest__schedule-623: https://github.com/dbader/schedule.git at 4386f4562df3ed24fd198324e279d215db2154d6 (parent of the upstream fix 2dcb5833cdf2b7d7a1bda90c19e2fb7e373e66df)
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone -q --filter=blob:none --no-checkout https://github.com/dbader/schedule.git /app \
    && git -C /app checkout -q 4386f4562df3ed24fd198324e279d215db2154d6 \
    # Keep only the base snapshot: no later commits (the fix) are reachable.
    && rm -rf /app/.git \
    && git -C /app init -q \
    && git -C /app add -A \
    && git -C /app -c user.email=mytest@local -c user.name=mytest commit -qm "base 4386f4562df3ed24fd198324e279d215db2154d6"
WORKDIR /app
RUN pip install --no-cache-dir -e . "pytest" "pytz" "mock"
