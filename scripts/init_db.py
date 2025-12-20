from __future__ import annotations

import argparse
import os

from app.db.session import init_db


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="reflux.db", help="Path to sqlite db file (default: reflux.db)")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL", "").strip() or None
    init_db(database_url, args.db)
    if database_url:
        print("Initialized DB using DATABASE_URL")
    else:
        print(f"Initialized DB at {args.db}")


if __name__ == "__main__":
    main()


