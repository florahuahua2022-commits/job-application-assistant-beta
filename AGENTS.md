# Production access

- Production database access is forbidden without the user's explicit approval for the specific purpose and scope of that access. Read-only queries, connection pools, diagnostics, migration preflight, and retries are included.
- Before connecting, read `docs/prod-access-log.md`, verify the authoritative log is current, and persist an authorization event containing the user's exact approval words. No writable, current log or no clear approval means no access.
- Prepare measurement before access. Immediately append each connection attempt's actual tables, returned row counts, approximate bytes based on actual results, Beijing start/end times, and approval reference. Never make an extra production query to fill in the log; never invent measurements or backfill historical access. Follow the file's full rules.
- Production service log/console inspection also needs explicit approval and a factual record; distinguish it from a database connection.
- Offline work must not load production credentials or execute startup, migrations, tests, or scripts that could reach production. Use an explicit in-memory test database and deny external connections when such tests are authorized.
