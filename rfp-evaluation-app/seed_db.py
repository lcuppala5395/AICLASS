"""Create the SQLite database and seed the sample evaluation criteria.

Usage:  python seed_db.py            (creates rfp.db if missing, seeds if empty)
        python seed_db.py --reset    (deletes rfp.db first - ALL runs are lost)
"""
import sys

import db


def main() -> None:
    if "--reset" in sys.argv and db.DB_PATH.exists():
        db.DB_PATH.unlink()
        print(f"Deleted {db.DB_PATH}")
    db.init_db()
    active = db.get_active_criteria()
    print(f"Database: {db.DB_PATH}")
    for c in active:
        print(f"  [{c['criterion_id']}] {c['name']:<24} weight={c['weight']:>5.1f}  max={c['max_score']}")
    print(f"Active criteria: {len(active)} | total weight = {db.weights_total(active)}")


if __name__ == "__main__":
    main()
