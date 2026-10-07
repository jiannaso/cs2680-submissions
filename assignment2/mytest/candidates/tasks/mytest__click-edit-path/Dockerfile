# mytest task mytest__click-edit-path: https://github.com/pallets/click.git at 2103e157683c5e4cadc8ee1838df526a54bde9a4 (parent of the upstream fix a1d87858abd77fa8e3ffc204670ccd75b96c4781)
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone -q --filter=blob:none --no-checkout https://github.com/pallets/click.git /app \
    && git -C /app checkout -q 2103e157683c5e4cadc8ee1838df526a54bde9a4 \
    # Keep only the base snapshot: no later commits (the fix) are reachable.
    && rm -rf /app/.git \
    && git -C /app init -q \
    && git -C /app add -A \
    && git -C /app -c user.email=mytest@local -c user.name=mytest commit -qm "base 2103e157683c5e4cadc8ee1838df526a54bde9a4"
WORKDIR /app
RUN pip install --no-cache-dir -e . "pytest"
