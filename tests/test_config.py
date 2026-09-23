import logging

import pytest

from alenna.config import Settings


@pytest.mark.parametrize("raw", ["info", "Info", " INFO "])
def test_log_level_is_normalized_to_uppercase(raw):
    settings = Settings(log_level=raw)
    assert settings.log_level == "INFO"
    logging.getLogger("test").setLevel(settings.log_level)  # não levanta ValueError


def test_log_level_from_environment(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "debug")
    assert Settings().log_level == "DEBUG"
