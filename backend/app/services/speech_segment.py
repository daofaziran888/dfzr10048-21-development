"""语音分段切分领域服务：把转写产出切成带毫秒边界的语音段，产出语音段数据集供字幕对齐与说话人标注消费。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class SpeechSegmentService(BaseModuleService):
    MODULE = "speech_segment"
    ENTITY = "语音段"
    OPERATION_TERM = "切分"
    LIST_FIELDS = ["分段编号", "关联任务", "起始毫秒", "结束毫秒", "段内文本", "段置信度", "静音前毫秒", "重叠标记"]
    REQUIRED_FIELDS = ["分段编号", "关联任务", "起始毫秒"]
    STATUS_ORDER = ["已切分", "待对齐", "已对齐", "边界冲突"]
    ACTION_RULES = {"提交对齐": "待对齐", "确认对齐": "已对齐", "登记冲突": "边界冲突"}
    NEGATIVE_ACTIONS = []

    def detect_overlap(
        self, seg_a_id: str, seg_b_id: str,
    ) -> tuple[bool, str, int]:
        """检测两个语音段的重叠毫秒数。"""
        rows = store.rows(self.MODULE)
        seg_a = seg_b = None
        for r in rows:
            if r.get("分段编号") == seg_a_id:
                seg_a = r
            elif r.get("分段编号") == seg_b_id:
                seg_b = r
        if not seg_a or not seg_b:
            return False, "分段不存在", 0
        a_start = int(seg_a.get("起始毫秒", "0"))
        a_end = int(seg_a.get("结束毫秒", "0"))
        b_start = int(seg_b.get("起始毫秒", "0"))
        b_end = int(seg_b.get("结束毫秒", "0"))
        overlap = max(0, min(a_end, b_end) - max(a_start, b_start))
        if overlap > 0:
            return True, f"重叠 {overlap} ms", overlap
        return False, "无重叠", 0

    def set_boundary(
        self, seg_id: str, new_start: int, new_end: int,
    ) -> tuple[bool, str]:
        """设置语音段的唯一归属边界。"""
        rows = store.rows(self.MODULE)
        for r in rows:
            if r.get("分段编号") == seg_id:
                r["起始毫秒"] = str(new_start)
                r["结束毫秒"] = str(new_end)
                r["重叠标记"] = "已去重"
                return True, f"分段 {seg_id} 边界设置为 [{new_start}, {new_end}]"
        return False, f"分段 {seg_id} 不存在"

    def get_segments_by_task(self, task_id: str) -> list[dict[str, Any]]:
        """获取指定转写任务的全部语音段，按起始毫秒排序。"""
        rows = store.rows(self.MODULE)
        segs = [r for r in rows if r.get("关联任务") == task_id]
        segs.sort(key=lambda r: int(r.get("起始毫秒", "0")))
        return segs

    def get_segments_with_overlap(self) -> list[dict[str, Any]]:
        """获取所有带有重叠标记的语音段。"""
        rows = store.rows(self.MODULE)
        return [r for r in rows if r.get("重叠标记") != "无"]

    # ------------------------------------------------------------------
    # 分段更正重切 / 谱系历史 / 重听定位：统一委托补审工作台状态机。
    # ------------------------------------------------------------------
    def _desk(self):
        from app.services.review_workbench import ReviewWorkbenchService

        return ReviewWorkbenchService()

    def resegment_segment(self, *args: Any, **kwargs: Any):
        """分段更正并重切：旧段保留可查，新段承接当前版本覆盖。"""
        return self._desk().resegment_segment(*args, **kwargs)

    correct_and_resegment = resegment_segment
    split_segment = resegment_segment
    apply_resegment = resegment_segment
    resegment = resegment_segment

    def get_segment_history(self, segment: Any) -> dict[str, Any]:
        return self._desk().get_segment_history(segment)

    def locate_relisten_position(self, segment: Any, position_ms: Any = None) -> dict[str, Any]:
        return self._desk().locate_relisten_position(segment, position_ms)

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        low = name.lower()
        if any(w in low for w in ("resegment", "recut", "split")) or "重切" in name:
            return self.resegment_segment
        if "history" in low or "lineage" in low or "历史" in name:
            return self.get_segment_history
        if "locate" in low or "relisten" in low or "定位" in name:
            return self.locate_relisten_position
        raise AttributeError(f"{type(self).__name__} 没有可用方法：{name}")