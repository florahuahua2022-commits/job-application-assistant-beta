# Production access

- Without the user's explicit approval, never amend an already-pushed commit and never force push, including --force-with-lease. Use new commits and ordinary fast-forward pushes instead.

- Never print, paste, or expose connection strings, passwords, or tokens in output, chat, or any log. Do not include them in commands or exception reports.
- Production writes, migrations, deployments, and environment-variable changes each require the user's explicit approval for that specific action. Read access approval never authorizes them.
- The sole authoritative production access log is `D:/Users/Administrator/Documents/job-application-assistant-audit/prod-access-log.md`, outside every repository checkout and Codex worktree. All branches and working directories must append only there. Repository copies of docs/prod-access-log.md are rule/pointer documents, never writable access ledgers. Relocation requires explicit approval and a recorded handover; Git operations must never overwrite the external ledger.
- Production database access is forbidden without the user's explicit approval for the specific purpose and scope of that access. Read-only queries, connection pools, diagnostics, migration preflight, and retries are included.
- Before connecting, read the external authoritative log at the absolute path above, verify it is current, and persist an authorization event containing the user's exact approval words. No writable, current log or no clear approval means no access.
- Prepare measurement before access. Immediately append each connection attempt's actual tables, returned row counts, approximate bytes based on actual results, Beijing start/end times, and approval reference. Never make an extra production query to fill in the log; never invent measurements or backfill historical access. Follow the file's full rules.
- Production service log/console inspection also needs explicit approval and a factual record; distinguish it from a database connection.
- Offline work must not load production credentials or execute startup, migrations, tests, or scripts that could reach production. Use an explicit in-memory test database and deny external connections when such tests are authorized.
