import ipaddress
import json
import re
import socket
from html import unescape
from html.parser import HTMLParser
from io import BytesIO
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from docx import Document
from pypdf import PdfReader


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PAGE_BYTES = 3 * 1024 * 1024


def extract_resume_text(filename: str, payload: bytes) -> str:
    if not payload:
        raise ValueError("The selected resume file is empty.")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise ValueError("The resume file is larger than 10 MB.")
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix == "docx":
        document = Document(BytesIO(payload))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    elif suffix == "pdf":
        reader = PdfReader(BytesIO(payload))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    elif suffix in {"txt", "md"}:
        text = payload.decode("utf-8-sig", errors="replace")
    else:
        raise ValueError("Upload a DOCX, PDF or TXT resume file.")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
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

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "meta":
            key = (values.get("property") or values.get("name")).lower()
            if key and values.get("content"):
                self.meta[key] = values["content"]
        if tag.lower() == "script" and "ld+json" in values.get("type", "").lower():
            self._in_json_ld = True
            self._json_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_json_ld:
            self._json_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._in_json_ld:
            self.json_ld.append("".join(self._json_parts))
            self._in_json_ld = False


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
        if value.get("@type") == "JobPosting" or "JobPosting" in value.get("@type", []):
            yield value
        if "@graph" in value:
            yield from _job_postings(value["@graph"])


def parse_job_page(html: str, url: str) -> dict[str, str]:
    parser = _JobPageParser()
    parser.feed(html)
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
            }
    title = parser.meta.get("og:title", "")
    description = parser.meta.get("og:description") or parser.meta.get("description", "")
    title = re.sub(r"\s*[|–-]\s*(SEEK|Indeed|LinkedIn).*", "", title, flags=re.I).strip()
    return {
        "company": "",
        "position_title": _clean_html(title),
        "job_description": _clean_html(description),
        "job_url": url,
        "source": "page_summary",
    }


def _plain_ad_line(value: str) -> str:
    value = re.sub(r"!\[[^]]*\]\([^)]*\)", "", value)
    value = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", value)
    value = value.replace("**", "").replace("__", "").strip(" #*\t")
    return re.sub(r"\s+", " ", value).strip()


def parse_job_ad_text(raw_text: str, previous_companies: list[str] | None = None) -> dict:
    text = raw_text.strip()
    if len(text) < 120:
        raise ValueError("Paste the complete job advertisement, not only the title or link.")
    lines = [_plain_ad_line(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    noise = re.compile(
        r"(?i)^(view all jobs|share or report ad|apply(?: now)?$|save$|posted\b|high application volume|how you match|show all|salary |full time$|part time$|employer questions?)"
    )
    candidates = [line for line in lines[:30] if not noise.search(line) and not line.lower().startswith("http")]
    position_title = candidates[0][:160] if candidates else ""
    company = ""
    company_patterns = (
        r"(?im)^\s*([A-Z][A-Za-z0-9&.'’ -]{2,100}?)\s+(?:is|are)\s+(?:growing|seeking|looking|hiring)\b",
        r"(?im)^\s*why\s+join\s+([A-Z][A-Za-z0-9&.'’ -]{2,100}?)\s*$",
    )
    for pattern in company_patterns:
        match = re.search(pattern, text)
        if match:
            company = _plain_ad_line(match.group(1))
            break
    section_heading = re.compile(
        r"(?i)^(what you(?:'|’)?ll be doing|what we(?:'|’)?re looking for|requirements|key responsibilities|about (?:the )?role|the position|why join\b|how to apply)"
    )
    for line in candidates[1:8] if not company else []:
        lowered = line.lower()
        if lowered.startswith(("about ", "key responsibilities", "the role", "location")):
            break
        if section_heading.search(line):
            continue
        is_location = bool(re.search(r"(?i),\s*(?:perth\s+)?WA(?:\s|\(|$)", line))
        is_category = bool(re.search(r"\([^)]*(?:construction|technology|administration|management)[^)]*\)", line, re.I))
        if len(line) <= 140 and not is_location and not is_category:
            company = line
            break

    criteria = ""
    criteria_match = re.search(
        r"(?is)(?:key selection criteria|selection criteria|essential criteria)\s*[:\n]+(.+?)(?=\n\s*(?:what we offer|how to apply|about us|employer questions?)\b|\Z)",
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
        "job_description": text,
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
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def import_job_url(url: str) -> dict[str, str]:
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
