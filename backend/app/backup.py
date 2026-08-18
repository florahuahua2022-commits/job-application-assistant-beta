import json
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, delete, select

from .models import ApplicantProfile, GeneratedDocument, JobApplication, JobSource, Referee, Resume


BACKUP_DIR = Path("data/backups")
BACKUP_MODELS = {
    "profiles": ApplicantProfile,
    "referees": Referee,
    "resumes": Resume,
    "applications": JobApplication,
    "sources": JobSource,
    "documents": GeneratedDocument,
}


def _safe_path(filename: str) -> Path:
    if not filename.startswith("job-assistant-") or not filename.endswith(".json"):
        raise ValueError("Invalid backup filename.")
    if Path(filename).name != filename:
        raise ValueError("Invalid backup filename.")
    return BACKUP_DIR / filename


def create_backup(session: Session) -> dict:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(timezone.utc)
    filename = f"job-assistant-{created_at.strftime('%Y%m%d-%H%M%S-%f')}.json"
    payload = {
        "format_version": 1,
        "created_at": created_at.isoformat(),
        "data": {
            name: [record.model_dump(mode="json") for record in session.exec(select(model)).all()]
            for name, model in BACKUP_MODELS.items()
        },
    }
    path = BACKUP_DIR / filename
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return backup_metadata(path)


def backup_metadata(path: Path) -> dict:
    stat = path.stat()
    return {
        "filename": path.name,
        "size": stat.st_size,
        "created_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def list_backups() -> list[dict]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return [backup_metadata(path) for path in sorted(BACKUP_DIR.glob("job-assistant-*.json"), reverse=True)]


def read_backup(filename: str) -> tuple[Path, dict]:
    path = _safe_path(filename)
    if not path.is_file():
        raise FileNotFoundError(filename)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("format_version") != 1 or not isinstance(payload.get("data"), dict):
        raise ValueError("Unsupported or invalid backup file.")
    return path, payload


def restore_backup(session: Session, filename: str) -> dict:
    _, payload = read_backup(filename)
    data = payload["data"]
    for model in (GeneratedDocument, JobSource, Referee, JobApplication, Resume, ApplicantProfile):
        session.exec(delete(model))
    session.commit()
    for name in ("profiles", "resumes", "applications", "sources", "referees", "documents"):
        model = BACKUP_MODELS[name]
        for record in data.get(name, []):
            session.add(model.model_validate(record))
    session.commit()
    return {"restored_from": filename, "counts": {name: len(data.get(name, [])) for name in BACKUP_MODELS}}
