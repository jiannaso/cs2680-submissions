# mytest task mytest__python-dotenv-680: https://github.com/theskumar/python-dotenv.git at 751f8c148222e58aa173c83c4e5e6cfccb2cc124 (parent of the upstream fix f7b18d9c72d1abcc2ad4023424b84f5bee30d266)
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone -q --filter=blob:none --no-checkout https://github.com/theskumar/python-dotenv.git /app \
    && git -C /app checkout -q 751f8c148222e58aa173c83c4e5e6cfccb2cc124 \
    # Keep only the base snapshot: no later commits (the fix) are reachable.
    && rm -rf /app/.git \
    && git -C /app init -q \
    && git -C /app add -A \
    && git -C /app -c user.email=mytest@local -c user.name=mytest commit -qm "base 751f8c148222e58aa173c83c4e5e6cfccb2cc124"
WORKDIR /app
RUN pip install --no-cache-dir -e . "click>=5.0" "pytest" "sh"
