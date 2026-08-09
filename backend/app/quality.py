import re


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
