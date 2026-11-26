"""补审服务的补充命名别名（supplement/backfill 两种英译）。"""
from __future__ import annotations

from app.services.confidence_review import ConfidenceReviewService


class SupplementReviewService(ConfidenceReviewService):
    pass


class BackfillReviewService(ConfidenceReviewService):
    pass


class MissingSignatureService(ConfidenceReviewService):
    pass
