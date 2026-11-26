"""领域服务注册表：通用资源路由按模块 key 分派到独立业务服务。"""
from __future__ import annotations

from app.services.base import BaseModuleService
from app.services.media_source import MediaSourceService
from app.services.capture_channel import CaptureChannelService
from app.services.media_shard import MediaShardService
from app.services.transcode_job import TranscodeJobService
from app.services.transcribe_task import TranscribeTaskService
from app.services.speech_segment import SpeechSegmentService
from app.services.speaker_label import SpeakerLabelService
from app.services.subtitle_align import SubtitleAlignService
from app.services.timeline_mark import TimelineMarkService
from app.services.term_extract import TermExtractService
from app.services.transcript_version import TranscriptVersionService
from app.services.confidence_review import ConfidenceReviewService
from app.services.review_workbench import ReviewWorkbenchService
from app.services.dictation_appeal import DictationAppealService, AppealService
from app.services.review_claim import ReviewClaimService
from app.services.supplement_review import (
    BackfillReviewService as BackfillReviewSvc,
    SupplementReviewService,
)
from app.services.release_service import ReleaseEligibilityService
from app.services.review_todo import ReviewTodoService
from app.services.correction_loop import CorrectionLoopService
from app.services.subtitle_pack import SubtitlePackService
from app.services.media_index import MediaIndexService
from app.services.shard_watermark import ShardWatermarkService
from app.services.fail_redeliver import FailRedeliverService
from app.services.transcribe_quota import TranscribeQuotaService
from app.services.sample_qc import SampleQcService
from app.services.delivery_receipt import DeliveryReceiptService
from app.services.archive_shelf import ArchiveShelfService
from app.services.media_audit import MediaAuditService

SERVICES: dict[str, BaseModuleService] = {
    "media_source": MediaSourceService(),
    "capture_channel": CaptureChannelService(),
    "media_shard": MediaShardService(),
    "transcode_job": TranscodeJobService(),
    "transcribe_task": TranscribeTaskService(),
    "speech_segment": SpeechSegmentService(),
    "speaker_label": SpeakerLabelService(),
    "subtitle_align": SubtitleAlignService(),
    "timeline_mark": TimelineMarkService(),
    "term_extract": TermExtractService(),
    "transcript_version": TranscriptVersionService(),
    "confidence_review": ConfidenceReviewService(),
    "review_workbench": ReviewWorkbenchService(),
    "review_desk": ReviewWorkbenchService(),
    "dictation_appeal": DictationAppealService(),
    "dictation_appeals": DictationAppealService(),
    "appeal": AppealService(),
    "appeals": AppealService(),
    "review_claim": ReviewClaimService(),
    "supplement_review": SupplementReviewService(),
    "backfill_review": BackfillReviewSvc(),
    "review_todo": ReviewTodoService(),
    "release_eligibility": ReleaseEligibilityService(),
    "correction_loop": CorrectionLoopService(),
    "subtitle_pack": SubtitlePackService(),
    "media_index": MediaIndexService(),
    "shard_watermark": ShardWatermarkService(),
    "fail_redeliver": FailRedeliverService(),
    "transcribe_quota": TranscribeQuotaService(),
    "sample_qc": SampleQcService(),
    "delivery_receipt": DeliveryReceiptService(),
    "archive_shelf": ArchiveShelfService(),
    "media_audit": MediaAuditService(),
}


def service_for(module: str) -> BaseModuleService | None:
    return SERVICES.get(module)
