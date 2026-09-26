"""Task-specific helper stubs.

Drop your reusable helpers here and they'll be auto-loaded into sql-harness's
heredoc exec namespace on the next invocation.

Example:

    def row_count(table):
        \"\"\"Return the row count of `table` in the current workspace.\"\"\"
        return query(f"SELECT count(*) AS n FROM {current_workspace().driver.quote_ident(table)}")[0]["n"]
"""

# Intentionally minimal — extend as you discover patterns.
__all__: list[str] = []