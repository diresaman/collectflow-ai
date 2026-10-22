from django.db import connections
from django.test import TransactionTestCase

from .services import install_triggers, remove_triggers


class TimelineTransactionTestCase(TransactionTestCase):
    """
    TransactionTestCase empties every table after each test (TRUNCATE on PostgreSQL, DELETE on
    SQLite), which the append-only triggers refuse. Lift them only for that flush, on every database
    the test uses.
    """

    def _fixture_teardown(self) -> None:
        aliases = sorted(self.databases)
        for alias in aliases:
            remove_triggers(connections[alias])
        try:
            super()._fixture_teardown()
        finally:
            for alias in aliases:
                install_triggers(connections[alias])
