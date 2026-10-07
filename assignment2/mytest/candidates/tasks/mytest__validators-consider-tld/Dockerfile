# mytest task mytest__validators-consider-tld: https://github.com/python-validators/validators.git at 6b970dc8083016bae258ddb9db563b224268ed6e (parent of the upstream fix ebcea683d52d844dc6f3c6053de01d3d23779e52)
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone -q --filter=blob:none --no-checkout https://github.com/python-validators/validators.git /app \
    && git -C /app checkout -q 6b970dc8083016bae258ddb9db563b224268ed6e \
    # Keep only the base snapshot: no later commits (the fix) are reachable.
    && rm -rf /app/.git \
    && git -C /app init -q \
    && git -C /app add -A \
    && git -C /app -c user.email=mytest@local -c user.name=mytest commit -qm "base 6b970dc8083016bae258ddb9db563b224268ed6e"
WORKDIR /app
RUN pip install --no-cache-dir -e . "pytest"
