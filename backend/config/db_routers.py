class SourceDatabaseRouter:
    """
    The optional `sqlite_source` alias (manage.py migrate_from_sqlite) is never migrated. It is opened
    with SQLite's `mode=ro`, so writes to it fail at the driver as well.
    """

    SOURCE = "sqlite_source"

    def allow_migrate(self, db, app_label, **hints):  # type: ignore[no-untyped-def]
        return False if db == self.SOURCE else None
