import shutil

from cad_photo_mcp.calibration import Calibration, CalibrationStore, photo_sha256


def calibration_for(photo, px_per_mm, **check):
    return Calibration(
        px_per_mm=px_per_mm,
        method="grid",
        photo_sha256=photo_sha256(photo),
        photo_path=str(photo),
        **check,
    )


def test_round_trips_a_calibration(tmp_path):
    photo = tmp_path / "a.png"
    photo.write_bytes(b"photo a")
    store = CalibrationStore(tmp_path / "store")
    saved = calibration_for(
        photo,
        6.95,
        check_measured_mm=40.1,
        check_actual_mm=40.0,
        check_deviation_pct=0.25,
    )
    store.save(saved)
    loaded = store.load(photo)
    assert loaded == saved
    assert loaded is not None and loaded.cross_checked


def test_never_hands_one_photos_scale_to_another(tmp_path):
    # Two shots of the same part from different heights: 6.95 vs 6.35 px/mm.
    first, second = tmp_path / "top1.png", tmp_path / "top2.png"
    first.write_bytes(b"shot from 30cm")
    second.write_bytes(b"shot from 33cm")
    store = CalibrationStore(tmp_path / "store")
    store.save(calibration_for(first, 6.95))
    assert store.load(second) is None
    store.save(calibration_for(second, 6.35))
    first_cal, second_cal = store.load(first), store.load(second)
    assert first_cal is not None and first_cal.px_per_mm == 6.95
    assert second_cal is not None and second_cal.px_per_mm == 6.35


def test_a_moved_photo_keeps_its_calibration(tmp_path):
    photo = tmp_path / "a.png"
    photo.write_bytes(b"photo a")
    store = CalibrationStore(tmp_path / "store")
    store.save(calibration_for(photo, 7.2))
    moved = tmp_path / "renamed.png"
    shutil.move(photo, moved)
    loaded = store.load(moved)
    assert loaded is not None and loaded.px_per_mm == 7.2


def test_an_edited_photo_loses_its_calibration(tmp_path):
    photo = tmp_path / "a.png"
    photo.write_bytes(b"original")
    store = CalibrationStore(tmp_path / "store")
    store.save(calibration_for(photo, 7.2))
    photo.write_bytes(b"cropped")
    assert store.load(photo) is None


def test_unchecked_calibration_says_so(tmp_path):
    photo = tmp_path / "a.png"
    photo.write_bytes(b"photo a")
    assert not calibration_for(photo, 7.2).cross_checked
