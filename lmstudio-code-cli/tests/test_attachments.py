import base64
from pathlib import Path

from lmstudio_code_cli.attachments import Attachment, parse


# ── parse ──────────────────────────────────────────────────────────────────────

def test_parse_removes_absolute_at_reference(tmp_path):
    img = tmp_path / "photo.png"
    img.write_bytes(b"\x89PNG")
    clean, attachments, errors = parse(f"Look at @{img} please", str(tmp_path))
    assert len(attachments) == 1
    assert not errors
    assert str(img) not in clean


def test_parse_quoted_path_with_spaces(tmp_path):
    img = tmp_path / "my photo.png"
    img.write_bytes(b"\x89PNG")
    clean, attachments, errors = parse(f'@"{img}"', str(tmp_path))
    assert len(attachments) == 1
    assert not errors


def test_parse_relative_path_resolved_against_cwd(tmp_path):
    (tmp_path / "shot.png").write_bytes(b"\x89PNG")
    clean, attachments, errors = parse("@shot.png", str(tmp_path))
    assert len(attachments) == 1
    assert not errors


def test_parse_multiple_references(tmp_path):
    (tmp_path / "a.png").write_bytes(b"\x89PNG")
    (tmp_path / "b.jpg").write_bytes(b"\xff\xd8\xff")
    clean, attachments, errors = parse("@a.png and @b.jpg", str(tmp_path))
    assert len(attachments) == 2
    assert not errors


def test_parse_missing_file_adds_error_and_keeps_ref(tmp_path):
    clean, attachments, errors = parse("@missing.png", str(tmp_path))
    assert not attachments
    assert len(errors) == 1
    assert "not found" in errors[0]
    assert "@missing.png" in clean  # ref preserved since it wasn't consumed


def test_parse_unsupported_extension_adds_error(tmp_path):
    (tmp_path / "doc.pdf").write_bytes(b"%PDF")
    clean, attachments, errors = parse("@doc.pdf", str(tmp_path))
    assert not attachments
    assert any("unsupported" in e for e in errors)


def test_parse_text_only_no_refs(tmp_path):
    clean, attachments, errors = parse("just a plain message", str(tmp_path))
    assert clean == "just a plain message"
    assert not attachments
    assert not errors


def test_parse_strips_ref_from_clean_text(tmp_path):
    (tmp_path / "img.png").write_bytes(b"\x89PNG")
    clean, _, _ = parse("before @img.png after", str(tmp_path))
    assert "before" in clean
    assert "after" in clean
    assert "@img.png" not in clean


# ── Attachment ─────────────────────────────────────────────────────────────────

def test_attachment_to_openai_structure():
    data = b"\x89PNG test"
    att = Attachment(path=Path("x.png"), mime_type="image/png", data=data)
    result = att.to_openai()
    assert result["type"] == "image_url"
    url = result["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    decoded = base64.standard_b64decode(url.split(",", 1)[1])
    assert decoded == data


def test_attachment_size_kb():
    att = Attachment(path=Path("x.png"), mime_type="image/png", data=b"A" * 2048)
    assert att.size_kb == 2.0


def test_attachment_size_kb_fractional():
    att = Attachment(path=Path("x.png"), mime_type="image/png", data=b"A" * 512)
    assert att.size_kb == 0.5
