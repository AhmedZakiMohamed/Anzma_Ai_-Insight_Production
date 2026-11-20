FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    ANZMA_SIMULATOR_STATE_PATH=/var/lib/simulator/outbox.sqlite3
RUN useradd --system --uid 10001 --create-home simulator \
    && mkdir -p /var/lib/simulator \
    && chmod 700 /var/lib/simulator \
    && chown simulator:simulator /var/lib/simulator
WORKDIR /opt/reference-simulator
COPY --chown=simulator:simulator simulator ./simulator
USER 10001:10001
ENTRYPOINT ["python", "-m", "simulator.cli"]
