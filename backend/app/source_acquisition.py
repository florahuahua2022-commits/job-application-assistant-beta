import hashlib
import json
import re
from datetime import datetime
from urllib.error import HTTPError
from urllib.request import Request, build_opener

from .ingest import MAX_UPLOAD_BYTES, _SafeRedirectHandler, _validate_public_url, extract_document_text


MAX_RUN_BYTES = 20 * 1024 * 1024
MAX_DOWNLOADS = 3
CHUNK_BYTES = 64 * 1024
ELIGIBLE_TYPES = {"job_description_attachment", "application_instruction_attachment"}


def _fetch(url: str, limit: int, opener_factory=build_opener):
    safe_url = _validate_public_url(url)
    request = Request(safe_url, headers={"User-Agent": "Mozilla/5.0 JobApplicationAssistant/1.0"})
    try:
        response = opener_factory(_SafeRedirectHandler()).open(request, timeout=15)
    except HTTPError as error:
        if error.code in {401, 403}:
            raise ValueError("requires_auth: The attachment requires authentication.") from error
        if error.code in {404, 410}:
            raise ValueError("unavailable: The attachment is unavailable.") from error
        raise ValueError("failed: The attachment could not be fetched.") from error
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("failed: The attachment could not be fetched safely.") from error
    with response:
        _validate_public_url(response.geturl())
        declared_size = response.headers.get("Content-Length")
        if declared_size and int(declared_size) > limit:
            raise ValueError("failed: The attachment exceeds the remaining safe download limit.")
        payload = bytearray()
        while chunk := response.read(min(CHUNK_BYTES, limit + 1 - len(payload))):
            payload.extend(chunk)
            if len(payload) > limit:
                raise ValueError("failed: The attachment exceeds the remaining safe download limit.")
        return bytes(payload), response.headers, response.geturl()


def validate_document_kind(payload: bytes, declared_type: str, filename: str) -> str:
    declared = declared_type.split(";", 1)[0].strip().lower()
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if payload.startswith(b"%PDF-"):
        actual = "pdf"
    elif payload.startswith(b"PK\x03\x04"):
        actual = "docx"
    elif b"\x00" not in payload[:4096] and (declared.startswith("text/plain") or suffix in {"txt", "md"}):
        actual = "txt"
    else:
        actual = ""
    expected = {
        "application/pdf": "pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "text/plain": "txt",
    }.get(declared)
    if expected and actual != expected:
        raise ValueError("unsupported: The declared content type does not match the attachment signature.")
    if not actual:
        raise ValueError("unsupported: The attachment type is unsupported or could not be validated.")
    return actual


def process_uploaded_document(filename: str, declared_type: str, payload: bytes) -> dict:
    kind = validate_document_kind(payload, declared_type, filename)
    text, status, warnings = extract_document_text(filename, payload, kind)
    if not text:
        raise ValueError("No readable text could be extracted from the uploaded document.")
    return {
        "filename": filename,
        "content_type": {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "txt": "text/plain"}[kind],
        "content_sha256": hashlib.sha256(payload).hexdigest(),
        "extracted_text": text,
        "extraction_status": status,
        "warnings_json": json.dumps(warnings),
    }


def acquire_sources(sources: list, opener_factory=build_opener) -> None:
    eligible = [source for source in sources if source.source_url and source.acquisition_status == "discovered"
                and source.classification_confidence == "high" and source.source_type in ELIGIBLE_TYPES][:MAX_DOWNLOADS]
    remaining = MAX_RUN_BYTES
    seen_hashes = {source.content_sha256: source for source in sources if source.content_sha256 and source.extracted_text}
    for source in eligible:
        try:
            payload, headers, final_url = _fetch(source.source_url, min(MAX_UPLOAD_BYTES, remaining), opener_factory)
            remaining -= len(payload)
            declared_type = headers.get("Content-Type", "")
            filename = (headers.get_filename() if hasattr(headers, "get_filename") else None) or source.filename or final_url.rsplit("/", 1)[-1].split("?", 1)[0]
            if (declared_type.lower().startswith("text/html") or payload.lstrip()[:20].lower().startswith((b"<!doctype html", b"<html"))):
                text = payload[:10000].decode("utf-8", errors="ignore").lower()
                if re.search(r"\b(?:sign in|log in|login|authentication|required account)\b", text):
                    raise ValueError("requires_auth: The attachment URL returned a login page.")
                raise ValueError("unsupported: The attachment URL returned HTML instead of a document.")
            kind = validate_document_kind(payload, declared_type, filename)
            content_type = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "txt": "text/plain"}[kind]
            digest = hashlib.sha256(payload).hexdigest()
            source.acquisition_status = "fetched"
            source.content_type = content_type
            source.filename = filename or source.filename
            source.content_sha256 = digest
            if digest in seen_hashes:
                source.extraction_status = "not_applicable"
                source.extracted_text = ""
                source.warnings_json = json.dumps([f"Duplicate content of source {seen_hashes[digest].source_id}; extraction was not duplicated."])
                continue
            text, status, warnings = extract_document_text(filename, payload, kind)
            if not text:
                raise ValueError("failed: No readable text could be extracted from the attachment.")
            source.extraction_status = status
            source.extracted_text = text
            source.warnings_json = json.dumps(warnings)
            seen_hashes[digest] = source
        except ValueError as error:
            prefix, separator, message = str(error).partition(": ")
            if separator and prefix in {"failed", "unavailable", "requires_auth", "unsupported"}:
                source.acquisition_status = prefix
                source.extraction_status = "not_attempted"
                warning = message
            else:
                source.acquisition_status = "fetched" if source.content_sha256 else "failed"
                source.extraction_status = "failed" if source.acquisition_status == "fetched" else "not_attempted"
                warning = str(error)
            source.extracted_text = ""
            source.warnings_json = json.dumps([warning])
        except Exception as error:
            source.acquisition_status = "fetched" if source.content_sha256 else "failed"
            source.extraction_status = "failed" if source.acquisition_status == "fetched" else "not_attempted"
            source.extracted_text = ""
            source.warnings_json = json.dumps([f"The attachment could not be processed: {error}"])
        finally:
            source.updated_at = datetime.utcnow()
