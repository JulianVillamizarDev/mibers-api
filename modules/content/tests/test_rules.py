import pytest

from modules.content import media_rules, state_machine
from modules.content.models import Estado
from modules.core.exceptions import DomainError
from modules.core.networks import Network

from .helpers import info

IG, FB = Network.INSTAGRAM, Network.FACEBOOK
MB = media_rules.MB


# --- RF8 / RNF08: formato, aspecto, duracion y tamano -----------------------------------
@pytest.mark.parametrize(
    ("kwargs", "red", "tipo", "valid"),
    [
        (dict(width=1080, height=1350), IG, "POST", True),  # 4:5
        (dict(width=1080, height=1080), IG, "POST", True),  # 1:1
        (dict(width=1080, height=566), IG, "POST", True),  # 1.91:1
        (dict(width=1080, height=1920), IG, "POST", False),  # 9:16 no es de feed
        (dict(width=2000, height=500), IG, "POST", False),  # demasiado panoramica
        (dict(width=200, height=250), IG, "POST", False),  # ancho minimo
        (dict(width=1080, height=1350, size=9 * MB), IG, "POST", False),  # pesa mas de 8 MB
        (dict(width=1080, height=1350, fmt="webp"), IG, "POST", False),  # formato
        (dict(width=1080, height=1920), IG, "HISTORIA", True),
        (dict(width=1080, height=1080), IG, "HISTORIA", False),
        (dict(width=1080, height=1080), IG, "PORTADA", True),
        (dict(width=820, height=312), FB, "PORTADA", True),
        (dict(width=1080, height=1080), FB, "PORTADA", False),
        (dict(width=1200, height=630), FB, "POST", True),
    ],
)
def test_reglas_de_imagen(kwargs, red, tipo, valid):
    assert (media_rules.validate_file(info(**kwargs), red, tipo) == []) is valid


@pytest.mark.parametrize(
    ("duration", "valid"),
    [(2.9, False), (3.0, True), (45.0, True), (90.0, True), (90.1, False), (None, False)],
)
def test_duracion_de_reel(duration, valid):
    video = info(kind="video", fmt="mp4", width=1080, height=1920, duration=duration)
    assert (media_rules.validate_file(video, IG, "REEL") == []) is valid


def test_historia_de_instagram_admite_video_hasta_60_segundos():
    ok = info(kind="video", fmt="mov", width=1080, height=1920, duration=59)
    long = info(kind="video", fmt="mov", width=1080, height=1920, duration=61)
    assert media_rules.validate_file(ok, IG, "HISTORIA") == []
    assert media_rules.validate_file(long, IG, "HISTORIA") != []


def test_un_reel_no_admite_imagenes_ni_un_post_de_ig_video():
    assert media_rules.validate_file(info(), IG, "REEL") != []
    video = info(kind="video", fmt="mp4", width=1080, height=1350, duration=20)
    assert media_rules.validate_file(video, IG, "POST") != []
    assert media_rules.validate_file(video, FB, "POST") == []


def test_video_con_formato_no_permitido():
    video = info(kind="video", fmt="avi", width=1080, height=1920, duration=20)
    assert any("formato" in e for e in media_rules.validate_file(video, IG, "REEL"))


def test_carrusel_exige_entre_2_y_10_archivos():
    one = [info()]
    many = [info() for _ in range(11)]
    ok = [info(), info(), info()]
    assert media_rules.validate_set(one, IG, "CARRUSEL") != []
    assert media_rules.validate_set(many, IG, "CARRUSEL") != []
    assert media_rules.validate_set(ok, IG, "CARRUSEL") == []


def test_post_admite_un_solo_archivo_y_exige_al_menos_uno():
    assert media_rules.validate_set([], FB, "POST") != []
    assert media_rules.validate_set([info(), info()], FB, "POST") != []


def test_todas_las_combinaciones_red_tipo_tienen_regla():
    for red in Network.values:
        for tipo in ("POST", "CARRUSEL", "REEL", "HISTORIA", "PORTADA"):
            assert (red, tipo) in media_rules.RULES


# --- RF10 / RF12: maquina de estados ---------------------------------------------------------
VALID = [
    ("BORRADOR", "EN_REVISION"),
    ("EN_REVISION", "APROBADA"),
    ("EN_REVISION", "CAMBIOS_SOLICITADOS"),
    ("CAMBIOS_SOLICITADOS", "EN_REVISION"),
    ("APROBADA", "PROGRAMADA"),
    ("APROBADA", "EN_REVISION"),
    ("PROGRAMADA", "PUBLICADA"),
    ("PROGRAMADA", "FALLIDA"),
    ("PROGRAMADA", "EN_REVISION"),
    ("FALLIDA", "PROGRAMADA"),
    ("PUBLICADA", "COMPARTIDA"),
]


@pytest.mark.parametrize(("current", "target"), VALID)
def test_transiciones_validas(current, target):
    assert state_machine.can_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("BORRADOR", "APROBADA"),  # nada sale sin revision
        ("BORRADOR", "PUBLICADA"),
        ("EN_REVISION", "PROGRAMADA"),  # hay que aprobar antes de programar
        ("CAMBIOS_SOLICITADOS", "APROBADA"),
        ("APROBADA", "PUBLICADA"),
        ("PUBLICADA", "BORRADOR"),
        ("COMPARTIDA", "PUBLICADA"),
        ("PUBLICADA", "EN_REVISION"),
    ],
)
def test_transiciones_invalidas(current, target):
    assert not state_machine.can_transition(current, target)
    with pytest.raises(DomainError) as error:
        state_machine.ensure_transition(current, target)
    assert error.value.code == "invalid_transition" and error.value.status_code == 409


def test_todos_los_estados_estan_en_la_tabla():
    assert set(state_machine.TRANSITIONS) == set(Estado.values)
