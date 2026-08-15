"""Resume copies held by the app, not pointed at.

The pointer model lost the "before" the moment somebody acted on our advice,
and a moved file read as an empty resume - which scores every skill as
missing and is indistinguishable from a genuinely thin one.
"""
from __future__ import annotations

from unlatched import resumes


def test_attaching_copies_the_file_in(home, tmp_path):
    src = tmp_path / "cv.txt"
    src.write_text("Customer service and troubleshooting.", encoding="utf-8")
    record = resumes.attach(src, resumes.ORIGINAL, home)
    stored = resumes.resumes_dir(home) / record["file"]
    assert stored.is_file()
    assert stored.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")
    # The source is untouched - we copy, never move.
    assert src.is_file()


def test_a_new_optimized_copy_never_destroys_the_previous_one(home, tmp_path):
    """A search screened against one resume has to stay reproducible after the
    next optimisation pass."""
    for text in ("first pass", "second pass"):
        src = tmp_path / "cv.txt"
        src.write_text(text, encoding="utf-8")
        resumes.attach(src, resumes.OPTIMIZED, home)
    optimized = [v for v in resumes.versions(home) if v["role"] == resumes.OPTIMIZED]
    assert len(optimized) == 2


def test_screening_reads_the_optimized_copy_when_there_is_one(home, tmp_path):
    original = tmp_path / "original.txt"
    original.write_text("before", encoding="utf-8")
    resumes.attach(original, resumes.ORIGINAL, home)
    assert resumes.active_path({}, home).read_text(encoding="utf-8") == "before"

    optimized = tmp_path / "optimized.txt"
    optimized.write_text("after", encoding="utf-8")
    resumes.attach(optimized, resumes.OPTIMIZED, home)
    assert resumes.active_path({}, home).read_text(encoding="utf-8") == "after"


def test_a_profile_with_only_the_legacy_pointer_still_works(home, tmp_path):
    """Profiles set up before any of this existed must not silently lose their
    resume."""
    legacy = tmp_path / "legacy.txt"
    legacy.write_text("legacy", encoding="utf-8")
    active = resumes.active_path({"resume_path": str(legacy)}, home)
    assert active is not None
    assert active.read_text(encoding="utf-8") == "legacy"


def test_a_legacy_pointer_at_a_missing_file_reports_nothing(home):
    assert resumes.active_path({"resume_path": "/nope/gone.txt"}, home) is None


def test_an_unreadable_format_is_stored_but_flagged(home, tmp_path):
    """Refusing somebody's own document helps nobody, but a format we cannot
    read scores every skill missing - so they hear it from us."""
    src = tmp_path / "cv.pdf"
    src.write_bytes(b"%PDF-1.4 not really")
    record = resumes.attach(src, resumes.ORIGINAL, home)
    assert record["readable"] is False


def test_an_unknown_role_is_refused(home, tmp_path):
    src = tmp_path / "cv.txt"
    src.write_text("x", encoding="utf-8")
    try:
        resumes.attach(src, "sideways", home)
    except ValueError:
        return
    raise AssertionError("an unknown role must not be stored")
