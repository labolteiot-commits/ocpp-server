"""Sprint Certification — Module de certification automatisée OCPP 1.6J.

Ce package expose un runner async qui exécute des suites de tests sur une
borne OCPP et produit un rapport HTML+JSON destiné au technicien de labo.

Surface publique :
  * ``catalog.SUITES`` / ``catalog.TESTS`` — définitions des tests
  * ``runner.CertificationRun`` — state machine d'un run
  * ``events.EventBus`` — pub/sub async pour WebSocket
  * ``report.render_html`` / ``render_json`` — génération rapports
"""

from certification.catalog import SUITES, TESTS, get_suite_tests
from certification.events import EventBus, get_bus
from certification.runner import CertificationRun, start_run, get_run, cancel_run

__all__ = [
    "SUITES",
    "TESTS",
    "get_suite_tests",
    "EventBus",
    "get_bus",
    "CertificationRun",
    "start_run",
    "get_run",
    "cancel_run",
]
