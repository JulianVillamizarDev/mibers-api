import uuid

from django.db import models


class BaseModel(models.Model):
    """Base comun: pk UUID y marcas de tiempo."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Job(BaseModel):
    """Cola de tareas respaldada en la base de datos (sin Redis): la toma `run_worker` con
    SELECT ... FOR UPDATE SKIP LOCKED, de modo que varios workers no duplican trabajo."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        RUNNING = "RUNNING", "En ejecucion"
        DONE = "DONE", "Completada"
        FAILED = "FAILED", "Fallida"

    kind = models.CharField(max_length=80)
    payload = models.JSONField(default=dict, blank=True)
    run_at = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    locked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    # Evita encolar dos veces la misma tarea mientras siga activa.
    dedupe_key = models.CharField(max_length=200, null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["status", "run_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["dedupe_key"],
                condition=models.Q(status__in=["PENDING", "RUNNING"], dedupe_key__isnull=False),
                name="job_dedupe_activo",
            )
        ]

    def __str__(self):
        return f"{self.kind} [{self.status}]"


class PeriodicRun(BaseModel):
    """Ultima ejecucion de cada tarea periodica, para que corra una sola vez por intervalo
    aunque haya varios workers."""

    name = models.CharField(max_length=80, unique=True)
    last_run_at = models.DateTimeField()

    def __str__(self):
        return self.name
