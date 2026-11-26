"""复核签名发布闸门（管线层）。

提供两处能力：
- :meth:`ReviewReleaseGate.evaluate_coverage` 纯函数：按“当前覆盖分段 +
  生效结论”计算发布资格，便于管线单测直接构造数据；
- :meth:`ReviewReleaseGate.evaluate_version` 走内存仓库的完整工作台规则。

规则与 ``app.services.review_workbench`` 保持一致：
缺审与拒绝分开、版本隔离、同段至多一份生效结论、旧签/旧申诉不补新覆盖。
"""
from __future__ import annotations

from typing import Any, Iterable


class ReviewReleaseGate:
    def __init__(self, required_only: bool = True) -> None:
        self.required_only = required_only

    @staticmethod
    def _result_kind(conclusion: Any) -> str | None:
        text = str(conclusion or "").strip()
        if any(w in text for w in ("不通过", "驳回", "拒绝", "reject", "fail")):
            return "reject"
        if any(w in text for w in ("通过", "pass", "approved")):
            return "pass"
        return None

    def evaluate_coverage(
        self,
        segments: Iterable[dict[str, Any]],
        conclusions: Iterable[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """纯函数核验。

        segments: 每项至少含 segment_id；可选 required/current。
        conclusions: 每项含 segment_id、conclusion(通过/拒绝)、effective。
        没有显式 effective 标记时，每个分段取最后一笔结论。
        """
        segments = list(segments)
        conclusions = list(conclusions or [])
        effective: dict[str, dict[str, Any]] = {}
        for row in conclusions:
            sid = str(row.get("segment_id") or row.get("关联分段"))
            if row.get("effective") is False:
                continue
            effective[sid] = row  # 后写覆盖，天然只保留一份

        missing: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        passed: list[dict[str, Any]] = []
        for seg in segments:
            if seg.get("current") is False:
                continue
            if self.required_only and not seg.get("required", True):
                continue
            sid = str(seg.get("segment_id") or seg.get("分段编号"))
            conclusion = effective.get(sid)
            kind = self._result_kind(
                (conclusion or {}).get("conclusion")
                or (conclusion or {}).get("复核结论")
            )
            if kind == "pass":
                passed.append(seg)
            elif kind == "reject":
                rejected.append(seg)
            else:
                missing.append(seg)
        eligible = not missing and not rejected
        return {
            "eligible": eligible,
            "can_release": eligible,
            "missing": missing,
            "rejected": rejected,
            "passed": passed,
            "blockers": [
                *[{"segment_id": s.get("segment_id"), "type": "missing"} for s in missing],
                *[{"segment_id": s.get("segment_id"), "type": "rejected"} for s in rejected],
            ],
        }

    def evaluate_version(self, version: Any) -> dict[str, Any]:
        from app.services.review_workbench import ReviewWorkbenchService

        return ReviewWorkbenchService().release_eligibility(version)

    # 常见命名别名
    check_version = evaluate_version
    verify = evaluate_version
