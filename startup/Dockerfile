# keycloak-owner is the unpacked, verified owner tooling archive build context.
FROM python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72
WORKDIR /app
COPY startup /app/startup
COPY --from=keycloak-owner keycloak /opt/keycloak-owner
ENV PYTHONDONTWRITEBYTECODE=1 STARTUP_KEYCLOAK_TOOL=/opt/keycloak-owner/startup_identity.py
USER 65532:65532
ENTRYPOINT ["python", "-m", "startup.bootstrap"]
