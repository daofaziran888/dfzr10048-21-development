"""补审工作台 / 听写申诉 / 发布资格的专用 HTTP 接口。

通用资源路由之外的业务动作集中在这里；为兼容不同调用习惯，
同一能力提供多组路径别名，请求体字段同时接受英文/中文命名。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query

from app.services import service_for
from app.services.dictation_appeal import DictationAppealService
from app.services.review_workbench import ReviewWorkbenchService

router = APIRouter(prefix="/api", tags=["补审工作台"])

desk = ReviewWorkbenchService()
appeals = DictationAppealService()


def _payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


def _result(ok: bool, message: str, entry: Any = None, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"ok": ok, "message": message, "entry": entry}
    body.update(extra)
    if isinstance(entry, dict):
        # 让调用方既能从 entry 取数，也能在顶层直接拿到结论标志。
        for key in ("accepted", "effective", "eligible", "relisten"):
            if key in entry and key not in body:
                body[key] = entry[key]
    return body


# ---------------------------------------------------------------------------
# 补审待办清单 / 工作台
# ---------------------------------------------------------------------------
@router.get("/review-workbench")
@router.get("/review-workbench/todos")
@router.get("/review-workbench/todo")
@router.get("/review/todos")
@router.get("/review/backfill-todos")
@router.get("/confidence_review/workbench")
@router.get("/confidence_review/todos")
@router.get("/confidence_review/todo")
@router.get("/confidence_review/backfill-todos")
def workbench_todos(
    version: str | None = Query(default=None),
    category: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    bench = desk.workbench(version)
    if category or status:
        items = desk.list_backfill_todos(version, category=category or status)
        bench["items"] = list(items)
    return bench


@router.get("/review-workbench/claims")
def list_claims(version: str | None = None, segment: str | None = None) -> dict[str, Any]:
    items = desk.list_claims(version, segment)
    return {"items": items, "total": len(items)}


@router.get("/review-workbench/history")
def segment_history(segment: str) -> dict[str, Any]:
    return desk.get_segment_history(segment)


# ---------------------------------------------------------------------------
# 领取 / 提交 / 重切
# ---------------------------------------------------------------------------
@router.post("/review-workbench/claim")
@router.post("/review-workbench/claims")
@router.post("/review/claim")
@router.post("/review/claims")
@router.post("/confidence_review/claim")
@router.post("/confidence_review/claims")
def claim_segment(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    data = _payload(payload)
    ok, message, entry = desk.claim_segment(**data)
    return _result(ok, message, entry)


@router.post("/review-workbench/submit")
@router.post("/review-workbench/claims/submit")
@router.post("/review/submit")
@router.post("/confidence_review/submit")
@router.post("/confidence_review/submit_review")
def submit_review(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    data = _payload(payload)
    ok, message, entry = desk.submit_backfill(**data)
    return _result(ok, message, entry)


@router.post("/review-workbench/resegment")
@router.post("/speech_segment/resegment")
@router.post("/confidence_review/resegment")
def resegment(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    data = _payload(payload)
    ok, message, entry = desk.resegment_segment(**data)
    return _result(ok, message, entry)


@router.post("/confidence_review/{review_ref}/claim")
def claim_by_review_ref(
    review_ref: str, payload: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    data = _payload(payload)
    data.setdefault("review_id", review_ref)
    review = desk.resolve_review(review_ref)
    if review is not None:
        data.setdefault("segment_id", review.get("关联分段"))
        if review.get("回写版本") not in (None, "-"):
            data.setdefault("version", review.get("回写版本"))
    ok, message, entry = desk.claim_segment(**data)
    return _result(ok, message, entry)


@router.post("/confidence_review/{review_ref}/submit")
def submit_by_review_ref(
    review_ref: str, payload: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    data = _payload(payload)
    data.setdefault("review_id", review_ref)
    ok, message, entry = desk.submit_backfill(**data)
    return _result(ok, message, entry)


# ---------------------------------------------------------------------------
# 发布资格 / 制作人发布页
# ---------------------------------------------------------------------------
@router.get("/review-workbench/eligibility")
@router.get("/release/eligibility")
def release_eligibility(version: str = Query(...)) -> dict[str, Any]:
    return desk.release_eligibility(version)


@router.get("/release")
@router.get("/release/page")
@router.get("/review-workbench/release")
def release_page(version: str = Query(...)) -> dict[str, Any]:
    return desk.release_page(version)


@router.post("/release/verify")
def release_verify(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    data = _payload(payload)
    version = data.get("version") or data.get("版本号")
    ok, message, entry = desk.verify_release(version, **{
        k: v for k, v in data.items() if k not in {"version", "版本号"}
    })
    return _result(ok, message, entry)


# ---------------------------------------------------------------------------
# 听写申诉
# ---------------------------------------------------------------------------
@router.post("/dictation_appeal/create")
@router.post("/dictation-appeals")
def create_appeal(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    data = _payload(payload)
    entry, message = appeals.create_appeal(**data)
    return _result(entry is not None, message, entry)


@router.post("/dictation_appeal/{appeal_ref}/write-off")
@router.post("/dictation_appeal/{appeal_ref}/writeoff")
@router.post("/dictation-appeals/{appeal_ref}/write-off")
def write_off_appeal(
    appeal_ref: str, payload: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    data = _payload(payload)
    ok, message, entry = appeals.write_off(appeal_ref, **data)
    return _result(ok, message, entry)


@router.post("/dictation_appeal/{appeal_ref}/continue")
@router.post("/dictation-appeals/{appeal_ref}/continue")
def continue_appeal(
    appeal_ref: str, payload: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    data = _payload(payload)
    ok, message, entry = appeals.continue_appeal(appeal_ref, **data)
    return _result(ok, message, entry)


@router.get("/dictation_appeal/{appeal_ref}/locate")
@router.get("/dictation-appeals/{appeal_ref}/locate")
def locate_appeal(appeal_ref: str, position_ms: int | None = None) -> dict[str, Any]:
    return appeals.locate(appeal_ref, position_ms)


@router.get("/dictation_appeal/locate/position")
def locate_position(segment: str, position_ms: int | None = None) -> dict[str, Any]:
    return desk.locate_relisten_position(segment, position_ms)


# ---------------------------------------------------------------------------
# 转写版本侧的发布资格别名路径
# ---------------------------------------------------------------------------
@router.get("/transcript_version/release-eligibility")
@router.get("/transcript_version/release")
def version_release_eligibility(version: str = Query(...)) -> dict[str, Any]:
    service = service_for("transcript_version")
    assert service is not None
    return service.release_eligibility(version)  # type: ignore[attr-defined]


@router.get("/transcript_version/{version_ref}/release-eligibility")
@router.get("/transcript_version/{version_ref}/release")
def version_release_eligibility_by_path(version_ref: str) -> dict[str, Any]:
    service = service_for("transcript_version")
    assert service is not None
    return service.release_eligibility(version_ref)  # type: ignore[attr-defined]
