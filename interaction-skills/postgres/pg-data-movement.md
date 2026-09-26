# PG Data Movement

Move data in/out/around PostgreSQL: logical dump + restore, bulk COPY, and
logical replication. (Schema migrations -- ALTER / CREATE under transaction
-- live in `migrations.md`; this file is for moving data across databases or
in/out of tables.)

## Detection

- **Move a whole database** to a new server -> `pg_dump` + `pg_restore`.
- **Bulk-load** a big table (>1M rows) -> `COPY` (10-100x faster than `INSERT`).
- **Live replication** of changes from one PG to another (zero-downtime
  migration, read replica, aggregation) -> logical replication (publication
  + subscription).
- **One-shot export** for analysis / downstream pipeline -> `COPY` /
  `psql \copy`.

## Approach

### 1. Logical dump + restore (server-side CLI)

`pg_dump` and `pg_restore` live on the PG host, not in SQL. Run them via
the SSH workspace that hosts your PG:

```python
use_workspace("2026aliyun")   # SSH workspace on the PG host

ssh_exec("pg_dump -Fc -d sourcedb -f /tmp/sourcedb.dump")     # custom format, compressed
ssh_exec("createdb targetdb")                                  # empty target
ssh_exec("pg_restore -d targetdb --no-owner --jobs=4 /tmp/sourcedb.dump")
```

Flags worth knowing:

- `-Fc` custom format (compressed, parallel-restoreable). `-Fp` plain SQL.
- `-s` schema-only / `-a` data-only. `-t public.orders` one table.
- `--no-owner` strips `OWNED BY` from objects (safer cross-cluster).

Cross-version note: `pg_dump` from the **target's** major is safest.
`pg_dump` 16 can read PG 15 / 14; restore with the target's own
`pg_restore`. Going major-down (PG 16 dump -> PG 14) is **not supported**.

### 2. Bulk COPY

`COPY` bypasses per-row parse/plan/execute -- it's the right tool for
anything >10k rows. `sql-harness`'s `execute` covers server-side file
reads; client-streaming needs psql's `\copy` via SSH.

```python
use_workspace("2026aliyun")

# (a) COPY TO -- export a query to a server-side CSV. psql \copy wraps
#     COPY and lets a client feed/collect the stream.
ssh_exec(r"""psql -U $PG_USER sourcedb -c "\copy (SELECT id,email FROM users WHERE created_at>'2025-01-01') TO '/tmp/users.csv' WITH CSV HEADER" """)
ssh_download("/tmp/users.csv", "./users.csv")     # pull it local

# (b) COPY FROM file -- server reads directly. Needs superuser OR
#     `GRANT pg_read_server_files TO <role>` (PG 14+).
execute("COPY users (id, email) FROM '/var/lib/pg/imports/users.csv' WITH CSV HEADER")

# (c) COPY FROM STDIN -- client streams. `execute` can't do streaming;
#     psql \copy from a *local* file is the practical path:
ssh_exec(r"""psql -h $PG_HOST -U $PG_USER targetdb -c "\copy users (id,email) FROM '/local/users.csv' WITH CSV HEADER" """)
```

### 3. Logical replication (publication + subscription)

For live, ongoing PG-to-PG replication of row changes. SQL-only on both
sides; the only server config is `wal_level = logical` on the source.

```python
use_workspace("source_pg")
# Source: declare what to publish. Default = all DML on the named tables.
execute("CREATE PUBLICATION pub_users FOR TABLE users")
# Add `WITH (publish = 'insert,update,delete,truncate')` to include TRUNCATEs.

use_workspace("target_pg")
# Target: subscribe. Initial copy runs, then changes stream.
execute("""CREATE SUBSCRIPTION sub_users
           CONNECTION 'host=src.db port=5432 user=repl password=... dbname=source'
           PUBLICATION pub_users""")

# Monitor catch-up from the TARGET:
for r in query("SELECT subname, status, received_lsn, latest_end_lsn FROM pg_stat_subscription"):
    print(r)      # status='streaming' + latest_end_lsn advancing = caught up
```

## Gotchas

- **Logical replication does NOT replicate**: schema (DDL), sequence state
  (manually sync with `pg_dump --section=pre-data` on the source +
  `pg_restore --section=post-data` on target), or `TRUNCATE` unless the
  publication includes `publish='truncate'`.
- **Tables need a PK** (or explicit `REPLICA IDENTITY`) -- without it,
  `UPDATE`/`DELETE` can't replicate.
- **`COPY FROM` file path** is server-side (`SHOW data_directory`).
  Permissions: superuser OR `pg_read_server_files` (PG 14+).
- **`pg_dump` cross-major** only goes *up* (or equal). PG 14 dump -> PG 16
  is fine; PG 16 dump -> PG 14 is rejected.
- **`pg_restore --jobs=N`** uses N cores but each job = one transaction
  per table -- mid-restore failures leave the DB half-loaded. For
  restartability, restore one table at a time.
- **Logical replication initial copy** puts write load on the source; for
  very large DBs, stop writes briefly or use `ALTER SUBSCRIPTION ...
  DISABLE` then re-enable after catching up.