import logging
import signal
import time

from django.core.management.base import BaseCommand

from modules.core import jobs

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Worker de tareas: publicaciones programadas, envios de WhatsApp, alertas y retencion."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=float, default=10.0, help="Segundos entre ciclos (def. 10)")
        parser.add_argument("--once", action="store_true", help="Ejecuta un solo ciclo y termina")

    def handle(self, *args, **options):
        running = True

        def stop(signum, frame):
            nonlocal running
            running = False

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)

        if options["once"]:
            self.stdout.write(str(jobs.process_once()))
            return
        self.stdout.write(f"Worker iniciado (cada {options['interval']} s)")
        while running:
            try:
                result = jobs.process_once()
                if result["jobs"] or result["periodic"]:
                    logger.info("ciclo: %s", result)
            except Exception:  # noqa: BLE001 - el worker no debe morir por un ciclo fallido
                logger.exception("Fallo el ciclo del worker")
            time.sleep(options["interval"])
        self.stdout.write("Worker detenido")
