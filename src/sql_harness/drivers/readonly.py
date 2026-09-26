"""Read-only statement guard, shared by the SQL drivers.

**Read this before trusting it.** This is a *guard rail*, not a security
boundary. It stops accidents and gives a fast, clear error; it does not stop
a determined caller. Two reasons, both verified against a live server:

- Any client-side check is bypassable in principle. This one is an allow-list
  over a comment/literal-stripped statement, which defeats the obvious dodges
  (`-- DROP`, `'DROP'`, `/*!50000 DROP */`), but it is still pattern matching,
  not a parser.
- Even the *server-enforced* rail is escapable. `SET SESSION TRANSACTION READ
  ONLY` makes MySQL and PostgreSQL reject writes with their own error, but the
  same session can `SET SESSION TRANSACTION READ WRITE` and carry on.

The only hard boundary is a database account that lacks the privilege. Use
`GRANT SELECT ON db.* TO ...` for sensitive databases, and treat everything
here as defence in depth on top of that.

Two design choices worth knowing:

- **Allow-list, not deny-list.** Matching for `INSERT|UPDATE|DELETE` misses
  `CALL`, `DO`, `LOAD DATA`, `HANDLER`, `CREATE ... SELECT` and every future
  statement type. Only the verbs below are permitted; anything unrecognised is
  refused. Unknown statement == refused is the safe default.
- **The statement is stripped before inspection**, so keywords hiding in
  comments or string literals do not count in either direction: `SELECT
  'DELETE'` is allowed, `/*!50000 DROP TABLE t */` is not.
"""

from __future__ import annotations

# Statements that change no data and must keep working under read-only:
# session/transaction control, not content.
CONTROL_VERBS = frozenset(
    {"BEGIN", "START", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE", "END", "USE"}
)

# Verbs that only read.
READ_VERBS = frozenset(
    {"SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN", "TABLE", "VALUES", "PRAGMA"}
)

# Verbs that introduce a writable body after a WITH clause.
WRITE_VERBS = frozenset({"INSERT", "UPDATE", "DELETE", "MERGE", "REPLACE"})


class ReadOnlyViolation(Exception):
    """A statement was refused because the connection is read-only."""


def _executable_comment_body(body: str) -> str | None:
    """Payload of a MySQL executable comment, or None if it isn't one.

    Returns the body with the ``!`` marker and its optional version number
    removed, so the unwrapped statement reads as SQL. ``/*!50000 DROP TABLE
    t */`` has to be judged as ``DROP TABLE t`` — not as a comment, and not as
    a token called ``50000``.
    """
    if not body.startswith("!"):
        return None
    rest = body[1:]
    index = 0
    while index < len(rest) and rest[index].isdigit():
        index += 1
    return rest[index:]


def strip_literals_and_comments(
    sql: str, *, hash_comments: bool = False, backslash_escapes: bool = False
) -> str:
    """Blank out comments and quoted text, leaving only executable structure.

    Keyword scanning runs on this, never on the raw statement — otherwise
    `WHERE note = 'please delete'` reads as a DELETE, and `-- UPDATE` hides one.

    Two dialect switches, because getting them wrong is a bypass either way:

    - ``hash_comments`` — MySQL treats ``#`` as a comment; PostgreSQL uses it
      as the bitwise-XOR operator, so stripping there would swallow real code.
    - ``backslash_escapes`` — MySQL honours ``\\'`` inside strings (so a quote
      can be escaped); standard PostgreSQL does not.

    MySQL's executable comments (``/*! ... */``, ``/*!50000 ... */``) are
    *unwrapped rather than removed* — the server runs their contents, so a
    guard that discarded them would be trivially defeated by
    ``/*!50000 DROP TABLE t */``.
    """
    out: list[str] = []
    i, n = 0, len(sql)

    while i < n:
        ch = sql[i]

        if ch == "-" and sql.startswith("--", i):
            # `--` needs a following space in MySQL; harmless to accept either.
            i = sql.find("\n", i)
            if i == -1:
                break
            out.append(" ")
        elif ch == "#" and hash_comments:
            i = sql.find("\n", i)
            if i == -1:
                break
            out.append(" ")
        elif ch == "/" and sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            end = n if end == -1 else end
            payload = _executable_comment_body(sql[i + 2 : end])
            out.append(f" {payload} " if payload is not None else " ")
            i = end + 2
        elif ch in ("'", '"', "`"):
            quote = ch
            i += 1
            while i < n:
                if backslash_escapes and quote != "`" and sql[i] == "\\":
                    i += 2
                    continue
                if sql[i] == quote:
                    if sql.startswith(quote * 2, i):  # doubled quote is an escape
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append(" ")
        else:
            out.append(ch)
            i += 1

    return "".join(out)


def _split_statements(sql: str) -> list[str]:
    """Split on semicolons that are not inside parentheses."""
    parts, depth, current = [], 0, []
    for ch in sql:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == ";" and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return [p for p in (p.strip() for p in parts) if p]


def _top_level_verb(statement: str) -> str:
    """The verb that actually decides the statement's effect.

    Handles the two cases where the leading token lies:

    - ``WITH x AS (...) DELETE FROM t`` — a CTE in front of a write. The
      top-level verb after the CTE list is what counts.
    - ``EXPLAIN ANALYZE DELETE FROM t`` — PostgreSQL *executes* the inner
      statement, so the inner verb is the real one. Plain ``EXPLAIN`` does not.
    """
    tokens = statement.split()
    if not tokens:
        return ""

    verb = tokens[0].upper().rstrip("(")

    if verb == "WITH":
        depth = 0
        for index, token in enumerate(tokens):
            depth += token.count("(") - token.count(")")
            if depth == 0 and index > 0:
                candidate = token.upper().rstrip("(")
                if candidate in READ_VERBS or candidate in WRITE_VERBS:
                    return candidate
        return "WITH"

    if verb == "EXPLAIN":
        rest = [t.upper().rstrip("(") for t in tokens[1:]]
        if "ANALYZE" in rest or "ANALYSE" in rest:
            for token in rest:
                if token in READ_VERBS or token in WRITE_VERBS:
                    return token
        return "EXPLAIN"

    return verb


def install_guard(
    engine,
    *,
    hash_comments: bool = False,
    backslash_escapes: bool = False,
    extra_forbidden: frozenset[str] = frozenset(),
    session_sql: str | None = None,
) -> None:
    """Attach read-only enforcement to a SQLAlchemy engine.

    Two independent rails, deliberately at different levels:

    - ``session_sql`` runs on **every new DBAPI connection** via the ``connect``
      event, so the *server* rejects writes. Per-connection, not once per
      engine: the pool hands the same connection back with its session state
      intact, so a one-off ``SET`` would leave the other pooled connections
      writable.
    - ``before_cursor_execute`` inspects **every statement the engine sends**,
      including ``conn.execute()``, ``run_sql_file()`` and any path that skips
      the helpers. Hooking here rather than in ``query()``/``execute()`` is what
      makes the guard cover the whole surface instead of the polite one.
    """
    from sqlalchemy import event

    @event.listens_for(engine, "before_cursor_execute")
    def _guard(conn, cursor, statement, parameters, context, executemany):
        check_statement(
            statement,
            hash_comments=hash_comments,
            backslash_escapes=backslash_escapes,
            extra_forbidden=extra_forbidden,
        )

    if session_sql:
        @event.listens_for(engine, "connect")
        def _readonly_session(dbapi_conn, record):
            cursor = dbapi_conn.cursor()
            try:
                cursor.execute(session_sql)
            finally:
                cursor.close()


def check_statement(
    sql: str,
    *,
    hash_comments: bool = False,
    backslash_escapes: bool = False,
    extra_forbidden: frozenset[str] = frozenset(),
) -> None:
    """Raise ``ReadOnlyViolation`` if ``sql`` is not safe to run read-only.

    ``extra_forbidden`` holds dialect-specific phrases that are dangerous even
    inside an otherwise read-only statement (file writes, table creation,
    row locks) — matched as whole words against the stripped statement.
    """
    stripped = strip_literals_and_comments(
        sql, hash_comments=hash_comments, backslash_escapes=backslash_escapes
    )
    upper = stripped.upper()

    # Multi-word phrases on the stripped text: literals and comments are gone,
    # so a substring hit is a real occurrence rather than prose in a string.
    for phrase in extra_forbidden:
        if phrase in upper:
            raise ReadOnlyViolation(
                f"{phrase!r} is not allowed on a read-only connection"
            )

    for statement in _split_statements(stripped):
        verb = _top_level_verb(statement)
        if not verb:
            continue
        if verb in CONTROL_VERBS or verb in READ_VERBS:
            continue
        raise ReadOnlyViolation(
            f"{verb} is not allowed on a read-only connection "
            f"(allowed: {'/'.join(sorted(READ_VERBS))})"
        )
