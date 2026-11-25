# Supabase for Kcalm

`bootstrap.sql` creates the private `bot` schema on a fresh project.
`seed.sql` is the default shared catalog. `runtime_role.sql` grants the
application role access to application rows and sequences.

Render uses `SUPABASE_DB_URL` for `kcalm_app` through the Session Pooler.
The TLS trust certificate came from Supabase Database Settings.
`SUPABASE_ADMIN_DB_URL` is only for local maintenance. Keep both URLs in
the appropriate secret stores.

For the one-time cutover, stop the old process so it cannot write to
Turso. Verify the source snapshot with `python -m tools.cutover_turso`,
then run it with `--apply --expect-target-meals 262 --backup data/pre-cutover.jsonl.gz`.
Use the verified count from the first snapshot if it differs on another
installation. This command replaces the application snapshot atomically,
checks counts and nutrition totals, and keeps a protected destination
backup. It excludes media and pending photo workflows.

Run `python -m tools.import_green /path/to/catalog.xlsx` to review the
catalog report and add `--apply` to import it after the snapshot. Resume
Render using Supabase and confirm a write through Mini App before retiring
the old connection. Once Supabase accepts new production writes, preserve
those writes before any rollback to a different database.

PostgreSQL integration checks can run against a dedicated local database
initialized with the schema and seed. Set `KCALM_TEST_POSTGRES_URL` and run
`python -m pytest tests/test_web_api.py`.
