import pytest


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    """Keep every test away from the real saved PI settings."""
    monkeypatch.setenv("PICT_CONFIG", str(tmp_path / "pict-test.toml"))
