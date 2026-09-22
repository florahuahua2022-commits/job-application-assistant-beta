import hashlib
import re


def experience_identity_value(value: object) -> str:
    return re.sub(r"[^\w]+", " ", str(value or "").casefold(), flags=re.UNICODE).strip()


def experience_candidate_id(organization: object, role_title: object, time_period_text: object) -> str:
    anchors = "|".join(experience_identity_value(value) for value in (organization, role_title, time_period_text))
    return "EX" + hashlib.sha1(anchors.encode("utf-8")).hexdigest()[:12].upper()
