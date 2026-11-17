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


# --------------------------------------------------------------------------- #
# Corrective retry: a guard rejection costs one extra call, not one lost day
# --------------------------------------------------------------------------- #
BAD_METRIC = GOOD_ANSWER + " In production this cut tail latency by 40 % on the same hardware."


def _dup_dataset(tmp_path):
    """Two rows sharing one answer, so --only-duplicated has a target."""
    import csv as _csv
    path = tmp_path / "combined_dataset.csv"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["prompt", "question", "answer", "subject", "difficulty", "data_flag"])
        w.writerow(["p", "How do you isolate a failing write?", "shared old answer", "Java", "Easy", ""])
        w.writerow(["p", "How do you bound queue lag?", "shared old answer", "Java", "Hard", ""])
    return path


def _run_regenerate(tmp_path, monkeypatch, capsys, extra_args=()):
    monkeypatch.setattr(ra, "BASE", tmp_path)
    monkeypatch.setattr(ra, "SRC", tmp_path / "combined_dataset.csv")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    argv = ["regenerate_answers.py", "--only-duplicated", "--workers", "1",
            "--max-requests", "5", "--out", "out.csv", *extra_args]
    monkeypatch.setattr(sys, "argv", argv)
    ra.main()
    import csv as _csv
    with open(tmp_path / "out.csv", encoding="utf-8", newline="") as fh:
        return list(_csv.DictReader(fh)), capsys.readouterr().out


def test_rejection_reason_is_fed_back_into_one_retry(tmp_path, monkeypatch, capsys):
    """First draft invents 40 %; the retry is told exactly that, and it succeeds."""
    _dup_dataset(tmp_path)
    bodies: list[dict] = []

    def fake(req, timeout=None):
        bodies.append(json.loads(req.data))
        return completion(BAD_METRIC) if len(bodies) == 1 else completion(GOOD_ANSWER)

    monkeypatch.setattr(ra.urllib.request, "urlopen", fake)
    # one target: the second row would get its own retry and muddy the call count
    rows, out = _run_regenerate(tmp_path, monkeypatch, capsys, ("--limit", "1", "--retry"))

    assert len(bodies) == 2, "expected exactly one corrective retry"
    retry_prompt = bodies[1]["messages"][1]["content"]
    assert "rejected by an automated check" in retry_prompt
    assert "40 %" in retry_prompt, "the offending figure must be quoted back"
    assert "NO figures at all" in retry_prompt
    assert rows[0]["status"] == "ok"
    assert rows[0]["new_answer"] == GOOD_ANSWER
    assert "1 recovered" in out


def test_second_failure_is_recorded_and_not_retried_again(tmp_path, monkeypatch, capsys):
    """One retry only: a model that keeps inventing figures must not eat the budget."""
    _dup_dataset(tmp_path)
    calls: list[int] = []

    def fake(req, timeout=None):
        calls.append(1)
        return completion(BAD_METRIC)

    monkeypatch.setattr(ra.urllib.request, "urlopen", fake)
    rows, _out = _run_regenerate(tmp_path, monkeypatch, capsys, ("--limit", "1", "--retry"))

    assert len(calls) == 2, f"expected 2 calls for 1 target, got {len(calls)}"
    assert rows[0]["status"].startswith("rejected:")
    assert "; retry:" in rows[0]["status"]


def test_retry_is_off_by_default(tmp_path, monkeypatch, capsys):
    """Opt-in: a rejection costs one call unless --retry is passed."""
    _dup_dataset(tmp_path)
    calls: list[int] = []

    def fake(req, timeout=None):
        calls.append(1)
        return completion(BAD_METRIC)

    monkeypatch.setattr(ra.urllib.request, "urlopen", fake)
    rows, _out = _run_regenerate(tmp_path, monkeypatch, capsys, ("--limit", "1"))

    assert len(calls) == 1
    assert rows[0]["status"].startswith("rejected:")


def test_retry_is_not_spent_once_the_request_budget_is_gone(tmp_path, monkeypatch, capsys):
    """The retry must respect --max-requests: no call beyond the allowance."""
    _dup_dataset(tmp_path)
    calls: list[int] = []

    def fake(req, timeout=None):
        calls.append(1)
        return completion(BAD_METRIC)

    monkeypatch.setattr(ra.urllib.request, "urlopen", fake)
    monkeypatch.setattr(ra, "BASE", tmp_path)
    monkeypatch.setattr(ra, "SRC", tmp_path / "combined_dataset.csv")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(sys, "argv", ["regenerate_answers.py", "--only-duplicated",
                                      "--workers", "1", "--max-requests", "1",
                                      "--retry", "--out", "out.csv"])
    ra.main()
    assert len(calls) == 1, "the budget of one call must not fund a retry"


def test_dead_retry_does_not_abandon_the_rest_of_the_chunk(tmp_path, monkeypatch, capsys):
    """A retry that dies on the daily cap must not drop the row still in flight.

    This is the regression the chunk loop was rewritten for: the old code broke out of
    the per-chunk loop on STOP, so a second target that had already been paid for was
    never written and vanished from the run file.
    """
    import threading

    _dup_dataset(tmp_path)
    bodies: list[dict] = []

    def fake(req, timeout=None):
        bodies.append(json.loads(req.data))
        user = bodies[-1]["messages"][1]["content"]
        if "rejected by an automated check" in user:
            # the corrective retry hits the daily cap: STOP is set mid-chunk
            raise http_error(429, '{"error":{"message":"Limit reached on tokens per day (TPD)"}}')
        if len(bodies) == 1:
            return completion(BAD_METRIC)          # target 1: rejected -> retried
        threading.Event().wait(0.25)               # target 2: slow enough to still be in flight
        return completion(GOOD_ANSWER)

    monkeypatch.setattr(ra.urllib.request, "urlopen", fake)
    rows, out = _run_regenerate(tmp_path, monkeypatch, capsys,
                                ("--limit", "2", "--workers", "2", "--retry"))

    assert len(rows) == 2, f"a paid-for row was dropped: {[r['status'] for r in rows]}"
    statuses = sorted(r["status"].split(":")[0] for r in rows)
    assert statuses == ["error", "ok"], statuses
    assert "quota hit after" in out


@pytest.mark.parametrize("workers", [2, 4])
def test_retry_runs_beside_another_in_flight_call(tmp_path, monkeypatch, capsys, workers):
    """The retry must run on a pool thread, not on the thread that drains results.

    Target 2 blocks until the retry has started, so the two are provably in flight
    together; the retry then records which thread it ran on. Neither assertion depends
    on how many targets the pool queues at once: target 2 is always in the first batch,
    and a retry executed inline runs on the consumer thread and fails the thread check.
    Parametrised over worker counts, because that is what sets the queue depth.
    """
    import csv as _csv
    import threading

    path = tmp_path / "combined_dataset.csv"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["prompt", "question", "answer", "subject", "difficulty", "data_flag"])
        for i in range(1, 9):
            w.writerow(["p", f"Question {i} about failure isolation?", "shared old answer",
                        "Java", "Easy", ""])

    retry_started = threading.Event()
    ran_on_main: list[bool] = []

    def fake(req, timeout=None):
        user = json.loads(req.data)["messages"][1]["content"]
        if "rejected by an automated check" in user:
            ran_on_main.append(threading.current_thread() is threading.main_thread())
            retry_started.set()
            return completion(GOOD_ANSWER + " I rechecked the isolation boundary.")
        if "Question 1 about" in user:
            return completion(BAD_METRIC)          # first target is rejected -> retried
        if "Question 2 about" in user:
            assert retry_started.wait(5), "the retry never ran while this call was waiting"
        return completion(GOOD_ANSWER + f" Ref {abs(hash(user)) % 9973}.")

    monkeypatch.setattr(ra.urllib.request, "urlopen", fake)
    rows, out = _run_regenerate(tmp_path, monkeypatch, capsys,
                                ("--workers", str(workers), "--max-requests", "20", "--retry"))

    assert len(rows) == 8, f"not every target was recorded: {len(rows)}"
    assert not [r for r in rows if r["status"].startswith("error")],         [r["status"] for r in rows if r["status"].startswith("error")]
    first = next(r for r in rows if "Question 1 about" in r["question"])
    assert first["status"] == "ok", first["status"]
    assert ran_on_main == [False], "the retry ran on the consumer thread"
    assert "1 recovered" in out


def test_duplicate_rejection_is_not_retried(tmp_path, monkeypatch, capsys):
    """A duplicate cannot be fixed by a prompt the model cannot see, so it is not retried."""
    _dup_dataset(tmp_path)
    calls: list[int] = []

    def fake(req, timeout=None):
        calls.append(1)
        return completion(GOOD_ANSWER)            # identical text for both targets

    monkeypatch.setattr(ra.urllib.request, "urlopen", fake)
    rows, out = _run_regenerate(tmp_path, monkeypatch, capsys, ("--limit", "2", "--retry"))

    assert len(calls) == 2, f"a duplicate rejection was retried: {len(calls)} calls"
    assert any(r["status"].startswith("rejected: duplicate") for r in rows), \
        [r["status"] for r in rows]
    assert not any("; retry:" in r["status"] for r in rows)
    assert "corrective retries" not in out, "the summary implies a retry was spent"


def test_retryable_reasons_are_the_ones_a_prompt_can_fix():
    assert ra.is_retryable("still contains a metric or company name")
    assert ra.is_retryable("too short")
    assert ra.is_retryable("contains banned phrase: 'certainly!'")
    assert not ra.is_retryable("duplicate of another generated answer")


def test_quota_stop_without_retry_records_every_in_flight_row(tmp_path, monkeypatch, capsys):
    """The default path: retries off, and the cap fires on the very first call.

    The remaining targets are already submitted, so they were paid for. Every one must
    appear in the run file — as `error:` for the capped call and `ok` for the rest —
    instead of the drain exiting with rows still unknown.
    """
    import csv as _csv
    import threading

    path = tmp_path / "combined_dataset.csv"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["prompt", "question", "answer", "subject", "difficulty", "data_flag"])
        for i in range(1, 5):
            w.writerow(["p", f"Question {i} about failure isolation?", "shared old answer",
                        "Java", "Easy", ""])

    def fake(req, timeout=None):
        user = json.loads(req.data)["messages"][1]["content"]
        if "Question 1 about" in user:
            raise http_error(429, '{"error":{"message":"Rate limit reached on tokens per day '
                                  '(TPD): Limit 200000, Used 200000."}}')
        threading.Event().wait(0.2)               # the rest are still in flight at the stop
        return completion(GOOD_ANSWER + f" Ref {abs(hash(user)) % 9973}.")

    monkeypatch.setattr(ra.urllib.request, "urlopen", fake)
    rows, out = _run_regenerate(tmp_path, monkeypatch, capsys,
                                ("--workers", "2", "--max-requests", "10"))

    assert len(rows) == 4, f"rows lost after the quota stop: {len(rows)}"
    assert sorted(r["status"].split(":")[0] for r in rows) == ["error", "ok", "ok", "ok"]
    assert "quota hit after" in out
    assert "retried=" not in out, "retry counters should stay quiet when retries are off"


# --------------------------------------------------------------------------- #
# Attempt-count quarantine
# --------------------------------------------------------------------------- #
def test_a_rejection_is_counted_in_the_quarantine_file(tmp_path, monkeypatch, capsys):
    _dup_dataset(tmp_path)
    monkeypatch.setattr(ra.urllib.request, "urlopen",
                        lambda req, timeout=None: completion(BAD_METRIC))
    _run_regenerate(tmp_path, monkeypatch, capsys, ("--limit", "1"))

    state = json.loads((tmp_path / "quarantine.json").read_text(encoding="utf-8"))
    assert list(state.values())[0]["n"] == 1
    assert "metric" in list(state.values())[0]["reason"]


def test_a_question_at_the_threshold_is_not_targeted_again(tmp_path, monkeypatch, capsys):
    _dup_dataset(tmp_path)
    (tmp_path / "quarantine.json").write_text(json.dumps({
        "how do you isolate a failing write?": {"n": 2, "reason": "too short"},
    }), encoding="utf-8")
    calls: list[int] = []

    def fake(req, timeout=None):
        calls.append(1)
        return completion(GOOD_ANSWER)

    monkeypatch.setattr(ra.urllib.request, "urlopen", fake)
    rows, out = _run_regenerate(tmp_path, monkeypatch, capsys, ("--limit", "2"))

    assert len(calls) == 1, f"a quarantined question was retried: {len(calls)} calls"
    assert len(rows) == 1
    assert "quarantine: 1 row(s) skipped" in out


def test_a_success_clears_a_quarantine_entry(tmp_path, monkeypatch, capsys):
    _dup_dataset(tmp_path)
    (tmp_path / "quarantine.json").write_text(json.dumps({
        "how do you isolate a failing write?": {"n": 1, "reason": "too short"},
    }), encoding="utf-8")
    monkeypatch.setattr(ra.urllib.request, "urlopen",
                        lambda req, timeout=None: completion(GOOD_ANSWER))

    _run_regenerate(tmp_path, monkeypatch, capsys, ("--limit", "2"))

    state = json.loads((tmp_path / "quarantine.json").read_text(encoding="utf-8"))
    assert "how do you isolate a failing write?" not in state, state


def test_quarantine_can_be_disabled(tmp_path, monkeypatch, capsys):
    _dup_dataset(tmp_path)
    (tmp_path / "quarantine.json").write_text(json.dumps({
        "how do you isolate a failing write?": {"n": 9, "reason": "too short"},
    }), encoding="utf-8")
    calls: list[int] = []

    def fake(req, timeout=None):
        calls.append(1)
        return completion(GOOD_ANSWER)

    monkeypatch.setattr(ra.urllib.request, "urlopen", fake)
    _run_regenerate(tmp_path, monkeypatch, capsys,
                    ("--limit", "2", "--quarantine-after", "0"))

    assert len(calls) == 2, "quarantine-after 0 must target everything"
