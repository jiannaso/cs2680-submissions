# mytest task mytest__python-dotenv-615: https://github.com/theskumar/python-dotenv.git at 6bdf86adb2b8e3d9e3031b3a81f24851c8bfca0e (parent of the upstream fix 791414804eff08a23f0b7970968e1717e3b28e66)
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone -q --filter=blob:none --no-checkout https://github.com/theskumar/python-dotenv.git /app \
    && git -C /app checkout -q 6bdf86adb2b8e3d9e3031b3a81f24851c8bfca0e \
    # Keep only the base snapshot: no later commits (the fix) are reachable.
    && rm -rf /app/.git \
    && git -C /app init -q \
    && git -C /app add -A \
    && git -C /app -c user.email=mytest@local -c user.name=mytest commit -qm "base 6bdf86adb2b8e3d9e3031b3a81f24851c8bfca0e"
WORKDIR /app
RUN pip install --no-cache-dir -e . "click>=5.0" "pytest" "sh"
