FROM python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72
COPY adapter.py /adapter-test.py
USER 10001:10001
ENTRYPOINT ["python", "/adapter-test.py"]
