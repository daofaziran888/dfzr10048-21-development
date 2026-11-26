"""统一资源接口：查询、导出、登记和动作按模块 key 分派到领域服务。"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, EntryPayload, PageResult
from app.services import service_for
from app.store import store

router = APIRouter(prefix="/api", tags=["业务资源"])


def require_service(module: str):
    service = service_for(module)
    if service is None:
        raise HTTPException(status_code=404, detail=f"未知业务模块：{module}")
    return service


def _find_row_by_code(module: str, ref: str) -> dict[str, Any] | None:
    """按数字 id 或任意“…编号”业务编码定位行。"""
    for row in store.rows(module):
        if str(row.get("id")) == ref:
            return row
        for key, value in row.items():
            if key.endswith("编号") and str(value) == ref:
                return row
    return None


@router.get("/{module}", response_model=PageResult[dict])
def list_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, total = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{module}/export")
def export_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, _ = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=1, size=1_000_000)
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *service.LIST_FIELDS, "status"])
    for item in items:
        values = [item.get("id"), *(item.get(field, "") for field in service.LIST_FIELDS),
                  item.get("status", "")]
        writer.writerow([
            "'" + str(value) if str(value or "").startswith(("=", "+", "-", "@")) else value
            for value in values
        ])
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{module}.csv"'},
    )


@router.get("/{module}/{entry_ref}", response_model=dict)
async def get_entry(module: str, entry_ref: str, request: Request) -> dict[str, Any]:
    service = service_for(module)
    # 数字 id：保持原有查询契约。
    if entry_ref.isdigit():
        require_service(module)
        entry = service.get_entry(int(entry_ref))  # type: ignore[union-attr]
        if entry is None:
            raise HTTPException(status_code=404, detail=f"{service.ENTITY} {entry_ref} 不存在或已归档")  # type: ignore[union-attr]
        return entry
    # 业务编码（如 CFRV-0001）：直接返回对应行。
    row = _find_row_by_code(module, entry_ref)
    if row is not None:
        return row
    # 非 id 的子资源语义路径（如 release_page/todos）交给兼容分派器；
    # 即使模块前缀不是已注册模块，也允许按关键词命中工作台用例。
    from app.routers.compat import compat_dispatch

    response = await compat_dispatch(f"{module}/{entry_ref}", request)
    if response.status_code != 404:
        import json

        return json.loads(response.body)
    if service is None:
        raise HTTPException(status_code=404, detail=f"未知业务模块：{module}")
    raise HTTPException(status_code=404, detail=f"{service.ENTITY} {entry_ref} 不存在或已归档")


@router.post("/{module}", response_model=ActionResult)
def create_entry(module: str, payload: EntryPayload) -> ActionResult:
    service = require_service(module)
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message=f"{service.ENTITY}已登记", entry=entry)


@router.post("/{module}/{entry_ref}/actions", response_model=ActionResult)
def run_action(module: str, entry_ref: str, payload: ActionPayload) -> ActionResult:
    service = require_service(module)
    entry_id: int | None = int(entry_ref) if entry_ref.isdigit() else None
    if entry_id is None:
        row = _find_row_by_code(module, entry_ref)
        if row is None:
            return ActionResult(ok=False, message=f"{service.ENTITY} {entry_ref} 不存在或已归档")
        entry_id = int(row.get("id", 0))

    import inspect

    params = inspect.signature(service.run_action).parameters
    accepts_payload = "payload" in params or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    )
    if accepts_payload:
        entry, message = service.run_action(entry_id, payload.action.strip(), payload=payload)
    else:
        entry, message = service.run_action(entry_id, payload.action.strip())
    return ActionResult(ok=entry is not None, message=message, entry=entry)
