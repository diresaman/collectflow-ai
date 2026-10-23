"""
Architecture boundary (v2 §9): nothing scheduled may reach Gmail send. The sequence, inbox (including
M10 reply polling and classification) and automation apps must not import the delivery module or the
Gmail send client; only `followups` (behind a person's explicit confirmation) does. The Gmail read
adapter (integrations.mailbox) must not import the send client either. import-linter will enforce the
same contract in CI (Phase 08).
"""

import ast
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

FORBIDDEN = ("apps.followups.delivery", "integrations.gmail")
RESTRICTED_APPS = ("sequences", "inbox", "automation", "insights")


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


class BoundaryTests(SimpleTestCase):
    def test_scheduled_apps_cannot_import_gmail_delivery(self) -> None:
        apps_dir = Path(settings.BASE_DIR) / "apps"
        checked = 0
        for app in RESTRICTED_APPS:
            for path in (apps_dir / app).rglob("*.py"):
                if "tests" in path.parts or "migrations" in path.parts:
                    continue
                checked += 1
                bad = {m for m in imported_modules(path) if m.startswith(FORBIDDEN)}
                with self.subTest(file=str(path.relative_to(apps_dir))):
                    self.assertEqual(bad, set())
        self.assertGreater(checked, 10)

    def test_mailbox_reader_has_no_path_to_sending(self) -> None:
        # M10: reply reading (integrations.mailbox) is separate from Gmail send.
        path = Path(settings.BASE_DIR) / "integrations" / "mailbox.py"
        bad = {m for m in imported_modules(path) if m.startswith(FORBIDDEN)}
        self.assertEqual(bad, set())
