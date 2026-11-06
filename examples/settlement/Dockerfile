FROM python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72
WORKDIR /app
COPY examples/settlement/requirements.lock /app/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock
COPY examples/settlement /app/examples/settlement
RUN useradd --uid 10001 --create-home service && mkdir /state && chown service:service /state
USER 10001
CMD ["python", "-m", "uvicorn", "examples.settlement.server:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
