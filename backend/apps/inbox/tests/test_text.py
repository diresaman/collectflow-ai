import base64

from django.test import SimpleTestCase

from apps.inbox.text import extract_text, reply_text, strip_reply

from .helpers import payload


class ExtractTextTests(SimpleTestCase):
    def test_prefers_plain_text_and_ignores_attachments(self) -> None:
        data = payload("We paid yesterday.", html="<p>HTML version</p>", attachment="secret attachment text")
        self.assertEqual(extract_text(data), "We paid yesterday.")

    def test_html_only_is_converted_without_scripts_or_quotes(self) -> None:
        html = ("<html><head><style>p{}</style></head><body><p>Hello,</p><p>We dispute this.</p>"
                "<script>alert(1)</script><div class='gmail_quote'>On Mon you wrote: pay now</div></body></html>")
        text = extract_text(payload(html=html))
        self.assertIn("We dispute this.", text)
        self.assertNotIn("alert", text)
        self.assertNotIn("pay now", text)

    def test_nested_multipart_and_charset(self) -> None:
        latin1 = base64.urlsafe_b64encode("Café payé".encode("iso-8859-1")).decode()
        data = {"mimeType": "multipart/mixed", "parts": [{"mimeType": "multipart/alternative", "parts": [
            {"mimeType": "text/plain", "headers": [{"name": "Content-Type", "value": 'text/plain; charset="iso-8859-1"'}],
             "body": {"data": latin1}},
        ]}]}
        self.assertEqual(extract_text(data), "Café payé")

    def test_invalid_base64_and_unknown_charset_do_not_crash(self) -> None:
        data = {"mimeType": "text/plain", "headers": [{"name": "Content-Type", "value": "text/plain; charset=nope"}],
                "body": {"data": "@@@"}}
        self.assertIsInstance(extract_text(data), str)


class StripReplyTests(SimpleTestCase):
    def test_cuts_gmail_quote(self) -> None:
        text = "We will pay Friday.\n\nOn Mon, 20 Oct 2026 at 09:00, AR <ar@x.test> wrote:\n> Please pay invoice INV-1."
        self.assertEqual(strip_reply(text), "We will pay Friday.")

    def test_cuts_wrapped_gmail_quote(self) -> None:
        text = "Paid already.\n\nOn Mon, 20 Oct 2026 at 09:00, Accounts Receivable Team <ar@x.test>\nwrote:\n> old"
        self.assertEqual(strip_reply(text), "Paid already.")

    def test_cuts_outlook_header_block_and_signature(self) -> None:
        self.assertEqual(strip_reply("Wrong person.\n\nFrom: AR\nSent: Monday\nTo: me"), "Wrong person.")
        self.assertEqual(strip_reply("Out until Monday.\n-- \nJane Doe\nCFO"), "Out until Monday.")
        self.assertEqual(strip_reply("ok\n\nSent from my iPhone"), "ok")

    def test_drops_quoted_lines_and_control_characters(self) -> None:
        self.assertEqual(strip_reply("Line one​\n> quoted\r\nLine two\x00"), "Line one\nLine two")

    def test_truncates_to_the_cap(self) -> None:
        text, truncated = reply_text(payload("x" * 50), max_chars=20)
        self.assertEqual((len(text), truncated), (20, True))
        text, truncated = reply_text(payload("short"), max_chars=20)
        self.assertEqual((text, truncated), ("short", False))
