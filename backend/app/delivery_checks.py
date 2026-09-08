"""Deterministic checks shared by Final Check and both export formats."""
import re
from datetime import date

from .resume_timeline import month_value


def apply_delivery_review(review, findings):
    if findings:
        review["status"] = "fail"
        review.setdefault("results", []).append({"criteria_id": "delivery_hard_checks", "status": "fail", "issues": [
            {"type": item["code"], "severity": "major", "blocks_release": True, "description": item["message"],
             "location": item["location"], "recommended_action": "Correct this issue before finalising."} for item in findings]})
    return review


def profile_missing_fields(profile):
    return [key for key in ("first_name", "last_name", "email", "phone")
            if not str(getattr(profile, key, "") or "").strip()]


def aggregate_experience(ckb, today=None):
    """Calendar tenure, never FTE or job-relevant tenure. Unknown dates block totals."""
    today = today or date.today()
    intervals, sources, excluded = [], [], []
    uncertain = False
    for item in ckb:
        if item.get("evidence_type") != "experience":
            excluded.append(item.get("evidence_id"))
            continue
        period = item.get("time_period") or {}
        # Count only fully established months; year-only dates cannot establish tenure.
        start = month_value(period.get("start"), today=today)
        end = month_value(period.get("end"), today=today)
        if (not start or not end or start > end or end > today.year * 12 + today.month
                or re.fullmatch(r"\d{4}", str(period.get("start") or ""))
                or re.fullmatch(r"\d{4}", str(period.get("end") or ""))):
            uncertain = True
        else:
            intervals.append((start + 1, end))
        sources.append({key: item.get(key) for key in
                        ("evidence_id", "source_group_id", "source_section", "organization", "role_title", "time_period", "source_text")})
    merged = []
    for start, end in sorted(set(intervals)):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    months = sum(end - start for start, end in merged)
    years = months // 12 if sources and not uncertain else None
    return {"rule": "employment_calendar_complete_months_v1", "as_of": today.isoformat(),
            "scope": "All experience records, including part-time as calendar tenure, excluding projects, volunteering and education; overlaps counted once; no FTE or relevant-experience inference.",
            "sources": sources, "excluded_evidence_ids": excluded, "merged_intervals": merged,
            "complete_months": months, "years": years,
            "allowed_claim": f"{years}+ years of total employment experience" if years else None}


def delivery_issues(content, document_type, profile=None, aggregate=None, ckb=None):
    issues = []
    def add(code, message, location=""):
        issues.append({"code": code, "message": message, "location": location})
    text = content.replace("’", "'")
    if re.search(r"(?i)unable to provide (?:a )?response|insufficient[_ ]evidence|missing_information|needs_user_confirmation|if you could provide additional details|cannot (?:answer|respond) (?:to )?(?:this|the) criteri", text):
        add("invalid_response_placeholder", "Replace the unanswered response with supported evidence or add the missing source information.")
    closing = re.search(r"(?im)^\s*(?:yours (?:sincerely|faithfully)|kind regards|sincerely|regards)[,\s]*$", text)
    if closing:
        tail = [line.strip() for line in text[closing.end():].splitlines() if line.strip()]
        if any(re.match(r"[-*•#]", line) or len(line.split()) > 8 for line in tail) or len(tail) > 4:
            add("body_after_signature", "Move all body paragraphs before the closing and applicant signature.")
    # ponytail: constrained English claim grammar; semantic review covers other paraphrases.
    claims = re.finditer(r"(?i)\b(?:over |more than |at least |approximately )?(?:\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty)\+?[- ]years?['’]?(?:\s+of)?\s+(?:[a-z-]+\s+){0,5}(?:experience|career|employment)\b", text)
    allowed = (aggregate or {}).get("allowed_claim")
    for claim in claims:
        if not allowed or claim[0].casefold() != allowed.casefold():
            add("aggregate_claim_unverified", "Use only the calculated total employment claim; relevant or FTE years need a separately confirmed scope.", claim[0])
    if profile:
        rights = getattr(profile, "work_rights", "not_specified")
        for phrase, expected in ((r"\b(?:Australian )?permanent resident\b", "permanent_resident"), (r"\bAustralian citizen\b", "citizen")):
            if re.search(phrase, text, re.I) and rights not in {expected, "australian_" + expected}:
                add("work_rights_conflict", "The work-rights declaration differs from the confirmed Profile.")
        name = " ".join(filter(None, [profile.first_name, profile.last_name]))
        if not re.search(r"(?i)(?<!\w)" + re.escape(name) + r"(?!\w)", text):
            add("canonical_name_missing", "Use the confirmed applicant name in this document.")
        if profile.email.casefold() not in text.casefold():
            add("canonical_email_missing", "Use the confirmed applicant email in this document.")
        digits = re.sub(r"\D", "", profile.phone)
        phones = {digits, "61" + digits[1:] if digits.startswith("0") else digits,
                  "0" + digits[2:] if digits.startswith("61") else digits}
        if not digits or not any(p in re.sub(r"\D", "", text) for p in phones if p):
            add("canonical_phone_missing", "Use the confirmed applicant phone in this document.")
        # Inspect identity positions, not names of referees or hiring managers in the body.
        identity_lines = [text.splitlines()[0].strip(" #*")] if text.strip() else []
        if closing:
            identity_lines += [line.strip(" #*") for line in text[closing.end():].splitlines() if line.strip()][:1]
        for line in identity_lines:
            if re.fullmatch(r"[A-Za-z'-]+(?: [A-Za-z'-]+){1,3}", line) and line.endswith(profile.last_name) and line != name:
                add("canonical_name_conflict", "The heading or signature uses a different applicant name.", line)
    if ckb:
        attribution_text = text
        if document_type == "tailored_resume":
            work = re.search(r"(?ims)^\s*(?:##\s*)?(?:work experience|employment history|professional experience)\s*$\n(.*?)(?=^\s*(?:##\s*)?(?:education(?: & qualifications)?|qualifications|technical skills|additional information|certifications(?: & training)?)\s*$|\Z)", text)
            attribution_text = work[1] if work else ""
        employers = {}
        for item in ckb:
            parts = str(item.get("source_section") or "").split(">")
            employer = str(item.get("organization") or (parts[-2].strip() if len(parts) >= 3 else ""))
            if employer:
                employers.setdefault(employer, []).append(str(item.get("source_text") or ""))
        # ponytail: literal high-risk finance terms catch known cross-role leaks;
        # semantic review remains required for paraphrases and other responsibilities.
        terms = (r"\bjournals?\b", r"\breconciliation\b", r"\bDayforce\b")
        all_source = "\n".join(s for group in employers.values() for s in group)
        occurrences = sorted((m.start(), employer) for employer in employers
                             for m in re.finditer(re.escape(employer), attribution_text, re.I))
        for index, (start, employer) in enumerate(occurrences):
            end = occurrences[index + 1][0] if index + 1 < len(occurrences) else len(attribution_text)
            if document_type == "tailored_resume":
                block = attribution_text[start:end]
            else:
                begin = attribution_text.rfind("\n\n", 0, start) + 2
                finish = attribution_text.find("\n\n", start)
                block = attribution_text[max(begin, 0):finish if finish >= 0 else len(attribution_text)]
                if sum(bool(re.search(re.escape(name), block, re.I)) for name in employers) != 1:
                    continue
            own_source = "\n".join(employers[employer])
            for term in terms:
                if re.search(term, block, re.I) and re.search(term, all_source, re.I) and not re.search(term, own_source, re.I):
                    add("cross_experience_attribution", f"A duty or system is assigned to {employer} but its source belongs to another experience.", re.search(term, block, re.I)[0])
    return issues
