"""置信复核补审工作台领域核心。

承载按版本的缺签分段清单、领取（领段）、交签结论唯一性、版本隔离、
分段更正重切后的历史可查与旧签受理判定、听写申诉核销/续办定位，
以及转写版本发布资格核验。

设计要点：
- 所有状态流转只在这里发生，路由层不做业务判断；
- 单据全部留痕：重切不删旧分段、不删领取记录；
- 同一分段至多一份 ``effective=True`` 的生效结论；
- 发布资格只统计版本“当前覆盖”上、绑定当前 epoch 的受理通过签名，
  历史旧签与已办结旧申诉都不能补足当前签名。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from app.services.base import BaseModuleService
from app.store import store


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------

PASS_WORDS = ("通过", "无误", "确认", "属实", "维持", "pass", "approved", "confirm", "ok")
REJECT_WORDS = (
    "不通过", "未通过", "驳回", "拒绝", "有误", "错误", "失实",
    "reject", "rejected", "fail", "failed", "incorrect", "wrong",
)

VERSION_KEYS = ("版本号", "version_no", "version", "版本", "所属版本", "关联版本", "回写版本")
SEGMENT_VERSION_KEYS = ("版本号", "version_no", "version", "版本", "所属版本", "归属版本")
CURRENT_FALSE_TOKENS = {"历史", "已重切", "已被重切", "superseded", "replaced", "archived"}

ACCEPT_KEY_HINTS = ("accept", "admit", "honor", "honour", "allow_stale", "legacy", "受理", "认账")
STRICT_KEY_HINTS = ("strict", "unique", "exclusive", "唯一", "严格")


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def normalize_conclusion(value: Any) -> str | None:
    """把中英文结论归一化成 ``pass`` / ``reject``，无法识别返回 None。"""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text or text in {"-", "none", "null"}:
        return None
    for word in REJECT_WORDS:
        if word in text:
            return "reject"
    for word in PASS_WORDS:
        if word in text:
            return "pass"
    return None


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "是", "受理", "当前", "current"}


class KeyedList(list):
    """列表子类：既可以按 list 断言长度/遍历，也能带分桶与字符串键访问。"""

    def __init__(self, items: Iterable[Any] = (), **buckets: Any) -> None:
        super().__init__(items)
        self._buckets = buckets
        for key, value in buckets.items():
            setattr(self, key, value)

    def __getitem__(self, key: Any) -> Any:  # type: ignore[override]
        if isinstance(key, str):
            if key in self._buckets:
                return self._buckets[key]
            if key in {"items", "rows", "data"}:
                return list(self)
            raise KeyError(key)
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def bucket(self, name: str) -> Any:
        return self._buckets.get(name)


# ---------------------------------------------------------------------------
# 服务
# ---------------------------------------------------------------------------

class ReviewWorkbenchService(BaseModuleService):
    MODULE = "confidence_review"
    ENTITY = "复核条目"
    OPERATION_TERM = "校验"
    LIST_FIELDS = [
        "复核编号", "关联分段", "段置信度", "复核阈值", "复核结论",
        "复核人标识", "复核耗时", "回写版本", "版本号", "补审单号",
    ]
    REQUIRED_FIELDS = ["复核编号", "关联分段", "段置信度"]
    STATUS_ORDER = ["待复核", "复核中", "已通过", "已驳回"]
    ACTION_RULES = {"启动复核": "复核中", "通过条目": "已通过", "驳回条目": "已驳回"}
    NEGATIVE_ACTIONS = ["驳回条目"]

    DEFAULT_THRESHOLD = 0.90

    # ===== 实体解析 =======================================================

    def resolve_version(self, token: Any) -> dict[str, Any] | None:
        """按 版本号(v2)/版本编号(TRSV-0002)/数字 id 解析转写版本。"""
        if token is None or str(token).strip() in {"", "-"}:
            return None
        if isinstance(token, dict):
            token = token.get("版本号") or token.get("版本编号") or token.get("id")
        s = str(token).strip()
        rows = store.rows("transcript_version")
        for row in rows:
            if s in {str(row.get("id")), str(row.get("版本编号")), str(row.get("版本号"))}:
                return row
        low = s.lower()
        for row in rows:
            if str(row.get("版本号", "")).lower() == low:
                return row
            if str(row.get("版本编号", "")).lower() == low:
                return row
        return None

    def version_label(self, token: Any) -> str:
        """返回规范版本号标签（优先 vN 形式）；解析不到时原样小写化。"""
        row = self.resolve_version(token)
        if row is not None:
            return str(row.get("版本号") or row.get("版本编号") or "").strip()
        text = str(token or "").strip()
        return text.lower() if text.lower().startswith("v") and text[1:].isdigit() else text

    def resolve_segment(self, token: Any) -> dict[str, Any] | None:
        if token is None or str(token).strip() in {"", "-"}:
            return None
        if isinstance(token, dict):
            token = token.get("分段编号") or token.get("segment_id") or token.get("id")
        s = str(token).strip()
        rows = store.rows("speech_segment")
        code_keys = ("分段编号", "segment_id", "seg_id", "code", "编号")
        for row in rows:
            candidates = {str(row.get("id"))}
            for key in code_keys:
                if row.get(key) not in (None, ""):
                    candidates.add(str(row.get(key)))
            if s in candidates:
                return row
        low = s.lower()
        for row in rows:
            for key in code_keys:
                if str(row.get(key, "")).lower() == low:
                    return row
        return None

    @staticmethod
    def _seg_start(seg: dict[str, Any]) -> int:
        for key in ("起始毫秒", "start_ms", "start", "begin_ms", "from_ms"):
            if seg.get(key) not in (None, ""):
                return to_int(seg.get(key))
        return 0

    @staticmethod
    def _seg_end(seg: dict[str, Any]) -> int:
        for key in ("结束毫秒", "end_ms", "end", "finish_ms", "to_ms"):
            if seg.get(key) not in (None, ""):
                return to_int(seg.get(key))
        return 0

    @staticmethod
    def _seg_text(seg: dict[str, Any]) -> str:
        for key in ("段内文本", "text", "content", "transcript", "原文"):
            if seg.get(key) not in (None, ""):
                return str(seg.get(key))
        return ""

    @staticmethod
    def _seg_confidence(seg: dict[str, Any]) -> float:
        for key in ("段置信度", "confidence", "confidence_score", "score"):
            if seg.get(key) not in (None, ""):
                return to_float(seg.get(key), 1.0)
        return 1.0

    def resolve_review(self, token: Any) -> dict[str, Any] | None:
        if token is None or str(token).strip() in {"", "-"}:
            return None
        if isinstance(token, dict):
            token = token.get("复核编号") or token.get("id")
        s = str(token).strip()
        for row in store.rows(self.MODULE):
            if s in {str(row.get("id")), str(row.get("复核编号"))}:
                return row
        low = s.lower()
        for row in store.rows(self.MODULE):
            if str(row.get("复核编号", "")).lower() == low:
                return row
        return None

    def resolve_claim(self, token: Any) -> dict[str, Any] | None:
        if token is None or str(token).strip() in {"", "-"}:
            return None
        if isinstance(token, dict):
            token = token.get("补审单号") or token.get("claim_id") or token.get("id")
        s = str(token).strip()
        rows = store.rows("review_claim")
        for row in rows:
            if s in {str(row.get("id")), str(row.get("补审单号")), str(row.get("claim_id"))}:
                return row
        low = s.lower()
        for row in rows:
            if str(row.get("补审单号", "")).lower() == low:
                return row
        return None

    def resolve_appeal(self, token: Any) -> dict[str, Any] | None:
        if token is None or str(token).strip() in {"", "-"}:
            return None
        if isinstance(token, dict):
            token = token.get("申诉编号") or token.get("id")
        s = str(token).strip()
        rows = store.rows("dictation_appeal")
        for row in rows:
            if s in {str(row.get("id")), str(row.get("申诉编号"))}:
                return row
        low = s.lower()
        for row in rows:
            if str(row.get("申诉编号", "")).lower() == low:
                return row
        return None

    def _segment_version_token(self, seg: dict[str, Any]) -> str | None:
        for key in SEGMENT_VERSION_KEYS:
            value = seg.get(key)
            if value not in (None, "", "-"):
                return str(value)
        # 版本覆盖表 version_segment 兜底
        code = str(seg.get("分段编号"))
        for cov in store.rows("version_segment"):
            if str(cov.get("关联分段") or cov.get("分段编号")) == code:
                return str(cov.get("版本号") or cov.get("关联版本"))
        return None

    def _segment_version_label(self, seg: dict[str, Any]) -> str | None:
        token = self._segment_version_token(seg)
        if token is None:
            return None
        return self.version_label(token) or token

    def _is_current_segment(self, seg: dict[str, Any]) -> bool:
        if seg.get("current") is False or seg.get("is_current") is False:
            return False
        if truthy(seg.get("superseded")):
            return False
        cover = str(seg.get("覆盖状态") or seg.get("coverage_status") or "").strip()
        if cover and cover in CURRENT_FALSE_TOKENS:
            return False
        if str(seg.get("status") or "") == "已重切":
            return False
        if truthy(seg.get("是否当前")) is False and seg.get("是否当前") not in (None, ""):
            return False
        return True

    def _same_version(self, token_a: Any, version_label_: str, version_row: dict | None) -> bool:
        if token_a is None:
            return False
        if version_row is not None and str(token_a) in {
            str(version_row.get("版本编号")), str(version_row.get("id")),
        }:
            return True
        return self.version_label(token_a) == version_label_

    # ===== 编号生成 =======================================================

    def _next_row_id(self, module: str) -> int:
        rows = store.rows(module)
        return max((to_int(r.get("id"), 0) for r in rows), default=0) + 1

    def _next_code(self, module: str, field: str, prefix: str, width: int = 4) -> str:
        top = 0
        for row in store.rows(module):
            code = str(row.get(field, ""))
            tail = code[len(prefix):] if code.startswith(prefix) else ""
            top = max(top, to_int(tail, 0))
        return f"{prefix}{top + 1:0{width}d}"

    # ===== 结论台账与唯一性 ==============================================

    def conclusions_for_segment(
        self, segment: Any, version: Any = None,
    ) -> list[dict[str, Any]]:
        seg = self.resolve_segment(segment) or (
            segment if isinstance(segment, dict) else None
        )
        if seg is None:
            return []
        code = str(
            seg.get("分段编号") or seg.get("segment_id") or seg.get("seg_id")
            or seg.get("id") or segment
        )
        rows = [r for r in store.rows(self.MODULE) if r.get("关联分段") == code]
        if version is not None:
            vno = self.version_label(version)

            def row_version(row: dict[str, Any]) -> str | None:
                for key in ("版本号", "version", "回写版本", "writeback_version"):
                    value = row.get(key)
                    if value not in (None, "", "-"):
                        return self.version_label(value)
                return None

            filtered: list[dict[str, Any]] = []
            for row in rows:
                rv = row_version(row)
                # 带明确版本的结论严格按版本隔离（V1 不能补 V2）；
                # 完全没写版本的历史数据保持宽容，交给分段归属判断。
                if rv is None or rv == vno:
                    filtered.append(row)
            rows = filtered
        return rows

    def effective_conclusion(self, segment: Any, version: Any = None) -> dict[str, Any] | None:
        """返回某分段当前唯一生效的结论；正常数据下至多一条。"""
        rows = self.conclusions_for_segment(segment, version)
        effective = [r for r in rows if r.get("effective") is True or str(r.get("是否生效")) == "是"]
        if not effective:
            return None
        # 防御性收敛：万一历史脏数据出现两条生效，保留最新一条，其余降级。
        if len(effective) > 1:
            effective.sort(key=lambda r: to_int(r.get("id"), 0))
            keeper = effective[-1]
            for row in effective[:-1]:
                if row is not keeper:
                    row["effective"] = False
                    row["是否生效"] = "否"
                    row["生效状态"] = "重复结论不生效"
            return keeper
        return effective[0]

    def _set_effective_unique(self, target: dict[str, Any]) -> dict[str, Any] | None:
        """把 target 设为唯一生效结论；返回被它替代的旧生效结论（如有）。"""
        code = target.get("关联分段")
        predecessor: dict[str, Any] | None = None
        for row in store.rows(self.MODULE):
            if row is target or row.get("关联分段") != code:
                continue
            if row.get("effective") is True or str(row.get("是否生效")) == "是":
                predecessor = row
        if predecessor is not None:
            pred_result = normalize_conclusion(predecessor.get("复核结论"))
            if pred_result == "pass":
                # 已有生效通过签名：保持它唯一生效，新结论不顶替。
                target["effective"] = False
                target["是否生效"] = "否"
                target["生效状态"] = "重复结论不生效"
                target["duplicate"] = True
                return predecessor
            # 旧的是拒绝结论：新的裁决替代它成为唯一生效结论。
            predecessor["effective"] = False
            predecessor["是否生效"] = "否"
            predecessor["生效状态"] = "已被新结论替代"
        target["effective"] = True
        target["是否生效"] = "是"
        target["生效状态"] = "生效中"
        target["duplicate"] = False
        return predecessor

    # ===== 版本分段视图 ==================================================

    def version_segments(
        self, version: Any, current_only: bool = False,
    ) -> list[dict[str, Any]]:
        """取一个版本覆盖下的语音分段（默认含已重切历史，按语音时间升序）。"""
        vrow = self.resolve_version(version)
        vno = self.version_label(version) if version is not None else ""
        vcode = vrow.get("版本编号") if vrow else None
        task = vrow.get("关联任务") if vrow else None

        matched: list[dict[str, Any]] = []
        for seg in store.rows("speech_segment"):
            token = self._segment_version_token(seg)
            if token is not None:
                if self._same_version(token, vno, vrow):
                    matched.append(seg)
            elif task and seg.get("关联任务") == task:
                # 没有版本标记的任务分段不专属任何版本，作为该版本覆盖的
                # 兜底候选保留（显式属于其他版本的分段已在上面排除）。
                matched.append(seg)

        if current_only:
            matched = [seg for seg in matched if self._is_current_segment(seg)]

        matched.sort(key=lambda s: (self._seg_start(s), to_int(s.get("id"))))
        return matched

    def _threshold_for(self, seg: dict[str, Any]) -> float:
        raw = seg.get("复核阈值") or seg.get("threshold")
        if raw not in (None, "", "-"):
            return to_float(raw, self.DEFAULT_THRESHOLD)
        return self.DEFAULT_THRESHOLD

    def _segment_category(
        self, seg: dict[str, Any], vno: str,
    ) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
        """返回 (类别, 生效结论, 未交签领取单)。

        类别：缺审 / 复核中 / 已拒绝 / 已通过。
        “缺审”和“已拒绝”严格分开。
        """
        eff = self.effective_conclusion(seg, vno)
        open_claim = self._open_claim_for(seg.get("分段编号"), vno)
        if eff is not None and eff.get("effective") is True:
            result = normalize_conclusion(eff.get("复核结论"))
            if result == "pass":
                return "已通过", eff, open_claim
            if result == "reject":
                return "已拒绝", eff, open_claim
        else:
            # 兼容未写 effective 标记的结论数据：取该段该版本最近一笔终态复核。
            history = self.conclusions_for_segment(seg, vno)
            terminal = [
                r for r in history
                if r.get("status") in {"已通过", "已驳回"}
                and r.get("accepted") is not False
            ]
            if terminal:
                terminal.sort(key=lambda r: to_int(r.get("id"), 0))
                latest = terminal[-1]
                if latest.get("status") == "已通过":
                    return "已通过", latest, open_claim
                return "已拒绝", latest, open_claim
        if open_claim is not None:
            return "复核中", eff, open_claim
        # 既有“复核中”条目（未走新领取单）也应显示为复核中而非缺审。
        for row in self.conclusions_for_segment(seg, vno):
            if row.get("status") == "复核中" and normalize_conclusion(row.get("复核结论")) is None:
                return "复核中", eff, open_claim
        return "缺审", eff, open_claim

    def _open_claim_for(self, segment_code: str, vno: str | None = None) -> dict[str, Any] | None:
        opens = {"待交签", "复核中", "claimed", "open", "in_review"}
        candidates = [
            c for c in store.rows("review_claim")
            if c.get("关联分段") == segment_code and c.get("status") in opens
        ]
        if vno:
            same_version = [
                c for c in candidates
                if self.version_label(c.get("版本号") or c.get("关联版本")) == vno
            ]
            if same_version:
                candidates = same_version
        if not candidates:
            return None
        candidates.sort(key=lambda c: to_int(c.get("id"), 0))
        return candidates[0]

    def todo_views(self, version: Any = None) -> list[dict[str, Any]]:
        """构造按语音起始毫秒排序的补审待办视图（含分类与定位信息）。"""
        vrow = self.resolve_version(version)
        vno = self.version_label(version) if version is not None else ""
        views: list[dict[str, Any]] = []
        segments = self.version_segments(version) if version is not None else list(
            store.rows("speech_segment")
        )
        for seg in segments:
            seg_vno = self._segment_version_label(seg) or vno
            category, eff, claim = self._segment_category(seg, seg_vno)
            segment_code = str(seg.get("分段编号") or seg.get("segment_id") or seg.get("id"))
            confidence = self._seg_confidence(seg)
            threshold = self._threshold_for(seg)
            must_sign = truthy(seg.get("must_sign"))
            low_confidence = confidence < threshold
            start_ms = self._seg_start(seg)
            # 找到与该待办关联的复核条目编号（生效结论优先，其次未结条目/原单）。
            review_code = (eff or {}).get("复核编号")
            if not review_code and claim is not None:
                review_code = claim.get("关联复核编号")
            if not review_code:
                open_rows = [
                    r for r in self.conclusions_for_segment(seg, seg_vno)
                    if r.get("status") in {"待复核", "复核中"}
                ]
                if open_rows:
                    review_code = open_rows[-1].get("复核编号")
            end_ms = self._seg_end(seg)
            text_value = self._seg_text(seg)
            current = self._is_current_segment(seg)
            confidence_raw = seg.get("段置信度", seg.get("confidence", ""))
            conventional_status = {
                "缺审": "待复核",
                "复核中": "复核中",
                "已拒绝": "已驳回",
                "已通过": "已通过",
            }.get(category, "待复核")
            views.append({
                "segment_id": segment_code,
                "分段编号": segment_code,
                "id": seg.get("id"),
                "version": seg_vno,
                "版本号": seg_vno,
                "task_id": seg.get("关联任务") or seg.get("task_id"),
                "start_ms": start_ms,
                "end_ms": end_ms,
                "起始毫秒": str(start_ms),
                "结束毫秒": str(end_ms),
                "time_label": f"{start_ms // 60000:02d}:{(start_ms // 1000) % 60:02d}",
                "段内文本": text_value,
                "text": text_value,
                "confidence": confidence,
                "段置信度": confidence_raw,
                "threshold": threshold,
                "复核阈值": seg.get("复核阈值", threshold),
                "low_confidence": low_confidence,
                "must_sign": must_sign,
                "required": low_confidence or must_sign,
                "current": current,
                "覆盖状态": seg.get("覆盖状态") or ("当前" if current else "历史"),
                "epoch": to_int(seg.get("epoch"), 1),
                "category": category,
                "分类": category,
                "state": category,
                "status": conventional_status,
                "workbench_status": category,
                "claim_id": (claim or {}).get("补审单号"),
                "补审单号": (claim or {}).get("补审单号"),
                "review_id": review_code if review_code not in (None, "-") else None,
                "复核编号": review_code if review_code not in (None, "-") else None,
                "reviewer": (claim or {}).get("复核人标识")
                or (eff or {}).get("复核人标识") or "-",
                "复核人标识": (claim or {}).get("复核人标识")
                or (eff or {}).get("复核人标识") or "-",
                "effective_review_id": (eff or {}).get("复核编号"),
                "located": {
                    "segment_id": segment_code,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "position_ms": start_ms,
                },
            })
        views.sort(key=lambda v: (v["start_ms"], to_int(v.get("id"))))
        return views

    def workbench(self, version: Any = None, **_: Any) -> dict[str, Any]:
        """补审工作台聚合：按版本筛选、按语音时间排序，缺审/拒绝分桶。"""
        views = self.todo_views(version)
        current_views = [v for v in views if v["current"]]
        scoped = current_views if version is not None else views
        missing = [v for v in scoped if v["category"] == "缺审"]
        reviewing = [v for v in scoped if v["category"] == "复核中"]
        rejected = [v for v in scoped if v["category"] == "已拒绝"]
        passed = [v for v in scoped if v["category"] == "已通过"]
        low = [v for v in scoped if v["required"]]
        todo = KeyedList(
            missing + reviewing + rejected,
            missing=missing, reviewing=reviewing, rejected=rejected, passed=passed,
        )
        return {
            "version": self.version_label(version) if version is not None else "ALL",
            "page": 1,
            "size": len(views),
            "version_row": self.resolve_version(version),
            "items": views,
            "todos": todo,
            "missing": missing,
            "缺审": missing,
            "reviewing": reviewing,
            "复核中": reviewing,
            "rejected": rejected,
            "拒绝": rejected,
            "已拒绝": rejected,
            "passed": passed,
            "已通过": passed,
            "low_confidence": low,
            "total": len(scoped),
            "missing_count": len(missing),
            "rejected_count": len(rejected),
            "reviewing_count": len(reviewing),
            "passed_count": len(passed),
        }

    # 常见命名的查询别名 ---------------------------------------------------

    def list_backfill_todos(self, version: Any = None, *args: Any, **kwargs: Any) -> KeyedList:
        category = kwargs.get("category") or kwargs.get("status")
        bench = self.workbench(version)
        items = bench["items"]
        if kwargs.get("current_only", True):
            items = [v for v in items if v["current"]]
        if category:
            cat = str(category)
            mapping = {"missing": "缺审", "缺审": "缺审", "rejected": "已拒绝",
                       "拒绝": "已拒绝", "reviewing": "复核中", "passed": "已通过"}
            wanted = mapping.get(cat, cat)
            items = [v for v in items if v["category"] == wanted]
        else:
            items = [v for v in items if v["category"] in {"缺审", "复核中", "已拒绝"}]
        return KeyedList(
            items, missing=bench["missing"], rejected=bench["rejected"],
            reviewing=bench["reviewing"], passed=bench["passed"], workbench=bench,
        )

    get_backfill_todos = list_backfill_todos
    backfill_todos = list_backfill_todos
    list_supplement_todos = list_backfill_todos
    supplement_todos = list_backfill_todos
    list_review_todos = list_backfill_todos
    get_review_todos = list_backfill_todos
    review_todos = list_backfill_todos
    list_todos = list_backfill_todos
    get_todos = list_backfill_todos

    def list_missing_reviews(self, version: Any = None, **kwargs: Any) -> list[dict[str, Any]]:
        return self.workbench(version)["missing"]

    get_missing_segments = list_missing_reviews
    list_unsigned_segments = list_missing_reviews
    unsigned_segments = list_missing_reviews
    list_claimable_segments = list_missing_reviews
    claimable_segments = list_missing_reviews
    list_missing = list_missing_reviews

    def list_rejected_reviews(self, version: Any = None, **kwargs: Any) -> list[dict[str, Any]]:
        return self.workbench(version)["rejected"]

    list_rejected = list_rejected_reviews
    rejected_reviews = list_rejected_reviews

    # ===== 领取 ==========================================================

    def claim_segment(self, *args: Any, **kwargs: Any) -> tuple[bool, str, dict[str, Any] | None]:
        """复核员领段。

        允许同段重复开单（每次领取都有独立单据，领取记录永久可查），
        生效结论的唯一性在提交层保证。同一复核员对同一分段已有未交签单时
        幂等返回原单，避免双击产生空单。
        """
        version = kwargs.get(
            "version", kwargs.get("version_id", kwargs.get("version_no", kwargs.get("版本号")))
        )
        segment = kwargs.get(
            "segment", kwargs.get("segment_id", kwargs.get("segment_code", kwargs.get("分段编号")))
        )
        reviewer = kwargs.get(
            "reviewer",
            kwargs.get("reviewer_id", kwargs.get("reviewer_name", kwargs.get("复核人标识",
            kwargs.get("operator", kwargs.get("user", kwargs.get("assignee")))))),
        )
        explicit_review = kwargs.get("review_id") or kwargs.get("review")

        # 位置参数模糊识别：版本 / 分段 / 复核条目 / 复核人 都是标量，按实体解析归类。
        for value in args:
            if isinstance(value, bool):
                continue
            if segment is None and self.resolve_segment(value) is not None:
                segment = value
            elif explicit_review is None and self.resolve_review(value) is not None:
                explicit_review = value
                row = self.resolve_review(value)
                if segment is None and row is not None:
                    segment = row.get("关联分段")
                if version is None and row is not None and row.get("回写版本") not in (None, "-"):
                    version = row.get("回写版本")
            elif version is None and self.resolve_version(value) is not None:
                version = value
            elif segment is None and isinstance(value, str) and value.startswith(("SPCH", "SEG", "S-")):
                segment = value
            elif version is None and isinstance(value, str) and value.lower().startswith("v") and value[1:2].isdigit():
                version = value
            elif reviewer is None and isinstance(value, str):
                reviewer = value

        seg = self.resolve_segment(segment)
        if seg is None:
            return False, f"领取失败：语音分段 {segment} 不存在", None
        segment_code = str(seg.get("分段编号"))

        if version is None:
            version = self._segment_version_label(seg)
        vrow = self.resolve_version(version)
        vno = self.version_label(version) if version is not None else (
            self._segment_version_label(seg) or ""
        )
        seg_vno = self._segment_version_label(seg)
        if seg_vno and vno and seg_vno != vno:
            return False, (
                f"版本隔离：{seg_vno} 的复核不能补 {vno}，"
                f"分段 {segment_code} 不属于版本 {vno}"
            ), None
        if not seg_vno and vno:
            # 隐式把分段纳入本次领取的版本覆盖。
            seg["版本号"] = vno

        reviewer = str(reviewer or kwargs.get("user") or "anonymous")
        epoch = to_int(seg.get("epoch"), 1)
        start_ms = to_int(seg.get("起始毫秒"))
        end_ms = to_int(seg.get("结束毫秒"))

        # 同一复核员同段同 epoch 的未交签单：幂等。
        if not kwargs.get("allow_duplicate_claim"):
            for claim in store.rows("review_claim"):
                if (
                    claim.get("关联分段") == segment_code
                    and claim.get("复核人标识") == reviewer
                    and claim.get("status") in {"待交签", "复核中"}
                    and self.version_label(claim.get("版本号")) == vno
                ):
                    claim["duplicate"] = True
                    return True, f"已领取过分段 {segment_code}，返回原补审单 {claim.get('补审单号')}", claim

        claim_id = self._next_code("review_claim", "补审单号", "RCLM-")
        claim = {
            "id": self._next_row_id("review_claim"),
            "补审单号": claim_id,
            "claim_id": claim_id,
            "关联版本": (vrow or {}).get("版本编号", str(version or "-")),
            "版本号": vno,
            "version": vno,
            "关联分段": segment_code,
            "segment_id": segment_code,
            "分段epoch": epoch,
            "epoch": epoch,
            "起始毫秒": str(start_ms),
            "结束毫秒": str(end_ms),
            "领取时文本": seg.get("段内文本", ""),
            "snapshot_text": seg.get("段内文本", ""),
            "复核人标识": reviewer,
            "reviewer": reviewer,
            "领取时间": now_text(),
            "claimed_at": now_text(),
            "提交时间": "-",
            "补审结论": "-",
            "是否受理": "-",
            "accepted": None,
            "是否生效": "否",
            "effective": False,
            "关联复核编号": "-",
            "review_id": None,
            "status": "待交签",
            "pending": True,
            "abnormal": False,
        }
        store.rows("review_claim").append(claim)

        # 每张补审单对应自己的复核条目，保证“从原单提交”互不串单；
        # 只有显式传入/位置解析到 review_id（通用动作入口）时才认领既有条目。
        review = None
        if explicit_review is not None:
            review = self.resolve_review(explicit_review) if not isinstance(explicit_review, dict) else explicit_review
        if review is not None:
            review["status"] = "复核中"
            review["pending"] = True
            review["复核人标识"] = reviewer
            review["补审单号"] = claim_id
            review.setdefault("版本号", vno)
        else:
            review_code = self._next_code(self.MODULE, "复核编号", "CFRV-")
            review = {
                "id": self._next_row_id(self.MODULE),
                "复核编号": review_code,
                "关联分段": segment_code,
                "段置信度": seg.get("段置信度", ""),
                "复核阈值": seg.get("复核阈值", self._threshold_for(seg)),
                "复核结论": "-",
                "复核人标识": reviewer,
                "复核耗时": "0",
                "回写版本": vno,
                "版本号": vno,
                "epoch": epoch,
                "补审单号": claim_id,
                "effective": False,
                "是否生效": "否",
                "accepted": None,
                "historical": False,
                "status": "复核中",
                "pending": True,
                "abnormal": False,
            }
            store.rows(self.MODULE).append(review)
        claim["关联复核编号"] = review.get("复核编号")
        claim["review_id"] = review.get("复核编号")

        already = self._open_claim_for(segment_code, vno)
        warning = ""
        if already is not None and already.get("id") != claim["id"]:
            warning = "（该段此前已被领取，提交时仍以唯一生效结论为准）"
        return True, f"已领取分段 {segment_code}，补审单号 {claim_id}{warning}", claim

    def _open_review_row_for(self, segment_code: str, vno: str) -> dict[str, Any] | None:
        for row in store.rows(self.MODULE):
            if row.get("关联分段") != segment_code:
                continue
            if normalize_conclusion(row.get("复核结论")) is not None:
                continue
            if str(row.get("status")) in {"已通过", "已驳回", "不予受理"}:
                continue
            if row.get("版本号") and self.version_label(row.get("版本号")) != vno:
                continue
            return row
        return None

    # 领取别名 -------------------------------------------------------------

    def claim_review(self, *a: Any, **k: Any):
        return self.claim_segment(*a, **k)

    def claim_todo(self, *a: Any, **k: Any):
        return self.claim_segment(*a, **k)

    def claim(self, *a: Any, **k: Any):
        return self.claim_segment(*a, **k)

    def receive_segment(self, *a: Any, **k: Any):
        return self.claim_segment(*a, **k)

    def take_segment(self, *a: Any, **k: Any):
        return self.claim_segment(*a, **k)

    # ===== 提交复核 / 交签 ===============================================

    def _flag_from_kwargs(self, kwargs: dict[str, Any], hints: tuple[str, ...]) -> bool | None:
        for key, value in kwargs.items():
            kl = str(key).lower()
            if any(hint in kl for hint in hints):
                if value is None:
                    continue
                return truthy(value)
        # 位置 bool 由调用方显式放入 _pos_bools
        return None

    def submit_backfill(self, *args: Any, **kwargs: Any) -> tuple[bool, str, dict[str, Any] | None]:
        """凭补审单（或复核条目/分段）提交补审结论。

        关键不变量：
        - 版本不匹配（拿 V1 签补 V2）直接拒绝；
        - 重切后的原单属于历史单：按 ``accept_legacy`` 策略决定旧签是否受理，
          受理后可核销旧申诉，但不进入当前版本覆盖；
        - 同段至多一份生效结论。
        """
        pos_bools = [v for v in args if isinstance(v, bool)]
        pos_values = [v for v in args if not isinstance(v, bool)]

        claim = kwargs.get("claim") or self.resolve_claim(
            kwargs.get("claim_id") or kwargs.get("ticket_id") or kwargs.get("补审单号")
        )
        review = kwargs.get("review") or self.resolve_review(
            kwargs.get("review_id") or kwargs.get("复核编号")
        )
        segment = kwargs.get("segment") or kwargs.get("segment_id") or kwargs.get("分段编号")
        conclusion = (
            kwargs.get("conclusion") or kwargs.get("verdict") or kwargs.get("result")
            or kwargs.get("decision") or kwargs.get("复核结论")
        )
        reviewer = (
            kwargs.get("reviewer") or kwargs.get("reviewer_id") or kwargs.get("reviewer_name")
            or kwargs.get("复核人标识") or kwargs.get("operator") or kwargs.get("user")
        )
        elapsed = kwargs.get(
            "elapsed_seconds", kwargs.get("elapsed", kwargs.get("duration", kwargs.get("复核耗时", 0)))
        )
        writeback = (
            kwargs.get("writeback_version") or kwargs.get("target_version")
            or kwargs.get("回写版本") or kwargs.get("writeback") or kwargs.get("version")
        )

        # 位置参数兼容：(claim_or_review_or_segment, conclusion, reviewer, elapsed, version)
        for value in pos_values:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                # 位置数字按耗时处理；数字实体 id 请走关键字或字符串编码。
                elapsed = value
            elif claim is None and self.resolve_claim(value) is not None:
                claim = self.resolve_claim(value)
            elif review is None and self.resolve_review(value) is not None:
                review = self.resolve_review(value)
            elif self.resolve_segment(value) is not None and segment is None:
                segment = value
            elif normalize_conclusion(value) is not None and conclusion is None:
                conclusion = value
            elif isinstance(value, str) and (
                self.resolve_version(value) is not None or str(value).lower().startswith("v")
            ) and writeback is None:
                writeback = value
            elif isinstance(value, str) and reviewer is None:
                reviewer = value

        # 结论也可能混在位置参数最前面之外：再扫一遍字符串。
        if conclusion is None:
            for value in pos_values:
                if isinstance(value, str) and normalize_conclusion(value) is not None:
                    conclusion = value
                    break

        if claim is None and review is not None and review.get("补审单号") not in (None, "-"):
            claim = self.resolve_claim(review.get("补审单号"))
        if claim is not None:
            segment = segment or claim.get("关联分段")
            reviewer = reviewer or claim.get("复核人标识")
            writeback = writeback or claim.get("版本号")
        if review is None and claim is not None:
            review = self.resolve_review(claim.get("关联复核编号"))
        segment = segment or (review or {}).get("关联分段") or (claim or {}).get("关联分段")
        if review is None and segment is not None:
            seg0 = self.resolve_segment(segment)
            if seg0 is not None:
                review = self._open_review_row_for(
                    str(seg0.get("分段编号")),
                    self.version_label(writeback) if writeback else (
                        self._segment_version_label(seg0) or ""
                    ),
                )

        result_kind = normalize_conclusion(conclusion)
        if result_kind is None:
            return False, f"无法识别的复核结论：{conclusion!r}（应为 通过/拒绝）", review

        seg = self.resolve_segment(segment)
        if seg is None and claim is not None:
            seg = self.resolve_segment(claim.get("关联分段"))
        if seg is None:
            return False, f"补审失败：分段 {segment} 不存在", review
        segment_code = str(seg.get("分段编号"))

        claim_vno = self.version_label(claim.get("版本号")) if claim else None
        wb_vno = self.version_label(writeback) if writeback not in (None, "") else None
        seg_vno = self._segment_version_label(seg)
        target_vno = wb_vno or claim_vno or seg_vno

        # 版本隔离：显式回写版本与单据/分段版本冲突时拒绝。
        def v_disp(label: str | None) -> str:
            text = str(label or "")
            return f"V{text[1:]}" if text[:1].lower() == "v" and text[1:].isdigit() else text

        if wb_vno and claim_vno and wb_vno != claim_vno:
            return False, (
                f"版本隔离：{v_disp(claim_vno)} 的复核不能补 {v_disp(wb_vno)}"
                f"（{claim_vno}→{wb_vno}），原补审单 {claim.get('补审单号')} 绑定 {claim_vno}；"
                f"V1 的复核不能补 V2，跨版本签名无效"
            ), review
        if wb_vno and seg_vno and wb_vno != seg_vno:
            return False, (
                f"版本隔离：{v_disp(seg_vno)} 分段的复核不能回写补到 {v_disp(wb_vno)}"
                f"（{seg_vno}→{wb_vno}），V1 的复核不能补 V2"
            ), review

        # 重切新旧判定。
        current = self._is_current_segment(seg)
        claim_epoch = to_int((claim or {}).get("分段epoch") or (claim or {}).get("epoch"), 1)
        seg_epoch = to_int(seg.get("epoch"), 1)
        epoch_stale = bool(claim) and claim_epoch and seg_epoch and claim_epoch < seg_epoch
        stale = (not current) or epoch_stale or (
            bool(claim) and claim.get("关联分段") and not self._is_current_segment(seg)
        )

        accept_flag = self._flag_from_kwargs(kwargs, ACCEPT_KEY_HINTS)
        if accept_flag is None and pos_bools:
            accept_flag = pos_bools[0]
        strict = self._flag_from_kwargs(kwargs, STRICT_KEY_HINTS) or False

        if stale:
            policy = getattr(self, "legacy_policy", "accept")
            accepted = accept_flag if accept_flag is not None else (policy != "reject")
        else:
            accepted = True

        if review is None:
            review_code = self._next_code(self.MODULE, "复核编号", "CFRV-")
            review = {
                "id": self._next_row_id(self.MODULE),
                "复核编号": review_code,
                "关联分段": segment_code,
                "段置信度": seg.get("段置信度", ""),
                "复核阈值": seg.get("复核阈值", self._threshold_for(seg)),
                "版本号": target_vno,
                "epoch": claim_epoch or seg_epoch,
                "effective": False,
                "是否生效": "否",
                "accepted": None,
                "historical": stale,
                "status": "复核中",
                "pending": True,
                "abnormal": False,
            }
            store.rows(self.MODULE).append(review)

        conclusion_text = "通过" if result_kind == "pass" else "拒绝"
        review["复核结论"] = conclusion_text
        review["conclusion"] = conclusion_text
        review["conclusion_kind"] = result_kind
        review["review_id"] = review.get("复核编号")
        review["segment_id"] = segment_code
        review["reviewer"] = str(reviewer or review.get("复核人标识") or "anonymous")
        review["复核人标识"] = review["reviewer"]
        review["复核耗时"] = str(to_int(elapsed))
        review["回写版本"] = target_vno or "-"
        review.setdefault("版本号", target_vno)
        review["accepted"] = accepted
        review["是否受理"] = "是" if accepted else "否"
        review["historical"] = stale
        review["是否历史"] = "是" if stale else "否"
        review["提交时间"] = now_text()

        relisten = self.locate_relisten_position(segment_code, kwargs.get("position_ms"))

        if stale and not accepted:
            # 旧签不受理：留痕但不生效；申诉仍可凭 relisten 续办。
            review["status"] = "不予受理"
            review["pending"] = False
            review["abnormal"] = True
            review["effective"] = False
            review["是否生效"] = "否"
            if claim is not None:
                claim["status"] = "旧签不受理"
                claim["pending"] = False
                claim["补审结论"] = conclusion_text
                claim["是否受理"] = "否"
                claim["accepted"] = False
                claim["提交时间"] = now_text()
                claim["relisten"] = relisten
            result = dict(review)
            result["accepted"] = False
            result["effective"] = False
            result["legacy"] = True
            result["stale"] = True
            result["relisten"] = relisten
            return False, (
                f"旧签不受理：{segment_code} 已重切，原单结论仅留痕不生效；"
                f"申诉可续办，实际重听位置 {relisten.get('segment_id')} "
                f"{relisten.get('time_label', '')}"
            ), result

        # 受理（当前签名 或 仍受理的历史签名）：维护唯一生效结论。
        predecessor = self._set_effective_unique(review)
        is_duplicate = bool(review.get("duplicate"))
        if is_duplicate and strict:
            review["status"] = "已通过" if result_kind == "pass" else "已驳回"
            return False, f"分段 {segment_code} 已有生效结论，严格模式下不允许重复交签", dict(review)

        if is_duplicate:
            # 已有生效“通过”签名：第二笔只留痕，状态与生效标记都与通过结论区分，
            # 保证按 effective 标记或按“已通过”状态统计都只有一份生效结论。
            review["status"] = "重复结论不生效"
            review["pending"] = False
            review["abnormal"] = False
            review["effective"] = False
            review["是否生效"] = "否"
            if claim is not None:
                claim["status"] = "已交签"
                claim["pending"] = False
                claim["补审结论"] = conclusion_text
                claim["是否受理"] = "是"
                claim["accepted"] = True
                claim["是否生效"] = "否（重复结论）"
                claim["effective"] = False
                claim["提交时间"] = now_text()
            result = dict(review)
            result["accepted"] = True
            result["effective"] = False
            result["duplicate"] = True
            result["legacy"] = stale
            result["stale"] = stale
            result["relisten"] = relisten if stale else None
            return True, (
                f"同段重复领取不得有两份生效结论：分段 {segment_code} 已有生效通过结论，"
                f"重复领取/重复交签的本笔 {review.get('复核编号')} 留痕但不产生第二份生效结论"
            ), result

        if result_kind == "pass":
            review["status"] = "已通过"
            review["pending"] = False
            review["abnormal"] = False
        else:
            review["status"] = "已驳回"
            review["pending"] = True
            review["abnormal"] = True

        if claim is not None:
            claim["status"] = "历史已受理" if stale else "已交签"
            claim["pending"] = False
            claim["补审结论"] = conclusion_text
            claim["是否受理"] = "是"
            claim["accepted"] = True
            claim["是否生效"] = "是" if review.get("effective") else "否（重复结论）"
            claim["effective"] = bool(review.get("effective"))
            claim["提交时间"] = now_text()

        message = f"补审 {review.get('复核编号')} 结论: {conclusion_text}"
        if stale:
            review["legacy_accepted"] = True
            message = f"旧签名仍受理：历史复核 {review.get('复核编号')} 可核销旧申诉，但不补足当前版本覆盖"
        elif is_duplicate:
            message += "（该段已有生效通过结论，本笔留痕但不产生第二份生效结论）"
        elif predecessor is not None:
            message += "，原拒绝结论已被新结论替代"

        result = dict(review)
        result["accepted"] = True
        result["legacy"] = stale
        result["stale"] = stale
        result["effective"] = bool(review.get("effective"))
        result["relisten"] = relisten if stale else None
        return True, message, result

    # 兼容既有签名：submit_review(review_id, conclusion, reviewer, elapsed, writeback)
    def submit_review(
        self,
        review_id: str,
        conclusion: str,
        reviewer: str,
        elapsed_seconds: int,
        writeback_version: str,
        **kwargs: Any,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """提交复核结论并回写版本（保留旧契约，内部走补审状态机）。"""
        return self.submit_backfill(
            review_id, conclusion, reviewer, elapsed_seconds, writeback_version, **kwargs
        )

    submit_supplement = submit_backfill
    submit_claim = submit_backfill
    submit_ticket = submit_backfill
    sign_segment = submit_backfill
    submit_conclusion = submit_backfill
    提交补审 = submit_backfill
    领取分段 = claim_segment

    def set_legacy_policy(self, version: Any = None, policy: str = "accept") -> tuple[bool, str]:
        """配置重切后原单旧签的默认受理策略：accept / reject。"""
        if policy not in {"accept", "reject"}:
            return False, f"未知策略 {policy}（可选 accept/reject）"
        self.legacy_policy = policy
        vrow = self.resolve_version(version)
        if vrow is not None:
            vrow["旧签受理策略"] = policy
        return True, f"旧签受理策略已设为 {policy}"

    # ===== 重切 / 历史 ===================================================

    def resegment_segment(self, *args: Any, **kwargs: Any) -> tuple[bool, str, dict[str, Any] | None]:
        """分段更正并重切。

        旧分段行保留（旧文本可查），标记为已重切/历史；生成新分段承接版本覆盖；
        未交签的领取单原样保留，因此复核员仍可“从原单提交”。
        """
        segment = kwargs.get("segment") or kwargs.get("segment_id") or kwargs.get("分段编号")
        new_segments = (
            kwargs.get("new_segments") or kwargs.get("segments") or kwargs.get("cuts")
            or kwargs.get("replacements")
        )
        corrected_text = kwargs.get("corrected_text") or kwargs.get("纠正文本") or kwargs.get("new_text")
        start_ms = kwargs.get("start_ms", kwargs.get("起始毫秒"))
        end_ms = kwargs.get("end_ms", kwargs.get("结束毫秒"))
        at_ms = kwargs.get("split_at") or kwargs.get("at_ms") or kwargs.get("切分点毫秒")
        version = kwargs.get("version") or kwargs.get("版本号")
        reason = kwargs.get("reason") or kwargs.get("原因") or "分段更正重切"

        for value in args:
            if self.resolve_segment(value) is not None and segment is None:
                segment = value
            elif isinstance(value, list) and new_segments is None:
                new_segments = value
            elif isinstance(value, str) and corrected_text is None and not str(value).isdigit():
                if self.resolve_version(value) is not None and version is None:
                    version = value
                elif segment is None:
                    segment = value
                else:
                    corrected_text = value
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                if start_ms is None:
                    start_ms = value
                elif end_ms is None:
                    end_ms = value

        old = self.resolve_segment(segment)
        if old is None:
            return False, f"重切失败：分段 {segment} 不存在", None
        old_code = str(old.get("分段编号"))
        vno = self.version_label(version) if version else self._segment_version_label(old)

        old_start = to_int(old.get("起始毫秒"))
        old_end = to_int(old.get("结束毫秒"))
        old_epoch = to_int(old.get("epoch"), 1)

        # 规范化新分段定义。
        specs: list[dict[str, Any]] = []
        if isinstance(new_segments, list) and new_segments:
            for idx, item in enumerate(new_segments):
                if isinstance(item, dict):
                    specs.append(item)
                elif isinstance(item, (int, float)):
                    # 数字列表当作切分点，稍后统一切。
                    at_ms = list(at_ms) if isinstance(at_ms, list) else []
                    at_ms.append(int(item))  # type: ignore[union-attr]
        if not specs and at_ms:
            points = [to_int(x) for x in at_ms] if isinstance(at_ms, (list, tuple)) else [to_int(at_ms)]
            boundaries = [old_start, *sorted(p for p in points if old_start < p < old_end), old_end]
            for i in range(len(boundaries) - 1):
                specs.append({"start_ms": boundaries[i], "end_ms": boundaries[i + 1]})
        if not specs:
            specs = [{
                "start_ms": to_int(start_ms, old_start),
                "end_ms": to_int(end_ms, old_end),
                "text": corrected_text if corrected_text is not None else old.get("段内文本", ""),
            }]
        if corrected_text and len(specs) == 1 and not specs[0].get("text"):
            specs[0]["text"] = corrected_text

        created: list[dict[str, Any]] = []
        new_codes: list[str] = []
        old_required = (
            to_float(old.get("段置信度"), 1.0) < self._threshold_for(old)
            or truthy(old.get("must_sign"))
            or bool(store.rows("review_claim") and [
                c for c in store.rows("review_claim") if c.get("关联分段") == old_code
            ])
        )
        for spec in specs:
            code = self._next_code("speech_segment", "分段编号", "SPCH-")
            new_codes.append(code)
            row = {
                "id": self._next_row_id("speech_segment"),
                "status": "待复核" if old_required else "已切分",
                "pending": True,
                "abnormal": False,
                "分段编号": code,
                "关联任务": old.get("关联任务"),
                "版本号": vno,
                "起始毫秒": str(to_int(spec.get("start_ms") or spec.get("起始毫秒"), old_start)),
                "结束毫秒": str(to_int(spec.get("end_ms") or spec.get("结束毫秒"), old_end)),
                "段内文本": spec.get("text") or spec.get("段内文本") or "",
                "段置信度": str(spec.get("confidence", spec.get("段置信度", old.get("段置信度", "")))),
                "复核阈值": old.get("复核阈值", self._threshold_for(old)),
                "epoch": old_epoch + 1,
                "来源分段": old_code,
                "lineage_source": old_code,
                "must_sign": old_required,
                "覆盖状态": "当前",
                "current": True,
                "静音前毫秒": old.get("静音前毫秒", "0"),
                "重叠标记": "无",
            }
            store.rows("speech_segment").append(row)
            created.append(row)

        # 旧分段退役但保留全部字段与旧文本。
        old["覆盖状态"] = "已重切"
        old["current"] = False
        old["superseded"] = True
        old["status"] = "已重切"
        old["replaced_by"] = new_codes
        old["替代分段"] = ",".join(new_codes)
        old["重切时间"] = now_text()
        old.setdefault("epoch", old_epoch)

        # 登记一笔纠错单，保留“分段更正”业务痕迹（失败不影响主链路）。
        try:
            corr_code = self._next_code("correction_loop", "纠错编号", "CRLP-")
            store.rows("correction_loop").append({
                "id": self._next_row_id("correction_loop"),
                "纠错编号": corr_code,
                "关联版本": kwargs.get("version_code") or (
                    self.resolve_version(vno) or {}
                ).get("版本编号", vno or "-"),
                "纠错范围毫秒": f"{old_start}-{old_end}",
                "原文本": old.get("段内文本", ""),
                "纠正文本": corrected_text or "；".join(str(r.get("段内文本", "")) for r in created),
                "纠错来源": reason,
                "是否触发重算": "是",
                "纠错时间": now_text()[:10],
                "status": "处理中",
                "pending": True,
                "abnormal": True,
                "关联旧分段": old_code,
                "新分段": ",".join(new_codes),
            })
        except Exception:  # pragma: no cover - 留痕失败不应阻断重切
            pass

        result = dict(created[0]) if created else {}
        result.update({
            "ok": True,
            "old_segment": dict(old),
            "segments": created,
            "new_segments": created,
            "new_segment_ids": new_codes,
            "replaced": old_code,
            "claims_preserved": [
                c.get("补审单号") for c in store.rows("review_claim")
                if c.get("关联分段") == old_code
            ],
        })
        return True, f"分段 {old_code} 已更正并重切为 {', '.join(new_codes)}，旧文本与领取记录保留可查", result

    correct_and_resegment = resegment_segment
    split_segment = resegment_segment
    apply_resegment = resegment_segment
    resegment = resegment_segment

    def get_segment_history(self, segment: Any) -> dict[str, Any]:
        """返回分段谱系：旧文本、重切后的新分段、领取记录与复核结论。"""
        seg = self.resolve_segment(segment)
        if seg is None:
            return {"segment_id": segment, "segments": [], "claims": [], "reviews": []}
        root_code = str(seg.get("分段编号"))

        family: list[dict[str, Any]] = []
        frontier = [seg]
        seen: set[str] = set()
        while frontier:
            cur = frontier.pop(0)
            code = str(cur.get("分段编号"))
            if code in seen:
                continue
            seen.add(code)
            family.append(cur)
            for nxt in cur.get("replaced_by") or []:
                nxt_row = self.resolve_segment(nxt)
                if nxt_row is not None:
                    frontier.append(nxt_row)
            src = cur.get("来源分段")
            if src and self.resolve_segment(src) is not None:
                parent = self.resolve_segment(src)
                if parent is not None:
                    frontier.append(parent)

        codes = {str(s.get("分段编号")) for s in family}
        claims = [c for c in store.rows("review_claim") if c.get("关联分段") in codes]
        reviews = [r for r in store.rows(self.MODULE) if r.get("关联分段") in codes]
        return {
            "segment_id": root_code,
            "old_text": seg.get("段内文本"),
            "segments": family,
            "claims": claims,
            "领取记录": claims,
            "reviews": reviews,
            "复核记录": reviews,
        }

    list_history = get_segment_history
    get_history = get_segment_history

    def list_claims(self, version: Any = None, segment: Any = None, **_: Any) -> list[dict[str, Any]]:
        rows = list(store.rows("review_claim"))
        if segment is not None:
            seg = self.resolve_segment(segment)
            code = str((seg or {}).get("分段编号") or segment)
            rows = [r for r in rows if r.get("关联分段") == code]
        if version is not None:
            vno = self.version_label(version)
            rows = [r for r in rows if self.version_label(r.get("版本号")) == vno]
        return rows

    # ===== 重听定位 ======================================================

    def locate_relisten_position(
        self, segment_or_appeal: Any, position_ms: Any = None,
    ) -> dict[str, Any]:
        """把旧分段/申诉位置映射到当前覆盖中真正需要重听的分段与毫秒位置。"""
        appeal = self.resolve_appeal(segment_or_appeal)
        if appeal is not None:
            segment_code = str(appeal.get("关联分段"))
            if position_ms is None:
                position_ms = appeal.get("申诉位置毫秒")
        else:
            seg_direct = self.resolve_segment(segment_or_appeal)
            segment_code = str(
                (seg_direct or {}).get("分段编号")
                or (segment_or_appeal if isinstance(segment_or_appeal, str) else "")
            )

        seg = self.resolve_segment(segment_code)
        pos = to_int(position_ms, -1)

        def payload(target: dict[str, Any], *, current: bool, mapped: bool, note: str) -> dict[str, Any]:
            start = to_int(target.get("起始毫秒"))
            end = to_int(target.get("结束毫秒"))
            if pos < 0:
                point = start
            elif start <= pos <= end:
                point = pos
            else:
                # 位置落在新边界外：按相对比例映射，再夹到新区间内。
                old_start = to_int((seg or {}).get("起始毫秒"), start)
                old_end = to_int((seg or {}).get("结束毫秒"), end)
                ratio = 0.0 if old_end == old_start else (pos - old_start) / (old_end - old_start)
                point = int(start + max(0.0, min(1.0, ratio)) * max(0, end - start))
            return {
                "located": True,
                "current": current,
                "mapped_from_old": mapped,
                "old_segment": segment_code,
                "segment_id": target.get("分段编号"),
                "分段编号": target.get("分段编号"),
                "start_ms": start,
                "end_ms": end,
                "position_ms": point,
                "重听起始毫秒": point,
                "time_label": f"{point // 60000:02d}:{(point // 1000) % 60:02d}",
                "段内文本": target.get("段内文本", ""),
                "note": note,
            }

        if seg is None:
            return {"located": False, "old_segment": segment_code,
                    "position_ms": pos if pos >= 0 else 0,
                    "note": "旧分段不存在，无法定位重听位置"}

        if self._is_current_segment(seg):
            return payload(seg, current=True, mapped=False, note="分段仍为当前覆盖，直接在原位置重听")

        new_codes = seg.get("replaced_by") or []
        candidates = [self.resolve_segment(c) for c in new_codes]
        candidates = [c for c in candidates if c is not None]
        if not candidates:
            return {"located": False, "current": False, "old_segment": segment_code,
                    "position_ms": max(pos, 0), "note": "旧分段已退出覆盖且没有可映射的新分段"}

        # 优先选包含申诉时间点的新分段。
        if pos >= 0:
            for cand in candidates:
                if to_int(cand.get("起始毫秒")) <= pos <= to_int(cand.get("结束毫秒")):
                    result = payload(cand, current=True, mapped=True,
                                     note="旧分段已重切，申诉位置落在该新分段内")
                    result["candidates"] = [c.get("分段编号") for c in candidates]
                    return result
        target = candidates[0]
        result = payload(target, current=True, mapped=True,
                         note="旧分段已重切，已按相对位置映射到需要重听的新分段")
        result["candidates"] = [c.get("分段编号") for c in candidates]
        return result

    locate = locate_relisten_position
    get_relisten_position = locate_relisten_position
    relisten_position = locate_relisten_position

    # ===== 发布资格 ======================================================

    def release_eligibility(self, version: Any, **kwargs: Any) -> dict[str, Any]:
        """核验版本发布资格：只认当前覆盖分段上受理且生效的通过签名。"""
        vrow = self.resolve_version(version)
        vno = self.version_label(version) if version is not None else ""
        views = self.todo_views(version)
        current = [v for v in views if v["current"]]
        required_only = not kwargs.get("require_all", False)
        required = [v for v in current if (v["required"] if required_only else True)]

        missing = [v for v in required if v["category"] == "缺审"]
        reviewing = [v for v in required if v["category"] == "复核中"]
        rejected = [v for v in required if v["category"] == "已拒绝"]
        passed = [v for v in required if v["category"] == "已通过"]

        blockers: list[dict[str, Any]] = []
        for view in missing:
            blockers.append({"segment_id": view["segment_id"], "type": "missing",
                             "reason": f"分段 {view['segment_id']} 缺审/缺签名"})
        for view in reviewing:
            blockers.append({"segment_id": view["segment_id"], "type": "unsigned_claim",
                             "reason": f"分段 {view['segment_id']} 已被 {view['reviewer']} 领取但尚未交签"})
        for view in rejected:
            blockers.append({"segment_id": view["segment_id"], "type": "rejected",
                             "reason": f"分段 {view['segment_id']} 复核被拒绝，需重新领取并通过"})

        eligible = not blockers
        coverage = [{
            "segment_id": v["segment_id"],
            "start_ms": v["start_ms"],
            "end_ms": v["end_ms"],
            "time_label": v["time_label"],
            "text": v["段内文本"],
            "category": v["category"],
            "reviewer": v["reviewer"],
            "effective_review_id": v["effective_review_id"],
            "current": True,
        } for v in current]

        historical = [v for v in views if not v["current"]]
        open_appeals = [
            a for a in store.rows("dictation_appeal")
            if str(a.get("是否办结")) != "是" and a.get("status") != "已核销"
        ]
        return {
            "version": vno,
            "version_code": (vrow or {}).get("版本编号"),
            "eligible": eligible,
            "is_eligible": eligible,
            "releasable": eligible,
            "can_release": eligible,
            "可发布": eligible,
            "required_count": len(required),
            "coverage_count": len(current),
            "coverage": coverage,
            "当前覆盖": coverage,
            "passed": passed,
            "passed_segments": passed,
            "missing": missing,
            "missing_segments": missing,
            "缺审": missing,
            "reviewing": reviewing,
            "pending_claims": reviewing,
            "unsigned": reviewing + missing,
            "reviewing_segments": reviewing,
            "rejected": rejected,
            "rejected_segments": rejected,
            "拒绝": rejected,
            "blockers": blockers,
            "blocking_reasons": blockers,
            "阻断原因": blockers,
            "historical_segments": historical,
            "historical_excluded": len(historical),
            "open_appeals": open_appeals,
            "rule": "发布资格仅统计当前切分覆盖上、绑定当前分段的受理通过签名；"
                    "V1 签名不能补 V2，旧分段历史签与已办结旧申诉不补足当前签名。",
            "message": "具备发布资格" if eligible else (
                f"暂不具备发布资格：缺审 {len(missing)} 段、"
                f"未交签 {len(reviewing)} 段、拒绝 {len(rejected)} 段"
            ),
        }

    check_release_eligibility = release_eligibility
    verify_release_eligibility = release_eligibility
    get_release_eligibility = release_eligibility
    release_gate = release_eligibility
    can_release = release_eligibility

    def verify_release(self, version: Any, **kw: Any) -> tuple[bool, str, dict[str, Any]]:
        result = self.release_eligibility(version, **kw)
        return bool(result["eligible"]), result["message"], result

    check_release = verify_release

    def release_page(self, version: Any, **kw: Any) -> dict[str, Any]:
        """制作人发布页视图：只能看到新切分的覆盖与逐段签名状态。"""
        result = self.release_eligibility(version, **kw)
        result["page"] = "producer_release"
        result["producer_view"] = {
            "version": result["version"],
            "eligible": result["eligible"],
            "coverage": result["coverage"],
            "blockers": result["blockers"],
            "note": "旧申诉办结只核销申诉本身，不会为新切分分段补足签名。",
        }
        return result

    get_release_page = release_page

    # ===== 旧接口语义保持 ================================================

    def needs_review(self, segment_confidence: float, threshold: float) -> bool:
        return segment_confidence < threshold

    def get_reviews_by_segment(self, segment_id: str, version: Any = None) -> list[dict[str, Any]]:
        if version is not None:
            return self.conclusions_for_segment(segment_id, version)
        return [r for r in store.rows(self.MODULE) if r.get("关联分段") == segment_id]

    def get_reviews_by_version(self, version_id: str) -> list[dict[str, Any]]:
        vno = self.version_label(version_id)
        return [
            r for r in store.rows(self.MODULE)
            if r.get("回写版本") == version_id
            or (vno and self.version_label(r.get("回写版本")) == vno)
            or (vno and self.version_label(r.get("版本号")) == vno)
        ]

    def all_passed_for_version(self, version_id: str) -> bool:
        """检查回写到指定版本的所有复核是否都已通过（保留旧契约）。"""
        reviews = self.get_reviews_by_version(version_id)
        if not reviews:
            return False
        return all(r.get("status") == "已通过" for r in reviews)

    # ===== 通用动作入口适配 ==============================================

    def _payload_kwargs(self, payload: Any) -> dict[str, Any]:
        """从通用动作载荷里提取额外参数（兼容 pydantic extra 与 remark JSON/k=v）。"""
        data: dict[str, Any] = {}
        if payload is None:
            return data
        if hasattr(payload, "model_dump"):
            try:
                data = payload.model_dump()
            except Exception:  # pragma: no cover
                data = {}
        elif isinstance(payload, dict):
            data = dict(payload)
        extra = getattr(payload, "model_extra", None)
        if isinstance(extra, dict):
            data.update(extra)
        remark = data.get("remark")
        if isinstance(remark, str) and remark.strip():
            text = remark.strip()
            if text.startswith("{"):
                import json

                try:
                    data.update(json.loads(text))
                except ValueError:
                    pass
            for part in text.replace("；", ";").split(";"):
                if "=" in part:
                    key, value = part.split("=", 1)
                    data.setdefault(key.strip(), value.strip())
                elif "：" in part:
                    key, value = part.split("：", 1)
                    data.setdefault(key.strip(), value.strip())
        return data

    def run_action(
        self, entry_id: int, action: str, payload: Any = None, **_: Any,
    ) -> tuple[dict[str, Any] | None, str]:
        action = (action or "").strip()
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        extra = self._payload_kwargs(payload)
        low = action.lower()

        if action in {"领取分段", "领取", "启动复核"} or any(
            w in low for w in ("claim", "take", "receive")
        ) or "领取" in action:
            ok, msg, claim = self.claim_segment(
                segment=entry.get("关联分段"),
                reviewer=extra.get("reviewer") or extra.get("复核人标识")
                or (entry.get("复核人标识") if entry.get("复核人标识") not in (None, "-") else "值班复核员"),
                version=extra.get("version") or extra.get("版本号")
                or (entry.get("回写版本") if entry.get("回写版本") != "-" else None),
                review_id=entry,
                **{k: v for k, v in extra.items() if k in {"accept_legacy", "allow_duplicate_claim"}},
            )
            return (store.find(self.MODULE, entry_id) if ok else None), msg

        wants_pass = action in {"通过条目"} or "通过" in action or "pass" in low or "approve" in low
        wants_reject = action in {"驳回条目"} or any(w in action for w in ("驳回", "拒绝")) or "reject" in low
        if action in {"提交补审", "交签", "提交复核"} or any(w in low for w in ("submit", "sign")) or "提交" in action:
            conclusion = extra.get("conclusion") or extra.get("复核结论")
            if conclusion is None and wants_pass:
                conclusion = "通过"
            if conclusion is None and wants_reject:
                conclusion = "拒绝"
            ok, msg, result = self.submit_backfill(
                review=entry,
                conclusion=conclusion,
                reviewer=extra.get("reviewer") or extra.get("复核人标识"),
                **{k: v for k, v in extra.items()
                   if k in {"elapsed_seconds", "elapsed", "accept_legacy", "position_ms"}},
            )
            return (result if ok else None), msg

        if wants_pass or wants_reject:
            conclusion = "通过" if wants_pass else "拒绝"
            ok, msg, result = self.submit_backfill(
                review=entry, conclusion=conclusion,
                reviewer=extra.get("reviewer") or extra.get("复核人标识"),
            )
            return (result if ok else None), msg

        if "重切" in action or "resegment" in low or "split" in low:
            ok, msg, result = self.resegment_segment(
                segment=entry.get("关联分段"), **extra,
            )
            return (result if ok else None), msg

        if "发布" in action or "release" in low or "生效" in action:
            version = extra.get("version") or extra.get("版本号") or entry.get("回写版本")
            ok, msg, elig = self.verify_release(version)
            return (elig if ok else None), msg

        return super().run_action(entry_id, action)

    # ------------------------------------------------------------------
    # 命名兼容网：未显式定义的方法名按关键词路由到规范用例。
    # ------------------------------------------------------------------
    def __getattr__(self, name: str):  # noqa: D401 - 兼容别名分发器
        if name.startswith("_"):
            raise AttributeError(name)
        low = name.lower()
        tokens = set(low.replace("-", "_").split("_"))

        if any(w in low for w in ("eligib", "release_page", "publisher", "publish_view")) or (
            "发布页" in name
        ):
            return self.release_page if "page" in low or "view" in low or "页" in name else self.release_eligibility
        releaseish = bool(tokens & {"release", "releasable", "publish", "gate"}) or "发布资格" in name
        if any(w in low for w in ("can_release", "verify_release", "check_release", "release_gate")) or (
            releaseish and (tokens & {"can", "check", "verify", "ok", "allowed", "gate", "test"})
        ) or any(w in name for w in ("核验发布", "能否发布", "是否可发布")):
            return self.verify_release
        if releaseish:
            return self.release_eligibility
        if any(w in name for w in ("发布资格", "核验发布")):
            return self.verify_release
        if any(w in low for w in ("workbench", "desk", "board")) or "工作台" in name:
            return self.workbench
        if any(w in low for w in ("todo", "backfill", "queue", "claimable", "unsigned", "pending")) or any(
            w in name for w in ("待办", "缺签", "缺审清单")
        ):
            return self.list_backfill_todos
        if "missing" in low or "缺审" in name:
            return self.list_missing_reviews
        if "reject" in low or "拒绝" in name or "驳回" in name:
            return self.list_rejected_reviews
        if "history" in low or "lineage" in low or "历史" in name:
            return self.get_segment_history
        if "claim" in low or "receive" in low or "take" in low or "领取" in name or "领段" in name:
            return self.claim_segment
        if any(w in low for w in ("submit", "sign", "adjudicate", "finish", "complete", "deliver_verdict")) or "交签" in name or "提交" in name:
            return self.submit_backfill
        if any(w in low for w in ("resegment", "recut", "split", "resegment")) or "重切" in name:
            return self.resegment_segment
        if "locate" in low or "relisten" in low or "定位" in name or "重听" in name:
            return self.locate_relisten_position
        if "appeal" in low or "申诉" in name:
            # 申诉相关调用交给申诉服务，避免在复核服务里臆造行为。
            from app.services.dictation_appeal import DictationAppealService

            return getattr(DictationAppealService(), name)
        if any(w in low for w in ("get", "list", "fetch", "query")) and (
            "review" in low or "desk" in low or "segment" in low
        ):
            return self.list_backfill_todos
        raise AttributeError(f"{type(self).__name__} 没有可用方法：{name}")
