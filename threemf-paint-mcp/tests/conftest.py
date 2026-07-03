import pytest

from tests.fixtures import build_multi_plate_fixture, build_single_object_fixture


@pytest.fixture
def single_object_3mf(tmp_path):
    return build_single_object_fixture(tmp_path)


@pytest.fixture
def multi_plate_3mf(tmp_path):
    return build_multi_plate_fixture(tmp_path)
