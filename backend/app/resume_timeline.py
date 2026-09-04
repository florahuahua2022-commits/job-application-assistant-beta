"""Month-precision timeline preservation, independent of writing length."""
from datetime import date, datetime
import re

MAX_UNEXPLAINED_GAP = 12
MAX_PLACEHOLDER_MERGE_GAP = 2


def month_value(value, *, end=False, today=None):
    today = today or date.today()
    value = str(value or "").strip()
    if re.fullmatch(r"(?i)present|current|ongoing|now", value):
        return today.year * 12 + today.month
    for fmt in ("%b %Y", "%B %Y", "%Y-%m", "%Y-%m-%d", "%Y"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.year * 12 + ((12 if end else 1) if fmt == "%Y" else parsed.month)
        except ValueError:
            continue
    return None


def apply_timeline(roles, role_groups, today=None):
    today = today or date.today()
    timeline_end = today.year * 12 + today.month
    intervals = {}
    for role in roles:
        role["relevance_tier"] = "core" if role["evidence_framing"] == "direct" else "adjacent" if role["evidence_framing"] == "adjacent" else "low"
        role["display_mode"] = "full" if role["relevance_tier"] == "core" else "condensed" if role["relevance_tier"] == "adjacent" else "hidden"
        if role["display_mode"] == "hidden":
            role.update(include_role_header=False, selected_evidence_ids=[], max_bullets=0, curation_action="omit")
        periods = [item.get("time_period") or {} for item in role_groups[role["source_section"]]]
        # Conflicting dates cannot establish an authoritative continuous interval.
        bounds = {(month_value(p.get("start"), today=today), month_value(p.get("end"), end=True, today=today)) for p in periods}
        if len(bounds) == 1:
            start, end = bounds.pop()
            if start and end and start <= end and start <= timeline_end:
                intervals[role["source_section"]] = (start, min(end, timeline_end))

    visible = sorted(intervals[r["source_section"]] for r in roles if r["display_mode"] != "hidden" and r["source_section"] in intervals)
    cursor = min((start for start, end in intervals.values()), default=timeline_end)
    gaps = []
    for start, end in [*visible, (timeline_end, timeline_end)]:
        if start - cursor > MAX_UNEXPLAINED_GAP:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    restored = []
    for role in roles:
        bounds = intervals.get(role["source_section"])
        if role["display_mode"] == "hidden" and bounds and any(bounds[0] < end and bounds[1] > start for start, end in gaps):
            role["display_mode"] = "timeline_only"
            restored.append(role)
    restored.sort(key=lambda r: intervals[r["source_section"]])
    groups = []
    for role in restored:
        start, end = intervals[role["source_section"]]
        if not groups or start - groups[-1]["end_month"] > MAX_PLACEHOLDER_MERGE_GAP:
            groups.append({"start_month": start, "end_month": end, "entries": []})
        group = groups[-1]
        group["end_month"] = max(group["end_month"], end)
        # Keep constituent periods even in a merged line; genuine gaps remain visible.
        group["entries"].append(f"{role['role_marker']} — {role['employer_marker']} | {role['display_period']}")
    return {"generation_date": today.isoformat(), "maximum_gap_months": MAX_UNEXPLAINED_GAP,
            "merge_gap_months": MAX_PLACEHOLDER_MERGE_GAP, "groups": list(reversed(groups)),
            "uncertain_sections": [r["source_section"] for r in roles if r["source_section"] not in intervals]}


def timeline_text(plan):
    groups = (plan.get("timeline") or {}).get("groups") or []
    return "\n\n".join("; ".join(group["entries"]) for group in groups)
