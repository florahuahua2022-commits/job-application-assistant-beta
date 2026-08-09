from typing import Mapping


GENERATION_FEATURES = {
    "tailored_resume": "enable_tailored_resume",
    "cover_letter": "enable_cover_letter",
    "selection_criteria": "enable_selection_criteria",
    "ats_analysis": "enable_ats_analysis",
}


def generation_feature_status(document_type: str, flags: Mapping[str, bool]) -> dict[str, object]:
    setting = GENERATION_FEATURES.get(document_type)
    if not setting:
        return {"supported": False, "enabled": False, "setting": None}
    return {"supported": True, "enabled": bool(flags.get(setting, True)), "setting": setting}

