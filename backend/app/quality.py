import re


def repair_sentence_spacing(content: str) -> str:
    """Repair sentence boundaries conservatively; never edit address-like tokens."""
    def repair_token(match: re.Match) -> str:
        token = match[0]
        if "@" in token or "://" in token or token.lower().startswith("www."):
            return token
        # Domains, initials and abbreviations are intentionally left unchanged.
        if re.search(r"(?i)\.(?:com|org|net|edu|gov|au|uk|invalid)\b", token):
            return token
        def boundary(part: re.Match) -> str:
            prefix = token[:part.start() + 1]
            if part[1] == "." and (re.search(r"(?i)\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|Mt|vs|etc|Inc|Ltd|approx)\.$", prefix)
                                    or re.search(r"(?:\b[A-Za-z]\.)+$", prefix)):
                return part[0]
            return part[1] + " "
        return re.sub(r"([.!?])(?=[A-Z][a-z]+\b|I\b)", boundary, token)
    return re.sub(r"\S+", repair_token, content)


def _line_number(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def find_writing_quality_issues(content: str) -> list[tuple[str, str]]:
    """Return concise, deterministic grammar and formatting warnings."""
    issues: list[tuple[str, str]] = []

    repeated_word = re.search(r"(?i)\b([A-Za-z]{2,})\s+\1\b", content)
    if repeated_word:
        issues.append(("repeated_word", f"Repeated word '{repeated_word.group(1)}' near line {_line_number(content, repeated_word.start())}."))

    punctuation_spacing = re.search(r"[ \t]+[,.!?;:]", content)
    if punctuation_spacing:
        issues.append(("punctuation_spacing", f"Remove the space before punctuation near line {_line_number(content, punctuation_spacing.start())}."))

    repeated_punctuation = re.search(r"!!+|\?\?+|,,+|;;+|::+", content)
    if repeated_punctuation:
        issues.append(("repeated_punctuation", f"Repeated punctuation appears near line {_line_number(content, repeated_punctuation.start())}."))

    double_space = re.search(r"(?<!\n) {2,}(?!\n)", content)
    if double_space:
        issues.append(("double_spacing", f"Repeated spaces appear near line {_line_number(content, double_space.start())}."))

    for paragraph in re.split(r"\n\s*\n", content):
        plain = re.sub(r"(?m)^\s*(?:#+|[-*]|\d+\.)\s*", "", paragraph).strip()
        if len(plain.split()) > 75 and len(re.findall(r"[.!?](?:\s|$)", plain)) <= 1:
            offset = content.find(paragraph)
            issues.append(("long_sentence", f"A sentence near line {_line_number(content, max(offset, 0))} is very long; split it for readability."))
            break

    if content.count("(") != content.count(")") or content.count("[") != content.count("]"):
        issues.append(("unbalanced_brackets", "Check for an unmatched opening or closing bracket."))

    return issues
