from difflib import SequenceMatcher
import re
from typing import Any
from .resume_plan import _display_period, _employer_marker, _normalise_identity_text, _role_identity_positions, _role_marker


SHARED_REVIEWER_SCHEMA_VERSION = "1.0"
REVIEW_POLICY_VERSION = "2.0"
FINDING_PRESENTATION_VERSION = "1a.1"
ISSUE_SEVERITY = {
    "unsupported_claim": "critical",
    "unsupported_inference": "major",
    "fabricated_figure": "critical",
    "fabricated_entity": "critical",
    "evidence_mismatch": "critical",
    "internal_inconsistency": "critical",
    "contradiction": "critical",
    "unmatched_evidence_used": "critical",
    "unsupported_motivation": "major",
    "requirement_omission": "major",
    "limit_violation": "critical",
    "jd_wording_repeated": "major",
    "ai_tone": "major",
    "declared_evidence_unused": "major",
    "unknown_reviewer_issue": "major",
    "style_only": "advisory",
    "unsupported_availability_claim": "major",
    "availability_conflict": "major",
    "generation_under_utilized": "major",
    "insufficient_source_detail": "major",
    "concise_but_relevant": "advisory",
    "source_parsing_uncertain": "advisory",
    "resume_repetitive_opening": "advisory",
    "thin_evidence_repeated": "major",
    "missing_role_header": "critical",
    "role_order_mismatch": "critical",
    "omitted_role_expanded": "critical",
}
SHARED_REVIEW_ISSUE_TYPES = set(ISSUE_SEVERITY)
SKILLS_ISSUE_TYPES = {"generation_under_utilized", "declared_evidence_unused", "requirement_omission", "thin_evidence_repeated", "internal_inconsistency"}
SKILLS_SECTION = r"(?:technical skills|key skills|skills list)"


def _has_material_problem(text: str) -> bool:
    # Negated approval is not an approval; these cues also protect mixed findings.
    return bool(re.search(
        r"\b(?:unsupported|fabricated|invented|overstat\w*|upgrad\w*|wrong|incorrect|"
        r"conflict\w*|contradict\w*|mismatch|unverified|not supported|no (?:source|evidence)|"
        r"absent from (?:the )?source|differs? from|not (?:match|confirmed|evidenced|established|sourced)|"
        r"without (?:source|evidence|support)|jd.only|not in (?:the )?source)\b", text, re.I,
    ) or (re.search(r"\b(?:cv|document|letter)\b[^.;\n]*\b(?:says|states|claims|reports)\b", text, re.I)
          and re.search(r"\b(?:source|ckb)\s+(?:says|states|shows|records)\b", text, re.I)))


def deduplicate_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique = {}
    for item in findings:
        key = tuple(re.sub(r"\s+", " ", str(item.get(field) or "")).strip().casefold()
                    for field in ("document_type", "document_id", "type", "description", "location", "evidence", "requirement_id"))
        previous = unique.get(key)
        if previous is None or (item.get("blocks_release") and not previous.get("blocks_release")):
            unique[key] = item
    return list(unique.values())


def _approval_only(description: str) -> bool:
    """Only standalone approvals or simple positive status reports can be hidden."""
    no_issue = r"no (?:issues?(?: with (?:the )?content| found)?|(?:actual )?defect)"
    if re.fullmatch(no_issue + r"[.!\s]*", description, re.I):
        return True
    positive = (
        r"(?:(?:(?:re-check: )?the (?:selected evidence|evidence|statement)|this skills summary) (?:is|are) "
        r"(?:selected|retained|supported|acceptable)(?: and not a material issue)?"
        r"|the (?:work-rights statement|availability statement|closing) matches (?:the )?(?:confirmed )?(?:applicant )?profile(?: and is supported)?"
        r"|the summary opens with a concrete supported setting)"
    )
    return bool(re.fullmatch(positive + r"[.!]?(?:\s*" + no_issue + r"[.!]?)?\s*", description, re.I))


def _skills_consistency_advice(description: str, issue_type: str, ckb: list[dict] | None, plan: dict | None) -> bool:
    skills = SKILLS_SECTION + r"(?:\s+(?:section|list|line))?"
    if not re.search(skills, description, re.I):
        return False
    # ponytail: bounded English clauses; uncertain names remain blocking.
    clauses = re.split(r"[;\n]|\.(?:\s|$)|\band\s+(?:also\s+)?(?=(?:lists|includes|contains|claims|omits)\b)", description, flags=re.I)
    negative = r"\b(?:not found|not listed|not mentioned|does not (?:appear|occur)|absent|unsupported|no (?:source|evidence)|not (?:in|under|within))\b"
    proper_name = r"(?<![\w.+/#-])(?:[A-Z][\w+/#-]*(?:\.[\w+/#-]+)*(?:[ \t]+[A-Z][\w+/#-]*(?:\.[\w+/#-]+)*)*|[a-z]\w*[A-Z0-9+/#.-][\w+/#.-]+)"
    negative_clauses = [clause for clause in clauses if re.search(negative, clause, re.I)]
    for clause in clauses:
        listing = re.search(r"\b(?:lists|includes|contains|claims)\b(?P<items>.*)", clause, re.I)
        if listing and (re.search(negative, clause, re.I) or any(
            re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", other)
            for name in re.findall(proper_name, listing["items"]) for other in negative_clauses
        )):
            return False
    omissions = []
    skills_subject = True
    for clause in clauses:
        match = re.search(r"\b(?:omits?|omitted|is missing(?!\s+from)|lacks?|does not (?:include|list|contain))\s+(?P<items>.+)", clause, re.I)
        operation = match or re.search(r"\b(?:lists|includes|contains|claims)\b", clause, re.I)
        if operation:
            subjects = list(re.finditer(skills + r"|work experience|body|ckb|source", clause[:operation.start()], re.I))
            if subjects:
                skills_subject = bool(re.fullmatch(skills, subjects[-1][0], re.I))
        if match:
            if not skills_subject:
                return False
            omissions.append(match["items"])
    if not omissions:
        match = re.search(r"(?P<items>[\w][\w +./'-]*?)\s+(?:appears|is (?:used|listed|present))\s+in\b[^.;\n]*\b(?:missing|omitted)\s+from\s+(?:the\s+)?" + skills, description, re.I)
        if match:
            omissions.append(match["items"])
    if not omissions:
        return issue_type in {"generation_under_utilized", "declared_evidence_unused"} and bool(re.search(r"\b(?:repeated|duplicat\w*)\b", description, re.I))
    if ckb is None:
        return False
    names = []
    for items in omissions:
        items = re.split(r",?\s+(?:which|that|already|even|although|despite|because|in|under)\b", items, maxsplit=1, flags=re.I)[0]
        found = re.findall(proper_name, items)
        if not found:
            return False
        names.extend(found)
    selected = {str(item.get("evidence_id")) for item in (plan or {}).get("selected_evidence") or []}
    return all(any(str(item.get("evidence_id")) in selected
                   and re.search(r"(?<![\w.+/#-])" + re.escape(name) + r"(?![\w+/#-]|\.[\w+/#-])", str(item.get("source_text") or ""), re.I)
                   for item in ckb) for name in names)


def _verbatim_source_id(issue: dict, content: str, ckb: list[dict], plan: dict) -> str | None:
    """Only complete CV duties in an unambiguous, source-matched role qualify."""
    # ponytail: plain headers and one-line bullets only; uncertain layouts stay blocking.
    if issue.get("type") not in {"evidence_mismatch", "unsupported_inference"} or issue.get("location_kind") != "exact_quote":
        return None
    if issue.get("type") == "unsupported_inference" and not re.search(r"responsibil|overstat|upgrad|stronger", str(issue.get("description") or ""), re.I):
        return None
    quote = str(issue.get("location") or "").strip()
    if not quote or re.search(r"\b(?:contact|phone|email|identity|name|profile|personal|citizen\w*|residen\w*|availability|motivation|values|work.rights)\b|@",
                              str(issue.get("description") or "") + " " + quote, re.I):
        return None
    section = re.search(r"(?ims)^##\s*Work Experience\s*$\n(.*?)(?=^##\s|\Z)", content)
    if not section:
        return None
    lines = [line.strip() for line in section[1].splitlines() if line.strip()]
    quote_positions = [i for i, line in enumerate(lines) if re.sub(r"^[-*+]\s+", "", line) == quote]
    if len(quote_positions) != 1:
        return None
    normalised = [_normalise_identity_text(line) for line in lines]
    roles = plan.get("roles") or []
    positions = [(role, _role_identity_positions(normalised, role)) for role in roles]
    selected = {str(item.get("evidence_id")) for item in plan.get("selected_evidence") or []}
    for role, starts in positions:
        if len(starts) != 1 or not role.get("include_role_header") or not role.get("employer_marker") or not role.get("role_marker"):
            continue
        start = starts[0]
        stop = min((p for _, values in positions for p in values if p > start), default=len(lines))
        if not start < quote_positions[0] < stop:
            continue
        header_end = next((i for i in range(start, stop) if re.match(r"^[-*+]\s+", lines[i])), stop)
        period = _normalise_identity_text(role.get("display_period"))
        header = " ".join(normalised[start:header_end])
        for field in ("employer_marker", "role_marker"):
            header = re.sub(r"(?<!\w)" + re.escape(_normalise_identity_text(role[field])) + r"(?!\w)", " ", header)
        if not 1 <= header_end - start <= 3 or not period or " ".join(header.split()) != period:
            continue
        # Unrecognised headers or prose make the claimed role boundary uncertain.
        if not all(re.match(r"^[-*+]\s+", line) for line in lines[header_end:quote_positions[0] + 1]):
            continue
        for item in ckb:
            eid = str(item.get("evidence_id"))
            source_section = str(item.get("source_section") or "")
            if (eid in selected and eid in role.get("selected_evidence_ids", [])
                    and item.get("evidence_type") == "experience" and source_section == role.get("source_section")
                    and _normalise_identity_text(_employer_marker(source_section)) == _normalise_identity_text(role["employer_marker"])
                    and _normalise_identity_text(_role_marker(source_section)) == _normalise_identity_text(role["role_marker"])
                    and _normalise_identity_text(_display_period([item])) == period
                    and quote in [re.sub(r"^\s*[-*+•]\s+", "", sentence).strip()
                                  for sentence in re.split(r"(?<=[.!?])\s+|\n+", str(item.get("source_text") or ""))]):
                return eid
    return None


def normalise_finding(issue: dict[str, Any], *, content: str = "", ckb: list[dict] | None = None,
                      plan: dict | None = None) -> dict[str, Any] | None:
    reported_type = str(issue.get("type") or "missing_type")
    issue_type = reported_type if reported_type in SHARED_REVIEW_ISSUE_TYPES else "unknown_reviewer_issue"
    severity = ISSUE_SEVERITY[issue_type]
    description = str(issue.get("description") or "").strip()
    recommended_action = str(issue.get("recommended_action") or "Review or regenerate the affected content.").strip()
    action = recommended_action.casefold().strip(" .")
    if not description:
        return None
    no_problem = (
        recommended_action.casefold().startswith(("no change required", "no action required"))
        or action in {"none", "advisory only", "no change", "no action"}
        or re.search(r"\bno (?:actual )?defect\.?$", description.casefold())
        or re.search(r"\bno issues?(?: with (?:the )?content| found)?[.!\s]*$", description, re.I)
    )
    if no_problem and _approval_only(description) and not _has_material_problem(description):
        return None
    category = "factual"
    optional = issue.get("obligation") not in {"must_answer", "must_submit"}
    skills_presentation = (
        issue_type in SKILLS_ISSUE_TYPES
        and _skills_consistency_advice(description, issue_type, ckb, plan)
    )
    if optional and skills_presentation and not _has_material_problem(description):
        severity, category = "advisory", "consistency"
    elif optional and issue_type in {"ai_tone", "style_only", "resume_repetitive_opening", "concise_but_relevant", "jd_wording_repeated"} and not _has_material_problem(description):
        severity, category = "advisory", "writing"
    source_id = _verbatim_source_id(issue, content, ckb or [], plan or {}) if optional else None
    if source_id:
        severity, category = "advisory", "consistency"
        recommended_action = "The quoted duty is verbatim in selected evidence for the same employer, role and period; retain the supported fact and review wording only."
    display_level = "advisory" if severity == "advisory" else "error"
    if issue_type == "unknown_reviewer_issue":
        description = f"Reviewer returned unsupported issue type '{reported_type}': {description}"
        category, display_level = "system", "warning"
    finding = {
        "type": issue_type,
        "severity": severity,
        "description": description,
        "evidence": str(issue.get("evidence") or "").strip(),
        "location": str(issue.get("location") or "").strip(),
        "recommended_action": recommended_action,
        "blocks_release": severity in {"critical", "major"},
    }
    if category != "factual":
        finding.update(category=category, display_level=display_level)
    if source_id:
        finding.update(classification_reason="verbatim_same_role_source", source_evidence_id=source_id)
    elif skills_presentation and severity == "advisory" and re.search(r"\b(?:omits?|omitted|missing|lacks?)\b", description, re.I):
        finding["classification_reason"] = "supported_skills_omission"
    for key in ("requirement_id", "owner", "obligation"):
        if issue.get(key):
            finding[key] = str(issue[key])
    if issue.get("location_kind") in {"exact_quote", "section", "document_wide"}:
        finding["location_kind"] = issue["location_kind"]
    return finding


def findings_block_release(findings: list[dict[str, Any]]) -> bool:
    return any(bool(item.get("blocks_release")) for item in findings)


def _has_blank_serious_finding(issues: list[dict]) -> bool:
    return any(isinstance(issue, dict) and not str(issue.get("description") or "").strip()
               and ISSUE_SEVERITY.get(str(issue.get("type")), "major") in {"critical", "major"}
               for issue in issues)


def normalise_document_review(raw: dict[str, Any], document_id: str, **context) -> dict[str, Any]:
    findings = []
    for issue in raw.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        finding = normalise_finding(issue, **context)
        if finding:
            findings.append(finding)
    findings = deduplicate_findings(findings)
    has_decision = any(isinstance(item, dict) and str(item.get("description") or "").strip() for item in raw.get("issues") or [])
    incomplete = _has_blank_serious_finding(raw.get("issues") or []) or (raw.get("status") == "fail" and not has_decision)
    status = "fail" if findings_block_release(findings) else "pending" if incomplete else "pass"
    return {
        "schema_version": SHARED_REVIEWER_SCHEMA_VERSION,
        "review_policy_version": REVIEW_POLICY_VERSION,
        "status": status,
        "results": [{
            "criteria_id": document_id,
            "status": status,
            "issues": findings,
            "recommendation": str(raw.get("recommendation") or "").strip(),
        }],
    }


def normalise_saved_review(review: dict[str, Any], **context) -> dict[str, Any]:
    """Apply the current policy to saved findings without rewriting stored drafts."""
    result = {**review, "results": []}
    had_findings = False
    blank_serious_finding = False
    context_unavailable = False
    for row in review.get("results") or []:
        findings = []
        blank_serious_finding |= _has_blank_serious_finding(row.get("issues") or [])
        had_findings = had_findings or any(isinstance(item, dict) and str(item.get("description") or "").strip() for item in row.get("issues") or [])
        for issue in row.get("issues") or []:
            if not isinstance(issue, dict):
                continue
            # System hard checks keep their original codes and cannot be downgraded.
            if issue.get("type") not in SHARED_REVIEW_ISSUE_TYPES and row.get("criteria_id") == "delivery_hard_checks":
                finding = {**issue, "display_level": "error", "blocks_release": True}
            else:
                finding = normalise_finding(issue, **context)
                needs_tools_source = issue.get("type") in SKILLS_ISSUE_TYPES and re.search(SKILLS_SECTION, str(issue.get("description") or ""), re.I)
                if finding and issue.get("grounding_status") == "unverified" and not needs_tools_source:
                    finding.update(severity="advisory", display_level="advisory", blocks_release=False, grounding_status="unverified")
                reason = issue.get("classification_reason")
                plan = context.get("plan") or {}
                if finding and reason in {"verbatim_same_role_source", "supported_skills_omission", "source_context_unavailable"} and (
                    not context.get("ckb") or not plan.get("selected_evidence")
                    or (reason == "verbatim_same_role_source" and not plan.get("roles"))
                ):
                    context_unavailable = True
                    finding.update(severity="major", display_level="warning", category="system", blocks_release=True,
                                   description="Source context unavailable — cannot re-verify", evidence="",
                                   classification_reason="source_context_unavailable",
                                   recommended_action="Restore the document's trusted source snapshot or re-run review; current Master Resume data is not substituted.")
            if finding:
                findings.append(finding)
        findings = deduplicate_findings(findings)
        result["results"].append({**row, "issues": findings, "status": "fail" if findings_block_release(findings) else "pass"})
    if review.get("status") in {"pending", "provider_failed"}:
        status = review["status"]
    elif any(row["status"] == "fail" for row in result["results"]):
        status = "fail"
    elif review.get("generation_status") == "needs_ckb_update" and review.get("remaining_issues"):
        status = "fail"
    elif blank_serious_finding:
        status = "pending"
    elif review.get("status") not in {"pass", "fail"} or (review.get("status") == "fail" and not had_findings):
        status = "pending"
    else:
        status = "pass"
    result.update(status=status, review_policy_version=REVIEW_POLICY_VERSION, finding_presentation_version=FINDING_PRESENTATION_VERSION)
    if status == "pass":
        if review.get("status") == "fail":
            result.update(factual_status="pass", quality_status="pass")
        if not result.get("generation_status"):
            result["generation_status"] = "clean"
    elif status == "pending":
        result["message"] = "The review did not return a usable decision. Retry the review; the draft is saved."
    if context_unavailable:
        result.update(factual_status="pending", quality_status="pending")
    return result


def _normalised_text(value: str) -> str:
    return re.sub(r"[^\w]+", " ", value.casefold()).strip()


def _phrase_in_content(phrase: str, content: str) -> bool:
    phrase_text = _normalised_text(phrase)
    content_text = _normalised_text(content)
    if not phrase_text or phrase_text in content_text:
        return bool(phrase_text)
    phrase_words = phrase_text.split()
    content_words = content_text.split()
    if len(phrase_words) < 4 or len(content_words) < len(phrase_words):
        return False
    size = len(phrase_words)
    return any(
        SequenceMatcher(None, phrase_text, " ".join(content_words[index:index + size])).ratio() >= .9
        for index in range(len(content_words) - size + 1)
    )


def _description_document_quotes(description: str) -> list[str]:
    quotes = re.finditer(r"['\u2018\u2019\"]([^'\u2018\u2019\"]{4,})['\u2018\u2019\"]", description)
    document_terms = ("cv", "cover letter", "letter", "document", "response", "summary", "heading", "bullet", "body", "states", "claims", "reads", "lists", "uses", "describes")
    source_terms = ("ckb", "source_text", "source text", "evidence", "job description", " jd ", "resume plan", "applicant profile")
    result = []
    for match in quotes:
        context = f" {description[max(0, match.start() - 120):match.start()].casefold()} "
        document_position = max((context.rfind(term) for term in document_terms), default=-1)
        source_position = max((context.rfind(term) for term in source_terms), default=-1)
        document_claims = list(re.finditer(
            r"\b(?:letter|cv|document|response)\s+(?:uses|includes|contains|states|claims)\b(?:.{0,80}\bevidence\b)?",
            context,
        ))
        if document_claims:
            document_position = max(document_position, document_claims[-1].end())
        if document_position > source_position:
            result.append(match.group(1))
    return result


def reconcile_review_grounding(review: dict[str, Any], content: str | dict[str, str]) -> dict[str, Any]:
    """Prevent unverifiable AI quotations from blocking the reviewed content."""
    for result in review.get("results") or []:
        reviewed_content = content.get(str(result.get("criteria_id") or ""), "") if isinstance(content, dict) else content
        for issue in result.get("issues") or []:
            kind = issue.get("location_kind")
            phrases = [issue.get("location", "")] if kind == "exact_quote" else []
            phrases.extend(_description_document_quotes(str(issue.get("description") or "")))
            phrases = [str(phrase).strip() for phrase in phrases if str(phrase).strip()]
            if not phrases:
                continue
            if all(_phrase_in_content(phrase, reviewed_content) for phrase in phrases):
                issue["grounding_status"] = "verified"
                continue
            issue.update(
                severity="advisory",
                blocks_release=False,
                grounding_status="unverified",
                grounding_reason="quoted_location_not_found",
            )
        result["status"] = "fail" if findings_block_release(result.get("issues") or []) else "pass"
    review["status"] = "fail" if any(item.get("status") == "fail" for item in review.get("results") or []) else "pass"
    review["review_policy_version"] = REVIEW_POLICY_VERSION
    return review
