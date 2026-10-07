# mytest task mytest__boltons-513: https://github.com/mahmoud/boltons.git at 961dcff3f42e73b245aef65e377fe82763b257bb (parent of the upstream fix 4e5faa3d7e4008d89e0d8bf1ea87b6d9a061a16d)
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone -q --filter=blob:none --no-checkout https://github.com/mahmoud/boltons.git /app \
    && git -C /app checkout -q 961dcff3f42e73b245aef65e377fe82763b257bb \
    # Keep only the base snapshot: no later commits (the fix) are reachable.
    && rm -rf /app/.git \
    && git -C /app init -q \
    && git -C /app add -A \
    && git -C /app -c user.email=mytest@local -c user.name=mytest commit -qm "base 961dcff3f42e73b245aef65e377fe82763b257bb"
WORKDIR /app
RUN pip install --no-cache-dir -e . "pytest"
