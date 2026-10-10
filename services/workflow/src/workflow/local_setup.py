"""Prepare the local PostgreSQL for `workflow`: `python -m workflow.local_setup`.

A developer tool only (see README, 'Run locally'): it creates the database
role the service runs as. Run it before the migrations, which grant that role
its rights.
"""

from workflow.adapters.local_role import ensure_local_service_role
from workflow.settings import get_settings


def main() -> None:
    role = ensure_local_service_role(get_settings())
    print(f"Database role ready: {role}")


if __name__ == "__main__":
    main()
