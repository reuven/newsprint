"""Tests for domain model invariants."""

import dataclasses
from datetime import UTC, datetime

import pytest

from newsprint.models import Document, Origin, Verdict


class TestOriginFrozen:
    """Origin must be frozen so that clean.py can use dataclasses.replace()."""

    def test_origin_is_frozen(self) -> None:
        """Attempting to mutate a frozen Origin raises FrozenInstanceError."""
        origin = Origin(kind="email", identifier="msg-123")
        with pytest.raises(dataclasses.FrozenInstanceError):
            origin.kind = "url"  # type: ignore

    def test_origin_uid_attribute_is_frozen(self) -> None:
        """uid field is also frozen."""
        origin = Origin(kind="email", identifier="msg-123", uid=42)
        with pytest.raises(dataclasses.FrozenInstanceError):
            origin.uid = 43  # type: ignore


class TestDocumentFrozen:
    """Document must be frozen so that clean.py can use dataclasses.replace()."""

    def test_document_is_frozen(self) -> None:
        """Attempting to mutate a frozen Document raises FrozenInstanceError."""
        origin = Origin(kind="email", identifier="msg-123")
        doc = Document(
            origin=origin,
            publication="Test Newsletter",
            title="Test Article",
            date=datetime(2026, 9, 7, tzinfo=UTC),
            html="<p>content</p>",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            doc.title = "New Title"  # type: ignore

    def test_document_html_attribute_is_frozen(self) -> None:
        """html field is also frozen."""
        origin = Origin(kind="email", identifier="msg-123")
        doc = Document(
            origin=origin,
            publication="Test Newsletter",
            title="Test Article",
            date=datetime(2026, 9, 7, tzinfo=UTC),
            html="<p>content</p>",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            doc.html = "<p>new</p>"  # type: ignore


class TestDocumentDefaultImages:
    """images_dropped defaults to tuple, not list, to prevent shared mutable state."""

    def test_images_dropped_defaults_to_empty_tuple(self) -> None:
        """Verify the default is an empty tuple, not a list."""
        origin = Origin(kind="email", identifier="msg-123")
        doc = Document(
            origin=origin,
            publication="Test Newsletter",
            title="Test Article",
            date=datetime(2026, 9, 7, tzinfo=UTC),
            html="<p>content</p>",
        )
        assert doc.images_dropped == ()
        assert isinstance(doc.images_dropped, tuple)

    def test_images_dropped_not_shared_between_documents(self) -> None:
        """Two Documents' images_dropped defaults must be immutable (tuple not list)."""
        origin1 = Origin(kind="email", identifier="msg-1")
        doc1 = Document(
            origin=origin1,
            publication="Newsletter",
            title="Article 1",
            date=datetime(2026, 9, 7, tzinfo=UTC),
            html="<p>content</p>",
        )

        origin2 = Origin(kind="email", identifier="msg-2")
        doc2 = Document(
            origin=origin2,
            publication="Newsletter",
            title="Article 2",
            date=datetime(2026, 9, 8, tzinfo=UTC),
            html="<p>other</p>",
        )

        # Both must have tuples (immutable); a list default would be a shared mutable bug
        assert isinstance(doc1.images_dropped, tuple)
        assert isinstance(doc2.images_dropped, tuple)
        assert doc1.images_dropped == ()
        assert doc2.images_dropped == ()


class TestDocumentDefaultBlocksDropped:
    """blocks_dropped defaults to tuple, not list, for the same reason
    images_dropped does: a mutable default would be shared between every
    Document that never overrides it."""

    def test_blocks_dropped_defaults_to_empty_tuple(self) -> None:
        origin = Origin(kind="email", identifier="msg-123")
        doc = Document(
            origin=origin,
            publication="Test Newsletter",
            title="Test Article",
            date=datetime(2026, 9, 7, tzinfo=UTC),
            html="<p>content</p>",
        )
        assert doc.blocks_dropped == ()
        assert isinstance(doc.blocks_dropped, tuple)


class TestOriginUidDefault:
    """uid defaults to None for URL-sourced documents and other origins without a uid."""

    def test_origin_uid_defaults_to_none(self) -> None:
        """Verify uid defaults to None when not provided."""
        origin = Origin(kind="email", identifier="msg-123")
        assert origin.uid is None


class TestVerdictMembers:
    """Verdict must have exactly FILLER, WIDOW, FULL for Task 6 branching."""

    def test_verdict_has_exactly_three_members(self) -> None:
        """Verdict enum has exactly the three required members."""
        members = {v.name for v in Verdict}
        assert members == {"FILLER", "WIDOW", "FULL"}

    def test_verdict_member_values(self) -> None:
        """Verify each member has the expected string value."""
        assert Verdict.FILLER.value == "filler"
        assert Verdict.WIDOW.value == "widow"
        assert Verdict.FULL.value == "full"
