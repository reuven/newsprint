import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Self

import pytest

from newsprint.config import load_config
from newsprint.models import Document, Origin, Verdict
from newsprint.pdfutil import page_text
from newsprint.pipeline import Built
from newsprint.summarize import (
    _SCHEMA,
    ApiResponse,
    Candidate,
    SummaryContent,
    SummaryError,
    Topic,
    _build_prompt,
    _default_caller,
    _parse_content,
    build_summary_pages,
    read_api_key,
)

PACKET_DATE = date(2026, 9, 5)


INTEREST_TEXT = "Ideas with a public dataset behind them, for pandas exercises."
INTEREST = 'interest = "Ideas with a public dataset behind them, for pandas exercises."'


@pytest.fixture
def config(tmp_path: Path):
    """enabled=true, pointed at a deliberately absent key file.

    Never the tracked default of ~/.env: on a machine that actually has
    one (mine does), a test that forgets to override the caller would
    silently make a real network call instead of failing loudly. Pointing
    every test's default config at a file that cannot exist makes that
    mistake fail fast instead.
    """
    path = tmp_path / "config.toml"
    key_file = tmp_path / "no-such-key-file.env"
    path.write_text(
        f'[summary]\nenabled = true\napi_key_file = "{key_file}"\n{INTEREST}'
    )
    return load_config(path)


@pytest.fixture
def working_config(tmp_path: Path):
    """Same as `config`, but api_key_file actually exists and holds a
    (fake, test-only) key - for tests that exercise a successful call via
    an injected caller and therefore need read_api_key() to succeed."""
    key_file = tmp_path / "working.env"
    key_file.write_text("ANTHROPIC_API_KEY=sk-test-not-a-real-key\n")
    path = tmp_path / "config.toml"
    path.write_text(
        f'[summary]\nenabled = true\napi_key_file = "{key_file}"\n{INTEREST}'
    )
    return load_config(path)


def _built(index: int, publication: str | None = None, title: str = "Issue") -> Built:
    name = publication or f"Newsletter {index:02d}"
    document = Document(
        origin=Origin(kind="email", identifier=f"<{index}@example.com>"),
        publication=name,
        title=title,
        date=datetime(2026, 9, 5, tzinfo=UTC),
        html=f"<p>Some cleaned prose about {name}, item {index}.</p>",
        author=f"Author {index}",
    )
    return Built(
        document=document, pdf=Path("/dev/null"), cells=3, verdict=Verdict.FULL
    )


def _valid_response(topics=None, candidates=None) -> ApiResponse:
    payload = {
        "topics": topics
        if topics is not None
        else [{"title": "Interest rates", "detail": "Several pieces on the Fed."}],
        "candidates": candidates
        if candidates is not None
        else [
            {
                "topic": "Municipal bond yields",
                "why": "Recurs across three newsletters this week.",
                "source": "FRED municipal bond yield series",
            }
        ],
    }
    return ApiResponse(text=json.dumps(payload), input_tokens=1234, output_tokens=56)


# --------------------------------------------------------------------------
# read_api_key
# --------------------------------------------------------------------------


def test_read_api_key_returns_the_named_variable(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("OTHER_KEY=unrelated\nANTHROPIC_API_KEY=sk-test-123\nMORE=x\n")
    assert read_api_key(path, "ANTHROPIC_API_KEY") == "sk-test-123"


def test_read_api_key_strips_surrounding_quotes(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text('ANTHROPIC_API_KEY="sk-test-123"\n')
    assert read_api_key(path, "ANTHROPIC_API_KEY") == "sk-test-123"


def test_read_api_key_ignores_blank_lines_and_comments(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("\n# a comment\n\nANTHROPIC_API_KEY=sk-test-123\n")
    assert read_api_key(path, "ANTHROPIC_API_KEY") == "sk-test-123"


def test_read_api_key_missing_file_names_the_file_not_the_contents(
    tmp_path: Path,
) -> None:
    path = tmp_path / "absent.env"
    with pytest.raises(SummaryError) as exc_info:
        read_api_key(path, "ANTHROPIC_API_KEY")
    assert str(path) in str(exc_info.value)


def test_read_api_key_unreadable_file_is_reported_without_the_contents(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / ".env"
    path.write_text("ANTHROPIC_API_KEY=sk-test-123\n")

    def explode(self, *args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", explode)
    with pytest.raises(SummaryError) as exc_info:
        read_api_key(path, "ANTHROPIC_API_KEY")
    assert "sk-test-123" not in str(exc_info.value)
    assert str(path) in str(exc_info.value)


def test_read_api_key_missing_variable_names_file_and_variable_only(
    tmp_path: Path,
) -> None:
    """The file holds several unrelated keys - nothing else in it is any
    of our business, so a missing-variable error names the file and the
    variable it was looking for, never any other key's name or value."""
    path = tmp_path / ".env"
    path.write_text("SOME_OTHER_SERVICE_KEY=super-secret-value\n")
    with pytest.raises(SummaryError) as exc_info:
        read_api_key(path, "ANTHROPIC_API_KEY")
    message = str(exc_info.value)
    assert "super-secret-value" not in message
    assert "SOME_OTHER_SERVICE_KEY" not in message
    assert str(path) in message
    assert "ANTHROPIC_API_KEY" in message


def test_read_api_key_rejects_an_empty_value(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("ANTHROPIC_API_KEY=\n")
    with pytest.raises(SummaryError):
        read_api_key(path, "ANTHROPIC_API_KEY")


# --------------------------------------------------------------------------
# _build_prompt
# --------------------------------------------------------------------------


def test_build_prompt_includes_every_newsletters_publication_and_subject() -> None:
    built = [_built(0, publication="Money Stuff", title="Bank Runs"), _built(1)]
    prompt = _build_prompt(built, INTEREST_TEXT)
    assert "Money Stuff" in prompt
    assert "Bank Runs" in prompt
    assert "Newsletter 01" in prompt


def test_the_readers_own_interest_reaches_the_prompt() -> None:
    """The second page used to be hardcoded to one person's newsletter.
    What it asks for now comes from config, so the configured words have
    to actually arrive in the prompt."""
    prompt = _build_prompt([_built(0)], INTEREST_TEXT)
    assert INTEREST_TEXT in prompt
    assert "candidates" in prompt


def test_no_interest_asks_only_for_topics() -> None:
    """With nothing configured there is no second page to ask for, and
    the prompt must not invent a purpose for the reader."""
    prompt = _build_prompt([_built(0)], "")
    assert "candidates" not in prompt
    assert "topics" in prompt


def test_the_schema_follows_the_interest() -> None:
    """A model asked for topics only must not be handed a schema that
    requires candidates it was never asked to produce."""
    from newsprint.summarize import _schema_for

    assert "candidates" in _schema_for(INTEREST_TEXT)["required"]
    assert "candidates" not in _schema_for("")["required"]
    assert "topics" in _schema_for("")["required"]


def test_build_prompt_asks_for_honesty_over_padding() -> None:
    prompt = _build_prompt([_built(0)], INTEREST_TEXT).lower()
    assert "nothing" in prompt or "honest" in prompt


# --------------------------------------------------------------------------
# _parse_content
# --------------------------------------------------------------------------


def test_parse_content_accepts_a_well_formed_payload() -> None:
    content = _parse_content(
        json.dumps(
            {
                "topics": [{"title": "T", "detail": "D"}],
                "candidates": [{"topic": "C", "why": "W", "source": "S"}],
            }
        ),
        INTEREST_TEXT,
    )
    assert content == SummaryContent(
        topics=(Topic(title="T", detail="D"),),
        candidates=(Candidate(topic="C", why="W", source="S"),),
    )


def test_parse_content_accepts_empty_lists() -> None:
    content = _parse_content(
        json.dumps({"topics": [], "candidates": []}), INTEREST_TEXT
    )
    assert content == SummaryContent(topics=(), candidates=())


def test_parse_content_rejects_invalid_json() -> None:
    with pytest.raises(SummaryError):
        _parse_content("not json at all {", INTEREST_TEXT)


def test_parse_content_rejects_a_non_object_top_level() -> None:
    with pytest.raises(SummaryError):
        _parse_content(json.dumps([1, 2, 3]), INTEREST_TEXT)


def test_parse_content_rejects_missing_keys() -> None:
    with pytest.raises(SummaryError):
        _parse_content(
            json.dumps({"topics": [{"title": "T"}], "candidates": []}), INTEREST_TEXT
        )


def test_parse_content_rejects_a_missing_top_level_key() -> None:
    with pytest.raises(SummaryError):
        _parse_content(json.dumps({"topics": []}), INTEREST_TEXT)


# --------------------------------------------------------------------------
# build_summary_pages
# --------------------------------------------------------------------------


def test_a_successful_call_renders_two_pages(working_config, tmp_path: Path) -> None:
    built = [_built(0), _built(1)]
    outcome = build_summary_pages(
        built,
        working_config,
        PACKET_DATE,
        tmp_path,
        caller=lambda *a, **kw: _valid_response(),
    )
    assert outcome.reason is None
    assert len(outcome.pages) == 2
    assert all(page.cells >= 1 for page in outcome.pages)
    assert outcome.input_tokens == 1234
    assert outcome.output_tokens == 56
    assert outcome.elapsed_seconds >= 0.0
    topics_text = page_text(outcome.pages[0].pdf, 0)
    assert "Interest rates" in topics_text
    candidates_text = page_text(outcome.pages[1].pdf, 0)
    assert "Municipal bond yields" in candidates_text
    assert "FRED municipal bond yield series" in candidates_text


def test_an_empty_candidates_list_renders_only_the_topics_page(
    working_config, tmp_path: Path
) -> None:
    """The model saying it has nothing good to add is not a failure - it
    is honest, and must not print a blank second page."""
    outcome = build_summary_pages(
        [_built(0)],
        working_config,
        PACKET_DATE,
        tmp_path,
        caller=lambda *a, **kw: _valid_response(candidates=[]),
    )
    assert outcome.reason is None
    assert len(outcome.pages) == 1


def test_both_sections_empty_yields_no_pages_and_no_failure(
    working_config, tmp_path: Path
) -> None:
    outcome = build_summary_pages(
        [_built(0)],
        working_config,
        PACKET_DATE,
        tmp_path,
        caller=lambda *a, **kw: _valid_response(topics=[], candidates=[]),
    )
    assert outcome.reason is None
    assert outcome.pages == ()


def test_a_missing_key_degrades_without_raising(config, tmp_path: Path) -> None:
    outcome = build_summary_pages(
        [_built(0)],
        config,
        PACKET_DATE,
        tmp_path,
    )
    assert outcome.pages == ()
    assert outcome.reason is not None
    assert str(config.summary.api_key_file) in outcome.reason


def test_a_caller_exception_degrades_and_reports_one_reason(
    working_config, tmp_path: Path
) -> None:
    def exploding_caller(*args, **kwargs):
        raise TimeoutError("timed out talking to Anthropic")

    outcome = build_summary_pages(
        [_built(0)],
        working_config,
        PACKET_DATE,
        tmp_path,
        caller=exploding_caller,
    )
    assert outcome.pages == ()
    assert "timed out" in outcome.reason


def test_malformed_json_from_the_model_degrades_rather_than_crashing(
    working_config, tmp_path: Path
) -> None:
    outcome = build_summary_pages(
        [_built(0)],
        working_config,
        PACKET_DATE,
        tmp_path,
        caller=lambda *a, **kw: ApiResponse(
            text="not valid json", input_tokens=10, output_tokens=1
        ),
    )
    assert outcome.pages == ()
    assert outcome.reason is not None


def test_the_real_api_key_never_appears_in_a_failure_reason(
    config, tmp_path: Path
) -> None:
    """Worst case: even if the underlying error text somehow echoed the
    key, the reason reported to the user must have it redacted."""
    path = tmp_path / ".env"
    path.write_text("ANTHROPIC_API_KEY=sk-super-secret-value\n")
    from dataclasses import replace

    leaking_config = replace(config, summary=replace(config.summary, api_key_file=path))

    def leaking_caller(api_key, model, prompt, timeout, schema):
        raise RuntimeError(f"connection failed while using key {api_key}")

    outcome = build_summary_pages(
        [_built(0)],
        leaking_config,
        PACKET_DATE,
        tmp_path,
        caller=leaking_caller,
    )
    assert outcome.pages == ()
    assert "sk-super-secret-value" not in outcome.reason


def test_caller_receives_the_configured_model_and_timeout(
    config, tmp_path: Path
) -> None:
    path = tmp_path / ".env"
    path.write_text("ANTHROPIC_API_KEY=sk-test-123\n")
    from dataclasses import replace

    my_config = replace(config, summary=replace(config.summary, api_key_file=path))
    calls = []

    def recording_caller(api_key, model, prompt, timeout, schema):
        calls.append((api_key, model, prompt, timeout))
        return _valid_response()

    build_summary_pages(
        [_built(0)], my_config, PACKET_DATE, tmp_path, caller=recording_caller
    )
    assert len(calls) == 1
    api_key, model, prompt, timeout = calls[0]
    assert api_key == "sk-test-123"
    assert model == my_config.summary.model
    assert timeout == pytest.approx(my_config.summary.timeout_seconds)
    assert isinstance(prompt, str) and prompt


# --------------------------------------------------------------------------
# _default_caller
# --------------------------------------------------------------------------


@dataclass
class _FakeBlock:
    type: str
    text: str = ""


@dataclass
class _FakeUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class _FakeMessage:
    content: list
    usage: _FakeUsage
    stop_reason: str = "end_turn"


class _FakeStreamContext:
    """Stands in for the object client.messages.stream(...) returns: a
    context manager whose get_final_message() yields the accumulated
    Message, mirroring the real streaming helper's shape."""

    def __init__(self, message: _FakeMessage) -> None:
        self._message = message

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def get_final_message(self) -> _FakeMessage:
        return self._message


def test_default_caller_calls_the_sdk_and_returns_usage(monkeypatch) -> None:
    import newsprint.summarize as summarize_module

    captured: dict = {}

    class FakeAnthropic:
        def __init__(self, api_key, **kwargs):
            captured["api_key"] = api_key
            captured["kwargs"] = kwargs
            self._message = _FakeMessage(
                # A thinking block genuinely precedes the text block on a
                # live Opus 5 response (thinking is on by default) - this
                # fixture reproduces that ordering rather than the
                # simpler, unrealistic text-only case.
                content=[
                    _FakeBlock(type="thinking", text=""),
                    _FakeBlock(
                        type="text", text=json.dumps({"topics": [], "candidates": []})
                    ),
                ],
                usage=_FakeUsage(input_tokens=100, output_tokens=20),
            )
            self.messages = self

        def with_options(self, timeout):
            captured["timeout"] = timeout
            return self

        def stream(self, **kwargs):
            captured["stream_kwargs"] = kwargs
            return _FakeStreamContext(self._message)

    monkeypatch.setattr(summarize_module.anthropic, "Anthropic", FakeAnthropic)
    response = _default_caller("sk-test", "claude-opus-5", "prompt text", 12.5, _SCHEMA)
    assert response.text == json.dumps({"topics": [], "candidates": []})
    assert response.input_tokens == 100
    assert response.output_tokens == 20
    assert captured["api_key"] == "sk-test"
    assert captured["timeout"] == pytest.approx(12.5)
    assert captured["stream_kwargs"]["model"] == "claude-opus-5"
    assert captured["stream_kwargs"]["messages"] == [
        {"role": "user", "content": "prompt text"}
    ]
    assert "output_config" in captured["stream_kwargs"]
    assert captured["stream_kwargs"]["output_config"]["effort"] == "medium"


def test_default_caller_raises_on_refusal(monkeypatch) -> None:
    import newsprint.summarize as summarize_module

    class RefusingAnthropic:
        def __init__(self, api_key, **kwargs):
            self.messages = self

        def with_options(self, timeout):
            return self

        def stream(self, **kwargs):
            return _FakeStreamContext(
                _FakeMessage(content=[], usage=_FakeUsage(0, 0), stop_reason="refusal")
            )

    monkeypatch.setattr(summarize_module.anthropic, "Anthropic", RefusingAnthropic)
    with pytest.raises(SummaryError):
        _default_caller("sk-test", "claude-opus-5", "prompt", 10.0, _SCHEMA)


def test_default_caller_raises_a_clear_error_when_no_text_block_is_present(
    monkeypatch,
) -> None:
    """A non-refusal response carrying only a thinking block (no text at
    all) must fail with a clear message, not an unguarded StopIteration."""
    import newsprint.summarize as summarize_module

    class ThinkingOnlyAnthropic:
        def __init__(self, api_key, **kwargs):
            self.messages = self

        def with_options(self, timeout):
            return self

        def stream(self, **kwargs):
            return _FakeStreamContext(
                _FakeMessage(
                    content=[_FakeBlock(type="thinking", text="")],
                    usage=_FakeUsage(0, 0),
                    stop_reason="end_turn",
                )
            )

    monkeypatch.setattr(summarize_module.anthropic, "Anthropic", ThinkingOnlyAnthropic)
    with pytest.raises(SummaryError, match="no text content"):
        _default_caller("sk-test", "claude-opus-5", "prompt", 10.0, _SCHEMA)
