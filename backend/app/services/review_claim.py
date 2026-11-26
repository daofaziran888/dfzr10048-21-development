"""补审领取单通用资源服务。

领取/交签的业务状态机在 ``ReviewWorkbenchService``；本类一方面让
``review_claim`` 表接入通用查询/登记/动作资源路由，另一方面把工作台
用例方法委托给状态机，方便从“领取单服务”入口调用。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService


class ReviewClaimService(BaseModuleService):
    MODULE = "review_claim"
    ENTITY = "补审单"
    OPERATION_TERM = "领取"
    LIST_FIELDS = [
        "补审单号", "关联版本", "版本号", "关联分段", "分段epoch",
        "起始毫秒", "结束毫秒", "领取时文本", "复核人标识", "领取时间",
        "提交时间", "补审结论", "是否受理", "是否生效", "关联复核编号",
    ]
    REQUIRED_FIELDS = ["关联分段", "复核人标识"]
    STATUS_ORDER = ["待交签", "已交签", "历史已受理", "旧签不受理"]
    ACTION_RULES = {"提交补审": "已交签", "受理历史签": "历史已受理", "判定不受理": "旧签不受理"}
    NEGATIVE_ACTIONS = ["判定不受理"]

    def _desk(self):
        from app.services.review_workbench import ReviewWorkbenchService

        return ReviewWorkbenchService()

    # 显式委托常用用例，保证签名/文档可见。
    def claim_segment(self, *args: Any, **kwargs: Any):
        return self._desk().claim_segment(*args, **kwargs)

    def submit_backfill(self, *args: Any, **kwargs: Any):
        return self._desk().submit_backfill(*args, **kwargs)

    def list_claims(self, *args: Any, **kwargs: Any):
        return self._desk().list_claims(*args, **kwargs)

    def get_segment_history(self, *args: Any, **kwargs: Any):
        return self._desk().get_segment_history(*args, **kwargs)

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        # 未在通用服务上显式定义的工作台用例，全部委托状态机。
        return getattr(self._desk(), name)


class ClaimService(ReviewClaimService):
    pass
