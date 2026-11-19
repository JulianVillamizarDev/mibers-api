"""Las tareas recurrentes de cada modulo quedan registradas al arrancar Django."""

from modules.core import jobs


def test_tareas_periodicas_registradas():
    assert {"publishing.tick", "audit.purge_expired", "clients.purge_expired"} <= set(jobs._PERIODIC)
    every = {name: seconds for name, (seconds, _) in jobs._PERIODIC.items()}
    assert every["publishing.tick"] == 60  # RNF06: +-5 min
    assert every["audit.purge_expired"] == every["clients.purge_expired"] == 24 * 3600
