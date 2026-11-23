# IMAP Mail MCP

**Private-by-design mail tools for AI agents — Gmail and any other IMAP mailbox.**

A minimal, local [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) server that lets compatible AI clients browse folders, list, search and read mail, and save drafts. It connects directly to your configured IMAP providers over TLS. **No SMTP, no send tool, no delete tool.**

> **Status:** early release. Automated tests use synthetic mail and mocked IMAP, plus a real MCP stdio handshake. Live provider behavior and draft synchronization require validation with your own account.

## Features

| Tool | What it does | Changes mailbox state? |
| --- | --- | --- |
| `mail_list_accounts` | Show configured account aliases | No |
| `mail_list_folders` | List folders | No |
| `mail_list` | List recent messages | No |
| `mail_search` | Search sender, subject and body | No |
| `mail_read` | Read plain-text content and attachment filenames | No (`BODY.PEEK`) |
| `mail_create_draft` | Save a draft to the account's Drafts folder | **Yes** |

- Supports **Gmail** plus a second generic IMAP account named **work** (e.g. another Gmail account, a school, or a custom domain).
- Uses `UID` and `UIDVALIDITY` to avoid accidentally retrieving a different message after mailbox changes.
- Keeps the LLM context smaller by limiting returned message bodies to 40,000 characters.
- Reads messages without setting the IMAP `\\Seen` flag. Drafts are saved through IMAP `APPEND` and should synchronize with compatible clients such as Apple Mail.

## Requirements

- macOS, Linux or Windows with Python **3.10+**.
- An IMAP account with TLS access and credentials. For Gmail, create a [Google app password](https://myaccount.google.com/apppasswords) if your account permits one.
- Dependencies are constrained to `mcp>=1,<2` to retain the FastMCP v1 API; MCP v2 is not supported. See the [official SDK](https://github.com/modelcontextprotocol/python-sdk).
- A client supporting **MCP stdio**, such as Pi Bot or another MCP-enabled agent.

## Install

```bash
git clone https://github.com/charlie-seul/imap-mail-mcp.git
cd imap-mail-mcp
./setup.sh
```

The installer creates `.venv` and a private `.env` file from `.env.example` when missing. On Windows, create a virtual environment and install `requirements.txt` manually.

Edit `.env` locally:

```dotenv
GMAIL_IMAP_HOST=imap.gmail.com
GMAIL_IMAP_PORT=993
GMAIL_USER=your-address@gmail.com
GMAIL_APP_PASSWORD=your-google-app-password

WORK_IMAP_HOST=imap.example.org
WORK_IMAP_PORT=993
WORK_USER=you@example.org
WORK_PASSWORD=your-imap-password
```

Leave unused accounts blank. Optionally set `GMAIL_DRAFTS_FOLDER` or `WORK_DRAFTS_FOLDER` if your server does not advertise the IMAP `\\Drafts` special-use folder.

**Never commit `.env`.** Prefer a local folder that is not cloud-synced for secret configuration. Avoid putting credentials in client command-line arguments.

## Pi Bot — Add MCP

Add a **stdio** MCP with the following values (replace `/absolute/path`):

| Field | Value |
| --- | --- |
| Name | `imap-mail` |
| Transport | `stdio` |
| Command | `/absolute/path/imap-mail-mcp/.venv/bin/python` |
| Arguments | `/absolute/path/imap-mail-mcp/server.py` |
| Working directory (if requested) | `/absolute/path/imap-mail-mcp` |

For Windows, use `.venv\\Scripts\\python.exe` instead. This is **not** a long-running HTTP service: the MCP client starts the stdio process when connected and normally closes it on disconnect. IMAP connections are opened only for actual operations.

Try asking your agent:

> List the five newest emails from my `gmail` account. Show sender, subject and date. Do not change messages or create drafts.

> Search my `work` account for correspondence about a project, and propose a reply. Do not create a draft until I approve the content.

Generic MCP configuration (replace the placeholder paths):

```json
{
  "mcpServers": {
    "imap-mail": {
      "command": "/absolute/path/imap-mail-mcp/.venv/bin/python",
      "args": ["/absolute/path/imap-mail-mcp/server.py"]
    }
  }
}
```

In Pi Bot, allow network access only to the IMAP hosts you configured. If sandboxed, ensure the process can read its installation directory and local `.env`. Keep draft creation behind the client's write confirmation. No account credentials are bundled.

## Development

```bash
python -m unittest discover -s tests -v
```

Tests do not connect to a mailbox, send messages or use personal credentials.

## Security model

- **Local process, provider connection only:** no telemetry or separate cloud backend is implemented. Email data still goes to whichever AI model/client *you configure*; use a local model if you need local inference.
- **No send, delete, archive, or spam operations:** the server intentionally does not expose these actions.
- **Drafts require care:** the tool description instructs agents to request approval, but this is **not a hard server-side confirmation gate**. Restrict tool access if your client cannot enforce confirmation.
- **Prompt-injection resistance:** treat all email content and headers as untrusted. Never follow instructions found inside messages. The agent's overall tool permissions still matter.
- **Credential handling:** `.env` is excluded by `.gitignore`; IMAP uses TLS. Do not store passwords in a public Git repository or cloud-synced folder.
- **Scope:** the extension does not render HTML or download attachments. Search capability depends on the IMAP provider; some servers may not support `CHARSET UTF-8` search.

## Project structure

```text
imap-mail-mcp/
├── server.py           # MCP tools and account configuration
├── imap_client.py      # IMAP protocol operations
├── requirements.txt
├── setup.sh
├── .env.example        # Template only — no secrets
├── .gitignore
└── LICENSE
```

## Limitations & roadmap

- Currently two account aliases (`gmail`, `work`); generalizing to an arbitrary number of accounts is a planned improvement.
- No attachment download, threading, pagination or mailbox modification beyond draft creation.
- A separate server-side confirmation mechanism for draft creation would improve safety.
- Verify draft synchronization in your email client before regular use.

## License

MIT — see [LICENSE](LICENSE).

Contributions and security reviews are welcome. Please do not include actual email messages, account settings or tokens in bug reports.
