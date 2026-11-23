"""IMAP SSL utilities, no SMTP and no remote services other than configured IMAP hosts."""
from __future__ import annotations

import email
import email.header
import email.policy
import email.utils
import imaplib
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Iterator


@dataclass(frozen=True)
class MailAccount:
    host: str
    port: int
    username: str
    password: str = field(repr=False)
    drafts_folder: str | None = None


@contextmanager
def connection(a: MailAccount) -> Iterator[imaplib.IMAP4_SSL]:
    imap = imaplib.IMAP4_SSL(a.host, a.port, timeout=20)
    try:
        imap.login(a.username, a.password)
        yield imap
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def _decode_header(value: str | None) -> str:
    if not value:
        return ''
    chunks = []
    for chunk, encoding in email.header.decode_header(value):
        chunks.append(chunk.decode(encoding or 'utf-8', errors='replace') if isinstance(chunk, bytes) else chunk)
    return ''.join(chunks)


def _validate_folder(folder: str) -> str:
    if not folder or len(folder) > 512 or any(c in folder for c in ('\x00', '\r', '\n')):
        raise ValueError('Invalid IMAP folder')
    return folder


def _quoted_folder(folder: str) -> str:
    return '"' + _validate_folder(folder).replace('\\', '\\\\').replace('"', '\\"') + '"'


def _select(imap: imaplib.IMAP4_SSL, folder: str) -> str:
    status, _ = imap.select(_quoted_folder(folder), readonly=True)
    if status != 'OK':
        raise RuntimeError('Unable to open IMAP folder')
    status, response = imap.response('UIDVALIDITY')
    if status != 'UIDVALIDITY' or not response or not response[0]:
        raise RuntimeError('IMAP server did not provide UIDVALIDITY')
    value = response[0].decode('ascii') if isinstance(response[0], bytes) else str(response[0])
    if not value.isdigit():
        raise RuntimeError('Invalid UIDVALIDITY')
    return value


def _ids(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.isascii() or not value.isdigit() or int(value) < 1:
        raise ValueError(f'Invalid {label}')
    return value


def _header_summary(raw: bytes, uid: str, validity: str, folder: str) -> dict:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    return {
        'uid': uid,
        'uidvalidity': validity,
        'folder': folder,
        'from': _decode_header(str(msg.get('From', ''))),
        'subject': _decode_header(str(msg.get('Subject', ''))),
        'date': str(msg.get('Date', '')),
        'message_id': str(msg.get('Message-ID', '')),
    }


def _fetched_bytes(data: list | None) -> bytes:
    for item in data or []:
        if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes):
            return item[1]
    raise RuntimeError('Missing IMAP message content')


def _fetch_headers(imap: imaplib.IMAP4_SSL, uids: list[bytes], validity: str, folder: str) -> list[dict]:
    result = []
    for uid in uids:
        status, data = imap.uid('FETCH', uid.decode('ascii'), '(BODY.PEEK[HEADER])')
        if status == 'OK':
            try:
                result.append(_header_summary(_fetched_bytes(data), uid.decode('ascii'), validity, folder))
            except RuntimeError:
                continue
    return result


def _bounded_limit(limit: int) -> int:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('limit must be an integer between 1 and 100')
    return limit


def list_messages(a: MailAccount, folder: str = 'INBOX', limit: int = 20) -> list[dict]:
    limit = _bounded_limit(limit)
    with connection(a) as imap:
        validity = _select(imap, folder)
        status, data = imap.uid('SEARCH', None, 'ALL')
        if status != 'OK':
            raise RuntimeError('IMAP search failed')
        uids = (data[0] or b'').split()[-limit:][::-1]
        return _fetch_headers(imap, uids, validity, folder)


def search_messages(a: MailAccount, query: str, folder: str = 'INBOX', limit: int = 30) -> list[dict]:
    limit = _bounded_limit(limit)
    query = query.strip()
    if not query or len(query) > 200 or any(c in query for c in ('\x00', '\r', '\n')):
        raise ValueError('Search query must be 1–200 characters, single line')
    with connection(a) as imap:
        validity = _select(imap, folder)
        # Quoted and escaped search string, restricted to a single IMAP operand.
        escaped = query.replace('\\', '\\\\').replace('"', '\\"')
        term = ('"' + escaped + '"').encode('utf-8')
        status, data = imap.uid('SEARCH', 'CHARSET', 'UTF-8', 'OR', 'FROM', term,
                                'OR', 'SUBJECT', term, 'BODY', term)
        if status != 'OK':
            raise RuntimeError('IMAP server rejected search (UTF-8 search might be unsupported)')
        uids = (data[0] or b'').split()[-limit:][::-1]
        return _fetch_headers(imap, uids, validity, folder)


def get_message(a: MailAccount, uid: str, uidvalidity: str, folder: str = 'INBOX') -> dict:
    uid = _ids(uid, 'UID')
    uidvalidity = _ids(uidvalidity, 'UIDVALIDITY')
    with connection(a) as imap:
        validity = _select(imap, folder)
        if validity != uidvalidity:
            raise RuntimeError('Folder UIDVALIDITY changed. List messages again to get fresh identifiers.')
        status, data = imap.uid('FETCH', uid, '(BODY.PEEK[])')
        if status != 'OK':
            raise RuntimeError('IMAP read failed')
        raw = _fetched_bytes(data)
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        bodies: list[str] = []
        for part in msg.walk() if msg.is_multipart() else (msg,):
            if part.get_content_type() == 'text/plain' and part.get_content_disposition() != 'attachment':
                try:
                    content = part.get_content()
                    bodies.append(content if isinstance(content, str) else str(content))
                except Exception:
                    bodies.append(part.get_payload(decode=True).decode('utf-8', errors='replace'))
        body = '\n'.join(bodies)
        # Preserve privacy and prevent huge model context expansions.
        truncated = len(body) > 40000
        summary = _header_summary(raw, uid, validity, folder)
        return {**summary, 'body': body[:40000], 'truncated': truncated,
                'attachments': [p.get_filename() for p in msg.walk()
                                if p.get_content_disposition() == 'attachment' and p.get_filename()]}


def _draft_folder(imap: imaplib.IMAP4_SSL, configured: str | None) -> str:
    if configured:
        return _validate_folder(configured)
    status, lines = imap.list()
    if status == 'OK':
        for raw in lines or []:
            line = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else str(raw)
            if re.search(r'\\Drafts(?:\s|\))', line, re.IGNORECASE):
                # IMAP LIST last field: quoted or unquoted mailbox name
                match = re.search(r'\)\s+"(?:[^"\\]|\\.)*"\s+("(?:[^"\\]|\\.)*"|\S+)\s*$', line)
                if match:
                    name = match.group(1)
                    if name.startswith('"'):
                        name = name[1:-1].replace('\\"', '"').replace('\\\\', '\\')
                    return _validate_folder(name)
    raise RuntimeError('No Drafts folder detected; set *_DRAFTS_FOLDER explicitly in local .env')


def _validate_address_header(value: str, label: str) -> None:
    if any(c in value for c in ('\r', '\n')) or len(value) > 4096:
        raise ValueError(f'Invalid {label} header')
    if not email.utils.getaddresses([value]):
        raise ValueError(f'{label} is empty')


def create_draft(a: MailAccount, to: str, subject: str, body: str, cc: str = '') -> dict:
    _validate_address_header(to, 'To')
    if cc:
        _validate_address_header(cc, 'Cc')
    if any(c in subject for c in ('\r', '\n')) or len(subject) > 998:
        raise ValueError('Invalid subject')
    msg = EmailMessage()
    msg['From'] = a.username
    msg['To'] = to
    if cc:
        msg['Cc'] = cc
    msg['Subject'] = subject
    msg['Date'] = email.utils.formatdate(localtime=True)
    msg.set_content(body)
    with connection(a) as imap:
        folder = _draft_folder(imap, a.drafts_folder)
        status, _ = imap.append(_quoted_folder(folder), '(\\Draft)', imaplib.Time2Internaldate(time.time()), msg.as_bytes(policy=email.policy.SMTP))
        if status != 'OK':
            raise RuntimeError('IMAP refused to save draft')
    return {'status': 'draft_created', 'folder': folder, 'to': to, 'subject': subject}


def list_folders(a: MailAccount) -> list[str]:
    with connection(a) as imap:
        status, lines = imap.list()
        if status != 'OK':
            raise RuntimeError('IMAP LIST failed')
        return [line.decode('utf-8', 'replace') if isinstance(line, bytes) else str(line)
                for line in (lines or [])]
