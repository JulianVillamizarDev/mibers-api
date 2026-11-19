"""Cola de tareas y tareas periodicas sobre PostgreSQL.

- `@register("tipo")` registra el manejador de un tipo de tarea (en `AppConfig.ready()`).
- `enqueue(...)` encola; `run_worker` las ejecuta con reintentos y backoff.
- `@periodic("nombre", every=60)` registra una tarea recurrente (p. ej. el tick de publicacion).
"""

import logging
from collections.abc import Callable
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Job, PeriodicRun

logger = logging.getLogger(__name__)

_HANDLERS: dict[str, Callable] = {}
_PERIODIC: dict[str, tuple[int, Callable]] = {}

STALE_AFTER = timedelta(minutes=10)  # una tarea RUNNING mas vieja que esto se considera abandonada
MAX_BACKOFF_SECONDS = 600


def register(kind: str):
    """Registra `handler(payload: dict, *, last_attempt: bool)` para un tipo de tarea."""

    def decorator(func):
        _HANDLERS[kind] = func
        return func

    return decorator


def periodic(name: str, *, every: int):
    """Registra una tarea que corre como maximo una vez cada `every` segundos."""

    def decorator(func):
        _PERIODIC[name] = (every, func)
        return func

    return decorator


def enqueue(kind, payload=None, *, run_at=None, dedupe_key=None, max_attempts=5) -> Job:
    """Encola una tarea. Con `dedupe_key`, si ya hay una activa igual, devuelve esa."""
    active = Job.objects.filter(status__in=["PENDING", "RUNNING"])
    if dedupe_key:
        existing = active.filter(dedupe_key=dedupe_key).first()
        if existing:
            return existing
    try:
        with transaction.atomic():
            return Job.objects.create(
                kind=kind,
                payload=payload or {},
                run_at=run_at or timezone.now(),
                dedupe_key=dedupe_key,
                max_attempts=max_attempts,
            )
    except IntegrityError:  # otro proceso la encolo en paralelo
        return active.get(dedupe_key=dedupe_key)


def backoff(attempts: int) -> timedelta:
    """30 s, 60 s, 2 min, 4 min... hasta 10 min: las alertas deben llegar en <= 5 min (RNF12)."""
    return timedelta(seconds=min(30 * 2 ** max(attempts - 1, 0), MAX_BACKOFF_SECONDS))


def recover_stale(now=None) -> int:
    now = now or timezone.now()
    return Job.objects.filter(status=Job.Status.RUNNING, locked_at__lt=now - STALE_AFTER).update(
        status=Job.Status.PENDING, locked_at=None
    )


def claim_due(limit: int = 20, now=None) -> list[Job]:
    """Toma tareas vencidas y las marca RUNNING. SKIP LOCKED evita pelear con otros workers."""
    now = now or timezone.now()
    with transaction.atomic():
        jobs = list(
            Job.objects.select_for_update(skip_locked=True)
            .filter(status=Job.Status.PENDING, run_at__lte=now)
            .order_by("run_at")[:limit]
        )
        for job in jobs:
            job.status = Job.Status.RUNNING
            job.locked_at = now
            job.attempts += 1
            job.save(update_fields=["status", "locked_at", "attempts", "updated_at"])
    return jobs


def run_job(job: Job, now=None) -> None:
    now = now or timezone.now()
    handler = _HANDLERS.get(job.kind)
    last_attempt = job.attempts >= job.max_attempts
    try:
        if handler is None:
            raise LookupError(f"No hay manejador registrado para '{job.kind}'")
        handler(job.payload, last_attempt=last_attempt)
    except Exception as exc:  # noqa: BLE001 - cualquier fallo se reintenta o se marca FAILED
        logger.exception("Fallo la tarea %s (%s), intento %s", job.kind, job.pk, job.attempts)
        job.last_error = f"{type(exc).__name__}: {exc}"[:2000]
        job.locked_at = None
        if last_attempt:
            job.status = Job.Status.FAILED
        else:
            job.status = Job.Status.PENDING
            job.run_at = now + backoff(job.attempts)
    else:
        job.status = Job.Status.DONE
        job.locked_at = None
    job.save(update_fields=["status", "run_at", "locked_at", "last_error", "updated_at"])


def run_periodic_due(now=None) -> list[str]:
    """Ejecuta las tareas periodicas cuyo intervalo se cumplio. Devuelve los nombres ejecutados."""
    now = now or timezone.now()
    executed = []
    for name, (every, func) in _PERIODIC.items():
        with transaction.atomic():
            row, _ = PeriodicRun.objects.select_for_update().get_or_create(
                name=name, defaults={"last_run_at": now - timedelta(seconds=every)}
            )
            if now - row.last_run_at < timedelta(seconds=every):
                continue
            row.last_run_at = now
            row.save(update_fields=["last_run_at", "updated_at"])
        try:
            func()
            executed.append(name)
        except Exception:  # noqa: BLE001
            logger.exception("Fallo la tarea periodica %s", name)
    return executed


def process_once(*, limit: int = 20, now=None) -> dict:
    """Un ciclo del worker: recupera abandonadas, corre periodicas y ejecuta tareas vencidas."""
    now = now or timezone.now()
    recovered = recover_stale(now)
    periodic_done = run_periodic_due(now)
    jobs = claim_due(limit, now)
    for job in jobs:
        run_job(job, now)
    return {"recovered": recovered, "periodic": periodic_done, "jobs": len(jobs)}
