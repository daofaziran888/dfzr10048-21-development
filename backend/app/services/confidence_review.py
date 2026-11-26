"""置信复核校验领域服务：对低于阈值的语音段做人工复核并回写结论。

补审工作台（按版本筛选、按语音时间排序定位、领段、交签、重切历史、
发布资格核验）的完整状态机在 :class:`ReviewWorkbenchService` 中，
本类保留原有导入路径并作为置信复核模块的注册服务。
"""
from __future__ import annotations

from app.services.review_workbench import ReviewWorkbenchService


class ConfidenceReviewService(ReviewWorkbenchService):
    """置信复核模块服务；全部工作台能力由父类提供。"""


# 常见命名别名，方便不同调用习惯导入。
ConfidenceReviewWorkbenchService = ConfidenceReviewService
ReviewDeskService = ConfidenceReviewService
BackfillReviewService = ConfidenceReviewService
