# scripts/reset_db.py
from __future__ import annotations

import os
import sys

from sqlalchemy import create_engine, text


def main() -> None:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL is not set (check your .env)", file=sys.stderr)
        sys.exit(1)

    print(f"[reset_db] Connecting to database: {db_url}")

    # future=True for SQLAlchemy 2-style behavior
    engine = create_engine(db_url, future=True)

    # Use engine.begin() so TRUNCATE is committed automatically.
    with engine.begin() as conn:
        # Find *all* user tables in non-system schemas.
        result = conn.execute(
            text(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_type = 'BASE TABLE'
                  AND table_schema NOT IN ('pg_catalog', 'information_schema')
                ORDER BY table_schema, table_name
                """
            )
        )
        rows = result.fetchall()

        if not rows:
            print("[reset_db] No user tables found; nothing to truncate.")
            return

        # Group by schema
        tables_by_schema: dict[str, list[str]] = {}
        for schema, table in rows:
            tables_by_schema.setdefault(schema, []).append(table)

        for schema, tables in tables_by_schema.items():
            # If you REALLY want to keep alembic_version, uncomment this:
            # tables = [t for t in tables if t != "alembic_version"]

            if not tables:
                continue

            fq_tables = [f'"{schema}"."{t}"' for t in tables]
            print(f"[reset_db] Truncating schema '{schema}' tables:")
            for t in fq_tables:
                print(f"  - {t}")

            stmt = (
                "TRUNCATE TABLE "
                + ", ".join(fq_tables)
                + " RESTART IDENTITY CASCADE"
            )
            conn.execute(text(stmt))

        print("[reset_db] Done. All user tables truncated and identities reset.")


if __name__ == "__main__":
    main()
