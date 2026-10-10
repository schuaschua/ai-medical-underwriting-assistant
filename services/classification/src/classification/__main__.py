"""Start the server: `python -m classification`."""

import uvicorn

from classification.adapters.telemetry import configure_logging
from classification.settings import get_settings


def main() -> None:
    configure_logging()
    settings = get_settings()
    uvicorn.run(
        "classification.adapters.http.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        # Do not name the server software in responses.
        server_header=False,
    )


if __name__ == "__main__":
    main()
