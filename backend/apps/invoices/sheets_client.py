"""
Google Sheets adapter. This module is the only place that talks to the Google API;
it knows nothing about invoices or the database.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any

import httplib2
from django.conf import settings
from google.auth.exceptions import GoogleAuthError
from google.oauth2 import service_account
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
REQUEST_TIMEOUT_SECONDS = 30


class SheetsFetchError(Exception):
    """Raised when spreadsheet values cannot be retrieved. Messages are safe to show users."""


@dataclass(frozen=True)
class SheetData:
    values: list[list[Any]]
    # Spreadsheet row number of values[0], used for error messages.
    start_row: int


def _start_row_from_range(a1_range: str) -> int:
    """'Sheet1!A3:G20' -> 3. Defaults to 1 when the range has no row number."""
    cells = a1_range.rsplit("!", 1)[-1]
    match = re.match(r"^\$?[A-Za-z]*\$?(\d+)", cells)
    return int(match.group(1)) if match else 1


class GoogleSheetsClient:
    def __init__(self, spreadsheet_id: str, a1_range: str, credentials_path: str) -> None:
        self.spreadsheet_id = spreadsheet_id
        self.a1_range = a1_range
        self.credentials_path = credentials_path

    @classmethod
    def from_settings(cls) -> "GoogleSheetsClient":
        missing = [
            name
            for name in (
                "GOOGLE_SHEETS_SPREADSHEET_ID",
                "GOOGLE_SHEETS_RANGE",
                "GOOGLE_APPLICATION_CREDENTIALS",
            )
            if not getattr(settings, name)
        ]
        if missing:
            raise SheetsFetchError(f"Google Sheets is not configured. Missing: {', '.join(missing)}.")
        return cls(
            spreadsheet_id=settings.GOOGLE_SHEETS_SPREADSHEET_ID,
            a1_range=settings.GOOGLE_SHEETS_RANGE,
            credentials_path=settings.GOOGLE_APPLICATION_CREDENTIALS,
        )

    def fetch_values(self) -> SheetData:
        try:
            credentials = service_account.Credentials.from_service_account_file(
                self.credentials_path, scopes=[READONLY_SCOPE]
            )
        except FileNotFoundError:
            raise SheetsFetchError("Google credentials file was not found.") from None
        except (ValueError, KeyError, OSError):
            # Never include the exception text: it can contain key material.
            raise SheetsFetchError("Google credentials file could not be loaded.") from None

        http = AuthorizedHttp(credentials, http=httplib2.Http(timeout=REQUEST_TIMEOUT_SECONDS))
        try:
            service = build("sheets", "v4", http=http, cache_discovery=False)
            response = (
                service.spreadsheets()
                .values()
                .get(
                    spreadsheetId=self.spreadsheet_id,
                    range=self.a1_range,
                    majorDimension="ROWS",
                    # Numbers come back as numbers and dates as serial numbers, independent
                    # of the sheet's display formatting and locale.
                    valueRenderOption="UNFORMATTED_VALUE",
                    dateTimeRenderOption="SERIAL_NUMBER",
                )
                .execute(num_retries=2)
            )
        except HttpError as exc:
            status = exc.resp.status if exc.resp is not None else "unknown"
            logger.warning("Google Sheets API returned HTTP %s", status)
            raise SheetsFetchError(_describe_http_error(exc)) from None
        except GoogleAuthError:
            logger.warning("Google authentication failed")
            raise SheetsFetchError("Google authentication failed. Check the service account key.") from None
        except (httplib2.HttpLib2Error, OSError, TimeoutError):
            logger.warning("Network error contacting Google Sheets")
            raise SheetsFetchError("Could not reach the Google Sheets API (network error or timeout).") from None

        values = response.get("values", [])
        if not isinstance(values, list):
            raise SheetsFetchError("Google Sheets returned an unexpected response.")
        return SheetData(
            values=values,
            start_row=_start_row_from_range(response.get("range", self.a1_range)),
        )


def _describe_http_error(exc: HttpError) -> str:
    status = exc.resp.status if exc.resp is not None else None
    if status == 403:
        return (
            "Google Sheets denied access (HTTP 403). Share the spreadsheet with the service "
            "account email and make sure the Sheets API is enabled."
        )
    if status == 404:
        return "Spreadsheet not found (HTTP 404). Check GOOGLE_SHEETS_SPREADSHEET_ID."
    if status == 400:
        return "Google Sheets rejected the request (HTTP 400). Check GOOGLE_SHEETS_RANGE."
    return f"Google Sheets API request failed (HTTP {status})."
