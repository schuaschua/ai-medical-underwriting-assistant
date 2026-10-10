"""Start the server: `python -m extraction`."""

import uvicorn

from extraction.adapters.telemetry import configure_logging
from extraction.settings import get_settings


def main() -> None:
    configure_logging()
    settings = get_settings()
    uvicorn.run(
        "extraction.adapters.http.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        # Do not name the server software in responses.
        server_header=False,
    )


if __name__ == "__main__":
    main()
