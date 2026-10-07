# mytest task mytest__python-dotenv-711: https://github.com/theskumar/python-dotenv.git at 828bdfe5ba91b736b545561ebb53326f1f88f6e5 (parent of the upstream fix 6a49d6d88fd9bd99bef8d63b9eb6f2e46ecc7072)
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone -q --filter=blob:none --no-checkout https://github.com/theskumar/python-dotenv.git /app \
    && git -C /app checkout -q 828bdfe5ba91b736b545561ebb53326f1f88f6e5 \
    # Keep only the base snapshot: no later commits (the fix) are reachable.
    && rm -rf /app/.git \
    && git -C /app init -q \
    && git -C /app add -A \
    && git -C /app -c user.email=mytest@local -c user.name=mytest commit -qm "base 828bdfe5ba91b736b545561ebb53326f1f88f6e5"
WORKDIR /app
RUN pip install --no-cache-dir -e . "click>=5.0" "pytest" "sh"
