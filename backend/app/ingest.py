import ipaddress
import json
import re
import socket
from html import unescape
from html.parser import HTMLParser
from io import BytesIO
from urllib.parse import unquote, urldefrag, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zipfile import BadZipFile, ZipFile

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader
from .ckb import EMPLOYMENT_PERIOD_PATTERN, stable_evidence_id
from .experience_identity import experience_candidate_id, experience_identity_value, source_anchor_present, source_fingerprint


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PAGE_BYTES = 3 * 1024 * 1024
MAX_OCR_PAGES = 6
MAX_ATTACHMENT_PDF_PAGES = 50
MAX_DOCX_ENTRIES = 1000
MAX_DOCX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024


def _extract_scanned_pdf_text(payload: bytes, document_factory=None, ocr_engine=None, image_adapter=None) -> str:
    """OCR a bounded number of PDF pages without sending the resume to another service."""
    if document_factory is None:
        try:
            import pypdfium2 as pdfium
        except ImportError as error:
            raise ValueError("OCR is not available on this server. Try a DOCX or text-based PDF.") from error
        document_factory = pdfium.PdfDocument
    if ocr_engine is None:
        try:
            from rapidocr import RapidOCR
        except ImportError as error:
            raise ValueError("OCR is not available on this server. Try a DOCX or text-based PDF.") from error
        ocr_engine = RapidOCR()
    if image_adapter is None:
        import numpy as np
        image_adapter = np.asarray

    document = document_factory(payload)
    extracted_pages: list[str] = []
    try:
        for page_index in range(min(len(document), MAX_OCR_PAGES)):
            page = document[page_index]
            bitmap = None
            image = None
            try:
                bitmap = page.render(scale=2.0, grayscale=True)
                image = bitmap.to_pil()
                result = ocr_engine(image_adapter(image))
                texts = tuple(getattr(result, "txts", ()) or ())
                scores = tuple(getattr(result, "scores", ()) or ())
                accepted = [
                    str(value).strip()
                    for index, value in enumerate(texts)
                    if str(value).strip() and (index >= len(scores) or float(scores[index]) >= 0.50)
                ]
                if accepted:
                    extracted_pages.append("\n".join(accepted))
            finally:
                if image is not None and hasattr(image, "close"):
                    image.close()
                if bitmap is not None and hasattr(bitmap, "close"):
                    bitmap.close()
                if hasattr(page, "close"):
                    page.close()
    finally:
        if hasattr(document, "close"):
            document.close()
    return "\n\n".join(extracted_pages).strip()


def _resume_line(value: str) -> str:
    value = re.sub(r"^[\s•●▪◦*-]+", "", value.strip())
    return re.sub(r"\s+", " ", value).strip()


_ROLE_WORDS = r"officer|assistant|administrator|coordinator|supervisor|manager|director|advisor|adviser|consultant|analyst|specialist|lead|engineer|accountant|clerk|secretary|executive|worker|operator|operations"
_ROLE_HINT = re.compile(fr"(?i)\b(?:{_ROLE_WORDS})\b")
_COVERAGE_ROLE_HINT = re.compile(fr"(?i)\b(?:{_ROLE_WORDS}|worker|operator|operations|roles)\b")
_COMPANY_HINT = re.compile(r"(?i)\b(?:pty|ltd|limited|inc|group|services|solutions|warehouse|council|department|university|college|government|authority|agency|company|corporation|corp|project|branch|self-employed)\b")
_ROLE_MODIFIER_HINT = re.compile(r"(?i)\b(?:senior|junior|lead|independent|executive)\b")
_ORGANISATION_PREFIX_HINT = re.compile(r"(?i)^(?:department|university|college|city|shire)\s+of\b")
_LOCATION_HINT = re.compile(
    r"(?i)^(?:(?:Perth|Adelaide|Melbourne|Sydney|Brisbane|Darwin|Hobart|Canberra)|"
    r"(?:[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+)*,\s*)?(?:WA|VIC|NSW|QLD|SA|TAS|NT|ACT)(?:\s+\d{4})?)$"
)
_EMBEDDED_LOCATION_HINT = re.compile(r"(?i)\b(?:Perth|Adelaide|Melbourne|Sydney|Brisbane|Darwin|Hobart|Canberra)\s*$")
_DUTY_START = re.compile(
    r"(?i)^(?:assisted|provided|prepared|supported|managed|coordinated|maintained|processed|reviewed|delivered|developed|"
    r"responsible|led|collated|served|handled|created|implemented|monitored|organised|organized)\b"
)
_NON_ROLE_HEADINGS = {
    "work experience", "professional experience", "employment history", "career history",
    "responsibilities", "key achievements", "achievements", "duties",
}


def _employment_identity(first: str, second: str) -> tuple[str, str]:
    first_role, second_role = bool(_ROLE_HINT.search(first)), bool(_ROLE_HINT.search(second))
    if first_role != second_role:
        return (first, second) if first_role else (second, first)
    first_company, second_company = bool(_COMPANY_HINT.search(first)), bool(_COMPANY_HINT.search(second))
    if first_company != second_company:
        return (second, first) if first_company else (first, second)
    return first, second


def _identity_choice_needs_review(best_score: int, runner_up_score: int) -> bool:
    return best_score - runner_up_score < 2


def _qualified_identity_fields(role: str, role_score: int, organisation: str, organisation_score: int) -> tuple[str, str]:
    return (role if role_score >= 4 else "", organisation if organisation_score >= 4 else "")


def _delimiter_split_is_confident(
    role_score: int, role_organisation_score: int, organisation_score: int, organisation_role_score: int,
) -> bool:
    return (
        role_score >= 4
        and role_score - role_organisation_score >= 2
        and organisation_score >= 4
        and organisation_score - organisation_role_score >= 2
        and role_score + organisation_score >= 9
    )


def _identity_scores(line: str) -> tuple[int, int, int, int]:
    words = re.findall(r"[A-Za-z0-9&'-]+", line)
    is_location = bool(_LOCATION_HINT.fullmatch(line))
    is_duty = bool(_DUTY_START.match(line)) or bool(re.search(r"[.!?]$", line))
    is_period = bool(EMPLOYMENT_PERIOD_PATTERN.search(line))
    role = 4 * bool(_ROLE_HINT.search(line)) + 2 * bool(_ROLE_MODIFIER_HINT.search(line))
    organisation = 5 * bool(_COMPANY_HINT.search(line) and not _ROLE_HINT.search(line)) + 3 * bool(_ORGANISATION_PREFIX_HINT.search(line))
    if 2 <= len(words) <= 10:
        role += 1
    if words and all(word == "&" or word[:1].isupper() or word.casefold() in {"of", "and", "the"} for word in words):
        organisation += 2
    if _COMPANY_HINT.search(line) and not _ROLE_HINT.search(line):
        role -= 4
    if _ROLE_HINT.search(line) and not _COMPANY_HINT.search(line):
        organisation -= 4
    if is_location:
        role -= 4
        organisation -= 5
    elif _EMBEDDED_LOCATION_HINT.search(line):
        role -= 4
        organisation -= 2
    if is_duty or is_period:
        role -= 6
        organisation -= 6
    return role, organisation, 5 if is_location else 0, 6 if is_duty else 0


def _split_inline_identity(value: str) -> tuple[str, str] | None:
    separators = [r"\s*\|\s*", r"\s*,\s*", r"\s+[–—-]\s+"]
    for separator in separators:
        parts = [part.strip() for part in re.split(separator, value, maxsplit=1) if part.strip()]
        if len(parts) != 2:
            continue
        first_role, first_org, _, _ = _identity_scores(parts[0])
        second_role, second_org, _, _ = _identity_scores(parts[1])
        choices = []
        if _delimiter_split_is_confident(first_role, first_org, second_org, second_role):
            choices.append((first_role + second_org, parts[0], parts[1]))
        if _delimiter_split_is_confident(second_role, second_org, first_org, first_role):
            choices.append((second_role + first_org, parts[1], parts[0]))
        if choices:
            _, role, organisation = max(choices)
            return role, organisation
    return None


def _header_identity(work_lines: list[str], date_index: int, inline: str) -> tuple[str, str, set[int], bool]:
    if inline:
        split = _split_inline_identity(inline)
        if split:
            return split[0], split[1], {date_index}, False
        if ":" in inline:
            role_score, organisation_score, _, _ = _identity_scores(inline)
            role, organisation = _qualified_identity_fields(inline, role_score, inline, organisation_score)
            return role, organisation, {date_index}, False

    indexes: list[int] = []
    for index in range(date_index - 1, max(-1, date_index - 4), -1):
        line = work_lines[index]
        if (_identity_scores(line)[3] or EMPLOYMENT_PERIOD_PATTERN.search(line)
                or line.casefold().rstrip(":") in _NON_ROLE_HEADINGS):
            break
        indexes.append(index)
    indexes.reverse()
    for index in range(date_index + 1, min(len(work_lines), date_index + 4)):
        line = work_lines[index]
        if (_identity_scores(line)[3] or EMPLOYMENT_PERIOD_PATTERN.search(line)
                or line.casefold().rstrip(":") in _NON_ROLE_HEADINGS):
            break
        indexes.append(index)
    if inline:
        indexes.append(date_index)

    for index in indexes:
        text = inline if index == date_index else work_lines[index]
        split = _split_inline_identity(text)
        if split:
            return split[0], split[1], {index}, False

    candidates = []
    for index in indexes:
        text = inline if index == date_index else work_lines[index]
        identity_text = re.sub(
            r"(?i),\s*(?:Perth|Adelaide|Melbourne|Sydney|Brisbane|Darwin|Hobart|Canberra)\s*$", "", text,
        )
        role, organisation, location, duty = _identity_scores(identity_text)
        if not location and not duty:
            candidates.append({"text": identity_text, "indexes": {index}, "role": role, "organisation": organisation})

    ordered = sorted(index for index in indexes if index != date_index)
    for start_at in range(len(ordered)):
        for width in (2, 3):
            span = ordered[start_at:start_at + width]
            if len(span) != width or span != list(range(span[0], span[0] + width)):
                continue
            parts = [work_lines[index] for index in span]
            if any(_identity_scores(part)[0] >= 4 or _identity_scores(part)[2] for part in parts):
                continue
            text = " ".join(parts)
            role, organisation, _, duty = _identity_scores(text)
            if not duty:
                candidates.append({"text": text, "indexes": set(span), "role": role,
                                   "organisation": organisation + width - 1})

    # A plain proper-name employer can lack a legal suffix. Immediate adjacency to a
    # strong role is independent structural evidence, but still must lift the employer
    # to the same per-field minimum score before it is accepted.
    for candidate in candidates:
        if candidate["role"] >= 4:
            continue
        if any(
            other["role"] >= 4
            and not candidate["indexes"] & other["indexes"]
            and min(abs(a - b) for a in candidate["indexes"] for b in other["indexes"]) == 1
            for other in candidates
        ):
            candidate["organisation"] += 2

    combinations = []
    roles = [item for item in candidates if item["role"] >= 4]
    organisations = [item for item in candidates if item["organisation"] >= 4]
    for role in roles:
        for organisation in organisations:
            if role["indexes"] & organisation["indexes"]:
                continue
            adjacent = min(abs(a - b) for a in role["indexes"] for b in organisation["indexes"]) == 1
            qualified = _qualified_identity_fields(
                role["text"], role["role"], organisation["text"], organisation["organisation"]
            )
            combinations.append((role["role"] + organisation["organisation"] + 2 * adjacent,
                                 qualified[0], qualified[1], role["indexes"] | organisation["indexes"]))
    for role in roles:
        combinations.append((role["role"], role["text"], "", role["indexes"]))
    for organisation in organisations:
        if organisation["organisation"] >= 4:
            combinations.append((organisation["organisation"], "", organisation["text"], organisation["indexes"]))
    if not combinations:
        return "", "", set(), False
    combinations.sort(key=lambda item: item[0], reverse=True)
    best = combinations[0]
    ambiguous = len(combinations) > 1 and _identity_choice_needs_review(best[0], combinations[1][0])
    return best[1], best[2], best[3], ambiguous


def _experience_review_reasons(source_text: str, item: dict) -> list[str]:
    role = str(item.get("role_title") or "").strip()
    responsibility = str(item.get("responsibility") or "").strip()
    source_block = str(item.get("source_text") or "").strip()
    sentence_shaped = len(role) > 80 and bool(re.search(r"[.;!?]|\b(?:and|including|within|through)\b", role, re.IGNORECASE))
    duty_shaped = bool(_DUTY_START.match(role)) and (not responsibility or sentence_shaped)
    reasons: list[str] = []
    if item.get("identity_ambiguous"):
        reasons.append("ambiguous_employment_identity")
    if not role and item.get("organization"):
        reasons.append("missing_role_title")
    if role and not item.get("organization"):
        reasons.append("missing_organization")
    if duty_shaped or (not responsibility and sentence_shaped):
        reasons.append("duty_shaped_role_title")

    start = source_text.find(source_block) if source_block else -1
    if reasons and start > 0:
        previous = next((_resume_line(line) for line in reversed(source_text[:start].splitlines()) if _resume_line(line)), "")
        if (previous and previous.casefold().rstrip(":") not in _NON_ROLE_HEADINGS
                and len(previous) <= 120 and not _DUTY_START.match(previous)
                and not EMPLOYMENT_PERIOD_PATTERN.search(previous) and not re.search(r"[.;!?]$", previous)):
            reasons.append("excluded_role_header")

    lines = [_resume_line(line) for line in source_block.splitlines() if _resume_line(line)]
    date_index = next((index for index, line in enumerate(lines) if EMPLOYMENT_PERIOD_PATTERN.search(line)), -1)
    possible_headers = [
        line for line in lines[date_index + 1:]
        if len(line) <= 100 and re.fullmatch(r"[^:.;!?]{2,60}:\s*[^.;!?]{2,60}", line)
        and line.split(":", 1)[0].strip().casefold() not in _NON_ROLE_HEADINGS
    ] if date_index >= 0 else []
    if len(possible_headers) >= 2:
        reasons.append("possible_merged_experiences")
    return list(dict.fromkeys(reasons))


def mark_resume_experience_risks(source_text: str, experiences: list[dict]) -> list[dict]:
    """Annotate suspicious parsed identities without changing their source facts."""
    marked = []
    for item in experiences:
        copy = dict(item)
        reasons = _experience_review_reasons(source_text, copy)
        copy.pop("identity_ambiguous", None)
        copy["needs_review"] = bool(reasons)
        copy["review_reasons"] = reasons
        marked.append(copy)
    return marked


def find_uncovered_experience_candidates(
    source_text: str, experiences: list[dict], exclusions: list[dict] | None = None,
) -> list[dict]:
    """Find explicit employer/title/period headers absent from structured experiences."""
    lines = [_resume_line(line) for line in source_text.splitlines()]
    lines = [line for line in lines if line]
    section_start = next((index + 1 for index, line in enumerate(lines) if re.fullmatch(
        r"(?i)(?:professional |relevant )?(?:work |employment )?(?:experience|history)|employment history|career history",
        line,
    )), 0)
    section_end = next((index for index in range(section_start, len(lines)) if re.fullmatch(
        r"(?i)(?:education|qualifications|certifications?|skills|technical skills|referees?|references|volunteering)",
        lines[index],
    )), len(lines))
    work_lines = lines[section_start:section_end]
    saved_identities = {
        (
            experience_identity_value(item.get("organization")),
            experience_identity_value(item.get("role_title")),
            experience_identity_value(item.get("time_period_text")),
        )
        for item in experiences if isinstance(item, dict)
    }
    saved_periods = {identity[2] for identity in saved_identities}
    saved_identity_pairs = {(identity[0], identity[1]) for identity in saved_identities}
    candidates = []
    seen = set()
    excluded_ids = {
        str(item.get("candidate_id") or "") for item in exclusions or []
        if isinstance(item, dict) and item.get("status") == "excluded_by_user"
    }
    excluded_fingerprints = {
        str(item.get("source_fingerprint") or "") for item in exclusions or []
        if isinstance(item, dict) and item.get("status") == "excluded_by_user"
    }
    represented_organisations = [identity[0] for identity in saved_identities if identity[0]] + [
        experience_identity_value(item.get("organization")) for item in exclusions or []
        if isinstance(item, dict) and item.get("status") == "excluded_by_user" and item.get("organization")
    ]
    for index, line in enumerate(work_lines):
        matches = list(EMPLOYMENT_PERIOD_PATTERN.finditer(line))
        if not matches or _DUTY_START.match(line):
            continue
        segment_start = 0
        for match in matches:
            period = match.group(0).strip()
            prefix = _resume_line(line[segment_start:match.start()]).strip(" .,:;|–—-")
            segment_start = match.end()
            if not prefix:
                continue
            organization, role = prefix, ""
            role_on_next_line = False
            if ":" in prefix:
                organization, role = (_resume_line(part) for part in prefix.split(":", 1))
            if not role and len(matches) == 1 and index + 1 < len(work_lines):
                following = work_lines[index + 1]
                if (len(following) <= 80 and _COVERAGE_ROLE_HINT.search(following)
                        and not EMPLOYMENT_PERIOD_PATTERN.search(following)
                        and not _DUTY_START.match(following) and not re.search(r"[.!?]$", following)):
                    role = following
                    role_on_next_line = True
            organization = organization.rstrip(":")
            role = role.strip(" ,:;|–—-")
            if not organization or not role:
                continue
            identity = tuple(experience_identity_value(value) for value in (organization, role, period))
            if identity in saved_identities or identity in seen:
                continue
            seen.add(identity)
            candidate_id = experience_candidate_id(organization, role, period)
            excerpt = "\n".join(work_lines[index:index + (2 if role_on_next_line else 1)])
            fingerprint = source_fingerprint(excerpt)
            candidates.append({
                "candidate_id": candidate_id,
                "status": "excluded_by_user" if candidate_id in excluded_ids or fingerprint in excluded_fingerprints else "unresolved",
                "candidate_type": "parsed_identity",
                "organization": organization,
                "role_title": role,
                "time_period_text": period,
                "source_excerpt": excerpt,
                "source_fingerprint": fingerprint,
                "source_occurrence": 1,
                "anchor_version": "source_fingerprint_v1",
                "review_reasons": ["possible_missing_experience"],
            })
    candidate_periods = {experience_identity_value(item["time_period_text"]) for item in candidates}
    for index, line in enumerate(work_lines):
        match = EMPLOYMENT_PERIOD_PATTERN.search(line)
        if not match:
            continue
        period = match.group(0).strip()
        period_anchor = experience_identity_value(period)
        if period_anchor in saved_periods or period_anchor in candidate_periods:
            continue
        inline = _resume_line(f"{line[:match.start()]} {line[match.end():]}").strip(" |–—-")
        parsed_role, parsed_organisation, _, _ = _header_identity(work_lines, index, inline)
        parsed_pair = (
            experience_identity_value(parsed_organisation),
            experience_identity_value(parsed_role),
        )
        if parsed_pair in saved_identity_pairs:
            continue
        nearby_values = {
            experience_identity_value(value)
            for value in work_lines[max(0, index - 3):index]
        }
        if any(org and role and org in nearby_values and role in nearby_values
               for org, role in saved_identity_pairs):
            continue
        heading = inline or (work_lines[index - 1] if index else "")
        role_score, organisation_score, location_score, duty_score = _identity_scores(heading)
        if not heading or location_score or duty_score or role_score >= 4 or organisation_score >= 4:
            continue
        excerpt_lines = ([heading] if heading != line else []) + [line]
        if index + 1 < len(work_lines) and _identity_scores(work_lines[index + 1])[3]:
            excerpt_lines.append(work_lines[index + 1])
        excerpt = "\n".join(excerpt_lines)
        fingerprint = source_fingerprint(excerpt)
        group_names = [experience_identity_value(part) for part in re.split(r"\s*/\s*", heading) if part.strip()]
        excluded_group = len(group_names) > 1 and all(
            any(name in organisation or organisation in name for organisation in represented_organisations)
            for name in group_names
        )
        candidates.append({
            "candidate_id": "EXSRC" + fingerprint[:12].upper(),
            "status": "excluded_by_user" if fingerprint in excluded_fingerprints or excluded_group else "unresolved",
            "candidate_type": "unparsed_block",
            "organization": "",
            "role_title": "",
            "time_period_text": period,
            "source_excerpt": excerpt,
            "source_fingerprint": fingerprint,
            "source_occurrence": 1,
            "anchor_version": "source_fingerprint_v1",
            "role_score": role_score,
            "organization_score": organisation_score,
            "review_reasons": ["insufficient_identity_signals"],
        })
    return candidates


def reconcile_experience_exclusions(source_text: str, experiences: list[dict], exclusions_json: str) -> str:
    try:
        exclusions = json.loads(exclusions_json or "[]")
    except (TypeError, json.JSONDecodeError):
        exclusions = []
    if not isinstance(exclusions, list):
        exclusions = []
    exclusions = [dict(item) for item in exclusions if isinstance(item, dict)]
    for item in exclusions:
        if (item.get("status") == "excluded_by_user" and item.get("source_excerpt")
                and item.get("anchor_version") != "source_fingerprint_v1"
                and source_anchor_present(source_text, item)):
            item.update(
                anchor_version="source_fingerprint_v1",
                source_fingerprint=source_fingerprint(item["source_excerpt"]),
                source_occurrence=1,
            )
    saved = {
        str(item.get("candidate_id") or ""): item for item in exclusions
        if item.get("status") == "excluded_by_user"
    }
    current = find_uncovered_experience_candidates(source_text, experiences, exclusions)
    reconciled = [
        item for item in exclusions
        if isinstance(item, dict) and item.get("status") == "excluded_by_user"
        and item.get("anchor_version") == "source_fingerprint_v1" and source_anchor_present(source_text, item)
    ]
    fingerprints = {item.get("source_fingerprint") for item in reconciled}
    reconciled.extend(
        saved[item["candidate_id"]] for item in current
        if item["candidate_id"] in saved and saved[item["candidate_id"]].get("source_fingerprint") not in fingerprints
    )
    return json.dumps(reconciled, ensure_ascii=False)


def normalise_resume_experiences(experiences_json: str) -> tuple[str, bool]:
    try:
        experiences = json.loads(experiences_json or "[]")
    except (TypeError, json.JSONDecodeError):
        return "[]", False
    if not isinstance(experiences, list):
        return "[]", False
    changed = False
    for item in experiences:
        if not isinstance(item, dict) or item.get("time_period_text"):
            continue
        period = item.get("time_period") or {}
        if period.get("start") or period.get("end"):
            continue
        for field, beginning_only in (("organization", False), ("responsibility", True)):
            value = str(item.get(field) or "")
            match = EMPLOYMENT_PERIOD_PATTERN.search(value)
            if not match or (beginning_only and value[:match.start()].strip(" |–—-")):
                continue
            item["time_period_text"] = match.group(0).strip()
            item[field] = _resume_line(f"{value[:match.start()]} {value[match.end():]}").strip(" |–—-")
            changed = True
            break
    return json.dumps(experiences, ensure_ascii=False), changed


def extract_resume_experiences(source_text: str) -> list[dict]:
    """Extract conservative, user-reviewable work-history records from resume text."""
    lines = [_resume_line(line) for line in source_text.splitlines()]
    lines = [line for line in lines if line]
    section_start = next(
        (index + 1 for index, line in enumerate(lines) if re.fullmatch(
            r"(?i)(?:professional |relevant )?(?:work |employment )?(?:experience|history)|employment history|career history",
            line,
        )),
        0,
    )
    section_end = next(
        (index for index in range(section_start, len(lines)) if re.fullmatch(
            r"(?i)(?:education|qualifications|certifications?|skills|technical skills|referees?|references|volunteering)",
            lines[index],
        )),
        len(lines),
    )
    work_lines = lines[section_start:section_end]
    period_match = lambda value: EMPLOYMENT_PERIOD_PATTERN.search(value) or re.search(r"(?<!\d)(?:19|20)\d{2}(?!\d)", value)
    date_indexes = [index for index, line in enumerate(work_lines) if period_match(line)
                    and not re.match(r"(?i)^(?:prepared|supported|assisted|managed|coordinated|maintained|processed|reviewed|delivered|developed|provided|responsible|led|collated)\b", line)]
    if not date_indexes:
        # ponytail: only explicit short role/company header pairs are inferred;
        # unusual layouts remain in source_text for user correction, not guessed identities.
        headers = [index for index in range(len(work_lines) - 2)
                   if len(work_lines[index]) <= 80 and _ROLE_HINT.search(work_lines[index])
                   and len(work_lines[index + 1]) <= 100 and _COMPANY_HINT.search(work_lines[index + 1])
                   and not re.search(r"[.!?]$", work_lines[index])]
        records = []
        for position, index in enumerate(headers):
            stop = headers[position + 1] if position + 1 < len(headers) else len(work_lines)
            detail = "\n".join(work_lines[index + 2:stop])
            if detail:
                records.append({"role_title": work_lines[index], "organization": work_lines[index + 1],
                                "responsibility": detail, "source_text": "\n".join(work_lines[index:stop]),
                                "source_section": f"Work Experience > {work_lines[index + 1]} > {work_lines[index]}"})
        return records

    headers: list[tuple[int, int, str, str, str, bool]] = []
    for date_index in date_indexes:
        line = work_lines[date_index]
        match = period_match(line)
        period = match.group(0).strip()
        inline = _resume_line(f"{line[:match.start()]} {line[match.end():]}").strip(" |–—-")
        role_title, organization, used_indexes, ambiguous = _header_identity(work_lines, date_index, inline)
        header_start = min(used_indexes | {date_index})
        used_after_date = [index for index in used_indexes if index > date_index]
        responsibility_start = max(used_after_date, default=date_index) + 1
        headers.append((header_start, responsibility_start, role_title[:160], organization[:160], period, ambiguous))

    experiences: list[dict] = []
    for position, (header_start, responsibility_start, role_title, organization, period, ambiguous) in enumerate(headers):
        next_header_start = headers[position + 1][0] if position + 1 < len(headers) else len(work_lines)
        responsibility_lines = [
            line for line in work_lines[responsibility_start:next_header_start]
            if len(line) > 2 and not re.fullmatch(r"(?i)(?:responsibilities|key achievements|achievements|duties):?", line)
        ]
        responsibility = " ".join(responsibility_lines).strip()
        if not role_title and not organization:
            continue
        source_block = "\n".join(work_lines[max(0, header_start):next_header_start]).strip()
        evidence_id = stable_evidence_id("experience", source_block)
        experiences.append({
            "id": evidence_id,
            "evidence_id": evidence_id,
            "evidence_type": "experience",
            "role_title": role_title,
            "organization": organization,
            "responsibility": responsibility,
            "context": f"Employment dates: {period}",
            "result": "",
            "no_result_data": False,
            "source_section": f"Work Experience > {organization or 'Unknown organisation'} > {role_title}",
            "source_text": source_block,
            "time_period_text": period,
            "competency_tags": [],
            "fact_verification": "explicit",
            "identity_ambiguous": ambiguous,
        })
    occurrences: dict[str, int] = {}
    for item in experiences:
        fingerprint = source_fingerprint(item["source_text"])
        occurrences[fingerprint] = occurrences.get(fingerprint, 0) + 1
        item["anchor_version"] = "source_fingerprint_v1"
        item["source_fingerprint"] = fingerprint
        item["source_occurrence"] = occurrences[fingerprint]
    return mark_resume_experience_risks(source_text, experiences)


def _docx_blocks(document: Document):
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, document).text
        elif child.tag.endswith("}tbl"):
            table = Table(child, document)
            yield from (cell.text for row in table.rows for cell in row.cells)


def extract_document_text(filename: str, payload: bytes, kind: str | None = None) -> tuple[str, str, list[str]]:
    """Extract a bounded PDF, DOCX or text document and report complete/partial status."""
    if not payload:
        raise ValueError("The selected file is empty.")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise ValueError("The file is larger than 10 MB.")
    suffix = kind or (filename.lower().rsplit(".", 1)[-1] if "." in filename else "")
    warnings: list[str] = []
    status = "extracted"
    if suffix == "docx":
        try:
            with ZipFile(BytesIO(payload)) as archive:
                entries = archive.infolist()
                total_size = sum(item.file_size for item in entries)
                if len(entries) > MAX_DOCX_ENTRIES or total_size > MAX_DOCX_UNCOMPRESSED_BYTES:
                    raise ValueError("The DOCX archive expands beyond the safe processing limit.")
                if any(item.file_size > max(1, item.compress_size) * 200 for item in entries):
                    raise ValueError("The DOCX archive has an unsafe compression ratio.")
                if "word/document.xml" not in archive.namelist() or archive.testzip() is not None:
                    raise ValueError("The DOCX file is corrupt or incomplete.")
        except BadZipFile as error:
            raise ValueError("The DOCX file is corrupt or incomplete.") from error
        document = Document(BytesIO(payload))
        text = "\n".join(_docx_blocks(document))
    elif suffix == "pdf":
        reader = PdfReader(BytesIO(payload), strict=False)
        page_count = len(reader.pages)
        text = "\n".join(reader.pages[index].extract_text() or "" for index in range(min(page_count, MAX_ATTACHMENT_PDF_PAGES)))
        if page_count > MAX_ATTACHMENT_PDF_PAGES:
            status = "partial"
            warnings.append(f"Only the first {MAX_ATTACHMENT_PDF_PAGES} PDF pages were processed.")
        if len(re.sub(r"\s+", "", text)) < 40:
            text = _extract_scanned_pdf_text(payload)
            if page_count > MAX_OCR_PAGES:
                status = "partial"
                warnings.append(f"OCR was limited to the first {MAX_OCR_PAGES} PDF pages.")
    elif suffix in {"txt", "md"}:
        text = payload.decode("utf-8-sig", errors="replace")
    else:
        raise ValueError("Only DOCX, PDF and plain-text files are supported.")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, status, list(dict.fromkeys(warnings))


def extract_resume_text(filename: str, payload: bytes) -> str:
    try:
        text, _, _ = extract_document_text(filename, payload)
    except ValueError as error:
        if str(error) == "The selected file is empty.":
            raise ValueError("The selected resume file is empty.") from error
        if str(error) == "The file is larger than 10 MB.":
            raise ValueError("The resume file is larger than 10 MB.") from error
        if "Only DOCX" in str(error):
            raise ValueError("Upload a DOCX, PDF or TXT resume file.") from error
        raise
    if len(text) < 40:
        raise ValueError("Very little text could be read from this file. Try a DOCX or text-based PDF.")
    return text


class _JobPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, str] = {}
        self.json_ld: list[str] = []
        self._in_json_ld = False
        self._json_parts: list[str] = []
        self._hidden_depth = 0
        self._in_title = False
        self.title_parts: list[str] = []
        self.body_parts: list[str] = []
        self.links: list[dict] = []
        self._active_link: dict | None = None
        self._contexts: list[tuple[str, list[str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        values = {key.lower(): value or "" for key, value in attrs}
        if tag == "meta":
            key = (values.get("property") or values.get("name")).lower()
            if key and values.get("content"):
                self.meta[key] = values["content"]
        if tag == "script" and "ld+json" in values.get("type", "").lower():
            self._in_json_ld = True
            self._json_parts = []
        elif tag in {"script", "style", "noscript", "svg"}:
            self._hidden_depth += 1
        if tag == "title":
            self._in_title = True
        if not self._hidden_depth and tag in {"p", "li"}:
            self._contexts.append((tag, []))
        if not self._hidden_depth and tag == "a" and values.get("href"):
            self._active_link = {"href": values["href"], "title": values.get("title", ""), "label_parts": [], "context_parts": self._contexts[-1][1] if self._contexts else []}
        if tag in {"p", "li", "div", "section", "article", "h1", "h2", "h3", "br"}:
            self.body_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_json_ld:
            self._json_parts.append(data)
        elif not self._hidden_depth:
            if self._contexts:
                self._contexts[-1][1].append(data)
            if self._active_link is not None:
                self._active_link["label_parts"].append(data)
            if self._in_title:
                self.title_parts.append(data)
            else:
                self.body_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "script" and self._in_json_ld:
            self.json_ld.append("".join(self._json_parts))
            self._in_json_ld = False
        elif tag in {"script", "style", "noscript", "svg"} and self._hidden_depth:
            self._hidden_depth -= 1
        if tag == "a" and self._active_link is not None:
            self.links.append(self._active_link)
            self._active_link = None
        if tag == "title":
            self._in_title = False
        if tag in {"p", "li"} and self._contexts and self._contexts[-1][0] == tag:
            self._contexts.pop()
        if tag in {"p", "li", "div", "section", "article", "h1", "h2", "h3"}:
            self.body_parts.append("\n")

    def discovered_links(self, page_url: str) -> list[dict]:
        result: list[dict] = []
        seen: set[str] = set()
        for link in self.links:
            resolved, _ = urldefrag(urljoin(page_url, link["href"]))
            parsed = urlparse(resolved)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or resolved in seen:
                continue
            seen.add(resolved)
            result.append({
                "url": resolved,
                "href": link["href"],
                "label": re.sub(r"\s+", " ", "".join(link["label_parts"])).strip(),
                "title": re.sub(r"\s+", " ", link["title"]).strip(),
                "context": re.sub(r"\s+", " ", "".join(link["context_parts"])).strip()[:1000],
                "filename": unquote(parsed.path.rsplit("/", 1)[-1]),
                "discovered_from_url": page_url,
            })
        return result


def _clean_html(value: str) -> str:
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(?:p|li|h[1-6]|div)>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def _job_postings(value):
    if isinstance(value, list):
        for item in value:
            yield from _job_postings(item)
    elif isinstance(value, dict):
        item_type = value.get("@type", [])
        item_types = [item_type] if isinstance(item_type, str) else item_type
        if "JobPosting" in item_types:
            yield value
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from _job_postings(child)


def parse_job_page(html: str, url: str) -> dict:
    parser = _JobPageParser()
    parser.feed(html)
    discovered_sources = parser.discovered_links(url)
    for raw in parser.json_ld:
        try:
            candidates = list(_job_postings(json.loads(raw)))
        except (json.JSONDecodeError, TypeError):
            continue
        if candidates:
            job = candidates[0]
            organisation = job.get("hiringOrganization") or {}
            company = organisation.get("name", "") if isinstance(organisation, dict) else str(organisation)
            return {
                "company": _clean_html(company),
                "position_title": _clean_html(str(job.get("title", ""))),
                "job_description": _clean_html(str(job.get("description", ""))),
                "job_url": url,
                "source": "structured_job_posting",
                "discovered_sources": discovered_sources,
            }

    page_body = _clean_html("".join(parser.body_parts))
    title = parser.meta.get("og:title") or " ".join(parser.title_parts)
    description = parser.meta.get("og:description") or parser.meta.get("description", "")
    title = re.sub(r"\s*[|\-–—]\s*(SEEK|Indeed|LinkedIn|Jora|Glassdoor).*", "", title, flags=re.I).strip()
    if len(page_body) >= 120:
        try:
            parsed_body = parse_job_ad_text(page_body)
        except ValueError:
            parsed_body = {}
        if parsed_body:
            return {
                "company": parsed_body.get("company", ""),
                "position_title": parsed_body.get("position_title") or _clean_html(title),
                "job_description": parsed_body.get("job_description") or _clean_html(description),
                "job_url": url,
                "source": "page_body",
                "discovered_sources": discovered_sources,
            }
    return {
        "company": "",
        "position_title": _clean_html(title),
        "job_description": _clean_html(description),
        "job_url": url,
        "source": "page_summary",
        "discovered_sources": discovered_sources,
    }


def _plain_ad_line(value: str) -> str:
    value = re.sub(r"!\[[^]]*\]\([^)]*\)", "", value)
    value = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", value)
    value = value.replace("**", "").replace("__", "").strip(" #*\t")
    return re.sub(r"\s+", " ", value).strip()


def expand_abbreviated_company(company: str, text: str) -> str:
    """Prefer a readable advertised name when extraction returns a short all-caps fragment."""
    if not re.fullmatch(r"[A-Z]{2,5}", company.strip()):
        return company
    suffix = company.strip().title()
    matches = re.findall(rf"\b(?:[A-Z][a-z]+\s+){{1,3}}{re.escape(suffix)}\b", text)
    excluded_prefixes = {"region", "location", "organisation", "organization", "employer"}
    candidates = [
        _plain_ad_line(match) for match in matches
        if _plain_ad_line(match).split()[0].lower() not in excluded_prefixes
    ]
    return min(candidates, key=lambda value: (len(value.split()), len(value)), default=company)


def parse_job_ad_text(raw_text: str, previous_companies: list[str] | None = None) -> dict:
    text = raw_text.strip()
    if len(text) < 120:
        raise ValueError("Paste the complete job advertisement, not only the title or link.")
    lines = [_plain_ad_line(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    noise = re.compile(
        r"(?i)^(view all jobs|share or report ad|apply(?: now)?$|save$|posted\b|high application volume|how you match|show all|"
        r"sign in|create (?:a )?job alert|job details$|classification$|subclassification$|location$|work type$|"
        r"salary(?:\s|$)|full[ -]?time$|part[ -]?time$|contract/temp$|employer questions?)"
    )
    candidates = [line for line in lines[:30] if not noise.search(line) and not line.lower().startswith("http")]
    labelled_title = re.search(r"(?im)^\s*(?:job title|position title|position|role)\s*:\s*(.+?)\s*$", text)
    labelled_company = re.search(r"(?im)^\s*(?:company|organisation|organization|employer)\s*:\s*(.+?)\s*$", text)
    position_title = _plain_ad_line(labelled_title.group(1))[:160] if labelled_title else ""
    company = _plain_ad_line(labelled_company.group(1))[:160] if labelled_company else ""
    excluded_heading = re.compile(
        r"(?i)^(about\b|what\b|who we\b|we offer\b|job summary\b|job description\b|key responsibilities\b|"
        r"responsibilities\b|requirements\b|selection criteria\b|the position\b|how to apply\b)"
    )
    if not position_title:
        for candidate in candidates:
            if excluded_heading.search(candidate):
                break  # Job identity belongs above the body headings, not inside the duties.
            if ":" in candidate[:30]:
                continue
            if re.search(r"(?i),\s*(?:perth\s+)?WA(?:\s|\(|$)", candidate):
                continue
            position_title = candidate[:160]
            break
    if not labelled_title and (not position_title or len(position_title.split()) > 12 or re.match(r"(?i)^(?:we|our|you|by)\b", position_title)):
        role_match = re.search(
            r"(?i)\b(?:demand for|seeking|looking for|hiring)[ \t]+(?:an?[ \t]+)?(?:experienced[ \t]+)?"
            r"([A-Z][A-Za-z&/' -]{2,70}?)(?=\s+(?:for|to|who|with|across|in)\b|[.,;]|$)",
            text,
        )
        position_title = _plain_ad_line(role_match.group(1)) if role_match else ""
    company_patterns = (
        r"(?im)^\s*([A-Z][A-Za-z0-9&.'’ -]{2,100}?)\s+(?:is|are)\s+(?:growing|seeking|looking|hiring)\b",
        r"(?im)^\s*why\s+join\s+([A-Z][A-Za-z0-9&.'’ -]{2,100}?)\s*$",
        r"(?i)\b(?:with|at)\s+([A-Z][A-Za-z0-9&.'’ -]{2,80}?)(?=\s*[,.;]|\s+(?:you|we|our|for|to|as)\b)",
    )
    for pattern in company_patterns if not company else ():
        for match in re.finditer(pattern, text):
            candidate = _plain_ad_line(match.group(1))
            if not excluded_heading.search(candidate) and not re.match(r"(?i)^(?:we|our|you|your)\b", candidate):
                company = candidate
                break
        if company:
            break
    for line in candidates[1:8] if not company and candidates and not excluded_heading.search(candidates[0]) else []:
        lowered = line.lower()
        if excluded_heading.search(line) or lowered.startswith(("the role", "location", "why join")):
            break
        is_location = bool(re.search(r"(?i),\s*(?:perth\s+)?WA(?:\s|\(|$)", line))
        is_category = bool(re.search(r"\([^)]*(?:construction|technology|administration|management)[^)]*\)", line, re.I))
        if len(line) <= 140 and len(line.split()) <= 10 and not is_location and not is_category and not excluded_heading.search(line):
            company = line
            break

    company = expand_abbreviated_company(company, text)

    criteria = ""
    criteria_match = re.search(
        r"(?is)(?:key selection criteria|selection criteria|essential criteria)\s*[:\n]+(.+?)"
        r"(?=\n\s*(?:who we are|about us|about (?:the|our) (?:company|organisation|organization)|"
        r"our values|company values|our culture|company culture|we offer|what we offer|benefits|employee benefits|"
        r"our benefits|perks|rewards and benefits|why join us|what(?:'|’)?s in it for you|"
        r"what you(?:'|’)?ll get|how to apply|employer questions?)\b|\Z)",
        text,
    )
    if criteria_match:
        criteria = _clean_html(criteria_match.group(1))

    warnings: list[str] = []
    if len(re.findall(r"(?im)^\s*\**about the role\**\s*$", text)) > 1:
        warnings.append("More than one 'About the Role' section was detected. The text may contain multiple job advertisements.")
    if len(re.findall(r"(?im)^\s*\**employer questions?\**\s*$", text)) > 1:
        warnings.append("More than one employer-question section was detected. Check whether two advertisements were pasted together.")
    lowered_text = text.lower()
    for previous_company in previous_companies or []:
        name = previous_company.strip()
        if len(name) >= 4 and name.lower() in lowered_text and name.lower() not in company.lower():
            warnings.append(f"The text mentions a company from an earlier saved job: {name}. Remove any old job content before saving.")
    if not company:
        warnings.append("The organisation could not be identified confidently. Check it before saving.")
    if not position_title:
        warnings.append("The position title could not be identified confidently. Check it before saving.")

    return {
        "company": company,
        "position_title": position_title,
        "job_description": "\n".join(lines),
        "selection_criteria": criteria,
        "warnings": list(dict.fromkeys(warnings)),
    }


def _validate_public_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Enter a valid public http or https job link.")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as error:
        raise ValueError("The job website could not be found.") from error
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("Only public job website links can be imported.")
    return parsed.geturl()


class _SafeRedirectHandler(HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def import_job_url(url: str) -> dict:
    safe_url = _validate_public_url(url)
    request = Request(safe_url, headers={"User-Agent": "Mozilla/5.0 JobApplicationAssistant/1.0"})
    try:
        with build_opener(_SafeRedirectHandler()).open(request, timeout=15) as response:
            final_url = _validate_public_url(response.geturl())
            payload = response.read(MAX_PAGE_BYTES + 1)
            if len(payload) > MAX_PAGE_BYTES:
                raise ValueError("The job page is too large to import safely.")
            charset = response.headers.get_content_charset() or "utf-8"
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("This website did not allow automatic reading. Paste the job details manually instead.") from error
    result = parse_job_page(payload.decode(charset, errors="replace"), final_url)
    if not result["position_title"] and not result["job_description"]:
        raise ValueError("No readable job details were found on this page. Paste the job details manually instead.")
    return result
