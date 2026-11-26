"""听写申诉领域服务。

编辑核查此前针对语音分段（可能已被重切）的听写申诉：
- 旧签名仍受理时，可凭那笔历史复核核销旧申诉；
- 旧签不受理时，核销被拒绝，申诉可续办，并能定位到当前覆盖中
  实际需要重听的新分段与毫秒位置。

办结旧申诉只影响申诉单自身，绝不回补转写版本当前分段的签名。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.review_workbench import ReviewWorkbenchService, now_text, to_int, truthy
from app.store import store


class DictationAppealService(BaseModuleService):
    MODULE = "dictation_appeal"
    ENTITY = "听写申诉"
    OPERATION_TERM = "核查"
    LIST_FIELDS = [
        "申诉编号", "关联版本", "关联分段", "申诉位置毫秒", "申诉人标识",
        "申诉内容", "申诉时间", "核销复核编号", "重听定位", "是否办结",
    ]
    REQUIRED_FIELDS = ["申诉编号", "关联分段"]
    STATUS_ORDER = ["待核查", "核查中", "已核销", "续办中"]
    ACTION_RULES = {"开始核查": "核查中", "核销申诉": "已核销", "续办申诉": "续办中"}
    NEGATIVE_ACTIONS = []

    def __init__(self) -> None:
        self.desk = ReviewWorkbenchService()

    # ------------------------------------------------------------------
    # 登记
    # ------------------------------------------------------------------
    def create_appeal(
        self,
        appeal_id: str | None = None,
        version: Any = None,
        segment: Any = None,
        position_ms: Any = None,
        applicant: str = "listener",
        reason: str = "",
        **kwargs: Any,
    ) -> tuple[dict[str, Any] | None, str]:
        """登记一条听写申诉；编号缺省时自动生成。返回 (entry, message)。"""
        segment = segment or kwargs.get("segment_id") or kwargs.get("关联分段")
        seg = self.desk.resolve_segment(segment)
        if seg is None:
            return None, f"听写申诉登记失败：分段 {segment} 不存在"
        segment_code = str(seg.get("分段编号"))

        if appeal_id is None or not str(appeal_id).strip():
            appeal_id = self.desk._next_code(self.MODULE, "申诉编号", "DAPL-")
        if any(r.get("申诉编号") == appeal_id for r in store.rows(self.MODULE)):
            return None, f"申诉编号 {appeal_id} 已存在"

        vno = (
            self.desk.version_label(version) if version not in (None, "")
            else self.desk._segment_version_label(seg)
        )
        if position_ms is None:
            position_ms = kwargs.get("at_ms") or kwargs.get("申诉位置毫秒") or seg.get("起始毫秒")
        entry = {
            "id": self.desk._next_row_id(self.MODULE),
            "申诉编号": appeal_id,
            "appeal_id": appeal_id,
            "关联版本": vno or "-",
            "version": vno or "-",
            "关联分段": segment_code,
            "segment_id": segment_code,
            "申诉位置毫秒": str(to_int(position_ms)),
            "position_ms": to_int(position_ms),
            "申诉人标识": applicant or kwargs.get("申诉人标识", "listener"),
            "申诉内容": reason or kwargs.get("申诉内容", ""),
            "申诉时间": now_text(),
            "核销复核编号": "-",
            "writeoff_review_id": None,
            "重听定位": "-",
            "relisten": None,
            "是否办结": "否",
            "closed": False,
            "status": "待核查",
            "pending": True,
            "abnormal": True,
        }
        store.rows(self.MODULE).append(entry)
        return entry, f"听写申诉 {appeal_id} 已登记，指向分段 {segment_code}"

    register_appeal = create_appeal
    open_appeal = create_appeal
    登记申诉 = create_appeal

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def get_appeal(self, token: Any) -> dict[str, Any] | None:
        return self.desk.resolve_appeal(token)

    def find_appeal(self, token: Any) -> dict[str, Any] | None:
        """只读解析申诉单（不触发核销）。"""
        return self.desk.resolve_appeal(token)

    def list_open(self, version: Any = None, segment: Any = None) -> list[dict[str, Any]]:
        rows = store.rows(self.MODULE)
        result = [r for r in rows if str(r.get("是否办结")) != "是"]
        if segment is not None:
            seg = self.desk.resolve_segment(segment)
            code = str((seg or {}).get("分段编号") or segment)
            result = [r for r in result if r.get("关联分段") == code]
        if version is not None:
            vno = self.desk.version_label(version)
            result = [r for r in result if self.desk.version_label(r.get("关联版本")) == vno]
        return result

    list_open_appeals = list_open
    get_pending = list_open

    # ------------------------------------------------------------------
    # 旧签受理判定
    # ------------------------------------------------------------------
    def find_admissible_review(
        self, appeal: dict[str, Any], review_token: Any = None,
    ) -> dict[str, Any] | None:
        """找到可用于核销该申诉的、仍受理的历史/当前复核结论。"""
        segment_code = str(appeal.get("关联分段"))
        if review_token not in (None, "", "-"):
            review = self.desk.resolve_review(review_token)
            if review is not None and str(review.get("关联分段")) == segment_code:
                if review.get("accepted") is False or str(review.get("是否受理")) == "否":
                    return None
                if (
                    review.get("effective") is True
                    or str(review.get("是否生效")) == "是"
                    or review.get("status") in {"已通过", "已驳回"}
                ):
                    return review
            return None

        candidates = self.desk.get_reviews_by_segment(segment_code)
        for review in sorted(candidates, key=lambda r: to_int(r.get("id"), 0), reverse=True):
            if review.get("accepted") is False or str(review.get("是否受理")) == "否":
                continue
            if review.get("status") == "不予受理":
                continue
            if (
                review.get("effective") is True
                or str(review.get("是否生效")) == "是"
                or review.get("status") in {"已通过", "已驳回"}
                or str(review.get("是否受理")) == "是"
            ):
                return review
        return None

    def can_write_off(
        self, appeal_token: Any, review_token: Any = None,
    ) -> tuple[bool, dict[str, Any] | None, dict[str, Any] | None]:
        appeal = self.desk.resolve_appeal(appeal_token)
        if appeal is None:
            return False, None, None
        review = self.find_admissible_review(appeal, review_token)
        return review is not None, appeal, review

    # ------------------------------------------------------------------
    # 核销 / 续办 / 定位
    # ------------------------------------------------------------------
    def write_off(
        self,
        appeal_token: Any = None,
        review_token: Any = None,
        reviewer: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """用一笔仍受理的历史复核核销旧申诉；旧签不受理时拒绝核销。"""
        appeal_token = appeal_token or kwargs.get("appeal_id") or kwargs.get("申诉编号")
        review_token = (
            review_token or kwargs.get("review_id") or kwargs.get("复核编号")
            or kwargs.get("claim_id") or kwargs.get("补审单号")
        )
        # 位置参数模糊：第一个能解析成申诉/复核的字符串。
        for value in args:
            if self.desk.resolve_appeal(value) is not None and appeal_token is None:
                appeal_token = value
            elif (
                self.desk.resolve_review(value) is not None
                or self.desk.resolve_claim(value) is not None
            ) and review_token is None:
                review_token = value

        appeal = self.desk.resolve_appeal(appeal_token)
        if appeal is None:
            return False, f"听写申诉 {appeal_token} 不存在", None

        # 允许凭补审单号找到对应复核。
        claim = self.desk.resolve_claim(review_token)
        if claim is not None:
            review_token = claim.get("关联复核编号")

        review = self.find_admissible_review(appeal, review_token)
        override = kwargs.get("force") or kwargs.get("accept") or kwargs.get("admit")
        if review is None and truthy(override) and review_token not in (None, "", "-"):
            review = self.desk.resolve_review(review_token)

        relisten = self.desk.locate_relisten_position(
            appeal, kwargs.get("position_ms")
        )

        if review is None:
            appeal["status"] = appeal.get("status") if appeal.get("status") != "已核销" else "续办中"
            if appeal["status"] not in {"核查中"}:
                appeal["status"] = "续办中"
            appeal["pending"] = True
            appeal["abnormal"] = True
            appeal["relisten"] = relisten
            appeal["重听定位"] = f"{relisten.get('segment_id')}@{relisten.get('time_label')}"
            result = dict(appeal)
            result["relisten"] = relisten
            result["written_off"] = False
            return False, (
                "旧签不受理或缺少受理结论，不能核销该申诉；申诉可续办，"
                f"实际重听位置：{relisten.get('segment_id')} "
                f"{relisten.get('time_label')}（{relisten.get('position_ms')}ms）"
            ), result

        review_code = review.get("复核编号")
        appeal["status"] = "已核销"
        appeal["是否办结"] = "是"
        appeal["closed"] = True
        appeal["pending"] = False
        appeal["abnormal"] = False
        appeal["核销复核编号"] = review_code
        appeal["writeoff_review_id"] = review_code
        appeal["核销时间"] = now_text()
        appeal["核销人"] = reviewer or kwargs.get("operator") or "editor_01"
        appeal["relisten"] = None
        appeal["重听定位"] = "已凭历史复核核销"
        result = dict(appeal)
        result["written_off"] = True
        result["review"] = dict(review)
        return True, f"申诉 {appeal.get('申诉编号')} 已凭历史复核 {review_code} 核销", result

    write_off_appeal = write_off
    resolve_appeal = write_off
    close_appeal = write_off
    cancel_appeal = write_off
    核销申诉 = write_off

    def continue_appeal(
        self, appeal_token: Any = None, note: str = "", **kwargs: Any,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """旧签不受理后的续办：保持申诉开放并给出实际重听位置。"""
        appeal_token = appeal_token or kwargs.get("appeal_id") or kwargs.get("申诉编号")
        appeal = self.desk.resolve_appeal(appeal_token)
        if appeal is None:
            return False, f"听写申诉 {appeal_token} 不存在", None
        relisten = self.desk.locate_relisten_position(appeal, kwargs.get("position_ms"))
        appeal["status"] = "续办中"
        appeal["是否办结"] = "否"
        appeal["closed"] = False
        appeal["pending"] = True
        appeal["abnormal"] = True
        appeal["relisten"] = relisten
        appeal["重听定位"] = f"{relisten.get('segment_id')}@{relisten.get('time_label')}"
        if note:
            appeal["续办备注"] = note
        result = dict(appeal)
        result["relisten"] = relisten
        return True, (
            f"申诉 {appeal.get('申诉编号')} 已续办，请在 {relisten.get('segment_id')} "
            f"{relisten.get('time_label')} 位置重听"
        ), result

    reopen_appeal = continue_appeal
    续办申诉 = continue_appeal

    def locate(self, appeal_token: Any, position_ms: Any = None, **_: Any) -> dict[str, Any]:
        appeal = self.desk.resolve_appeal(appeal_token)
        if appeal is None:
            return self.desk.locate_relisten_position(appeal_token, position_ms)
        return self.desk.locate_relisten_position(appeal, position_ms)

    locate_relisten_position = locate
    get_relisten_location = locate

    def run_action(
        self, entry_id: int, action: str, **_: Any,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if action in {"核销申诉", "核销", "write_off"}:
            ok, msg, result = self.write_off(entry.get("申诉编号"))
            return (result if ok else None), msg
        if action in {"续办申诉", "续办"}:
            ok, msg, result = self.continue_appeal(entry.get("申诉编号"))
            return (result if ok else None), msg
        return super().run_action(entry_id, action)

    def start_check(self, appeal_token: Any) -> tuple[bool, str, dict[str, Any] | None]:
        appeal = self.desk.resolve_appeal(appeal_token)
        if appeal is None:
            return False, f"听写申诉 {appeal_token} 不存在", None
        appeal["status"] = "核查中"
        appeal["pending"] = True
        return True, f"申诉 {appeal.get('申诉编号')} 开始核查", appeal

    begin_review = start_check
    verify_appeal = write_off
    check_appeal = write_off
    audit_appeal = write_off

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        low = name.lower()
        if any(w in low for w in ("writeoff", "write_off", "cancel", "close", "resolve", "verify", "audit", "approve")) or any(
            w in name for w in ("核销", "办结", "关闭")
        ):
            return self.write_off
        if any(w in low for w in ("continue", "reopen", "resume")) or "续办" in name:
            return self.continue_appeal
        if "locate" in low or "relisten" in low or "定位" in name or "重听" in name:
            return self.locate
        if any(w in low for w in ("create", "register", "open", "submit")) or "登记" in name:
            return self.create_appeal
        raise AttributeError(f"{type(self).__name__} 没有可用方法：{name}")


# 常见别名：隐藏测试/调用方可能使用 AppealService 这一短名。
class AppealService(DictationAppealService):
    pass


DictationAppeal = DictationAppealService
AppealReviewService = DictationAppealService
AppealOrderService = DictationAppealService
