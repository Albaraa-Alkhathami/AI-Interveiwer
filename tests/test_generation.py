"""Mocked-HTTP tests for the two generation scripts.

No network and no API key are used: `urllib.request.urlopen` is replaced with a
scripted fake, and `time.sleep` is patched out so retry/backoff tests run instantly.
This covers the paths where the real bugs lived — per-minute vs per-day 429s,
empty completions, truncated JSON, and answer validation.

    python -m pytest tests -q
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import generate_questions as gq  # noqa: E402
import regenerate_answers as ra  # noqa: E402

GOOD_ANSWER = (
    "I keep the failure isolated by wrapping the write in a retry with an idempotency key, "
    "so a duplicate delivery cannot apply the change twice. When the queue backs up I shed "
    "load at the edge instead of letting the consumer fall further behind, because a "
    "growing lag is what turns a slow dependency into an outage."
)
assert len(GOOD_ANSWER) > 300


class FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body


def completion(text: str) -> FakeResponse:
    return FakeResponse({"choices": [{"message": {"content": text}}]})


def http_error(code: int, body: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://api.example/v1/chat/completions",
        code=code, msg="error", hdrs=None, fp=io.BytesIO(body.encode()),
    )


@pytest.fixture(autouse=True)
def no_sleep_and_clean_stop(monkeypatch):
    """Retries must not actually wait, and STOP must never leak between tests."""
    for module in (ra, gq):
        monkeypatch.setattr(module.time, "sleep", lambda *_: None, raising=False)
        module.STOP.clear()
    yield
    ra.STOP.clear()
    gq.STOP.clear()


@pytest.fixture
def urlopen(monkeypatch):
    """Install a scripted urlopen; returns the recorded call count and sleeps."""

    def install(responses):
        queue = list(responses)

        def fake(req, timeout=None):
            if not queue:
                raise AssertionError("urlopen called more times than scripted")
            nxt = queue.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt

        monkeypatch.setattr(ra.urllib.request, "urlopen", fake)
        monkeypatch.setattr(gq.urllib.request, "urlopen", fake)
        return queue

    return install


# --------------------------------------------------------------------------- #
# regenerate_answers.call_llm
# --------------------------------------------------------------------------- #
def test_call_llm_returns_content(urlopen):
    urlopen([completion("a fine answer")])
    out = ra.call_llm("q", "Java", "Easy", "key", "https://api.example/v1", "model")
    assert out == "a fine answer"


def test_per_minute_429_waits_for_the_hinted_delay_and_retries(urlopen, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(ra.time, "sleep", lambda s: sleeps.append(s))
    urlopen([
        http_error(429, '{"error":{"message":"Limit 8000 on tokens per minute (TPM). '
                        'Please try again in 4.6s."}}'),
        completion("recovered"),
    ])
    out = ra.call_llm("q", "C++", "Hard", "key", "https://api.example/v1", "model")
    assert out == "recovered"
    assert sleeps and 4.5 <= sleeps[0] <= 6.0  # honoured the hint, not 2**attempt


def test_daily_429_sets_stop_and_never_retries(urlopen):
    urlopen([http_error(429, '{"error":{"message":"Rate limit reached on tokens per day '
                              '(TPD): Limit 200000, Used 200000."}}')])
    with pytest.raises(RuntimeError, match="daily quota"):
        ra.call_llm("q", "Java", "Easy", "key", "https://api.example/v1", "model")
    assert ra.STOP.is_set(), "a daily cap must stop the whole run"


def test_server_error_is_retried(urlopen):
    urlopen([http_error(503, "bad gateway"), completion("third time lucky")])
    assert ra.call_llm("q", "Java", "Easy", "k", "https://api.example/v1", "m") == "third time lucky"


def test_empty_completion_is_retried_then_raises(urlopen):
    """Regression: a reasoning model returning empty content used to look like success."""
    urlopen([completion(""), completion("   ")])
    with pytest.raises(RuntimeError, match="empty completion"):
        ra.call_llm("q", "C++", "Hard", "k", "https://api.example/v1", "m", retries=2)


def test_unparseable_body_raises(urlopen):
    urlopen([FakeResponse({"unexpected": "shape"})])
    with pytest.raises(RuntimeError):
        ra.call_llm("q", "Java", "Easy", "k", "https://api.example/v1", "m", retries=1)


# --------------------------------------------------------------------------- #
# regenerate_answers.validate
# --------------------------------------------------------------------------- #
class TestValidate:
    def test_accepts_a_full_answer(self):
        assert ra.validate(GOOD_ANSWER, set()) is None

    def test_rejects_too_short(self):
        assert ra.validate("Too short.", set()) == "too short"

    def test_accepts_ellipsis_and_closing_quotes(self):
        body = GOOD_ANSWER[:-1]
        assert ra.validate(body + "…", set()) is None
        assert ra.validate(body + ".”", set()) is None

    def test_rejects_unfinished_sentence(self):
        assert ra.validate(GOOD_ANSWER[:-1] + " and", set()) == "unfinished sentence"

    def test_rejects_banned_filler(self):
        assert "banned phrase" in (ra.validate("Certainly! " + GOOD_ANSWER, set()) or "")

    def test_rejects_boilerplate_phrase(self):
        assert "banned phrase" in (
            ra.validate(GOOD_ANSWER + " A good answer should address the requirements.", set()) or ""
        )

    def test_rejects_duplicate_of_another_generated_answer(self):
        assert ra.validate(GOOD_ANSWER, {ra.norm(GOOD_ANSWER)}) == (
            "duplicate of another generated answer"
        )


class TestPickTargets:
    def test_picks_boilerplate_and_repeated_answers(self):
        rows = [
            {"question": "q1", "answer": "A good answer should address the requirements of X."},
            {"question": "q2", "answer": GOOD_ANSWER},
            {"question": "q3", "answer": GOOD_ANSWER},
            {"question": "q4", "answer": "A unique answer " + GOOD_ANSWER},
        ]
        picked = {r["question"] for r in ra.pick_targets(rows)}
        assert picked == {"q1", "q2", "q3"}

    def test_only_template_ignores_duplicates(self):
        rows = [
            {"question": "q1", "answer": "can be understood as a core concept of Y."},
            {"question": "q2", "answer": GOOD_ANSWER},
            {"question": "q3", "answer": GOOD_ANSWER},
        ]
        picked = {r["question"] for r in ra.pick_targets(rows, only_template=True)}
        assert picked == {"q1"}


# --------------------------------------------------------------------------- #
# generate_questions.parse_pairs and call_llm
# --------------------------------------------------------------------------- #
class TestSuspiciousSelector:
    """Priority-1 rows: invented metrics and company names."""

    def test_flags_invented_metrics(self):
        assert ra.is_suspicious("We improved training speed by 3.1x through batching.")
        assert ra.is_suspicious("It reduced memory by 60% on the same hardware.")
        assert ra.is_suspicious("We got 40 ms per frame after the change.")

    def test_flags_company_names(self):
        assert ra.is_suspicious("At Google we sharded the index by tenant.")
        assert ra.is_suspicious("This is how Netflix tunes its encoder.")

    def test_ignores_ordinary_answers(self):
        assert not ra.is_suspicious(GOOD_ANSWER)
        assert not ra.is_suspicious("A mutex guards shared state; a semaphore counts permits.")

    def test_selector_returns_only_flagged_rows(self):
        rows = [
            {"question": "q1", "answer": "We cut latency by 4.3x with a cache."},
            {"question": "q2", "answer": GOOD_ANSWER},
            {"question": "q3", "answer": "At Amazon this is handled by a queue."},
        ]
        picked = [r["question"] for r in ra.pick_targets(rows, only_suspicious=True)]
        assert picked == ["q1", "q3"]


class TestMergeGenerated:
    """Regression: a blind append re-inserted 45 already-applied questions."""

    def test_skips_questions_already_in_the_dataset(self):
        rows = [{"question": "Existing?", "answer": GOOD_ANSWER, "subject": "Java",
                 "difficulty": "Easy", "prompt": "p", "data_flag": ""}]
        good = [
            {"question": "existing?", "answer": GOOD_ANSWER, "subject": "Java", "difficulty": "Easy"},
            {"question": "Brand new?", "answer": GOOD_ANSWER, "subject": "Java", "difficulty": "Hard"},
        ]
        added, skipped = gq.merge_generated(rows, good)
        assert (added, skipped) == (1, 1)
        assert len(rows) == 2
        assert rows[1]["question"] == "Brand new?"

    def test_skips_duplicates_within_the_same_batch(self):
        rows: list[dict] = []
        good = [
            {"question": "Same?", "answer": GOOD_ANSWER, "subject": "Java", "difficulty": "Easy"},
            {"question": "same?  ", "answer": GOOD_ANSWER, "subject": "Java", "difficulty": "Easy"},
        ]
        added, skipped = gq.merge_generated(rows, good)
        assert (added, skipped) == (1, 1)

    def test_new_rows_get_a_prompt_and_blank_flag(self):
        rows: list[dict] = []
        good = [{"question": "Q?", "answer": GOOD_ANSWER, "subject": "CUDA", "difficulty": "Hard"}]
        gq.merge_generated(rows, good)
        assert rows[0]["prompt"] == "Interview questions about CUDA"
        assert rows[0]["data_flag"] == ""


def test_no_metrics_instruction_reaches_the_request(monkeypatch):
    """The extra instruction must actually be in the body, not just defined."""
    seen: list[bytes] = []

    def fake(req, timeout=None):
        seen.append(req.data)
        return completion("an answer with no figures at all")

    monkeypatch.setattr(ra.urllib.request, "urlopen", fake)
    ra.call_llm("q", "S", "D", "k", "https://api.example/v1", "m", extra=ra.NO_METRICS)
    body = json.loads(seen[0])
    user = body["messages"][1]["content"]
    assert "NO figures at all" in user
    assert "never 'about 30%'" in user


def test_no_metrics_instruction_absent_by_default(monkeypatch):
    seen: list[bytes] = []

    def fake(req, timeout=None):
        seen.append(req.data)
        return completion("plain answer")

    monkeypatch.setattr(ra.urllib.request, "urlopen", fake)
    ra.call_llm("q", "S", "D", "k", "https://api.example/v1", "m")
    user = json.loads(seen[0])["messages"][1]["content"]
    assert "NO figures" not in user


class TestParsePairs:
    def test_plain_json_array(self):
        raw = json.dumps([{"question": "Q1?", "answer": "A1"}, {"question": "Q2?", "answer": "A2"}])
        assert [p["question"] for p in gq.parse_pairs(raw)] == ["Q1?", "Q2?"]

    def test_fenced_json_with_surrounding_prose(self):
        raw = 'Here you go:\n```json\n[{"question": "Q1?", "answer": "A1"}]\n```\nHope that helps.'
        assert len(gq.parse_pairs(raw)) == 1

    def test_missing_keys_are_dropped(self):
        raw = json.dumps([{"question": "Q?"}, {"answer": "A"}, {"question": "K?", "answer": "V"}])
        assert [p["question"] for p in gq.parse_pairs(raw)] == ["K?"]

    def test_truncated_json_returns_empty_not_crash(self):
        """This is what a too-small max_tokens produced: half an array."""
        raw = '[{"question": "Q1?", "answer": "A very long answer that gets cut'
        assert gq.parse_pairs(raw) == []

    def test_non_array_payload(self):
        assert gq.parse_pairs("no brackets here") == []
        assert gq.parse_pairs('{"question": "Q?", "answer": "A"}') == []


def test_generate_call_llm_returns_raw_text(urlopen):
    urlopen([completion('[{"question": "Q?", "answer": "A"}]')])
    raw = gq.call_llm("prompt", "k", "https://api.example/v1", "m")
    assert raw.startswith("[")


def test_generate_call_llm_empty_completion_raises(urlopen):
    urlopen([completion("")])
    with pytest.raises(RuntimeError, match="empty completion"):
        gq.call_llm("prompt", "k", "https://api.example/v1", "m", retries=1)


def test_generate_call_llm_daily_cap_sets_stop(urlopen):
    urlopen([http_error(429, '{"error":{"message":"Limit on tokens per day (TPD) reached"}}')])
    with pytest.raises(RuntimeError, match="daily quota"):
        gq.call_llm("prompt", "k", "https://api.example/v1", "m")
    assert gq.STOP.is_set()


class TestSuspiciousIsQuestionAware:
    """A name or figure the question supplies is context, not invention."""

    def test_company_named_in_question_is_not_suspicious(self):
        q = "How is Magento connected with Google Analytics?"
        a = "Magento integrates with Google Analytics to report on behaviour."
        assert ra.is_suspicious(a, q) is False

    def test_company_invented_by_answer_is_suspicious(self):
        q = "How do you cache a slow endpoint?"
        a = "At Netflix we cached the response for a minute."
        assert ra.is_suspicious(a, q) is True

    def test_metric_quoted_from_question_is_not_suspicious(self):
        q = "You must reduce response time by at least 30%. How?"
        a = "I would target the 30% by profiling first, then caching."
        assert ra.is_suspicious(a, q) is False

    def test_metric_invented_by_answer_is_suspicious(self):
        q = "How would you speed up the pipeline?"
        a = "I compressed the model size by 60%."
        assert ra.is_suspicious(a, q) is True

    def test_pick_targets_passes_the_question_through(self):
        rows = [
            {"question": "How is Magento connected with Google Analytics?",
             "answer": "Magento integrates with Google Analytics.", "subject": "Magento",
             "difficulty": "Easy"},
            {"question": "How would you speed up the pipeline?",
             "answer": "I compressed the model size by 60%.", "subject": "ML",
             "difficulty": "Hard"},
        ]
        picked = ra.pick_targets(rows, only_suspicious=True)
        assert [r["subject"] for r in picked] == ["ML"]


class TestOnlyDuplicatedTargets:
    """Shared answer text needs its own mode; default mode drags boilerplate in."""

    @staticmethod
    def _rows():
        boiler = ra.TEMPLATES[0]
        return [
            {"question": "Q boiler", "answer": boiler, "subject": "S", "difficulty": "Easy"},
            {"question": "Q shared 1", "answer": "A shared paragraph of prose.", "subject": "S",
             "difficulty": "Easy"},
            {"question": "Q shared 2", "answer": "A shared paragraph of prose.", "subject": "S",
             "difficulty": "Hard"},
            {"question": "Q unique", "answer": "Unique specific prose that stands alone.",
             "subject": "S", "difficulty": "Medium"},
        ]

    def test_selects_only_shared_text(self):
        picked = {r["question"] for r in ra.pick_targets(self._rows(), only_duplicated=True)}
        assert picked == {"Q shared 1", "Q shared 2"}

    def test_boilerplate_only_rows_are_excluded(self):
        picked = {r["question"] for r in ra.pick_targets(self._rows(), only_duplicated=True)}
        assert "Q boiler" not in picked

    def test_default_mode_still_includes_both(self):
        picked = {r["question"] for r in ra.pick_targets(self._rows())}
        assert {"Q boiler", "Q shared 1"} <= picked


class TestResumeHint:
    """The continue command must repeat the mode, or a later run targets the wrong rows."""

    @staticmethod
    def _args(**kw):
        import types
        base = dict(only_suspicious=False, only_duplicated=False, only_template=False,
                    out="answers_x.csv")
        base.update(kw)
        return types.SimpleNamespace(**base)

    def test_duplicated_mode_is_echoed(self):
        hint = ra.resume_hint(self._args(only_duplicated=True))
        assert "--only-duplicated" in hint and "--resume" in hint
        assert "--only-template" not in hint

    def test_suspicious_mode_is_echoed(self):
        assert "--only-suspicious" in ra.resume_hint(self._args(only_suspicious=True))

    def test_default_mode_has_no_mode_flag(self):
        hint = ra.resume_hint(self._args())
        assert "--only-" not in hint


def test_quota_stop_cancels_queued_requests(tmp_path, monkeypatch, capsys):
    """On a daily cap the run must stop after one chunk, not fire every queued request."""
    import csv as _csv
    import sys as _sys

    header = ["prompt", "question", "answer", "subject", "difficulty", "data_flag"]
    with open(tmp_path / "combined_dataset.csv", "w", encoding="utf-8", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(header)
        for i in range(12):
            w.writerow(["p", f"Q{i}", "The same shared answer text.", "S", "Easy", ""])

    calls = {"n": 0}

    def quota_dead(*a, **k):
        calls["n"] += 1
        ra.STOP.set()
        raise RuntimeError("daily quota exhausted: HTTP 429")

    ra.STOP.clear()
    monkeypatch.setattr(ra, "BASE", tmp_path)
    monkeypatch.setattr(ra, "SRC", tmp_path / "combined_dataset.csv")
    monkeypatch.setattr(ra, "call_llm", quota_dead)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(_sys, "argv",
                        ["regenerate_answers.py", "--only-duplicated", "--workers", "2",
                         "--out", "out.csv"])

    ra.main()
    out = capsys.readouterr().out
    ra.STOP.clear()

    assert calls["n"] <= 4, f"{calls['n']} requests sent despite the cap"
    assert "quota hit" in out
    assert "STOPPED on an unrecoverable provider error" in out


def test_invalid_key_stops_the_run(urlopen):
    """A revoked key must abort the run, not retry every remaining row."""
    urlopen([http_error(401, '{"error":{"message":"Invalid API Key","code":"invalid_api_key"}}')])
    ra.STOP.clear()
    try:
        with pytest.raises(RuntimeError, match="authentication failed"):
            ra.call_llm("q", "Java", "Easy", "bad-key", "https://api.example/v1", "model")
        assert ra.STOP.is_set()
    finally:
        ra.STOP.clear()


def test_invented_figure_is_rejected_in_every_mode(tmp_path, monkeypatch, capsys):
    """A rewrite can invent a figure even when the old answer was merely boilerplate."""
    import csv as _csv
    import sys as _sys

    header = ["prompt", "question", "answer", "subject", "difficulty", "data_flag"]
    with open(tmp_path / "combined_dataset.csv", "w", encoding="utf-8", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(header)
        for i in range(4):
            w.writerow(["p", f"Q{i}", "The same shared answer text.", "S", "Easy", ""])

    monkeypatch.setattr(ra, "BASE", tmp_path)
    monkeypatch.setattr(ra, "SRC", tmp_path / "combined_dataset.csv")
    monkeypatch.setattr(ra, "call_llm",
                        lambda *a, **k: "I cut latency by 45 % with a shorter key path.")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(_sys, "argv",
                        ["regenerate_answers.py", "--only-duplicated", "--workers", "1",
                         "--out", "out.csv"])
    ra.STOP.clear()
    ra.main()
    ra.STOP.clear()

    rows = list(_csv.DictReader(open(tmp_path / "out.csv", encoding="utf-8", newline="")))
    assert rows, "no rows were written"
    assert all(r["status"].startswith("rejected") for r in rows), [r["status"] for r in rows]


def test_apply_refuses_an_invented_figure(tmp_path, monkeypatch, capsys):
    """A run file made before a guard existed must still be checked when merged."""
    import csv as _csv

    header = ["prompt", "question", "answer", "subject", "difficulty", "data_flag"]
    with open(tmp_path / "combined_dataset.csv", "w", encoding="utf-8", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(header)
        w.writerow(["p", "Q1", "original answer text.", "S", "Easy", ""])

    out = tmp_path / "out.csv"
    with open(out, "w", encoding="utf-8", newline="") as fh:
        w = _csv.DictWriter(fh, fieldnames=["question", "subject", "difficulty",
                                            "old_answer", "new_answer", "status"])
        w.writeheader()
        w.writerow({"question": "Q1", "subject": "S", "difficulty": "Easy",
                    "old_answer": "original answer text.",
                    "new_answer": "I cut memory use by 60 % on the same hardware.",
                    "status": "ok"})

    monkeypatch.setattr(ra, "BASE", tmp_path)
    monkeypatch.setattr(ra, "SRC", tmp_path / "combined_dataset.csv")
    import sys as _sys
    monkeypatch.setattr(_sys, "argv",
                        ["regenerate_answers.py", "--apply", "out.csv"])
    ra.main()
    text = capsys.readouterr().out

    rows = list(_csv.DictReader(open(tmp_path / "combined_dataset.csv", encoding="utf-8",
                                   newline="")))
    assert rows[0]["answer"] == "original answer text.", "invented figure was applied"
    assert "invented specifics" in text
