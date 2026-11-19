"""Ciclo de estados de la pieza (RF10, RF12). Toda transicion pasa por `ensure_transition`."""

from modules.core.exceptions import DomainError

from .models import Estado

E = Estado

TRANSITIONS: dict[str, frozenset[str]] = {
    E.BORRADOR: frozenset({E.EN_REVISION}),
    E.EN_REVISION: frozenset({E.APROBADA, E.CAMBIOS_SOLICITADOS}),
    E.CAMBIOS_SOLICITADOS: frozenset({E.EN_REVISION}),
    # Editar archivos o copy de una pieza aprobada/programada la devuelve a revision (RF12).
    E.APROBADA: frozenset({E.PROGRAMADA, E.EN_REVISION}),
    E.PROGRAMADA: frozenset({E.PUBLICADA, E.FALLIDA, E.APROBADA, E.EN_REVISION}),
    # Reintento (RF17): una fallida vuelve a programarse o se confirma como publicada.
    E.FALLIDA: frozenset({E.PROGRAMADA, E.PUBLICADA, E.EN_REVISION}),
    E.PUBLICADA: frozenset({E.COMPARTIDA}),
    E.COMPARTIDA: frozenset(),
}

# Estados en los que el contenido ya no puede editarse.
LOCKED_STATES = frozenset({E.PUBLICADA, E.COMPARTIDA})
# Estados que ya pasaron revision: editar contenido obliga a una nueva revision.
REVIEWED_STATES = frozenset({E.APROBADA, E.PROGRAMADA, E.FALLIDA})


def can_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, frozenset())


def ensure_transition(current: str, target: str) -> None:
    if not can_transition(current, target):
        raise DomainError(
            f"No se puede pasar de {Estado(current).label} a {Estado(target).label}.",
            "invalid_transition",
            409,
        )
