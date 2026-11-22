#!/bin/sh
set -e

python manage.py migrate --noinput

# API y worker en el mismo contenedor: en Render un Persistent Disk (/app/media) se monta en un
# solo servicio y el worker necesita leer los archivos para enviarlos por WhatsApp.
# honcho termina ambos procesos si alguno se cae, para que Render reinicie el servicio.
#
# -p fija el puerto de gunicorn: honcho asigna su propia variable PORT a cada proceso y, si no se
# le indica, empieza en 5000 (gunicorn quedaba en 5000 y el proxy, que apunta a 8000, daba 502).
# Render define PORT y se respeta; en Dokploy u otros sin PORT se usa 8000.
exec honcho -f docker/Procfile start -p "${PORT:-8000}"
