from __future__ import annotations

import argparse

from app.db.session import init_db


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="reflux.db", help="Path to sqlite db file (default: reflux.db)")
    args = parser.parse_args()

    init_db(args.db)
    print(f"Initialized DB at {args.db}")


if __name__ == "__main__":
    main()


