# Encoding & charset

## MySQL

Always include `?charset=utf8mb4` in your URL:

```toml
url = "mysql+pymysql://user:pw@host:3306/db?charset=utf8mb4"
```

`utf8mb4` is the only charset that supports 4-byte UTF-8 (emoji, some CJK). The MySQL `utf8` alias is 3-byte only and silently corrupts data.

### Gotchas

- Server-side character set defaults are configured per-database (`CREATE DATABASE ... CHARACTER SET utf8mb4`).
- Client connection charset (the URL param) and server collation interact; mismatches cause silent mojibake.
- For multi-language data: use `utf8mb4_unicode_ci` (sorts accurately) or `utf8mb4_bin` (byte-exact).

## PostgreSQL

Server-side encoding is set at `initdb` time; client encoding is set per-connection.

```sql
SHOW client_encoding;            -- typically UTF8
SET client_encoding = 'UTF8';
```

Default is `UTF8` on modern PostgreSQL — usually no action needed.

## Gotchas

- `psycopg` reads/writes Python `str` as UTF-8 by default.
- PyMySQL's `?charset=utf8mb4` controls both inbound and outbound encoding; missing it defaults to the server's `character_set_client`, which may not be UTF-8.
- A `\0` byte in a Python string will silently truncate when written to MySQL TEXT/VARCHAR columns — strip or reject upstream.