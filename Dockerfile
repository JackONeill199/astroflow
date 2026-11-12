FROM python:3.12-slim

# Plate-Solving: ASTAP-Kommandozeile + Sterndatenbank (d05 = klein, Bildfeld ≥ 0,6°; d50 für kleinere Bildfelder).
# Nur https und nur die geprüften Fassungen: SHA-256 wie in astroflow/core/components.py (ASTAP_SHA256) – SourceForge ersetzt die
# Dateien bei neuen ASTAP-Versionen unter gleichem Namen; dann bricht der Bau ab und die Prüfsummen werden dort bewusst ergänzt.
ARG ASTAP_DB=d05
ARG ASTAP_CLI_SHA256=3f19121d39d1855e6e01c1419658ec6a5e0610c554ca32dc6bf903f1146ba1b3
ARG ASTAP_D05_SHA256=39d59b73cb960fa0956c35162e70b09a8af10ef20a1e8f5cae7b77d48be5ce31
ARG ASTAP_D50_SHA256=ca89a958ca0808185f0e546541b4ac73addc40fee4ad8bdd8bb79981c1dd8b43
RUN set -e; apt-get update && apt-get install -y --no-install-recommends wget unzip ca-certificates \
 && case "$ASTAP_DB" in d05) DB_SHA="$ASTAP_D05_SHA256";; d50) DB_SHA="$ASTAP_D50_SHA256";; *) echo "ASTAP_DB muss d05 oder d50 sein" >&2; exit 1;; esac \
 && mkdir -p /opt/astap \
 && wget -qO /tmp/astap.zip --https-only "https://sourceforge.net/projects/astap-program/files/linux_installer/astap_command-line_version_Linux_amd64.zip/download" \
 && echo "${ASTAP_CLI_SHA256}  /tmp/astap.zip" | sha256sum -c - \
 && unzip -o /tmp/astap.zip -d /tmp/astap \
 && cp "$(find /tmp/astap -type f -name 'astap_cli' | head -n1)" /opt/astap/astap_cli \
 && chmod +x /opt/astap/astap_cli \
 && wget -qO /tmp/db.zip --https-only "https://sourceforge.net/projects/astap-program/files/star_databases/${ASTAP_DB}_star_database.zip/download" \
 && echo "${DB_SHA}  /tmp/db.zip" | sha256sum -c - \
 && unzip -o /tmp/db.zip -d /tmp/db \
 && find /tmp/db -type f -name "${ASTAP_DB}_*" -exec cp {} /opt/astap/ \; \
 && ls /opt/astap | grep -q "${ASTAP_DB}_0101" \
 && rm -rf /tmp/astap /tmp/astap.zip /tmp/db /tmp/db.zip \
 && apt-get purge -y wget unzip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
ENV ASTROFLOW_ASTAP=/opt/astap/astap_cli ASTROFLOW_ASTAP_DB=/opt/astap

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY astroflow ./astroflow
# SkyTrace-Analyse-Engine (OpenCV, PyTorch/YOLO, PyAV …, mehrere GB) nur auf Wunsch: docker compose build --build-arg SKYTRACE=1
ARG SKYTRACE=0
# Gleicher Weg wie die Komponente „skytrace“ (core/components.py): erst PyTorch aus dem CPU-Index, dann requirements-engine.txt;
# libgl1/libglib2.0-0 braucht OpenCV im schlanken Image
RUN if [ "$SKYTRACE" = "1" ]; then       apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*       && pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu       && pip install --no-cache-dir -r astroflow/modules/skytrace/requirements-engine.txt;     fi
ENV ASTROFLOW_DATA=/data
VOLUME /data
EXPOSE 8080
# Im Container auf allen Schnittstellen; nach außen begrenzt docker-compose.yml den Port auf 127.0.0.1
CMD ["python", "-m", "astroflow", "serve", "--host", "0.0.0.0", "--port", "8080"]
