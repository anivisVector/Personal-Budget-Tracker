"""Apply database migrations explicitly.

    python init_db.py                 # uses DATABASE_URL, or SQLite at SQLITE_PATH
    DATABASE_URL=... python init_db.py

The app also migrates automatically on startup (AUTO_MIGRATE=1, the default);
this script is useful before a first deploy or for checking the schema version.
"""

import sys

from dotenv import load_dotenv

from budget_app.config import PROJECT_ROOT, load_settings
from budget_app.db import connect
from budget_app.schema import MIGRATIONS, current_version, migrate


def main() -> int:
    load_dotenv(PROJECT_ROOT / '.env', override=False)
    settings = load_settings()
    target = 'Postgres (DATABASE_URL)' if settings.uses_postgres else f'SQLite at {settings.sqlite_path}'
    db = connect(settings.database_url, settings.sqlite_path)
    try:
        applied = migrate(db)
        version = current_version(db)
    except Exception as exc:
        print(f'Migration failed on {target}: {exc}', file=sys.stderr)
        return 1
    finally:
        db.close()
    print(f'{target}: schema version {version}/{MIGRATIONS[-1][0]}' + (f' (applied {applied})' if applied else ' (up to date)'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
