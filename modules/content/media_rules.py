"""Reglas de formato, relacion de aspecto, duracion y tamano por (red, tipo de pieza) (RF8, RNF08).

IMPORTANTE: son valores iniciales y conservadores (rechazar de mas es preferible a publicar algo
invalido). Deben contrastarse con la documentacion vigente de Meta antes de salir a produccion;
estan centralizados aqui para ajustarlos en un solo lugar.
"""

from dataclasses import dataclass, field

from modules.core.networks import Network

MB = 1024 * 1024
IMAGE_FORMATS = frozenset({"jpeg", "png"})
VIDEO_FORMATS = frozenset({"mp4", "mov"})
ALLOWED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".mp4", ".mov"})


@dataclass(frozen=True)
class MediaInfo:
    kind: str  # "image" | "video"
    fmt: str  # "jpeg", "png", "mp4", "mov"
    width: int
    height: int
    size: int  # bytes
    duration: float | None = None  # segundos (solo video)

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 0.0


@dataclass(frozen=True)
class Rule:
    allow_image: bool = True
    allow_video: bool = False
    aspect: tuple[float, float] = (0.4, 2.5)  # ancho/alto, ambos inclusivos
    min_items: int = 1
    max_items: int = 1
    min_width: int = 0
    image_max_bytes: int = 8 * MB
    video_max_bytes: int = 300 * MB
    video_duration: tuple[float, float] = (3.0, 90.0)
    image_formats: frozenset = field(default=IMAGE_FORMATS)
    video_formats: frozenset = field(default=VIDEO_FORMATS)


_VERTICAL = (0.5, 0.65)  # 9:16 con tolerancia

RULES: dict[tuple[str, str], Rule] = {
    # --- Instagram ---
    (Network.INSTAGRAM, "POST"): Rule(aspect=(0.8, 1.91), min_width=320),
    (Network.INSTAGRAM, "CARRUSEL"): Rule(aspect=(0.8, 1.91), min_items=2, max_items=10, min_width=320),
    (Network.INSTAGRAM, "REEL"): Rule(
        allow_image=False, allow_video=True, aspect=(0.5, 1.0), video_duration=(3.0, 90.0)
    ),
    (Network.INSTAGRAM, "HISTORIA"): Rule(
        allow_video=True, aspect=_VERTICAL, video_duration=(3.0, 60.0), min_width=320
    ),
    (Network.INSTAGRAM, "PORTADA"): Rule(aspect=(0.9, 1.1), min_width=110),
    # --- Facebook ---
    (Network.FACEBOOK, "POST"): Rule(
        allow_video=True, image_max_bytes=10 * MB, video_max_bytes=1024 * MB, video_duration=(1.0, 3600.0)
    ),
    (Network.FACEBOOK, "CARRUSEL"): Rule(min_items=2, max_items=10, image_max_bytes=10 * MB),
    (Network.FACEBOOK, "REEL"): Rule(
        allow_image=False, allow_video=True, aspect=_VERTICAL, video_duration=(3.0, 90.0)
    ),
    (Network.FACEBOOK, "HISTORIA"): Rule(
        allow_video=True, aspect=_VERTICAL, video_duration=(1.0, 120.0), min_width=320
    ),
    (Network.FACEBOOK, "PORTADA"): Rule(aspect=(2.3, 2.8), min_width=400, image_max_bytes=10 * MB),
}


def rule_for(red: str, tipo: str) -> Rule:
    return RULES[(red, tipo)]


def validate_file(info: MediaInfo, red: str, tipo: str) -> list[str]:
    """Errores de un archivo para una red; lista vacia si es valido."""
    rule = rule_for(red, tipo)
    label = Network(red).label
    errors: list[str] = []
    if info.kind == "image":
        if not rule.allow_image:
            return [f"{label}: una pieza de tipo {tipo.lower()} no admite imagenes."]
        if info.fmt not in rule.image_formats:
            errors.append(f"{label}: formato de imagen no permitido ({info.fmt}); usa JPG o PNG.")
        if info.size > rule.image_max_bytes:
            errors.append(f"{label}: la imagen supera {rule.image_max_bytes // MB} MB.")
    else:
        if not rule.allow_video:
            return [f"{label}: una pieza de tipo {tipo.lower()} no admite video."]
        if info.fmt not in rule.video_formats:
            errors.append(f"{label}: formato de video no permitido ({info.fmt}); usa MP4 o MOV.")
        if info.size > rule.video_max_bytes:
            errors.append(f"{label}: el video supera {rule.video_max_bytes // MB} MB.")
        low, high = rule.video_duration
        if info.duration is None or not (low <= info.duration <= high):
            errors.append(f"{label}: la duracion debe estar entre {low:g} y {high:g} segundos.")
    low, high = rule.aspect
    if not (low <= info.aspect <= high):
        errors.append(
            f"{label}: relacion de aspecto {info.width}x{info.height} fuera del rango permitido "
            f"({low:g} a {high:g} de ancho/alto)."
        )
    if info.width < rule.min_width:
        errors.append(f"{label}: el ancho minimo es {rule.min_width}px.")
    return errors


def validate_set(infos: list[MediaInfo], red: str, tipo: str) -> list[str]:
    """Errores del conjunto de archivos de una pieza para una red (cantidad + cada archivo)."""
    rule = rule_for(red, tipo)
    label = Network(red).label
    errors: list[str] = []
    if len(infos) < rule.min_items:
        errors.append(f"{label}: se requieren al menos {rule.min_items} archivo(s).")
    if len(infos) > rule.max_items:
        errors.append(f"{label}: se admiten como maximo {rule.max_items} archivo(s).")
    for info in infos:
        errors.extend(validate_file(info, red, tipo))
    return errors
