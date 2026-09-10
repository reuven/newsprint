from pathlib import Path

import pytest

from newsprint import _libpath


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let a real DYLD_FALLBACK_LIBRARY_PATH leak into or out of a test."""
    monkeypatch.delenv("DYLD_FALLBACK_LIBRARY_PATH", raising=False)


def test_non_darwin_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_libpath.sys, "platform", "linux")
    assert _libpath.prepare_dyld_fallback_library_path() is None
    assert "DYLD_FALLBACK_LIBRARY_PATH" not in _libpath.os.environ


def test_existing_value_is_left_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_libpath.sys, "platform", "darwin")
    monkeypatch.setenv("DYLD_FALLBACK_LIBRARY_PATH", "/already/set")
    assert _libpath.prepare_dyld_fallback_library_path() is None
    assert _libpath.os.environ["DYLD_FALLBACK_LIBRARY_PATH"] == "/already/set"


def test_no_candidate_has_the_library(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(_libpath.sys, "platform", "darwin")
    empty_a = tmp_path / "a"
    empty_b = tmp_path / "b"
    empty_a.mkdir()
    empty_b.mkdir()
    monkeypatch.setattr(_libpath, "_CANDIDATES", (empty_a, empty_b))
    assert _libpath.prepare_dyld_fallback_library_path() is None
    assert "DYLD_FALLBACK_LIBRARY_PATH" not in _libpath.os.environ


def test_first_candidate_with_the_library_is_used(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(_libpath.sys, "platform", "darwin")
    empty = tmp_path / "empty"
    empty.mkdir()
    has_lib = tmp_path / "has_lib"
    has_lib.mkdir()
    (has_lib / "libgobject-2.0.dylib").touch()
    monkeypatch.setattr(_libpath, "_CANDIDATES", (empty, has_lib))
    result = _libpath.prepare_dyld_fallback_library_path()
    assert result == has_lib
    assert _libpath.os.environ["DYLD_FALLBACK_LIBRARY_PATH"] == str(has_lib)
