import tempfile
from unittest import mock

import httplib2
from django.test import SimpleTestCase, override_settings
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from integrations.mailbox import (
    READ_SCOPE,
    HistoryExpired,
    MailboxNotAuthorized,
    MailboxNotReady,
    MailboxReader,
    MailboxUnavailable,
    parse_message,
)

MAILBOX = "ar@collectflow.test"


def http_error(status: int) -> HttpError:
    return HttpError(httplib2.Response({"status": status}), b"{}")


@override_settings(GMAIL_READ_ENABLED=True)
@mock.patch("integrations.mailbox.build")
@mock.patch("integrations.mailbox.Credentials.from_authorized_user_file")
class MailboxReaderTests(SimpleTestCase):
    def setUp(self) -> None:
        self.token = tempfile.NamedTemporaryFile(suffix=".json")
        self.addCleanup(self.token.close)

    def reader(self) -> MailboxReader:
        return MailboxReader(token_file=self.token.name, mailbox_email=MAILBOX)

    def users(self, build: mock.Mock) -> mock.Mock:
        return build.return_value.users.return_value

    def test_requests_only_the_read_scope_and_never_saves_the_token(self, load: mock.Mock, build: mock.Mock) -> None:
        load.return_value = mock.Mock(refresh_token="r")
        self.users(build).getProfile.return_value.execute.return_value = {"emailAddress": MAILBOX, "historyId": "77"}
        with mock.patch("integrations.gmail.save_token", side_effect=AssertionError("must not save")):
            self.assertEqual(self.reader().profile(), (MAILBOX, "77"))
        self.assertEqual(load.call_args.args[1], [READ_SCOPE])

    def test_profile_of_another_mailbox_is_refused(self, load: mock.Mock, build: mock.Mock) -> None:
        load.return_value = mock.Mock(refresh_token="r")
        self.users(build).getProfile.return_value.execute.return_value = {"emailAddress": "other@x.test", "historyId": "1"}
        with self.assertRaises(MailboxNotAuthorized):
            self.reader().profile()

    def test_refused_refresh_means_read_access_is_not_granted(self, load: mock.Mock, build: mock.Mock) -> None:
        credentials = mock.Mock(refresh_token="refresh-secret")
        credentials.refresh.side_effect = RefreshError("invalid_scope")
        load.return_value = credentials
        with self.assertLogs("integrations.mailbox"), self.assertRaises(MailboxNotAuthorized) as caught:
            self.reader().profile()
        self.assertNotIn("refresh-secret", str(caught.exception))
        self.assertIn("--with-read-access", str(caught.exception))

    def test_history_page_parsing(self, load: mock.Mock, build: mock.Mock) -> None:
        load.return_value = mock.Mock(refresh_token="r")
        self.users(build).history.return_value.list.return_value.execute.return_value = {
            "history": [{"messagesAdded": [{"message": {"id": "m1", "threadId": "t1", "labelIds": ["INBOX"]}}]},
                        {"messagesAdded": [{"message": {"id": "m2"}}]}],  # no thread: ignored
            "nextPageToken": "p2", "historyId": "90",
        }
        page = self.reader().history_page("80")
        self.assertEqual([(r.message_id, r.thread_id) for r in page.messages], [("m1", "t1")])
        self.assertEqual((page.next_page_token, page.history_id), ("p2", "90"))
        kwargs = self.users(build).history.return_value.list.call_args.kwargs
        self.assertEqual((kwargs["startHistoryId"], kwargs["historyTypes"]), ("80", ["messageAdded"]))
        self.users(build).history.return_value.list.return_value.execute.assert_called_once_with(num_retries=0)

    def test_error_mapping(self, load: mock.Mock, build: mock.Mock) -> None:
        load.return_value = mock.Mock(refresh_token="r")
        execute = self.users(build).history.return_value.list.return_value.execute
        for status, error in ((404, HistoryExpired), (401, MailboxNotAuthorized), (403, MailboxNotAuthorized),
                              (429, MailboxUnavailable), (500, MailboxUnavailable)):
            execute.side_effect = http_error(status)
            with self.subTest(status=status), self.assertLogs("integrations.mailbox"), self.assertRaises(error):
                self.reader().history_page("1")
        execute.side_effect = TimeoutError()
        with self.assertLogs("integrations.mailbox"), self.assertRaises(MailboxUnavailable):
            self.reader().history_page("1")

    def test_deleted_message_returns_none(self, load: mock.Mock, build: mock.Mock) -> None:
        load.return_value = mock.Mock(refresh_token="r")
        self.users(build).messages.return_value.get.return_value.execute.side_effect = http_error(404)
        with self.assertLogs("integrations.mailbox"):
            self.assertIsNone(self.reader().get_message("gone"))


class MailboxConfigurationTests(SimpleTestCase):
    @override_settings(GMAIL_READ_ENABLED=False)
    def test_disabled_reader_never_loads_credentials(self) -> None:
        with mock.patch("integrations.mailbox.Credentials.from_authorized_user_file", side_effect=AssertionError), \
                self.assertRaises(MailboxNotReady):
            MailboxReader(token_file="/tmp/x.json", mailbox_email=MAILBOX).profile()

    @override_settings(GMAIL_READ_ENABLED=True)
    def test_missing_token_is_not_ready(self) -> None:
        ready, detail = MailboxReader(token_file="/nonexistent.json", mailbox_email=MAILBOX).check_configured()
        self.assertFalse(ready)
        self.assertIn("--with-read-access", detail)

    def test_parse_message_headers(self) -> None:
        message = parse_message({"id": "m", "threadId": "t", "labelIds": ["INBOX"], "internalDate": "1700000000000",
                                 "payload": {"headers": [{"name": "From", "value": "A <a@x.test>"},
                                                         {"name": "Subject", "value": "Re: Hi"}]}})
        self.assertEqual((message.headers["from"], message.headers["subject"], message.internal_date_ms),
                         ("A <a@x.test>", "Re: Hi", 1700000000000))
