from unittest import mock

import httplib2
from django.test import SimpleTestCase
from googleapiclient.errors import HttpError

from apps.invoices.sheets_client import (
    READONLY_SCOPE,
    GoogleSheetsClient,
    SheetsFetchError,
    _start_row_from_range,
)


def http_error(status: int) -> HttpError:
    return HttpError(httplib2.Response({"status": status}), b'{"error": "denied"}')


@mock.patch("apps.invoices.sheets_client.service_account.Credentials.from_service_account_file")
@mock.patch("apps.invoices.sheets_client.build")
class GoogleSheetsClientTests(SimpleTestCase):
    def make_client(self) -> GoogleSheetsClient:
        return GoogleSheetsClient("sheet-id", "Invoices!A1:G", "/secrets/key.json")

    def values_get(self, build: mock.Mock) -> mock.Mock:
        return build.return_value.spreadsheets.return_value.values.return_value.get

    def test_reads_values_with_readonly_scope(self, build: mock.Mock, load_credentials: mock.Mock) -> None:
        self.values_get(build).return_value.execute.return_value = {
            "range": "Invoices!A1:G3",
            "values": [["invoice_id"], ["INV-1"]],
        }

        data = self.make_client().fetch_values()

        self.assertEqual(data.values, [["invoice_id"], ["INV-1"]])
        self.assertEqual(data.start_row, 1)
        load_credentials.assert_called_once_with("/secrets/key.json", scopes=[READONLY_SCOPE])
        kwargs = self.values_get(build).call_args.kwargs
        self.assertEqual((kwargs["spreadsheetId"], kwargs["range"]), ("sheet-id", "Invoices!A1:G"))

    def test_http_error_becomes_safe_fetch_error(self, build: mock.Mock, load_credentials: mock.Mock) -> None:
        self.values_get(build).return_value.execute.side_effect = http_error(403)

        with self.assertRaises(SheetsFetchError) as ctx, self.assertLogs("apps.invoices.sheets_client"):
            self.make_client().fetch_values()

        self.assertIn("HTTP 403", str(ctx.exception))

    def test_network_error_becomes_fetch_error(self, build: mock.Mock, load_credentials: mock.Mock) -> None:
        self.values_get(build).return_value.execute.side_effect = TimeoutError()

        with self.assertRaises(SheetsFetchError), self.assertLogs("apps.invoices.sheets_client"):
            self.make_client().fetch_values()

    def test_credential_errors_do_not_leak_key_material(self, build: mock.Mock, load_credentials: mock.Mock) -> None:
        load_credentials.side_effect = ValueError("-----BEGIN PRIVATE KEY----- secret")

        with self.assertRaises(SheetsFetchError) as ctx:
            self.make_client().fetch_values()

        self.assertNotIn("PRIVATE KEY", str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)
        build.assert_not_called()

    def test_missing_credentials_file(self, build: mock.Mock, load_credentials: mock.Mock) -> None:
        load_credentials.side_effect = FileNotFoundError()

        with self.assertRaisesMessage(SheetsFetchError, "not found"):
            self.make_client().fetch_values()


class StartRowTests(SimpleTestCase):
    def test_start_row_parsing(self) -> None:
        cases = {"Sheet1!A1:G": 1, "'My Sheet'!A3:G20": 3, "Sheet1!A:G": 1, "A5:G": 5, "Sheet1!$A$2:$G": 2}
        for a1_range, expected in cases.items():
            with self.subTest(a1_range=a1_range):
                self.assertEqual(_start_row_from_range(a1_range), expected)
