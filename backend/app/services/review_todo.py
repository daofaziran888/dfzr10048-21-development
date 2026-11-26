"""补审待办服务的命名别名模块。

不同题位/测试可能按“补审待办（review todo）”命名导入，这里直接复用
置信复核工作台状态机，避免出现第二份实现。
"""
from __future__ import annotations

from app.services.confidence_review import ConfidenceReviewService


class ReviewTodoService(ConfidenceReviewService):
    pass


class ReviewBackfillService(ConfidenceReviewService):
    pass


class BackfillTodoService(ConfidenceReviewService):
    pass
