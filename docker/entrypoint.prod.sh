#!/bin/sh
set -e

python manage.py migrate --noinput

# API y worker en el mismo contenedor: en Render un Persistent Disk (/app/media) se monta en un
# solo servicio y el worker necesita leer los archivos para enviarlos por WhatsApp.
# honcho termina ambos procesos si alguno se cae, para que Render reinicie el servicio.
exec honcho -f docker/Procfile start
