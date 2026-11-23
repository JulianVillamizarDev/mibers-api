# Mibers API

API interna para que la agencia gestione el contenido de redes sociales de sus clientes: clientes y contratos, piezas con revision y aprobacion, programacion, publicacion en Instagram y Facebook, envio del contenido al WhatsApp del cliente, alertas al director, cronograma y metricas. Los requerimientos estan en [`context.txt`](context.txt).

**Stack:** Django 5.2 + Django REST Framework, PostgreSQL, JWT, Docker (dev y prod), monolito modular, archivos en `/media`.

## Integracion con Meta: Graph API bajo el modelo de agencia, con respaldo manual

La API publica en Instagram y Facebook **de los clientes** con la Graph API, bajo el modelo de agencia (ADR0007): un unico
token de usuario del sistema de Meta Business de la agencia (`META_SYSTEM_USER_TOKEN`) que debe tener acceso a la pagina o a la
cuenta de cada cliente. La publicacion manual asistida no se elimino: es el **respaldo** cuando lo automatico no es posible.

| Requerimiento | Como se resuelve hoy |
|---|---|
| RF7 vincular redes | El **administrador registra la cuenta** (red, usuario, URL e `id_externo`: id de la pagina de Facebook o de la cuenta profesional de Instagram) y su estado `vinculada / expirada / desvinculada`. Con Meta habilitado, la API **verifica las cuentas cada 6 h** y al recibir un token invalido (codigo 190): un token invalido expira todas, y una cuenta sin permiso o inexistente se desvincula; ambos casos alertan al director. Nunca se guardan contrasenas ni tokens de clientes (el token de pagina se pide a Meta en cada uso y no se guarda). |
| RF15-RF17 publicacion | **Automatica por Graph API** a la hora programada, cuando `META_PUBLISHING=graph` con token, la cuenta tiene `id_externo` y esta `vinculada`, y el tipo de pieza esta soportado en esa red. En cualquier otro caso la tarea cae a **manual asistida**: se avisa al responsable por WhatsApp, el personal publica y confirma con el enlace (o reporta el fallo con su motivo). En ambos modos se registra todo, se puede reintentar y un fallo alerta al director. |
| RF23 metricas | **Sincronizacion diaria desde Meta** (alcance, visualizaciones, interacciones, seguidores por cliente, red y mes). La carga manual del equipo se conserva como respaldo y **no se sobrescribe** salvo que se fuerce. |
| RF18/RF19 WhatsApp | **WhatsApp Cloud API oficial** con el numero de la agencia (plantillas aprobadas). |

El modo se elige con `META_PUBLISHING`: `manual` (por defecto; no se llama a Meta), `graph` (Graph API real) o `fake` (adaptador
de pruebas). Ver [Meta Graph API](#meta-graph-api-publicacion-automatica) mas abajo.

## Arquitectura

Monolito modular: cada carpeta de `modules/` es una app Django con `models`, `services` (escrituras y reglas), `serializers`,
`views` y `urls`. Las dependencias entre modulos solo van hacia abajo y **import-linter las hace cumplir** (`lint-imports`):

```
publishing | metrics | legal      <- scheduler, tareas de publicacion, envio al cliente | metricas | politica de datos
content                           <- piezas, archivos, reglas de medios, revision, cronograma
clients                           <- clientes, contactos de WhatsApp, cuentas sociales, cliente de la Graph API (meta.py)
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
duplican trabajo), con reintentos y backoff, y ejecuta las tareas periodicas:

| Tarea periodica | Frecuencia | Que hace |
|---|---|---|
| `publishing.tick` | 60 s (RNF06) | Crea las tareas de publicacion de las piezas que llegan a su hora y las despacha (automatica o manual) |
| `clients.verificar_cuentas_meta` | 6 h | Verifica las cuentas con `id_externo` contra Meta y actualiza su estado (no hace nada con `META_PUBLISHING=manual`) |
| `metrics.sincronizar_meta` | 24 h | Sincroniza desde Meta las metricas del mes en curso y del anterior (no hace nada con `META_PUBLISHING=manual`) |
| `audit.purge_expired`, `clients.purge_expired` | 24 h | Retencion: historial 12 meses, clientes desactivados 1 anio |

La publicacion automatica tambien corre en este worker (job `publishing.graph_publish`): el tick solo encola; la llamada a Meta
ocurre en el worker.

### Ciclo de la pieza

`Borrador -> En revision -> (Cambios solicitados) -> Aprobada -> Programada -> Publicada | Fallida -> Compartida`.
Solo el Administrador aprueba. **Editar archivos o copy de una pieza aprobada o programada la devuelve a revision** y cancela sus
tareas pendientes. Una pieza sin aprobacion que llega a su hora se **bloquea y genera alerta**; nunca se publica.

### Publicacion por red (RF15-RF17)

A la hora programada el tick crea **una `TareaPublicacion` por red** y la despacha con `publishing/publishers.py`:

- **Automatica (`GraphApiPublisher`)**: encola el job `publishing.graph_publish`. El worker crea el contenido en Meta (en Instagram
  espera hasta 10 min a que procese un video, re-encolando el job sin bloquear el worker), publica, consulta el enlace y confirma la tarea con el usuario
  `sistema`; despues se envia al cliente por WhatsApp (RF18). Es idempotente: si el job se reintenta reutiliza el contenedor o la
  publicacion ya creados y nunca publica dos veces. Los errores transitorios de Meta (limite de uso, 5xx, red) se reintentan con
  backoff; los demas dejan la tarea en `FALLIDA`, con el codigo y el motivo, y alertan al director.
- **Manual asistida (`ManualPublisher`)**: se avisa por WhatsApp al responsable; el personal publica y confirma con el enlace
  (`POST publicaciones/{id}/confirmar`) o reporta el fallo (`.../fallar`).

Cae a manual, avisando al responsable y sin cambiar el estado de la tarea, cuando falta el token, la cuenta no tiene `id_externo`
o no esta `vinculada`, o el tipo de pieza no esta soportado en esa red:

| Red | Soportado en automatico | Siempre manual |
|---|---|---|
| Instagram | Post (1 imagen o video), carrusel (2-10 imagenes o videos), reel (1 video), historia (1 imagen o video) | Portada |
| Facebook | Post (1 imagen o video), carrusel (2-10 imagenes), reel (1 video) | Historia, portada |

`POST publicaciones/{id}/reintentar` devuelve una tarea fallida a `PENDIENTE` y la vuelve a despachar por el mismo criterio. Si
Meta ya habia creado la publicacion, se conserva su id para no duplicarla. `GET publicaciones` muestra `modo`
(`AUTOMATICA`/`MANUAL`) y `externo_estado` (`ENCOLADO`, `CREANDO`, `SUBIENDO`, `PROCESANDO`, `PUBLICANDO`, `REINTENTANDO`,
`PUBLICADO`, `FALLIDO`, `MANUAL`).

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
`CORS_ALLOWED_ORIGINS`, `FIELD_ENCRYPTION_KEY`, `MEDIA_ROOT`, `WHATSAPP_*`, `META_*` y `PUBLIC_BASE_URL`.

Generar la clave de cifrado:

```bash
python -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

> **Guardar `FIELD_ENCRYPTION_KEY` en un lugar seguro.** Cifra contactos y numeros de WhatsApp (AES-256-GCM). Si se pierde
> o se cambia, esos datos no se pueden recuperar.

## Meta Graph API (publicacion automatica)

Con `META_PUBLISHING=manual` (valor por defecto) no se llama a Meta: todo se publica en modo manual asistido, no se verifican
las cuentas y no se sincronizan metricas.

| Variable | Para que sirve |
|---|---|
| `META_PUBLISHING` | `manual`, `graph` (real) o `fake` (pruebas, sin red) |
| `META_SYSTEM_USER_TOKEN` | Token del usuario del sistema de Meta Business de la agencia. Solo en variables de entorno; nunca se guarda en la base ni se imprime |
| `META_APP_SECRET` | Si se define, cada llamada lleva `appsecret_proof` |
| `META_APP_ID` | Id de la app de Meta |
| `META_GRAPH_VERSION` | Version de la Graph API (por defecto `v25.0`) |
| `PUBLIC_BASE_URL` | URL publica de **esta API**, alcanzable desde Internet: Meta descarga de ahi los archivos. Por defecto `http://localhost:8000`, que no sirve en produccion |
| `PUBLIC_MEDIA_TTL_SECONDS` | Vigencia de las URLs firmadas de archivos (por defecto 3600) |

**Como obtiene Meta los archivos.** Meta no puede entrar a `/media`, asi que descarga cada archivo de
`GET /api/v1/public/media/{archivo_id}?exp=...&sig=...`: URL firmada con HMAC (`DJANGO_SECRET_KEY`) que vence a los
`PUBLIC_MEDIA_TTL_SECONDS` y no exige sesion. Solo entrega archivos de piezas con una tarea de publicacion `PENDIENTE` o
`FALLIDA`; el resto responde 404. Las imagenes que no son JPEG se convierten en memoria, porque Instagram no acepta PNG.

**Puesta en marcha en una cuenta de cliente:**

1. Con el token cargado, `python manage.py meta_check` lista las paginas de Facebook y las cuentas de Instagram a las que
   tiene acceso el token, con sus ids (no imprime el token).
2. Registrar la cuenta del cliente con su `id_externo` (`POST clientes/{id}/cuentas`).
3. Probar de punta a punta con `python manage.py meta_smoke --red instagram|facebook|ambas --imagen <ruta>`. Esto **publica de
   verdad**: crea (si no existen) el usuario `smoke-admin`, el cliente `Mibers Creativos` y sus cuentas, y programa una pieza de
   prueba. Exige `META_PUBLISHING=graph` y un `PUBLIC_BASE_URL` publico; con `--fake` no llama a Meta.
4. Dejar `META_PUBLISHING=graph`.

Comandos de apoyo (los dos ultimos tambien corren solos en el worker): `python manage.py meta_verificar_cuentas` y
`python manage.py meta_sync_metricas [--mes AAAA-MM] [--cliente UUID] [--forzar]` (`--forzar` sobrescribe tambien las filas cargadas
a mano).

## WhatsApp Cloud API

Con `WHATSAPP_BACKEND=cloud` se necesitan `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET` y
`WHATSAPP_WEBHOOK_VERIFY_TOKEN`. Configura el webhook de Meta en `https://<tu-api>/api/v1/webhooks/whatsapp` (campo
`messages`) para registrar si cada envio fue entregado/leido. `python manage.py whatsapp_check [--enviar-a <E.164>]
[--plantilla <nombre>]` valida la configuracion y opcionalmente envia una plantilla de prueba.

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
servicio: **una sola instancia**. Cargar en el panel las variables marcadas `sync: false`, en particular `PUBLIC_BASE_URL` (la URL
publica del servicio, para que Meta descargue los archivos) y, para publicar de forma automatica, `META_SYSTEM_USER_TOKEN` y
`META_APP_SECRET`: el blueprint deja `META_PUBLISHING=manual`, asi que hay que cambiarlo a `graph` en el panel. TLS lo termina
Render (1.2+).

## API (resumen)

Todo bajo `/api/v1/`, JSON, autenticacion `Authorization: Bearer <access>` (excepto login, refresh, politica, webhook y
`public/media/{id}`, que usa una URL firmada).

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
| Publicacion | `GET publicaciones?estado=PENDIENTE`, `GET publicaciones/{id}`, `GET piezas/{id}/publicaciones`, `POST publicaciones/{id}/confirmar \| fallar` (modo manual), `POST publicaciones/{id}/reintentar`, `GET publicaciones/cumplimiento` (admin). Cada tarea trae `modo` (`AUTOMATICA`/`MANUAL`) y los ids y el estado externos de Meta |
| Archivos para Meta | `GET public/media/{archivo_id}?exp=&sig=`: URL firmada y temporal que usa Meta para descargar los archivos de una pieza a publicar; sin sesion |
| Envios al cliente (HU-19, RF18) | `GET envios` (paginado, mas reciente primero; filtros `cliente`, `pieza`, `estado`, `desde`, `hasta` en dia de Bogota), `GET piezas/{id}/envios` (cronologico, sin paginar). Estado de entrega `PENDIENTE / ENVIADO / ENTREGADO / LEIDO / FALLIDO`; el numero del contacto sale enmascarado (`•••• 2233`) |
| Alertas (admin) | `GET alertas`, `POST alertas/{id}/leer` |
| Metricas | `GET metricas` (cada fila trae `origen`: `API` o `MANUAL`), `PUT clientes/{id}/metricas` (carga manual), `POST clientes/{id}/metricas/sincronizar {"mes": "AAAA-MM"}` (admin: trae las metricas desde Meta) |
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

La publicacion automatica, la verificacion de cuentas y la sincronizacion de metricas con Meta se prueban con un cliente de
Graph API simulado (`FakeGraphClient` y transporte HTTP simulado). Lo que Meta devuelve de verdad (permisos del token, limites de
uso, formatos aceptados) se comprueba con `meta_check` y `meta_smoke` (ver "Meta Graph API").
