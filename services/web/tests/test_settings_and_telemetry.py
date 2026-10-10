"""Story 1.3: settings come from `WEB_` variables; telemetry is off unless configured."""

from pydantic import SecretStr

from web.settings import Settings

# A made-up address in the connection string's format; it is never contacted.
CONNECTION_STRING = (
    "InstrumentationKey=00000000-0000-0000-0000-000000000000;"
    "IngestionEndpoint=https://example.invalid/"
)


def test_story_1_3_connection_string_is_not_shown_when_settings_are_printed() -> None:
    settings = Settings(
        applicationinsights_connection_string=SecretStr(CONNECTION_STRING)
    )

    assert "InstrumentationKey" not in repr(settings)
    assert "InstrumentationKey" not in str(settings)
