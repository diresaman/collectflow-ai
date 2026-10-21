from django.test import TransactionTestCase

from .services import install_triggers, remove_triggers


class TimelineTransactionTestCase(TransactionTestCase):
    """
    TransactionTestCase empties tables with DELETE after each test, which the append-only
    triggers refuse. Lift them only for that flush.
    """

    def _fixture_teardown(self) -> None:
        remove_triggers()
        try:
            super()._fixture_teardown()
        finally:
            install_triggers()
