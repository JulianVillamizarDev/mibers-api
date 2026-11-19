from django.dispatch import Signal

# Se emite tras cada cambio de estado de una pieza.
# kwargs: pieza, old_state, new_state, actor
# Los modulos superiores (publishing) se suscriben para, por ejemplo, cancelar tareas de
# publicacion pendientes cuando una pieza programada vuelve a revision.
piece_state_changed = Signal()
