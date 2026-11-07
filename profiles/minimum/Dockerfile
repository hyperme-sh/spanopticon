FROM python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72
WORKDIR /app
COPY examples/operator/requirements.lock requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock
COPY examples/operator/identity.py examples/operator/common.py examples/operator/
COPY profiles/minimum/service.py profiles/minimum/client.py profiles/minimum/
RUN mkdir /state && chown 10001:10001 /state
USER 10001:10001
