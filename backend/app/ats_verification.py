from io import BytesIO
import re
from typing import Any

from docx import Document
from pypdf import PdfReader

from .exporter import create_docx, create_pdf, resolve_page_size


def _plain(value: str) -> str:
    value = value.translate(str.maketrans({"–": "-", "—": "-", "‑": "-"}))
    value = re.sub(r"[*_#`]", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _words(value: str) -> list[str]:
    return re.findall(r"[^\W_]+(?:['-][^\W_]+)*", _plain(value).casefold(), re.UNICODE)


def _contains(haystack: str, needle: str) -> bool:
    return _plain(needle).casefold() in _plain(haystack).casefold()


def _check(code: str, state: str, message: str, blocking: bool = False) -> dict:
    return {"code": code, "state": state, "message": message, "blocking": blocking}


def extract_artifact(payload: bytes, format: str) -> tuple[str, dict[str, Any]]:
    if format == "docx":
        document = Document(BytesIO(payload))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        return text, {"page_count": None, "page_size": None}
    reader = PdfReader(BytesIO(payload))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    page_size = None
    if reader.pages:
        box = reader.pages[0].mediabox
        page_size = f"{float(box.width):.0f} x {float(box.height):.0f} pt"
    return text, {"page_count": len(reader.pages), "page_size": page_size}


def _role_marker(role: dict, ckb: list[dict]) -> str | None:
    section = str(role.get("source_section") or "").strip()
    candidates = [section]
    candidates.extend(
        str(item.get("source_section") or "")
        for item in ckb
        if str(item.get("evidence_id")) in set(map(str, role.get("selected_evidence_ids") or []))
    )
    for candidate in candidates:
        parts = [part.strip() for part in candidate.split(">") if part.strip()]
        marker = " > ".join(parts[-2:]) if len(parts) >= 3 else (parts[-1] if parts else "")
        if len(_words(marker)) >= 2 and len(marker) >= 8:
            return marker
    return None


def _marker_position(text: str, marker: str) -> int:
    parts = [_plain(part).casefold() for part in marker.split(">") if part.strip()]
    normalized = _plain(text).casefold()
    direct = normalized.find(_plain(marker).casefold())
    if direct >= 0:
        return direct
    if len(parts) >= 2:
        first, last = map(re.escape, (parts[0], parts[-1]))
        match = re.search(rf"(?:{first}.{{0,100}}{last}|{last}.{{0,100}}{first})", normalized)
        return match.start() if match else -1
    return normalized.find(parts[0]) if parts else -1


def _keywords(content: str, job_model: dict, decision: dict, plan: dict, ckb: list[dict]) -> list[dict]:
    selected = {str(item.get("evidence_id")) for item in plan.get("selected_evidence") or []}
    evidence = {str(item.get("evidence_id")): item for item in ckb if str(item.get("evidence_id")) in selected}
    decisions = {str(item.get("criteria_id")): item for item in decision.get("requirements") or []}
    result = []
    seen = set()
    for criterion in job_model.get("criteria") or []:
        decision_item = decisions.get(str(criterion.get("criteria_id")), {})
        matched = set(map(str, decision_item.get("matched_evidence") or [])) & selected
        labels = [str(value).strip() for value in criterion.get("key_competencies") or [] if str(value).strip()]
        for term in labels:
            key = term.casefold()
            if key in seen:
                continue
            seen.add(key)
            alternates = sorted({
                str(tag).strip() for evidence_id in matched
                for tag in evidence.get(evidence_id, {}).get("competency_tags") or []
                if str(tag).strip() and str(tag).casefold() != key
            })
            if _contains(content, term):
                status, message = "covered", "Grounded target term is visible."
            elif any(_contains(content, alternate) for alternate in alternates):
                status, message = "synonym_only", "A grounded canonical alternate label is visible."
            elif decision_item.get("evidence_classification") in {"confirmed_gap", "unverified_possible"} or not matched:
                status, message = "missing_genuine_gap", "No selected grounded support; do not add this term."
            else:
                status, message = "missing_but_supported", "Selected grounded support exists; target wording is absent."
            result.append({"term": term, "status": status, "message": message, "advisory": True})
    return result


def verify_resume_artifact(
    content: str, format: str, template: str, plan: dict, profile: Any,
    ckb: list[dict], job_model: dict, decision: dict, market: str | None = None,
    page_size: str | None = None,
) -> dict[str, Any]:
    resolved_page_size = resolve_page_size(market, page_size)
    checks = []
    try:
        payload = create_docx(content, "Tailored Resume", template, market, page_size) if format == "docx" else create_pdf(content, "Tailored Resume", template, market, page_size)
    except Exception as error:
        checks.append(_check("artifact_generation", "fail", f"Artifact generation failed: {error}", True))
        return _result(format, template, checks)
    try:
        extracted, metadata = extract_artifact(payload, format)
    except Exception as error:
        checks.append(_check("artifact_extraction", "unavailable", f"Artifact extraction failed: {error}", True))
        return _result(format, template, checks)

    if format == "pdf" and len(_words(extracted)) < 5:
        checks.append(_check("pdf_text_layer", "fail", "The generated PDF has no usable text layer.", True))
    else:
        checks.append(_check("artifact_extraction", "pass", "Artifact text was extracted."))

    name = f"{getattr(profile, 'first_name', '')} {getattr(profile, 'last_name', '')}".strip()
    for code, value, label in (("applicant_name", name, "applicant name"), ("email", getattr(profile, "email", ""), "email")):
        present = bool(value) and _contains(extracted, value)
        checks.append(_check(code, "pass" if present else "fail", f"The {label} is {'present' if present else 'missing'}." , not present))
    phone = re.sub(r"\D", "", str(getattr(profile, "phone", "")))
    phone_present = bool(phone) and phone in re.sub(r"\D", "", extracted)
    checks.append(_check("phone", "pass" if phone_present else "fail", f"The phone number is {'present' if phone_present else 'missing'}." , not phone_present))

    for heading in plan.get("required_sections") or []:
        present = _contains(extracted, str(heading))
        checks.append(_check("required_heading", "pass" if present else "fail", f"Required heading '{heading}' is {'present' if present else 'missing'}." , not present))

    markers = []
    for role in plan.get("roles") or []:
        if not role.get("include_role_header"):
            continue
        marker = _role_marker(role, ckb)
        if not marker:
            checks.append(_check("role_header", "warning", "A reliable literal role marker could not be derived."))
            continue
        position = _marker_position(extracted, marker)
        markers.append((marker, position))
        required = bool(role.get("is_current"))
        state = "pass" if position >= 0 else ("fail" if required else "warning")
        checks.append(_check("current_role_header" if required else "role_header", state, f"Role marker '{marker}' is {'present' if position >= 0 else 'missing'}." , required and position < 0))
    present_positions = [position for _, position in markers if position >= 0]
    if len(present_positions) >= 2:
        ordered = present_positions == sorted(present_positions)
        checks.append(_check("role_chronology", "pass" if ordered else "fail", "Reliable role markers follow Resume Plan order." if ordered else "Reliable role markers do not follow Resume Plan order.", not ordered))
    else:
        checks.append(_check("role_chronology", "warning", "Too few reliable role markers are visible to verify order."))

    expected_words, actual_words = _words(content), _words(extracted)
    actual_counts = {}
    for word in actual_words:
        actual_counts[word] = actual_counts.get(word, 0) + 1
    retained = 0
    for word in expected_words:
        if actual_counts.get(word, 0):
            retained += 1; actual_counts[word] -= 1
    ratio = retained / len(expected_words) if expected_words else 1.0
    checks.append(_check("major_content_loss", "pass" if ratio >= .85 else "fail", f"ATS-visible content retention is {ratio:.1%}.", ratio < .85))
    corrupt = "\ufffd" in extracted or bool(re.search(r"\?{3,}|(?:\u25a1\s*){2,}", extracted))
    checks.append(_check("corrupted_glyphs", "fail" if corrupt else "pass", "Corrupted or replacement glyphs detected." if corrupt else "No corrupted glyph runs detected.", corrupt))
    unicode_terms = {word for word in _words(content) if any(ord(character) > 127 for character in word)}
    lost_unicode = sorted(term for term in unicode_terms if term not in _words(extracted))
    checks.append(_check(
        "unicode_survival", "fail" if lost_unicode else "pass",
        "Non-ASCII applicant content was lost or changed during export." if lost_unicode else "Non-ASCII content remains machine-readable.",
        bool(lost_unicode),
    ))

    maximum_pages = plan.get("maximum_pages")
    if format == "docx":
        checks.append(_check("page_count", "not_applicable", "DOCX pagination is not estimated."))
    elif isinstance(maximum_pages, int) and metadata["page_count"] > maximum_pages:
        checks.append(_check("page_count", "fail", f"PDF has {metadata['page_count']} pages; maximum is {maximum_pages}.", True))
    else:
        checks.append(_check("page_count", "pass", f"PDF has {metadata['page_count']} pages."))

    expected_dimensions = f"{resolved_page_size['dimensions'][0]:.0f} x {resolved_page_size['dimensions'][1]:.0f} pt"
    metadata["expected_page_size"] = resolved_page_size["name"] if resolved_page_size["source"] != "product_fallback" else None
    metadata["page_size_source"] = resolved_page_size["source"]
    if format == "docx":
        checks.append(_check("page_size_match", "not_applicable", f"DOCX uses resolved {resolved_page_size['name']} section dimensions."))
    elif resolved_page_size["source"] == "product_fallback":
        checks.append(_check("page_size_match", "warning", f"PDF uses product fallback {resolved_page_size['name']}; market context is unknown."))
    else:
        matches_size = metadata["page_size"] == expected_dimensions
        checks.append(_check("page_size_match", "pass" if matches_size else "fail", f"PDF page dimensions {'match' if matches_size else 'do not match'} resolved {resolved_page_size['name']}.", not matches_size))

    result = _result(format, template, checks, metadata, extracted, ratio)
    result["keywords"] = _keywords(extracted, job_model, decision, plan, ckb)
    repeated = [item["term"] for item in result["keywords"] if len(re.findall(rf"(?i)\b{re.escape(item['term'])}\b", extracted)) > 8]
    result["checks"].append(_check(
        "keyword_repetition", "warning" if repeated else "pass",
        f"Possible keyword repetition: {', '.join(repeated)}." if repeated else "No excessive grounded keyword repetition detected.",
    ))
    return result


def _result(format: str, template: str, checks: list[dict], metadata: dict | None = None, text: str = "", ratio: float | None = None) -> dict:
    ready = not any(item["blocking"] and item["state"] in {"fail", "unavailable"} for item in checks)
    metadata = metadata or {"page_count": None, "page_size": None}
    return {
        "schema_version": "1.0", "status": "pass" if ready else "fail", "ready": ready,
        "format": format, "template": template,
        "artifact": {"extraction_status": "extracted" if text else "failed", **metadata,
                     "character_count": len(text), "word_count": len(_words(text)), "content_retention_ratio": ratio},
        "checks": checks, "keywords": [],
    }
