import base64
import os
import stat
import tempfile
from email import message_from_bytes
from pathlib import Path
from unittest import mock

import httplib2
from django.test import SimpleTestCase
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from integrations.gmail import (
    SEND_SCOPE,
    DeliveryResult,
    GmailClient,
    GmailDeliveryUnknown,
    GmailNotReady,
    GmailRejected,
    build_message,
    encode_message,
    save_token,
)

SENDER = "sender@example.test"


def http_error(status: int) -> HttpError:
    return HttpError(httplib2.Response({"status": status}), b'{"error": {"message": "nope"}}')


class MessageBuildingTests(SimpleTestCase):
    def test_mime_message_round_trips_through_urlsafe_base64(self) -> None:
        raw = encode_message(
            build_message(sender=SENDER, recipient="client@example.test", subject="Invoice INV-1", body="Hello ünïcode")
        )

        self.assertNotIn("+", raw)
        self.assertNotIn("/", raw)
        parsed = message_from_bytes(base64.urlsafe_b64decode(raw))
        self.assertEqual(parsed["From"], SENDER)
        self.assertEqual(parsed["To"], "client@example.test")
        self.assertEqual(parsed["Subject"], "Invoice INV-1")
        self.assertEqual(parsed.get_content_type(), "text/plain")
        self.assertIn("Hello ünïcode", parsed.get_payload(decode=True).decode("utf-8"))

    def test_header_injection_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_message(sender=SENDER, recipient="a@b.test", subject="Hi\nBcc: victim@example.test", body="x")


class TokenStorageTests(SimpleTestCase):
    def test_token_is_written_with_owner_only_permissions(self) -> None:
        credentials = mock.Mock()
        credentials.to_json.return_value = '{"token": "t"}'
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / "gmail-token.json"
            save_token(credentials, token_file)

            self.assertEqual(token_file.read_text(), '{"token": "t"}')
            self.assertEqual(stat.S_IMODE(os.stat(token_file).st_mode), 0o600)


def ready_client(email: str = SENDER, scopes: set[str] | None = None) -> GmailClient:
    client = GmailClient(token_file="/tmp/does-not-matter.json", sender_email=SENDER)
    credentials = mock.Mock(valid=True, token="secret-access-token")
    patcher_load = mock.patch.object(GmailClient, "_load_credentials", return_value=credentials)
    patcher_identity = mock.patch.object(
        GmailClient, "_token_identity", return_value=(email, scopes if scopes is not None else {SEND_SCOPE})
    )
    patcher_load.start()
    patcher_identity.start()
    return client


class ReadinessTests(SimpleTestCase):
    def tearDown(self) -> None:
        mock.patch.stopall()

    def test_missing_token_file_is_not_ready(self) -> None:
        status = GmailClient(token_file="/nonexistent/token.json", sender_email=SENDER).check_ready()
        self.assertFalse(status.ready)
        self.assertIn("authorize_gmail", status.detail)

    def test_missing_sender_is_not_ready(self) -> None:
        self.assertFalse(GmailClient(token_file="/tmp/t.json", sender_email="").check_ready().ready)

    @mock.patch("integrations.gmail.Credentials.from_authorized_user_file")
    def test_revoked_refresh_token_is_not_ready(self, load: mock.Mock) -> None:
        credentials = mock.Mock(valid=False, refresh_token="refresh-secret")
        credentials.refresh.side_effect = RefreshError("invalid_grant: Token has been expired or revoked.")
        load.return_value = credentials
        with tempfile.NamedTemporaryFile(suffix=".json") as token_file:
            with self.assertLogs("integrations.gmail"):
                status = GmailClient(token_file=token_file.name, sender_email=SENDER).check_ready()

        self.assertFalse(status.ready)
        self.assertIn("revoked or expired", status.detail)
        self.assertNotIn("refresh-secret", status.detail)

    @mock.patch("integrations.gmail.save_token")
    @mock.patch.object(GmailClient, "_token_identity", return_value=(SENDER, {SEND_SCOPE}))
    @mock.patch("integrations.gmail.Credentials.from_authorized_user_file")
    def test_expired_token_is_refreshed_and_saved(self, load: mock.Mock, identity: mock.Mock, save: mock.Mock) -> None:
        credentials = mock.Mock(valid=False, refresh_token="refresh")
        load.return_value = credentials
        with tempfile.NamedTemporaryFile(suffix=".json") as token_file:
            status = GmailClient(token_file=token_file.name, sender_email=SENDER).check_ready()

        self.assertTrue(status.ready)
        credentials.refresh.assert_called_once()
        save.assert_called_once()

    def test_sender_must_match_authorized_account(self) -> None:
        status = ready_client(email="someone-else@example.test").check_ready()
        self.assertFalse(status.ready)
        self.assertIn("does not match", status.detail)

    def test_send_scope_is_required(self) -> None:
        status = ready_client(scopes={"openid"}).check_ready()
        self.assertFalse(status.ready)
        self.assertIn("gmail.send", status.detail)

    def test_ready(self) -> None:
        status = ready_client().check_ready()
        self.assertTrue(status.ready)
        self.assertNotIn("secret-access-token", repr(status))


@mock.patch("integrations.gmail.build")
class SendTests(SimpleTestCase):
    def tearDown(self) -> None:
        mock.patch.stopall()

    def gmail_client(self) -> GmailClient:
        client = ready_client()
        client.check_ready()
        return client

    def send_request(self, build: mock.Mock) -> mock.Mock:
        return build.return_value.users.return_value.messages.return_value.send

    def send(self, client: GmailClient) -> DeliveryResult:
        return client.send(recipient="client@example.test", subject="Invoice INV-1", body="Please pay.")

    def test_success_returns_ids_and_never_retries(self, build: mock.Mock) -> None:
        self.send_request(build).return_value.execute.return_value = {"id": "m-1", "threadId": "t-1"}

        result = self.send(self.gmail_client())

        self.assertEqual(result, DeliveryResult(message_id="m-1", thread_id="t-1"))
        kwargs = self.send_request(build).call_args.kwargs
        self.assertEqual(kwargs["userId"], "me")
        parsed = message_from_bytes(base64.urlsafe_b64decode(kwargs["body"]["raw"]))
        self.assertEqual((parsed["From"], parsed["To"]), (SENDER, "client@example.test"))
        self.send_request(build).return_value.execute.assert_called_once_with(num_retries=0)

    def test_send_requires_readiness_check(self, build: mock.Mock) -> None:
        with self.assertRaises(GmailNotReady):
            GmailClient(token_file="/tmp/t.json", sender_email=SENDER).send(recipient="a@b.test", subject="s", body="b")
        build.assert_not_called()

    def test_4xx_is_a_confirmed_rejection(self, build: mock.Mock) -> None:
        for status in (400, 401, 403, 429):
            with self.subTest(status=status):
                self.send_request(build).return_value.execute.side_effect = http_error(status)
                with self.assertRaises(GmailRejected) as ctx, self.assertLogs("integrations.gmail"):
                    self.send(self.gmail_client())
                self.assertEqual(ctx.exception.code, f"gmail_http_{status}")

    def test_5xx_is_ambiguous(self, build: mock.Mock) -> None:
        self.send_request(build).return_value.execute.side_effect = http_error(503)
        with self.assertRaises(GmailDeliveryUnknown), self.assertLogs("integrations.gmail"):
            self.send(self.gmail_client())

    def test_timeout_is_ambiguous(self, build: mock.Mock) -> None:
        for error in (TimeoutError("read timed out"), ConnectionResetError("reset"), httplib2.HttpLib2Error("x")):
            with self.subTest(error=type(error).__name__):
                self.send_request(build).return_value.execute.side_effect = error
                with self.assertRaises(GmailDeliveryUnknown), self.assertLogs("integrations.gmail"):
                    self.send(self.gmail_client())

    def test_connection_never_established_is_a_confirmed_failure(self, build: mock.Mock) -> None:
        for error in (httplib2.ServerNotFoundError("dns"), ConnectionRefusedError()):
            with self.subTest(error=type(error).__name__):
                self.send_request(build).return_value.execute.side_effect = error
                with self.assertRaises(GmailRejected), self.assertLogs("integrations.gmail"):
                    self.send(self.gmail_client())

    def test_response_without_message_id_is_ambiguous(self, build: mock.Mock) -> None:
        self.send_request(build).return_value.execute.return_value = {"labelIds": ["SENT"]}
        with self.assertRaises(GmailDeliveryUnknown):
            self.send(self.gmail_client())
