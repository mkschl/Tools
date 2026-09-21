import pytest
from fakes import MODEL, SLICE, FakeBackend

from cad_photo_mcp import server
from cad_photo_mcp.calibration import CalibrationStore


@pytest.fixture
def fake_backend(monkeypatch):
    fake = FakeBackend(MODEL, SLICE)
    monkeypatch.setattr(server, "backend", fake)
    return fake


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    """Never let a test touch the real calibration directory."""
    store = CalibrationStore(tmp_path / "calibrations")
    monkeypatch.setattr(server, "store", store)
    return store
