# mytest task mytest__schedule-604: https://github.com/dbader/schedule.git at 073dbc6e5f8254d27c656f88a5062cca2cdfc4a2 (parent of the upstream fix a38862005fb775521d5482e5be4affcd10eebb15)
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone -q --filter=blob:none --no-checkout https://github.com/dbader/schedule.git /app \
    && git -C /app checkout -q 073dbc6e5f8254d27c656f88a5062cca2cdfc4a2 \
    # Keep only the base snapshot: no later commits (the fix) are reachable.
    && rm -rf /app/.git \
    && git -C /app init -q \
    && git -C /app add -A \
    && git -C /app -c user.email=mytest@local -c user.name=mytest commit -qm "base 073dbc6e5f8254d27c656f88a5062cca2cdfc4a2"
WORKDIR /app
RUN pip install --no-cache-dir -e . "pytest" "pytz" "mock"
