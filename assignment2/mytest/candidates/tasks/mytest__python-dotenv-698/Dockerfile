# mytest task mytest__python-dotenv-698: https://github.com/theskumar/python-dotenv.git at a00cb2eed0704cd6d2071b2004c37e95ccc86ee5 (parent of the upstream fix e0310e5bb3f2b701b11bbc007ca0c82c1bd56f38)
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone -q --filter=blob:none --no-checkout https://github.com/theskumar/python-dotenv.git /app \
    && git -C /app checkout -q a00cb2eed0704cd6d2071b2004c37e95ccc86ee5 \
    # Keep only the base snapshot: no later commits (the fix) are reachable.
    && rm -rf /app/.git \
    && git -C /app init -q \
    && git -C /app add -A \
    && git -C /app -c user.email=mytest@local -c user.name=mytest commit -qm "base a00cb2eed0704cd6d2071b2004c37e95ccc86ee5"
WORKDIR /app
RUN pip install --no-cache-dir -e . "click>=5.0" "pytest" "sh"
