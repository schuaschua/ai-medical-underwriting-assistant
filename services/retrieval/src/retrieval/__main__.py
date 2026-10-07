"""Start the server: `python -m retrieval`."""

import uvicorn

from retrieval.adapters.telemetry import configure_logging
from retrieval.settings import get_settings


def main() -> None:
    configure_logging()
    settings = get_settings()
    uvicorn.run(
        "retrieval.adapters.http.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        # Do not name the server software in responses.
        server_header=False,
    )


if __name__ == "__main__":
    main()
