# mytest task mytest__tldextract-344: https://github.com/john-kurkowski/tldextract.git at ad256d9003e6d26e2d2b04b0525d823ba1abc8fb (parent of the upstream fix a4a02b1d04870e443b257198b97066784b75035e)
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone -q --filter=blob:none --no-checkout https://github.com/john-kurkowski/tldextract.git /app \
    && git -C /app checkout -q ad256d9003e6d26e2d2b04b0525d823ba1abc8fb \
    # Keep only the base snapshot: no later commits (the fix) are reachable.
    && rm -rf /app/.git \
    && git -C /app init -q \
    && git -C /app add -A \
    && git -C /app -c user.email=mytest@local -c user.name=mytest commit -qm "base ad256d9003e6d26e2d2b04b0525d823ba1abc8fb"
WORKDIR /app
RUN pip install --no-cache-dir -e . "pytest" "pytest-mock" "responses" "syrupy" "sybil[pytest]"
