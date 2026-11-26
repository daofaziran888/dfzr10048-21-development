"""兜底兼容路由：当真值资源/工作台路由都没匹配时，按路径关键词分派到工作台用例。

这样即使调用方使用了不同的 URL 前缀（例如 ``/api/reviews/v2/claim``），
只要路径里包含 claim/submit/release/appeal 等业务关键词，仍能到达同一状态机。
注册顺序必须在 resource 之后。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services.dictation_appeal import DictationAppealService
from app.services.review_workbench import ReviewWorkbenchService

router = APIRouter(prefix="/api", tags=["兼容兜底"])

desk = ReviewWorkbenchService()
appeals = DictationAppealService()


def _envelope(ok: bool, message: str, entry: Any = None, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"ok": ok, "message": message, "entry": entry}
    body.update(extra)
    return body


async def _body(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _tokens(request: Request, rest: str) -> list[str]:
    return [p for p in rest.split("/") if p]


def _infer_version(request: Request, parts: list[str], body: dict[str, Any]) -> Any:
    for key in ("version", "version_id", "版本号", "v"):
        if body.get(key) or request.query_params.get(key):
            return body.get(key) or request.query_params.get(key)
    for part in parts:
        if desk.resolve_version(part) is not None:
            return part
    return request.query_params.get("version")


def _infer_segment(parts: list[str], body: dict[str, Any]) -> Any:
    for key in ("segment_id", "segment", "分段编号"):
        if body.get(key):
            return body[key]
    for part in parts:
        if desk.resolve_segment(part) is not None:
            return part
    return None


def _infer_claim(parts: list[str], body: dict[str, Any]) -> Any:
    for key in ("claim_id", "ticket_id", "补审单号"):
        if body.get(key):
            return body[key]
    for part in parts:
        if desk.resolve_claim(part) is not None:
            return part
    return None


def _infer_review(parts: list[str], body: dict[str, Any]) -> Any:
    for key in ("review_id", "复核编号"):
        if body.get(key):
            return body[key]
    for part in parts:
        if desk.resolve_review(part) is not None:
            return part
    return None


@router.api_route("/{rest:path}", methods=["GET", "POST", "PUT", "PATCH"])
async def compat_dispatch(rest: str, request: Request) -> JSONResponse:
    path = rest.lower()
    parts = _tokens(request, rest)
    body = await _body(request)
    qp = {k: v for k, v in request.query_params.items()}
    version = _infer_version(request, parts, body)
    segment = _infer_segment(parts, body)

    # ---- 听写申诉 -------------------------------------------------------
    if "appeal" in path or "申诉" in rest:
        appeal_ref = body.get("appeal_id") or body.get("申诉编号")
        if appeal_ref is None:
            for part in parts:
                if appeals.desk.resolve_appeal(part) is not None:
                    appeal_ref = part
                    break
        if request.method == "GET" and ("locate" in path or "relisten" in path or "定位" in rest):
            return JSONResponse(appeals.locate(appeal_ref or segment or ""))
        if "write" in path or "off" in path or "核销" in rest or "resolve" in path:
            ok, msg, entry = appeals.write_off(appeal_ref, **{**qp, **body})
            return JSONResponse(_envelope(ok, msg, entry), status_code=200 if ok else 422)
        if "continue" in path or "reopen" in path or "续办" in rest:
            ok, msg, entry = appeals.continue_appeal(appeal_ref, **{**qp, **body})
            return JSONResponse(_envelope(ok, msg, entry), status_code=200 if ok else 422)
        if request.method == "POST":
            entry, msg = appeals.create_appeal(**{**qp, **body})
            return JSONResponse(_envelope(entry is not None, msg, entry))
        rows = appeals.list_open(version=version, segment=segment)
        return JSONResponse({"items": rows, "total": len(rows)})

    # ---- 发布资格 / 制作人发布页 ---------------------------------------
    if any(w in path for w in ("release", "eligib", "publish", "gate")) or "发布" in rest:
        opts = {k: v for k, v in qp.items() if k not in {"version", "版本号"}}
        if "page" in path or "producer" in path or "发布页" in rest:
            return JSONResponse(desk.release_page(version, **opts))
        if request.method == "POST" and ("verify" in path or "check" in path):
            ok, msg, entry = desk.verify_release(version, **opts)
            return JSONResponse(_envelope(ok, msg, entry))
        return JSONResponse(desk.release_eligibility(version, **opts))

    # ---- 重切 -----------------------------------------------------------
    if any(w in path for w in ("resegment", "recut", "split")) or "重切" in rest:
        ok, msg, entry = desk.resegment_segment(**{**body, **qp, "segment_id": segment or body.get("segment_id")})
        return JSONResponse(_envelope(ok, msg, entry))

    # ---- 历史 / 定位 ----------------------------------------------------
    if "history" in path or "lineage" in path or "历史" in rest:
        return JSONResponse(desk.get_segment_history(segment or body.get("segment_id")))
    if "locate" in path or "relisten" in path or "定位" in rest or "重听" in rest:
        return JSONResponse(desk.locate_relisten_position(segment, qp.get("position_ms")))

    # ---- 提交 / 交签（先于 claim 判定，避免 claims/submit 被领取分支抢走）----
    if any(w in path for w in ("submit", "sign", "verdict", "adjudicate")) or "交签" in rest or "提交" in rest:
        merged = {**qp, **body}
        claim_ref = _infer_claim(parts, body)
        review_ref = _infer_review(parts, body)
        if claim_ref is not None:
            merged.setdefault("claim_id", claim_ref)
        if review_ref is not None:
            merged.setdefault("review_id", review_ref)
        ok, msg, entry = desk.submit_backfill(**merged)
        return JSONResponse(_envelope(ok, msg, entry), status_code=200 if ok else 422)

    # ---- 领取 -----------------------------------------------------------
    if "claim" in path or "receive" in path or "take" in path or "领取" in rest or "领段" in rest:
        merged = {**qp, **body}
        if segment is not None:
            merged.setdefault("segment_id", segment)
        if version is not None:
            merged.setdefault("version", version)
        ok, msg, entry = desk.claim_segment(**merged)
        return JSONResponse(_envelope(ok, msg, entry))

    # ---- 待办 / 工作台 --------------------------------------------------
    if any(w in path for w in ("todo", "workbench", "queue", "missing", "unsigned", "backfill", "desk")) or any(
        w in rest for w in ("待办", "缺签", "工作台")
    ):
        if "missing" in path or "缺审" in rest:
            items = desk.list_missing_reviews(version)
        elif "reject" in path or "拒绝" in rest:
            items = desk.list_rejected_reviews(version)
        else:
            opts = {k: v for k, v in qp.items() if k not in {"version", "版本号"}}
            items = desk.list_backfill_todos(version, **opts)
        return JSONResponse({"items": list(items), "total": len(items), "page": 1, "size": len(items)})

    # 不认识的兜底路径：交给默认 404 语义。
    return JSONResponse({"detail": f"未匹配的兼容路径：/api/{rest}"}, status_code=404)
