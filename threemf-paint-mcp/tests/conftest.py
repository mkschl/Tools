import pytest

from tests.fixtures import (
    build_embossed_repaired_fixture,
    build_embossed_wall_gap_fixture,
    build_engraved_fixture,
    build_multi_plate_fixture,
    build_named_objects_fixture,
    build_single_object_fixture,
    build_unpainted_box_fixture,
)


@pytest.fixture
def single_object_3mf(tmp_path):
    return build_single_object_fixture(tmp_path)


@pytest.fixture
def named_objects_3mf(tmp_path):
    return build_named_objects_fixture(tmp_path)


@pytest.fixture
def multi_plate_3mf(tmp_path):
    return build_multi_plate_fixture(tmp_path)


@pytest.fixture
def embossed_wall_gap_3mf(tmp_path):
    return build_embossed_wall_gap_fixture(tmp_path)


@pytest.fixture
def embossed_repaired_3mf(tmp_path):
    return build_embossed_repaired_fixture(tmp_path)


@pytest.fixture
def unpainted_box_3mf(tmp_path):
    return build_unpainted_box_fixture(tmp_path)


@pytest.fixture
def engraved_3mf(tmp_path):
    return build_engraved_fixture(tmp_path)
