"""Apply the legacy migration baseline to a brand-new test database."""

from argparse import ArgumentParser
from dataclasses import dataclass
import os
from pathlib import Path
from shutil import copy2
from urllib.parse import unquote, urlsplit

import psycopg


TEST_PROJECT_REF = "edywbrvugcptvifbcjnk"
PRODUCTION_PROJECT_REF = "bbrlvyatkgotcmlswtse"
EXPECTED_REGION = "ap-southeast-1"
BASELINE_FILES = (
    "20260804_online_beta.sql",
    "20260807_commercial_foundation.sql",
    "20260809_applicant_intent_contract.sql",
    "20260809_batch_evidence_matcher.sql",
    "20260809_batch_reviewer.sql",
    "20260809_ckb_foundation.sql",
    "20260809_generation_trace.sql",
    "20260809_per_criterion_generation.sql",
    "20260809_selection_application_logic.sql",
    "20260809_selection_review_confirmations.sql",
    "20260809_shared_job_model.sql",
    "20260818_application_requirements.sql",
    "20260818_job_sources.sql",
    "20260818_job_source_discovery.sql",
    "20260820_application_decision.sql",
    "20260820_outcome_learning.sql",
    "20260820_release_state.sql",
)
BASELINE_PLACEHOLDERS = (
    "20260804_online_beta.sql",
    "20260807_commercial_foundation.sql",
    "20260809_applicant_intent_contract.sql",
    "20260818_application_requirements.sql",
    "20260820_application_decision.sql",
)


@dataclass(frozen=True)
class Target:
    host: str
    port: int
    username: str


def inspect_target(url: str) -> Target:
    parsed = urlsplit(url)
    if not parsed.hostname or not parsed.username:
        raise RuntimeError("Database URL must include a hostname and username")
    return Target(parsed.hostname.lower(), parsed.port or 5432, unquote(parsed.username))


def print_target(target: Target) -> None:
    print(f"Host     : {target.host}")
    print(f"Port     : {target.port}")
    print(f"Username : {target.username}")
    print("Password : ******")


def validate_target(target: Target, *, allow_local_rehearsal: bool) -> None:
    if allow_local_rehearsal and target.host in {"127.0.0.1", "localhost", "::1"}:
        return
    if not target.host.endswith("pooler.supabase.com"):
        raise RuntimeError("Refusing write: host is not a Supabase Session pooler")
    if EXPECTED_REGION not in target.host:
        raise RuntimeError(f"Refusing write: host is not in {EXPECTED_REGION}")
    if target.port != 5432:
        raise RuntimeError("Refusing write: Session pooler port must be 5432")
    if target.username != f"postgres.{TEST_PROJECT_REF}":
        raise RuntimeError("Refusing write: username does not identify the approved test project")
    if PRODUCTION_PROJECT_REF in target.host or PRODUCTION_PROJECT_REF in target.username:
        raise RuntimeError("Refusing write: production project identifier detected")


def apply_baseline(url: str, target: Target, *, allow_local_rehearsal: bool = False) -> None:
    validate_target(target, allow_local_rehearsal=allow_local_rehearsal)
    migrations = Path(__file__).resolve().parents[2] / "supabase" / "migrations"
    with psycopg.connect(url) as connection:
        if allow_local_rehearsal and "test" not in connection.info.dbname.lower():
            raise RuntimeError("Local rehearsal database name must contain 'test'")
        for name in BASELINE_FILES:
            connection.execute((migrations / name).read_text(encoding="utf-8"))
        connection.execute("alter table public.generationusage add column if not exists completed_at timestamptz")
    for name in BASELINE_FILES:
        print(f"APPLIED: {name}")


def prepare_cli_workdir(destination: Path) -> Path:
    migrations = Path(__file__).resolve().parents[2] / "supabase" / "migrations"
    target = destination / "supabase" / "migrations"
    if destination.exists() and any(destination.iterdir()):
        raise RuntimeError(f"Refusing to overwrite non-empty CLI workdir: {destination}")
    target.mkdir(parents=True, exist_ok=True)
    selected = [migrations / name for name in BASELINE_PLACEHOLDERS]
    selected.extend(path for path in sorted(migrations.glob("*.sql")) if path.name.split("_", 1)[0] > "20260820")
    versions = [path.name.split("_", 1)[0] for path in selected]
    if len(versions) != len(set(versions)):
        raise RuntimeError("Refusing to prepare CLI workdir: a post-baseline migration version is duplicated")
    for source in selected:
        copy2(source, target / source.name)
    return destination


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("--allow-local-rehearsal", action="store_true", help="only for a loopback PostgreSQL test database")
    args = parser.parse_args()
    url = os.environ.get("PAYMENT_TEST_DB_URL")
    if not url:
        raise SystemExit("PAYMENT_TEST_DB_URL is not set")
    target = inspect_target(url)
    print_target(target)
    validate_target(target, allow_local_rehearsal=args.allow_local_rehearsal)
    if not args.allow_local_rehearsal and input(f"Type {TEST_PROJECT_REF} to apply the baseline: ").strip() != TEST_PROJECT_REF:
        raise SystemExit("Confirmation did not match; nothing was written")
    apply_baseline(url, target, allow_local_rehearsal=args.allow_local_rehearsal)
    workdir = prepare_cli_workdir(Path(__file__).resolve().parents[2] / "tmp" / "payment-test-cli-workdir")
    print(f"CLI_WORKDIR: {workdir}")


if __name__ == "__main__":
    main()
