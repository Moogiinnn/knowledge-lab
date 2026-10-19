# Sandbox image for C and C++ submissions: the Python harness plus gcc, g++ and the C/C++ library headers.
# Build: manage.py check_code_runner --build-c   (needs network once; grading itself runs offline)
FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc g++ libc6-dev \
 && rm -rf /var/lib/apt/lists/*
