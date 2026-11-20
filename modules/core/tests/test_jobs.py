from datetime import timedelta

import pytest
from django.core.management import call_command
from django.db import IntegrityError
from django.utils import timezone

from modules.core import jobs
from modules.core.models import Job, PeriodicRun

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    """Cada prueba parte de un registro vacio, sin tareas periodicas de otros modulos."""
    monkeypatch.setattr(jobs, "_HANDLERS", {})
    monkeypatch.setattr(jobs, "_PERIODIC", {})


def test_enqueue_crea_tarea_pendiente_para_ahora():
    job = jobs.enqueue("demo", {"x": 1})
    assert job.status == "PENDING" and job.payload == {"x": 1}
    assert abs(job.run_at - timezone.now()) < timedelta(seconds=5)


def test_enqueue_con_dedupe_key_no_duplica_mientras_este_activa():
    first = jobs.enqueue("demo", dedupe_key="k1")
    second = jobs.enqueue("demo", dedupe_key="k1")
    assert first.pk == second.pk and Job.objects.count() == 1
    jobs.run_job(first)  # sin manejador -> reintento, sigue activa
    assert jobs.enqueue("demo", dedupe_key="k1").pk == first.pk


def test_dedupe_key_se_libera_cuando_la_tarea_termina():
    first = jobs.enqueue("demo", dedupe_key="k1")
    Job.objects.filter(pk=first.pk).update(status="DONE")
    assert jobs.enqueue("demo", dedupe_key="k1").pk != first.pk


def test_la_base_impide_dos_tareas_activas_con_la_misma_clave():
    Job.objects.create(kind="a", run_at=timezone.now(), dedupe_key="dup")
    with pytest.raises(IntegrityError):
        Job.objects.create(kind="a", run_at=timezone.now(), dedupe_key="dup")


def test_ejecuta_solo_tareas_vencidas_en_orden():
    calls = []
    jobs.register("demo")(lambda payload, last_attempt: calls.append(payload["n"]))
    now = timezone.now()
    jobs.enqueue("demo", {"n": 2}, run_at=now - timedelta(minutes=1))
    jobs.enqueue("demo", {"n": 1}, run_at=now - timedelta(minutes=5))
    jobs.enqueue("demo", {"n": 3}, run_at=now + timedelta(minutes=5))
    result = jobs.process_once()
    assert calls == [1, 2] and result["jobs"] == 2
    assert Job.objects.filter(status="DONE").count() == 2
    assert Job.objects.get(payload={"n": 3}).status == "PENDING"


def test_una_tarea_exitosa_no_se_ejecuta_dos_veces():
    calls = []
    jobs.register("demo")(lambda payload, last_attempt: calls.append(1))
    jobs.enqueue("demo")
    jobs.process_once()
    jobs.process_once()
    assert calls == [1]


def test_fallo_reintenta_con_backoff_creciente():
    def boom(payload, last_attempt):
        raise RuntimeError("fallo temporal")

    jobs.register("demo")(boom)
    job = jobs.enqueue("demo", max_attempts=3)
    now = timezone.now()
    expected = [30, 60]
    for seconds in expected:
        jobs.process_once(now=now)
        job.refresh_from_db()
        assert job.status == "PENDING" and "fallo temporal" in job.last_error
        assert job.run_at == now + timedelta(seconds=seconds)
        now = job.run_at  # el siguiente ciclo ocurre cuando vence


def test_al_agotar_intentos_queda_fallida_y_avisa_al_manejador():
    seen = []

    def boom(payload, last_attempt):
        seen.append(last_attempt)
        raise RuntimeError("siempre falla")

    jobs.register("demo")(boom)
    job = jobs.enqueue("demo", max_attempts=2)
    now = timezone.now()
    jobs.process_once(now=now)
    jobs.process_once(now=now + timedelta(minutes=5))
    job.refresh_from_db()
    assert job.status == "FAILED" and job.attempts == 2 and seen == [False, True]
    jobs.process_once(now=now + timedelta(hours=1))
    assert seen == [False, True]  # una tarea fallida no se vuelve a ejecutar


def test_tipo_sin_manejador_falla_sin_romper_el_worker():
    job = jobs.enqueue("desconocida", max_attempts=1)
    jobs.process_once()
    job.refresh_from_db()
    assert job.status == "FAILED" and "No hay manejador" in job.last_error


def test_un_fallo_no_impide_ejecutar_las_demas():
    done = []

    def boom(payload, last_attempt):
        raise RuntimeError("x")

    jobs.register("malo")(boom)
    jobs.register("bueno")(lambda payload, last_attempt: done.append(1))
    jobs.enqueue("malo")
    jobs.enqueue("bueno")
    jobs.process_once()
    assert done == [1]


def test_recupera_tareas_abandonadas_por_un_worker_caido():
    job = jobs.enqueue("demo")
    Job.objects.filter(pk=job.pk).update(status="RUNNING", locked_at=timezone.now() - timedelta(minutes=11))
    assert jobs.recover_stale() == 1
    job.refresh_from_db()
    assert job.status == "PENDING" and job.locked_at is None
    # una tarea en ejecucion reciente no se toca
    Job.objects.filter(pk=job.pk).update(status="RUNNING", locked_at=timezone.now())
    assert jobs.recover_stale() == 0


def test_tarea_periodica_corre_una_vez_por_intervalo():
    calls = []
    jobs.periodic("tick", every=60)(lambda: calls.append(1))
    now = timezone.now()
    assert jobs.run_periodic_due(now) == ["tick"]  # la primera vez corre de inmediato
    assert jobs.run_periodic_due(now + timedelta(seconds=30)) == []
    assert jobs.run_periodic_due(now + timedelta(seconds=61)) == ["tick"]
    assert len(calls) == 2 and PeriodicRun.objects.count() == 1


def test_fallo_en_tarea_periodica_no_detiene_las_otras():
    calls = []

    def boom():
        raise RuntimeError("x")

    jobs.periodic("a_malo", every=60)(boom)
    jobs.periodic("b_bueno", every=60)(lambda: calls.append(1))
    assert jobs.run_periodic_due() == ["b_bueno"]


def test_backoff_tiene_techo():
    assert [jobs.backoff(n).total_seconds() for n in (1, 2, 3, 4, 5)] == [30, 60, 120, 240, 480]
    assert jobs.backoff(10).total_seconds() == 600


def test_comando_run_worker_once(capsys):
    calls = []
    jobs.register("demo")(lambda payload, last_attempt: calls.append(1))
    jobs.enqueue("demo")
    call_command("run_worker", "--once")
    assert calls == [1] and "'jobs': 1" in capsys.readouterr().out
