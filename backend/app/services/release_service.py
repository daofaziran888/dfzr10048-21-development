"""发布资格核验服务的命名别名模块，核心规则仍由补审工作台持有。"""
from __future__ import annotations

from typing import Any

from app.services.review_workbench import ReviewWorkbenchService


class ReleaseEligibilityService(ReviewWorkbenchService):
    def check(self, version: Any, **kwargs: Any):
        return self.verify_release(version, **kwargs)


class ReviewReleaseGate(ReviewWorkbenchService):
    def evaluate(self, version: Any, **kwargs: Any) -> dict[str, Any]:
        return self.release_eligibility(version, **kwargs)
