# Mibers API

API interna para que la agencia gestione el contenido de redes sociales de sus clientes: clientes y contratos, piezas con revision y aprobacion, programacion, publicacion en Instagram y Facebook, envio del contenido al WhatsApp del cliente, alertas al director, cronograma y metricas. Los requerimientos estan en [`context.txt`](context.txt).

**Stack:** Django 5.2 + Django REST Framework, PostgreSQL, JWT, Docker (dev y prod), monolito modular, archivos en `/media`.

## Decision clave: la agencia no es Tech Provider de Meta

Publicar en las cuentas de Instagram/Facebook **de los clientes** por la Graph API exige que la agencia sea Tech Provider, y
todavia no lo es. Por eso:

| Requerimiento | Como se resuelve hoy |
|---|---|
| RF7 vincular redes con autorizacion de Meta | El **administrador registra la cuenta** (red, usuario, URL) y gestiona su estado `vinculada / expirada / desvinculada`. Nunca se guardan contrasenas ni tokens de clientes. |
| RF15-RF17 publicacion automatica | **Publicacion manual asistida.** A la hora programada el scheduler crea una tarea de publicacion por red, avisa al responsable (WhatsApp) y el personal publica en la red y confirma con el enlace, o reporta el fallo con su motivo. Todo se registra, se puede reintentar y alerta al director. |
| RF23 metricas por API | El equipo **carga las metricas mensuales** (alcance, visualizaciones, interacciones, seguidores) por cliente, red y mes. |
| RF18/RF19 WhatsApp | **WhatsApp Cloud API oficial** con el numero de la agencia (no requiere ser Tech Provider, si plantillas aprobadas). |

La publicacion esta detras de un puerto (`modules/publishing/publishers.py`): si algun dia se obtiene el estatus de Tech
Provider basta con anadir un `GraphApiPublisher` sin tocar scheduler, estados ni tareas.

## Arquitectura

Monolito modular: cada carpeta de `modules/` es una app Django con `models`, `services` (escrituras y reglas), `serializers`,
`views` y `urls`. Las dependencias entre modulos solo van hacia abajo y **import-linter las hace cumplir** (`lint-imports`):

```
publishing | metrics | legal      <- scheduler, tareas de publicacion, envio al cliente | metricas | politica de datos
content                           <- piezas, archivos, reglas de medios, revision, cronograma
clients                           <- clientes, contactos de WhatsApp, cuentas sociales
notifications                     <- gateway de WhatsApp, alertas al admin, webhook
accounts                          <- usuarios, JWT con sesiones, roles
audit                             <- historial inmutable
core                              <- base, cifrado AES-256, cola de tareas, permisos, utilidades
```

Los modulos se comunican hacia arriba con **senales** (`content.signals.piece_state_changed`,
`notifications.services.envio_actualizado`), p. ej. para cancelar tareas al devolver una pieza a revision o marcar la pieza
como Compartida cuando salen los envios.

### Worker (sin Redis)

`python manage.py run_worker` toma tareas de la tabla `core_job` con `SELECT ... FOR UPDATE SKIP LOCKED` (varios workers no
duplican trabajo), con reintentos y backoff, y ejecuta las tareas periodicas: el **tick del scheduler cada 60 s** (RNF06),
y la retencion diaria (historial 12 meses, clientes desactivados 1 anio).

### Ciclo de la pieza

`Borrador -> En revision -> (Cambios solicitados) -> Aprobada -> Programada -> Publicada | Fallida -> Compartida`.
Solo el Administrador aprueba. **Editar archivos o copy de una pieza aprobada o programada la devuelve a revision** y cancela sus
tareas pendientes. Una pieza sin aprobacion que llega a su hora se **bloquea y genera alerta**; nunca se publica.

## Puesta en marcha

### Con Docker (recomendado)

```bash
cp .env.example .env            # y completar
docker compose -f docker-compose.dev.yml up --build
docker compose -f docker-compose.dev.yml exec web python manage.py createadmin --username director
```

API en `http://localhost:8000`, salud en `/health`, documentacion OpenAPI en `/api/docs`. Con `WHATSAPP_BACKEND=fake` (por
defecto) los envios se simulan.

### Sin Docker

```bash
python -m venv .venv && .venv/Scripts/activate        # Windows (en Linux/macOS: source .venv/bin/activate)
pip install -r requirements/dev.txt
python manage.py migrate
python manage.py createadmin --username director
python manage.py runserver
python manage.py run_worker --interval 5               # en otra terminal
```

`DATABASE_URL` por defecto es SQLite; para PostgreSQL: `postgres://usuario:clave@host:5432/mibers`.

### Pruebas y calidad

```bash
pytest                                   # todo
pytest modules/publishing/tests/test_tick.py::test_pieza_no_programada_o_sin_aprobacion_se_bloquea_y_alerta
pytest --cov=modules/publishing --cov=modules/core --cov-fail-under=60   # RNF14
ruff check . && ruff format --check .
lint-imports                             # contrato de capas
```

Las pruebas corren sobre SQLite. Las marcadas `postgres` (trigger de solo-anexar del historial) se omiten salvo que
`DATABASE_URL` apunte a PostgreSQL. `ffprobe` (ffmpeg) es necesario para validar videos; las pruebas simulan su resultado.

## Variables de entorno

Ver [`.env.example`](.env.example). Las importantes: `DJANGO_SECRET_KEY`, `DATABASE_URL`, `ALLOWED_HOSTS`,
`CORS_ALLOWED_ORIGINS`, `FIELD_ENCRYPTION_KEY`, `MEDIA_ROOT`, `WHATSAPP_*`.

Generar la clave de cifrado:

```bash
python -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

> **Guardar `FIELD_ENCRYPTION_KEY` en un lugar seguro.** Cifra contactos y numeros de WhatsApp (AES-256-GCM). Si se pierde
> o se cambia, esos datos no se pueden recuperar.

## WhatsApp Cloud API

Con `WHATSAPP_BACKEND=cloud` se necesitan `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET` y
`WHATSAPP_WEBHOOK_VERIFY_TOKEN`. Configura el webhook de Meta en `https://<tu-api>/api/v1/webhooks/whatsapp` (campo
`messages`) para registrar si cada envio fue entregado/leido.

Plantillas a crear y aprobar en Meta Business (los mensajes iniciados por la empresa fuera de la ventana de 24 h exigen plantilla):

| Plantilla (variable) | Cabecera | Body |
|---|---|---|
| `pieza_cliente_imagen` (`WHATSAPP_TEMPLATE_CLIENT_IMAGE`) | imagen | 1 parametro = mensaje para el cliente |
| `pieza_cliente_video` (`..._CLIENT_VIDEO`) | video | 1 parametro |
| `pieza_cliente_documento` (`..._CLIENT_DOCUMENT`) | documento | 1 parametro |
| `alerta_director` (`..._ALERT`) | - | 1 parametro = texto de la alerta |
| `recordatorio_publicacion` (`..._REMINDER`) | - | 1 parametro |

Limites de WhatsApp aplicados: imagen <= 5 MB, video <= 16 MB; lo que exceda viaja como documento (<= 100 MB); mas de 100 MB
no se puede enviar y alerta al director. Los parametros no admiten saltos de linea (se reemplazan por espacios).

## Despliegue en Render

`render.yaml` define un servicio web Docker (`docker/Dockerfile.prod`) con disco persistente en `/app/media` y una base
PostgreSQL. API y worker corren en el mismo contenedor (`honcho` + `docker/Procfile`) porque el disco solo puede montarse en un
servicio: **una sola instancia**. Cargar en el panel las variables marcadas `sync: false`. TLS lo termina Render (1.2+).

## API (resumen)

Todo bajo `/api/v1/`, JSON, autenticacion `Authorization: Bearer <access>` (excepto login, refresh, politica y webhook).

| Area | Endpoints |
|---|---|
| Sesion | `POST auth/login`, `auth/refresh`, `auth/logout`, `auth/change-password`; `GET auth/me` |
| Personal (admin) | `GET/POST usuarios`, `GET/PATCH usuarios/{id}`, `POST usuarios/{id}/desactivar \| activar \| restablecer-password` |
| Historial (admin) | `GET auditoria` (filtros `actor, action, entity_type, desde, hasta`), `GET auditoria/acciones` |
| Clientes | `GET/POST clientes`, `GET/PATCH clientes/{id}`, `POST clientes/{id}/desactivar \| reactivar`, `clientes/actuales`, `clientes/{id}/cronograma?vista=semana\|mes&fecha=` |
| Contactos y redes | `clientes/{id}/contactos`, `clientes/{id}/cuentas` (registrar/cambiar estado: admin) |
| Piezas | `GET/POST piezas` (filtros `cliente, estado, tipo, red, desde, hasta`), `GET/PATCH/DELETE piezas/{id}`, `piezas/{id}/archivos`, `archivo-editable` |
| Revision | `POST piezas/{id}/enviar-revision`, `aprobar` y `solicitar-cambios` (admin), `GET piezas/{id}/revisiones` |
| Programacion | `GET piezas/{id}/programacion` (vista previa), `POST piezas/{id}/programar {"confirmar": true}`, `cancelar-programacion` |
| Publicacion | `GET publicaciones?estado=PENDIENTE`, `POST publicaciones/{id}/confirmar \| fallar \| reintentar`, `GET publicaciones/cumplimiento` (admin) |
| Envios al cliente (HU-19, RF18) | `GET envios` (paginado, mas reciente primero; filtros `cliente`, `pieza`, `estado`, `desde`, `hasta` en dia de Bogota), `GET piezas/{id}/envios` (cronologico, sin paginar). Estado de entrega `PENDIENTE / ENVIADO / ENTREGADO / LEIDO / FALLIDO`; el numero del contacto sale enmascarado (`•••• 2233`) |
| Alertas (admin) | `GET alertas`, `POST alertas/{id}/leer` |
| Metricas | `GET metricas`, `PUT clientes/{id}/metricas` |
| Legal | `GET legal/politica` (publico), `GET/POST legal/politicas` (admin) |

Errores: `{"code": "...", "detail": ...}`. Las fechas se reciben y devuelven en hora de Colombia (UTC-5).

## Cobertura de requerimientos

Implementados en la API: RF1-RF12, RF14-RF20, RF23 y RNF01, RNF02, RNF05, RNF06 (medicion y tick de 60 s), RNF08-RNF14.
Dependen del frontend o de medicion en produccion: RNF03 (usabilidad), RNF04 (pantallas/navegadores), RNF07 (tiempos de carga).

Pendiente antes de produccion (ver "Verificacion pendiente" abajo): las reglas de medios por red
(`modules/content/media_rules.py`) son valores iniciales conservadores y deben contrastarse con la documentacion vigente de Meta;
politica de tratamiento de datos (RNF13): el texto lo publica el administrador por `legal/politicas`.

## Verificacion pendiente

Lo construido se probo con 340+ pruebas automaticas sobre SQLite (97 % de cobertura). **No se ha ejecutado aun**: el trigger
de solo-anexar del historial en PostgreSQL (hay prueba, marcada `postgres`), la construccion de las imagenes Docker, el despliegue
en Render ni la integracion real con WhatsApp Cloud API (se probo contra un transporte HTTP simulado).
