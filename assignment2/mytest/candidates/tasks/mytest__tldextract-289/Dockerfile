# mytest task mytest__tldextract-289: https://github.com/john-kurkowski/tldextract.git at 4b358ce274cfd2e3b7fb67933433f5a412e7939b (parent of the upstream fix 36b90496dca31b3400eefd2937ea0712f636f59c)
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone -q --filter=blob:none --no-checkout https://github.com/john-kurkowski/tldextract.git /app \
    && git -C /app checkout -q 4b358ce274cfd2e3b7fb67933433f5a412e7939b \
    # Keep only the base snapshot: no later commits (the fix) are reachable.
    && rm -rf /app/.git \
    && git -C /app init -q \
    && git -C /app add -A \
    && git -C /app -c user.email=mytest@local -c user.name=mytest commit -qm "base 4b358ce274cfd2e3b7fb67933433f5a412e7939b"
WORKDIR /app
RUN pip install --no-cache-dir -e . "pytest" "pytest-mock" "responses"
