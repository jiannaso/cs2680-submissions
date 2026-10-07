# mytest task mytest__tldextract-383: https://github.com/john-kurkowski/tldextract.git at 544e3cc2082751de8ea1fa5fd49be71402992a78 (parent of the upstream fix 89b9aebf6d9860cca2bd5fe6a5f3f506bb266363)
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone -q --filter=blob:none --no-checkout https://github.com/john-kurkowski/tldextract.git /app \
    && git -C /app checkout -q 544e3cc2082751de8ea1fa5fd49be71402992a78 \
    # Keep only the base snapshot: no later commits (the fix) are reachable.
    && rm -rf /app/.git \
    && git -C /app init -q \
    && git -C /app add -A \
    && git -C /app -c user.email=mytest@local -c user.name=mytest commit -qm "base 544e3cc2082751de8ea1fa5fd49be71402992a78"
WORKDIR /app
RUN pip install --no-cache-dir -e . "pytest" "pytest-mock" "responses" "sybil[pytest]"
