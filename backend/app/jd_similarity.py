from difflib import SequenceMatcher
import re
from typing import Any


JD_SIMILARITY_RULES_VERSION = "1.0"
MIN_PHRASE_WORDS = 6
BLOCKING_CONTIGUOUS_WORDS = 10
BLOCKING_CUMULATIVE_WORDS = 12
SENTENCE_MIN_WORDS = 12
SENTENCE_SIMILARITY_THRESHOLD = 0.85

COMMON_TERM_ALLOWLIST = {
    "project officer",
    "project documentation",
    "stakeholder engagement",
    "selection criteria",
    "cover letter",
    "job description",
    "department of transport and major infrastructure",
}


def _tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (value or "").lower())


def _chunks(value: str) -> list[list[str]]:
    return [
        tokens
        for tokens in (_tokens(part) for part in re.split(r"[\r\n.!?;•]+", value or ""))
        if tokens
    ]


def _allowlisted(words: list[str]) -> bool:
    phrase = " ".join(words)
    return phrase in COMMON_TERM_ALLOWLIST


def _maximal_exact_matches(output: list[str], jd: list[str]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for output_start, token in enumerate(output):
        for jd_start, jd_token in enumerate(jd):
            if token != jd_token:
                continue
            if output_start and jd_start and output[output_start - 1] == jd[jd_start - 1]:
                continue
            length = 0
            while (
                output_start + length < len(output)
                and jd_start + length < len(jd)
                and output[output_start + length] == jd[jd_start + length]
            ):
                length += 1
            words = output[output_start:output_start + length]
            if length >= MIN_PHRASE_WORDS and not _allowlisted(words):
                candidates.append({
                    "output_start": output_start,
                    "output_end": output_start + length,
                    "jd_start": jd_start,
                    "jd_end": jd_start + length,
                    "word_count": length,
                    "phrase": " ".join(words),
                })

    selected: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: item["word_count"], reverse=True):
        overlaps = any(
            (
                candidate["output_end"] > item["output_start"]
                and candidate["output_start"] < item["output_end"]
            )
            or (
                candidate["jd_end"] > item["jd_start"]
                and candidate["jd_start"] < item["jd_end"]
            )
            for item in selected
        )
        if not overlaps:
            selected.append(candidate)
    return sorted(selected, key=lambda item: item["output_start"])


def _sentence_match(content: str, job_description: str) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for output_words in _chunks(content):
        if len(output_words) < SENTENCE_MIN_WORDS:
            continue
        for jd_words in _chunks(job_description):
            if len(jd_words) < SENTENCE_MIN_WORDS:
                continue
            ratio = SequenceMatcher(None, output_words, jd_words, autojunk=False).ratio()
            if ratio >= SENTENCE_SIMILARITY_THRESHOLD and (best is None or ratio > best["similarity"]):
                best = {
                    "phrase": " ".join(output_words),
                    "jd_phrase": " ".join(jd_words),
                    "word_count": len(output_words),
                    "similarity": ratio,
                }
    return best


def find_jd_similarity_issues(content: str, job_description: str) -> list[dict[str, Any]]:
    output_tokens = _tokens(content)
    jd_tokens = _tokens(job_description)
    if not output_tokens or not jd_tokens:
        return []

    matches = _maximal_exact_matches(output_tokens, jd_tokens)
    sentence_match = _sentence_match(content, job_description)
    longest = max((item["word_count"] for item in matches), default=0)
    cumulative = sum(item["word_count"] for item in matches)

    blocking_reason = ""
    if longest >= BLOCKING_CONTIGUOUS_WORDS:
        blocking_reason = f"a {longest}-word verbatim JD phrase"
    elif sentence_match:
        blocking_reason = f"a {sentence_match['word_count']}-word sentence with {sentence_match['similarity']:.0%} JD similarity"
    elif len(matches) >= 2 and cumulative >= BLOCKING_CUMULATIVE_WORDS:
        blocking_reason = f"{len(matches)} distinct JD phrases with {cumulative} repeated words in total"

    if blocking_reason:
        phrases = [item["phrase"] for item in matches]
        if sentence_match and sentence_match["phrase"] not in phrases:
            phrases.append(sentence_match["phrase"])
        return [{
            "severity": "error",
            "code": "jd_wording_repeated",
            "message": f"The document repeats {blocking_reason}. Paraphrase the requirements using applicant evidence.",
            "matches": phrases,
            "longest_match_words": longest,
            "cumulative_match_words": cumulative,
        }]

    if matches:
        return [{
            "severity": "warning",
            "code": "jd_wording_repeated",
            "message": f"The document contains a {longest}-word JD phrase. Confirm it is necessary industry terminology or paraphrase it.",
            "matches": [item["phrase"] for item in matches],
            "longest_match_words": longest,
            "cumulative_match_words": cumulative,
        }]
    return []
