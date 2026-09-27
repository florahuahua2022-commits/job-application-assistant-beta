import hashlib
import re


def experience_identity_value(value: object) -> str:
    return re.sub(r"[^\w]+", " ", str(value or "").casefold(), flags=re.UNICODE).strip()


def experience_candidate_id(organization: object, role_title: object, time_period_text: object) -> str:
    anchors = "|".join(experience_identity_value(value) for value in (organization, role_title, time_period_text))
    return "EX" + hashlib.sha1(anchors.encode("utf-8")).hexdigest()[:12].upper()


def normalise_source_text(value: object) -> str:
    lines = []
    for line in str(value or "").replace("\r\n", "\n").replace("\r", "\n").splitlines():
        line = re.sub(r"^[\s•●▪◦*\-]+", "", line)
        line = re.sub(r"\s+", " ", line.replace("–", "-").replace("—", "-")).strip().casefold()
        if line:
            lines.append(line)
    return "\n".join(lines)


def source_fingerprint(value: object) -> str:
    return hashlib.sha256(normalise_source_text(value).encode("utf-8")).hexdigest()


def source_anchor_present(source_text: object, exclusion: dict) -> bool:
    excerpt = normalise_source_text(exclusion.get("source_excerpt"))
    return bool(excerpt) and excerpt in normalise_source_text(source_text)


def source_anchor_matches(source_text: object, occurrence: int, exclusion: dict) -> bool:
    if exclusion.get("anchor_version") != "source_fingerprint_v1":
        return False
    if int(exclusion.get("source_occurrence") or 1) != occurrence:
        return False
    if str(exclusion.get("source_fingerprint") or "") == source_fingerprint(source_text):
        return True
    return source_anchor_present(source_text, exclusion)
