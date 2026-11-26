"""补审工作台服务的命名别名模块。"""
from __future__ import annotations

from app.services.review_workbench import ReviewWorkbenchService


class ReviewDeskService(ReviewWorkbenchService):
    pass
