from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Iterator


class AICallBudgetExceeded(Exception):
    pass


@dataclass
class AIRunTelemetry:
    document_type: str
    limits: dict[str, int]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def before_call(self, category: str) -> float:
        used = sum(item["category"] == category for item in self.calls)
        limit = self.limits.get(category, 0)
        if used >= limit:
            raise AICallBudgetExceeded(
                f"The {self.document_type.replace('_', ' ')} AI call budget was reached "
                f"for {category}. Automatic processing stopped; review the current draft or try again."
            )
        return perf_counter()

    def record_call(
        self,
        *,
        category: str,
        provider: str,
        model: str,
        started: float,
        input_tokens: int,
        output_tokens: int,
        retry_reason: str = "",
        estimated_cost: float = 0.0,
        status: str = "completed",
    ) -> None:
        self.calls.append({
            "sequence": len(self.calls) + 1,
            "category": category,
            "provider": provider,
            "model": model,
            "input_tokens": max(int(input_tokens), 0),
            "output_tokens": max(int(output_tokens), 0),
            "total_tokens": max(int(input_tokens), 0) + max(int(output_tokens), 0),
            "latency_ms": max(round((perf_counter() - started) * 1000), 0),
            "retry_reason": retry_reason,
            "estimated_cost": max(float(estimated_cost), 0.0),
            "status": status,
        })

    def snapshot(self) -> dict[str, Any]:
        return {
            "call_count": len(self.calls),
            "call_limits": dict(self.limits),
            "input_tokens": sum(item["input_tokens"] for item in self.calls),
            "output_tokens": sum(item["output_tokens"] for item in self.calls),
            "total_tokens": sum(item["total_tokens"] for item in self.calls),
            "estimated_cost": round(sum(item["estimated_cost"] for item in self.calls), 8),
            "calls": list(self.calls),
        }


_RUN: ContextVar[AIRunTelemetry | None] = ContextVar("ai_run_telemetry", default=None)
_CATEGORY: ContextVar[str] = ContextVar("ai_call_category", default="generation")
_RETRY_REASON: ContextVar[str] = ContextVar("ai_retry_reason", default="")


def begin_ai_run(document_type: str, criterion_count: int = 0) -> Token:
    generation_limit = max(criterion_count * 2, 1) if document_type == "selection_criteria" else (2 if document_type == "cover_letter" else 1)
    tracker = AIRunTelemetry(
        document_type=document_type,
        limits={
            "matching": 1,
            "generation": generation_limit,
            "review": 3 if document_type == "selection_criteria" else 1,
            "compression": 1,
        },
    )
    return _RUN.set(tracker)


def end_ai_run(token: Token) -> None:
    _RUN.reset(token)


def current_ai_run() -> AIRunTelemetry | None:
    return _RUN.get()


@contextmanager
def ai_call_scope(category: str, retry_reason: str = "") -> Iterator[None]:
    category_token = _CATEGORY.set(category)
    reason_token = _RETRY_REASON.set(retry_reason)
    try:
        yield
    finally:
        _RETRY_REASON.reset(reason_token)
        _CATEGORY.reset(category_token)


def start_ai_call() -> tuple[AIRunTelemetry | None, str, str, float]:
    tracker = current_ai_run()
    category = _CATEGORY.get()
    reason = _RETRY_REASON.get()
    started = tracker.before_call(category) if tracker else perf_counter()
    return tracker, category, reason, started


def record_ai_call(
    call: tuple[AIRunTelemetry | None, str, str, float],
    *,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    estimated_cost: float,
    status: str = "completed",
) -> None:
    tracker, category, reason, started = call
    if tracker:
        tracker.record_call(
            category=category,
            provider=provider,
            model=model,
            started=started,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            retry_reason=reason,
            estimated_cost=estimated_cost,
            status=status,
        )
