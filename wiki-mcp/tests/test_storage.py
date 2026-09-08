import importlib
from pathlib import Path

import pytest

import wiki_mcp.storage as storage


@pytest.fixture(autouse=True)
def wiki_root(tmp_path, monkeypatch):
    """Point WIKI_DIR at a fresh tmp_path and reload storage for each test."""
    monkeypatch.setenv("WIKI_DIR", str(tmp_path))
    importlib.reload(storage)
    return tmp_path


def write(root, rel_path: str, content: str = "") -> None:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


# ── WIKI_DIR validation ──────────────────────────────────────────────────────


def test_wiki_dir_missing_env_raises_at_import(monkeypatch):
    monkeypatch.delenv("WIKI_DIR", raising=False)
    with pytest.raises(RuntimeError, match="WIKI_DIR environment variable must be set"):
        importlib.reload(storage)


def test_wiki_dir_nonexistent_raises_at_import(tmp_path, monkeypatch):
    monkeypatch.setenv("WIKI_DIR", str(tmp_path / "nonexistent"))
    with pytest.raises(RuntimeError, match="does not exist"):
        importlib.reload(storage)


def test_wiki_dir_not_writable_raises_at_import(tmp_path, monkeypatch):
    read_only = tmp_path / "read-only"
    read_only.mkdir(mode=0o555)
    monkeypatch.setenv("WIKI_DIR", str(read_only))
    try:
        with pytest.raises(RuntimeError, match="not writable"):
            importlib.reload(storage)
    finally:
        read_only.chmod(0o755)


# ── list_pages ───────────────────────────────────────────────────────────────


def test_list_pages_returns_all_pages(wiki_root):
    write(wiki_root, "Cloud/AWS/Lambda.md")
    write(wiki_root, "Cloud/Azure/Functions.md")
    result = storage.list_pages()
    assert "Cloud/AWS/Lambda.md" in result
    assert "Cloud/Azure/Functions.md" in result


def test_list_pages_scoped_to_subdirectory(wiki_root):
    write(wiki_root, "Cloud/AWS/Lambda.md")
    write(wiki_root, "Cloud/Azure/Functions.md")
    result = storage.list_pages("Cloud/AWS")
    assert "Lambda.md" in result
    assert "Functions.md" not in result


def test_list_pages_excludes_build_and_hidden_dirs(wiki_root):
    write(wiki_root, "Real.md")
    write(wiki_root, "build/Generated.md")
    write(wiki_root, ".git/Internal.md")
    write(wiki_root, "wiki.egg-info/Meta.md")
    result = storage.list_pages()
    assert "Real.md" in result
    assert "Generated.md" not in result
    assert "Internal.md" not in result
    assert "Meta.md" not in result


def test_list_pages_no_pages_returns_message(wiki_root):
    result = storage.list_pages()
    assert "no pages found" in result.lower()


def test_list_pages_unknown_directory_returns_message(wiki_root):
    result = storage.list_pages("Ghost")
    assert "not found" in result.lower()


def test_list_pages_rejects_path_traversal(wiki_root):
    result = storage.list_pages("../../etc")
    assert "escapes the wiki root" in result.lower()


# ── read_page ────────────────────────────────────────────────────────────────


def test_read_page_returns_content(wiki_root):
    write(wiki_root, "Cloud/AWS/Lambda.md", "# Lambda\n\nServerless compute.")
    result = storage.read_page("Cloud/AWS/Lambda.md")
    assert "Serverless compute" in result


def test_read_page_not_found_returns_message(wiki_root):
    result = storage.read_page("Ghost.md")
    assert "not found" in result.lower()


def test_read_page_not_found_suggests_close_match(wiki_root):
    write(wiki_root, "Cloud/AWS/Lambda.md")
    result = storage.read_page("Cloud/AWS/Lamda.md")
    assert "did you mean" in result.lower()
    assert "Lambda.md" in result


def test_read_page_rejects_path_traversal(wiki_root):
    result = storage.read_page("../outside.md")
    assert "escapes the wiki root" in result.lower()


def test_read_page_handles_file_vanishing_mid_read(wiki_root, monkeypatch):
    """read_page takes no lock (server.py offloads to a thread with a
    timeout but no per-page lock — see that module's docstring), so it can
    race a concurrent write_page/delete_page that removes the file between
    resolving its path and reading it. Must treat that as 'not found'."""
    write(wiki_root, "Lambda.md", "content")

    def read_text_that_vanishes(self, *args, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(Path, "read_text", read_text_that_vanishes)
    result = storage.read_page("Lambda.md")
    assert "not found" in result.lower()


# ── search ───────────────────────────────────────────────────────────────────


def test_search_finds_matching_line(wiki_root):
    write(wiki_root, "Cloud/AWS/Lambda.md", "Lambda supports Python and Node runtimes.")
    result = storage.search("python")
    assert "Cloud/AWS/Lambda.md" in result
    assert "runtimes" in result.lower()


def test_search_no_matches_returns_message(wiki_root):
    write(wiki_root, "Cloud/AWS/Lambda.md", "content")
    result = storage.search("zzznomatch")
    assert "no matches" in result.lower()


def test_search_respects_limit(wiki_root):
    write(wiki_root, "Big.md", "\n".join("match line" for _ in range(10)))
    result = storage.search("match", limit=3)
    assert result.count("match line") == 3
    assert "truncated" in result.lower()


# ── backlinks ────────────────────────────────────────────────────────────────


def test_backlinks_finds_relative_link(wiki_root):
    write(wiki_root, "Containerisation/Helm/Values.md", "# Values")
    write(
        wiki_root,
        "Containerisation/Helm/Guide.md",
        "See [Values](./Values.md) for details.",
    )
    result = storage.backlinks("Containerisation/Helm/Values.md")
    assert "Containerisation/Helm/Guide.md" in result


def test_backlinks_handles_url_encoded_spaces_and_parent_relative(wiki_root):
    write(wiki_root, "Containerisation/Helm/Chart Template Guide/Values Files.md", "# Values")
    write(
        wiki_root,
        "Containerisation/Helm/Topics/Charts.md",
        "See [Values Files](../Chart%20Template%20Guide/Values%20Files.md).",
    )
    result = storage.backlinks("Containerisation/Helm/Chart Template Guide/Values Files.md")
    assert "Containerisation/Helm/Topics/Charts.md" in result


def test_backlinks_handles_unencoded_space_in_target(wiki_root):
    """Regression test: a link target with a literal (non-percent-encoded)
    space must not be truncated at the first space — that used to be
    mistaken for a '(url "title")' separator, e.g. this exact filename."""
    write(wiki_root, "Containerisation/Helm/Chart Template Guide/Values Files.md", "# Values")
    write(
        wiki_root,
        "Containerisation/Helm/Guide.md",
        "See [Values Files](Chart Template Guide/Values Files.md) for details.",
    )
    result = storage.backlinks("Containerisation/Helm/Chart Template Guide/Values Files.md")
    assert "Containerisation/Helm/Guide.md" in result


def test_backlinks_still_strips_a_genuine_quoted_title(wiki_root):
    """A real '(url "title")' annotation should still be recognized and
    dropped — the fix for unencoded-space targets must not regress this."""
    write(wiki_root, "Values Files.md", "# Values")
    write(wiki_root, "Guide.md", 'See [Values Files](Values Files.md "the values doc").')
    result = storage.backlinks("Values Files.md")
    assert "Guide.md" in result


def test_backlinks_ignores_anchor_fragment(wiki_root):
    write(wiki_root, "Topics/Charts.md", "# Charts")
    write(wiki_root, "Guide.md", "See [Charts](Topics/Charts.md#section) here.")
    result = storage.backlinks("Topics/Charts.md")
    assert "Guide.md" in result


def test_backlinks_ignores_external_links(wiki_root):
    write(wiki_root, "Page.md", "# Page")
    write(wiki_root, "Other.md", "See [external](https://example.com/Page.md).")
    result = storage.backlinks("Page.md")
    assert "no pages link" in result.lower()


def test_backlinks_no_links_returns_message(wiki_root):
    write(wiki_root, "Lonely.md", "# Lonely")
    result = storage.backlinks("Lonely.md")
    assert "no pages link" in result.lower()


def test_backlinks_page_not_found_returns_message(wiki_root):
    result = storage.backlinks("Ghost.md")
    assert "not found" in result.lower()


# ── write_page ───────────────────────────────────────────────────────────────


def test_write_page_unknown_page_requires_create(wiki_root):
    result = storage.write_page("New.md", "content")
    assert "does not exist" in result.lower()
    assert "create=true" in result.lower()
    assert not (wiki_root / "New.md").exists()


def test_write_page_create_true_creates_page(wiki_root):
    result = storage.write_page("New.md", "# New page", create=True)
    assert "created" in result.lower()
    assert (wiki_root / "New.md").read_text() == "# New page\n" or (
        wiki_root / "New.md"
    ).read_text().startswith("# New page")


def test_write_page_overwrites_existing_without_create(wiki_root):
    write(wiki_root, "Existing.md", "old content")
    result = storage.write_page("Existing.md", "new content")
    assert "updated" in result.lower()
    assert "new content" in (wiki_root / "Existing.md").read_text()


def test_write_page_requires_md_extension(wiki_root):
    result = storage.write_page("New.txt", "content", create=True)
    assert "must end in .md" in result.lower()


def test_write_page_rejects_path_traversal(wiki_root):
    result = storage.write_page("../outside.md", "content", create=True)
    assert "escapes the wiki root" in result.lower()


def test_write_page_applies_markdownlint_autofix(wiki_root, monkeypatch):
    monkeypatch.setattr(
        storage.markdownlint,
        "fix_content",
        lambda content, wiki_root: (content.replace("v1", "FIXED"), []),
    )
    storage.write_page("New.md", "v1", create=True)
    assert "FIXED" in (wiki_root / "New.md").read_text()


def test_write_page_leaves_no_temp_file_behind(wiki_root):
    storage.write_page("New.md", "content", create=True)
    assert list(wiki_root.iterdir()) == [wiki_root / "New.md"]


# ── _atomic_write_text ───────────────────────────────────────────────────────


def test_atomic_write_text_writes_content(wiki_root):
    target = wiki_root / "Page.md"
    storage._atomic_write_text(target, "hello world")
    assert target.read_text() == "hello world"


def test_atomic_write_text_overwrites_existing_content(wiki_root):
    target = wiki_root / "Page.md"
    target.write_text("old")
    storage._atomic_write_text(target, "new")
    assert target.read_text() == "new"


def test_atomic_write_text_cleans_up_temp_file_on_failure(wiki_root, monkeypatch):
    target = wiki_root / "Page.md"

    def broken_replace(src, dst):
        raise OSError("simulated failure")

    monkeypatch.setattr(storage.os, "replace", broken_replace)
    with pytest.raises(OSError, match="simulated failure"):
        storage._atomic_write_text(target, "content")
    assert list(wiki_root.iterdir()) == []


# ── delete_page ──────────────────────────────────────────────────────────────


def test_delete_page_not_found_returns_message(wiki_root):
    result = storage.delete_page("Ghost.md")
    assert "not found" in result.lower()


def test_delete_page_without_confirm_reports_plan(wiki_root):
    write(wiki_root, "Lonely.md", "# Lonely")
    result = storage.delete_page("Lonely.md")
    assert "confirm=true" in result.lower()
    assert (wiki_root / "Lonely.md").exists()


def test_delete_page_without_confirm_reports_inbound_links(wiki_root):
    write(wiki_root, "Target.md", "# Target")
    write(wiki_root, "Linker.md", "See [Target](Target.md).")
    result = storage.delete_page("Target.md")
    assert "linked from" in result.lower()
    assert "Linker.md" in result
    assert (wiki_root / "Target.md").exists()


def test_delete_page_inbound_links_formatted_as_readable_list(wiki_root):
    """Regression test: the warning used to interpolate the list of linking
    pages via its Python repr (e.g. "['Guide.md', 'Other.md']") instead of a
    readable comma-joined list."""
    write(wiki_root, "Target.md", "# Target")
    write(wiki_root, "Linker.md", "See [Target](Target.md).")
    result = storage.delete_page("Target.md")
    assert "['Linker.md']" not in result
    assert "linked from 1 page(s): Linker.md" in result


def test_delete_page_with_confirm_deletes(wiki_root):
    write(wiki_root, "Lonely.md", "# Lonely")
    result = storage.delete_page("Lonely.md", confirm=True)
    assert "deleted" in result.lower()
    assert not (wiki_root / "Lonely.md").exists()


def test_delete_page_handles_file_vanishing_before_unlink(wiki_root, monkeypatch):
    """delete_page takes no lock, so a concurrent delete for the same page
    could remove the file between the is_file() check and the unlink call."""
    write(wiki_root, "Lonely.md", "# Lonely")

    def unlink_that_raises(self, *args, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(Path, "unlink", unlink_that_raises)
    result = storage.delete_page("Lonely.md", confirm=True)
    assert "not found" in result.lower()
