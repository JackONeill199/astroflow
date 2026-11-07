FROM python:3.12-slim

# Plate-Solving: ASTAP-Kommandozeile + Sterndatenbank (d05 = klein, Bildfeld ≥ 0,6°; d50 für kleinere Bildfelder)
ARG ASTAP_DB=d05
RUN apt-get update && apt-get install -y --no-install-recommends wget unzip ca-certificates \
 && mkdir -p /opt/astap \
 && wget -qO /tmp/astap.zip "https://sourceforge.net/projects/astap-program/files/linux_installer/astap_command-line_version_Linux_amd64.zip/download" \
 && unzip -o /tmp/astap.zip -d /tmp/astap \
 && cp "$(find /tmp/astap -type f -name 'astap_cli' | head -n1)" /opt/astap/astap_cli \
 && chmod +x /opt/astap/astap_cli \
 && wget -qO /tmp/db.deb "https://sourceforge.net/projects/astap-program/files/star_databases/${ASTAP_DB}_star_database.deb/download" \
 && dpkg-deb -x /tmp/db.deb /tmp/db \
 && find /tmp/db -type f -name "${ASTAP_DB}_*" -exec cp {} /opt/astap/ \; \
 && ls /opt/astap | grep -q "${ASTAP_DB}_0101" \
 && rm -rf /tmp/astap /tmp/astap.zip /tmp/db /tmp/db.deb \
 && apt-get purge -y wget unzip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
ENV ASTROFLOW_ASTAP=/opt/astap/astap_cli ASTROFLOW_ASTAP_DB=/opt/astap

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY astroflow ./astroflow
# SkyTrace-Analyse-Engine (OpenCV, PyTorch/YOLO, PyAV …, mehrere GB) nur auf Wunsch: docker compose build --build-arg SKYTRACE=1
ARG SKYTRACE=0
RUN if [ "$SKYTRACE" = "1" ]; then pip install --no-cache-dir -r astroflow/modules/skytrace/requirements.txt; fi
ENV ASTROFLOW_DATA=/data
VOLUME /data
EXPOSE 8080
# Im Container auf allen Schnittstellen; nach außen begrenzt docker-compose.yml den Port auf 127.0.0.1
CMD ["python", "-m", "astroflow", "serve", "--host", "0.0.0.0", "--port", "8080"]
