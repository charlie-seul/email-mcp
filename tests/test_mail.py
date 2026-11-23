import asyncio
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import imap_client as mail
import server

ACCOUNT = mail.MailAccount('imap.example.org', 993, 'reader@example.org', 'synthetic-secret')
RAW = b'From: sender@example.org\r\nSubject: Example\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nHello\r\n'


class MailTests(unittest.TestCase):
    def setUp(self):
        self.patcher = patch('imap_client.imaplib.IMAP4_SSL')
        self.imap = self.patcher.start().return_value
        self.addCleanup(self.patcher.stop)
        self.imap.select.return_value = ('OK', [b'1'])
        self.imap.response.return_value = ('UIDVALIDITY', [b'42'])
        self.imap.list.return_value = ('OK', [b'(\\Drafts) "/" "Draft Folder"'])
        self.imap.append.return_value = ('OK', [b'APPENDUID 42 7'])

    def test_password_not_in_repr(self):
        self.assertNotIn('synthetic-secret', repr(ACCOUNT))

    def test_list_uses_peek_and_readonly(self):
        self.imap.uid.side_effect = [('OK', [b'1 2 3']), ('OK', [(b'', RAW)]), ('OK', [(b'', RAW)])]
        result = mail.list_messages(ACCOUNT, 'Folder Name', 2)
        self.assertEqual([r['uid'] for r in result], ['3', '2'])
        self.imap.select.assert_called_once_with('"Folder Name"', readonly=True)
        self.imap.uid.assert_any_call('FETCH', '3', '(BODY.PEEK[HEADER])')
        self.imap.logout.assert_called_once()

    def test_read_rejects_stale_uidvalidity(self):
        with self.assertRaises(RuntimeError):
            mail.get_message(ACCOUNT, '1', '41')
        self.imap.uid.assert_not_called()

    def test_read_plain_body(self):
        self.imap.uid.return_value = ('OK', [(b'', RAW)])
        result = mail.get_message(ACCOUNT, '1', '42')
        self.assertEqual(result['body'], 'Hello\r\n')
        self.imap.uid.assert_called_once_with('FETCH', '1', '(BODY.PEEK[])')

    def test_truncation(self):
        self.imap.uid.return_value = ('OK', [(b'', RAW + b'x' * 41000)])
        result = mail.get_message(ACCOUNT, '1', '42')
        self.assertTrue(result['truncated'])
        self.assertEqual(len(result['body']), 40000)

    def test_search_unicode_and_escaping(self):
        self.imap.uid.return_value = ('OK', [b''])
        mail.search_messages(ACCOUNT, 'café "sample"')
        args = self.imap.uid.call_args.args
        self.assertEqual(args[5], '"café \\"sample\\""'.encode())

    def test_validation_before_connection(self):
        for limit in (0, 101, True):
            with self.assertRaises(ValueError):
                mail.list_messages(ACCOUNT, limit=limit)
        for uid in ('0', '1:*', '１'):
            with self.assertRaises(ValueError):
                mail.get_message(ACCOUNT, uid, '42')
        for query in ('', 'test\r\nALL'):
            with self.assertRaises(ValueError):
                mail.search_messages(ACCOUNT, query)

    def test_draft_append_only(self):
        result = mail.create_draft(ACCOUNT, 'recipient@example.org', 'Example', 'Draft text')
        self.assertEqual(result['status'], 'draft_created')
        args = self.imap.append.call_args.args
        self.assertEqual(args[:2], ('"Draft Folder"', '(\\Draft)'))
        self.assertIn(b'\r\n', args[3])
        self.imap.uid.assert_not_called()

    def test_header_injection_rejected(self):
        with self.assertRaises(ValueError):
            mail.create_draft(ACCOUNT, 'recipient@example.org\nBcc: other@example.org', 'Test', 'Body')
        self.imap.append.assert_not_called()

    def test_missing_draft_folder_fails_closed(self):
        self.imap.list.return_value = ('OK', [b'(\\HasNoChildren) "/" "Inbox"'])
        with self.assertRaises(RuntimeError):
            mail.create_draft(ACCOUNT, 'recipient@example.org', 'Test', 'Body')
        self.imap.append.assert_not_called()

    def test_logout_on_login_failure(self):
        self.imap.login.side_effect = RuntimeError('Login failed')
        with self.assertRaises(RuntimeError):
            mail.list_messages(ACCOUNT)
        self.imap.logout.assert_called_once()

    def test_generic_configuration(self):
        with patch.dict(os.environ, {'WORK_IMAP_HOST': 'imap.example.org', 'WORK_USER': 'reader@example.org', 'WORK_PASSWORD': 'synthetic-secret'}, clear=True):
            self.assertEqual(server.mail_list_accounts(), ['work'])
            self.assertEqual(server.account('work').host, 'imap.example.org')
            with self.assertRaises(ValueError):
                server.account('unknown')


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_handshake_and_tools(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        params = StdioServerParameters(command=sys.executable, args=[str(Path(server.__file__).resolve())])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                self.assertEqual({t.name for t in result.tools}, {
                    'mail_list_accounts', 'mail_list_folders', 'mail_list',
                    'mail_search', 'mail_read', 'mail_create_draft'})
                response = await session.call_tool('mail_list_accounts', {})
                self.assertFalse(response.isError)


if __name__ == '__main__':
    unittest.main()
