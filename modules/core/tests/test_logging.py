"""La configuracion de logging no debe exponer URLs de httpx con appsecret_proof."""

from django.conf import settings


def test_httpx_y_httpcore_quedan_en_warning():
    for logger_name in ("httpx", "httpcore"):
        config = settings.LOGGING["loggers"][logger_name]
        assert config["level"] == "WARNING"
        assert config["propagate"] is False
