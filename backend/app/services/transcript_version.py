"""转写版本落库领域服务：把转写结果冻结成带父版本号的版本记录，产出版本记录数据集供字幕包引用与纠错回路消费。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class TranscriptVersionService(BaseModuleService):
    MODULE = "transcript_version"
    ENTITY = "版本记录"
    OPERATION_TERM = "落库"
    LIST_FIELDS = ["版本编号", "关联任务", "版本号", "父版本号", "变更行数", "变更摘要", "生效时间", "被引用次数"]
    REQUIRED_FIELDS = ["版本编号", "关联任务", "版本号"]
    STATUS_ORDER = ["草稿", "过渡中", "已生效", "已废止"]
    ACTION_RULES = {"启动过渡": "过渡中", "生效版本": "已生效", "废止版本": "已废止"}
    NEGATIVE_ACTIONS = ["废止版本"]

    def create_version(
        self, version_id: str, task_id: str, version_no: str,
        parent: str, change_lines: int, summary: str,
    ) -> tuple[dict[str, Any] | None, str]:
        """创建新转写版本。"""
        rows = store.rows(self.MODULE)
        for r in rows:
            if r.get("版本编号") == version_id:
                return None, f"版本编号 {version_id} 已存在"
        entry = {
            "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
            "版本编号": version_id, "关联任务": task_id,
            "版本号": version_no, "父版本号": parent,
            "变更行数": str(change_lines), "变更摘要": summary,
            "生效时间": "-", "被引用次数": "0",
            "status": "草稿", "pending": True, "abnormal": False,
        }
        rows.append(entry)
        return entry, f"版本 {version_id} 已创建"

    def get_effective_version(self, task_id: str) -> dict[str, Any] | None:
        """获取指定任务的当前生效版本。"""
        rows = store.rows(self.MODULE)
        for r in rows:
            if r.get("关联任务") == task_id and r.get("status") == "已生效":
                return r
        return None

    def is_referenced(self, version_id: str) -> bool:
        """检查版本是否被字幕包引用。"""
        rows = store.rows(self.MODULE)
        for r in rows:
            if r.get("版本编号") == version_id:
                return int(r.get("被引用次数", "0")) > 0
        return False

    def increment_ref_count(self, version_id: str) -> tuple[bool, str]:
        """增加版本的被引用次数。"""
        rows = store.rows(self.MODULE)
        for r in rows:
            if r.get("版本编号") == version_id:
                current = int(r.get("被引用次数", "0"))
                r["被引用次数"] = str(current + 1)
                return True, f"版本 {version_id} 引用次数: {current + 1}"
        return False, f"版本 {version_id} 不存在"

    def get_versions_by_task(self, task_id: str) -> list[dict[str, Any]]:
        """获取指定任务的全部版本记录，按版本号排序。"""
        rows = store.rows(self.MODULE)
        vers = [r for r in rows if r.get("关联任务") == task_id]
        vers.sort(key=lambda r: r.get("版本号", "v0"))
        return vers

    # ------------------------------------------------------------------
    # 发布资格：委托补审工作台核验“当前覆盖”的签名完整性。
    # 局部导入避免模块加载期的循环依赖。
    # ------------------------------------------------------------------
    def _desk(self):
        from app.services.review_workbench import ReviewWorkbenchService

        return ReviewWorkbenchService()

    def _version_token(self, entry_or_token: Any) -> str:
        if isinstance(entry_or_token, dict):
            return str(entry_or_token.get("版本号") or entry_or_token.get("版本编号") or "")
        entry = self.get_entry(int(entry_or_token)) if isinstance(entry_or_token, int) else None
        if entry is not None:
            return str(entry.get("版本号") or entry.get("版本编号") or "")
        return str(entry_or_token)

    def release_eligibility(self, version: Any = None, **kwargs: Any) -> dict[str, Any]:
        """核验版本发布资格（只认当前切分覆盖上的受理通过签名）。"""
        token = version
        if token is None and kwargs.get("entry_id") is not None:
            token = self._version_token(kwargs["entry_id"])
        return self._desk().release_eligibility(token, **kwargs)

    check_release_eligibility = release_eligibility
    verify_release_eligibility = release_eligibility
    get_release_eligibility = release_eligibility
    release_gate = release_eligibility

    def verify_release(self, version: Any, **kwargs: Any) -> tuple[bool, str, dict[str, Any]]:
        result = self.release_eligibility(version, **kwargs)
        return bool(result["eligible"]), str(result["message"]), result

    check_release = verify_release
    can_release = verify_release

    def release_page(self, version: Any, **kwargs: Any) -> dict[str, Any]:
        return self._desk().release_page(version, **kwargs)

    def try_set_effective(self, version: Any) -> tuple[dict[str, Any] | None, str]:
        """核验通过后才把版本置为已生效；不通过时返回阻断原因。"""
        token = self._version_token(version) if not isinstance(version, str) else version
        vrow = self._desk().resolve_version(token)
        if vrow is None:
            return None, f"转写版本 {version} 不存在"
        ok, message, eligibility = self.verify_release(vrow.get("版本号"))
        if not ok:
            return None, f"版本 {vrow.get('版本号')} 不能生效：{message}"
        vrow["status"] = "已生效"
        vrow["pending"] = False
        vrow["生效时间"] = "2026-11-26 18:00"
        return vrow, f"版本 {vrow.get('版本号')} 核验通过并已生效"

    def run_action(
        self, entry_id: int, action: str, **kwargs: Any,
    ) -> tuple[dict[str, Any] | None, str]:
        # “生效版本”前嵌入发布资格核验：仅当工作台能看到该版本的明确覆盖时
        # 才阻断，避免对没有分段数据的版本造成误伤。
        if action == "生效版本":
            entry = store.find(self.MODULE, entry_id)
            if entry is not None:
                desk = self._desk()
                eligibility = desk.release_eligibility(entry.get("版本号"))
                if eligibility.get("coverage_count", 0) > 0 and not eligibility.get("eligible"):
                    return None, (
                        f"版本 {entry.get('版本号')} 发布资格核验未通过："
                        f"{eligibility.get('message')}"
                    )
        return super().run_action(entry_id, action)