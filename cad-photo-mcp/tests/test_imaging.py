import numpy as np
import pytest
from PIL import Image

from cad_photo_mcp.geometry import BBox, Bounds, render_mapping
from cad_photo_mcp.imaging import (
    Backdrop,
    composite_overlay,
    grid_period_px,
    mask_bbox,
    outline,
    silhouette_mask,
    tint_layer,
    touches_border,
)

BACKDROP = Backdrop((255, 255, 229), 12)


def render_with_rect(size, rect, colour=(20, 20, 20)):
    img = Image.new("RGB", size, BACKDROP.colour)
    x0, y0, x1, y1 = rect
    for x in range(x0, x1):
        for y in range(y0, y1):
            img.putpixel((x, y), colour)
    return img


def test_silhouette_mask_ignores_background():
    img = render_with_rect((10, 8), (2, 3, 5, 6))
    mask = silhouette_mask(img, BACKDROP)
    assert mask.sum() == 3 * 3
    assert mask[3, 2] and not mask[0, 0]


def test_mask_bbox_is_half_open():
    mask = np.zeros((10, 10), dtype=bool)
    mask[3:6, 2:8] = True
    box = mask_bbox(mask)
    assert box == BBox(2, 3, 8, 6)
    assert box.width == 6
    assert box.height == 3


def test_mask_bbox_rejects_empty():
    with pytest.raises(ValueError, match="empty"):
        mask_bbox(np.zeros((4, 4), dtype=bool))


def test_touches_border_flags_clipped_render():
    clipped = np.zeros((6, 6), dtype=bool)
    clipped[0, 3] = True
    assert touches_border(clipped)


def test_touches_border_passes_framed_render():
    framed = np.zeros((6, 6), dtype=bool)
    framed[2:4, 2:4] = True
    assert not touches_border(framed)


def test_outline_keeps_only_boundary():
    mask = np.zeros((7, 7), dtype=bool)
    mask[2:5, 2:5] = True
    edge = outline(mask)
    assert not edge[3, 3]
    assert edge[2, 2] and edge[4, 4]
    assert edge.sum() == 8


def test_tint_layer_draws_solid_edge_over_translucent_fill():
    mask = np.zeros((5, 5), dtype=bool)
    mask[1:4, 1:4] = True
    layer = np.asarray(tint_layer(mask, (255, 0, 0), opacity=0.5))
    assert layer[2, 2, 3] == 127  # interior is translucent
    assert layer[1, 1, 3] == 255  # edge is solid
    assert layer[0, 0, 3] == 0  # outside is clear


def test_grid_period_finds_stripe_spacing():
    img = Image.new("L", (200, 20), 0)
    for x in range(0, 200, 16):
        for y in range(20):
            img.putpixel((x, y), 255)
    assert grid_period_px(img) == pytest.approx(16.0, abs=1.0)


def test_grid_period_rejects_flat_region():
    with pytest.raises(ValueError, match="no intensity variation"):
        grid_period_px(Image.new("L", (100, 10), 128))


def test_grid_period_honours_region():
    img = Image.new("L", (200, 40), 0)
    for x in range(0, 200, 10):  # top half: 10px grid
        for y in range(20):
            img.putpixel((x, y), 255)
    for x in range(0, 200, 25):  # bottom half: 25px grid
        for y in range(20, 40):
            img.putpixel((x, y), 255)
    assert grid_period_px(img, BBox(0, 20, 200, 40)) == pytest.approx(25.0, abs=1.5)


def test_silhouette_mask_uses_the_given_backdrop():
    # a render on a different renderer's background must not be all "model"
    img = Image.new("RGB", (6, 6), (0, 0, 0))
    img.putpixel((2, 2), (200, 200, 200))
    mask = silhouette_mask(img, Backdrop((0, 0, 0), 12))
    assert mask.sum() == 1


def mapping_for(render, model, from_below=False):
    return render_mapping(
        model, mask_bbox(silhouette_mask(render, BACKDROP)), from_below
    )


def test_composite_overlay_refuses_clipped_render():
    photo = Image.new("RGB", (100, 100), (0, 0, 0))
    framed = render_with_rect((40, 40), (10, 10, 30, 30))
    clipped = render_with_rect((40, 40), (0, 0, 20, 20))  # runs off the frame
    model = Bounds(0.0, 0.0, 0.0, 10.0, 10.0, 1.0)
    with pytest.raises(ValueError, match="clipped"):
        composite_overlay(
            photo,
            [(clipped, (255, 0, 0))],
            mapping_for(framed, model),
            BACKDROP,
            2.0,
            (50, 50),
            0.0,
            0.3,
        )


def test_composite_overlay_places_origin_at_requested_centre():
    photo = Image.new("RGB", (200, 200), (0, 0, 0))
    # 20x20px silhouette for a 10x10mm model whose origin is its lower-left corner
    render = render_with_rect((60, 60), (20, 20, 40, 40))
    model = Bounds(0.0, 0.0, 0.0, 10.0, 10.0, 1.0)

    result = composite_overlay(
        photo,
        [(render, (255, 0, 0))],
        mapping_for(render, model),
        BACKDROP,
        2.0,
        (100, 100),
        0.0,
        1.0,
    )

    arr = np.asarray(result.convert("RGB"))
    # render is 2px/mm, photo is 2px/mm, so the model lands 20px tall ending at y=100
    assert tuple(arr[99, 100]) == (255, 0, 0)  # just inside the origin corner
    assert tuple(arr[101, 100]) == (0, 0, 0)  # below the origin is untouched


def test_section_layer_keeps_the_solid_scale():
    # Regression: each layer used to derive its scale from its own silhouette,
    # so a slice narrower than the model was blown up to the model's full width.
    photo = Image.new("RGB", (200, 200), (0, 0, 0))
    model = Bounds(0.0, 0.0, 0.0, 10.0, 10.0, 1.0)
    solid = render_with_rect((60, 60), (20, 20, 40, 40))  # 10mm at 2px/mm
    section = render_with_rect((60, 60), (22, 30, 32, 40))  # 5mm slice, off-centre

    result = composite_overlay(
        photo,
        [(solid, (255, 0, 0)), (section, (0, 0, 255))],
        mapping_for(solid, model),
        BACKDROP,
        4.0,  # photo is twice the render's scale
        (100, 100),
        0.0,
        1.0,
    )

    arr = np.asarray(result.convert("RGB")).astype(int)
    blue = arr[..., 2] > arr[..., 0]  # resampled edges blend, so "mostly blue"
    rows = np.flatnonzero(blue.any(axis=1))
    cols = np.flatnonzero(blue.any(axis=0))
    # 5mm at 4px/mm = 20px; slice spans x 1..6mm, y 0..5mm from the origin
    assert rows[-1] - rows[0] + 1 == pytest.approx(20, abs=1)
    assert cols[-1] - cols[0] + 1 == pytest.approx(20, abs=1)
    assert cols[0] == pytest.approx(104, abs=1)
    assert rows[-1] == pytest.approx(99, abs=1)
