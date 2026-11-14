"""Kalıcı değer kümeleri (veritabanı CHECK kısıtlarıyla aynı)."""
from enum import StrEnum


class Confidence(StrEnum):
    CONFIRMED = "CONFIRMED"
    STRONGLY_INFERRED = "STRONGLY_INFERRED"
    WEAKLY_INFERRED = "WEAKLY_INFERRED"
    UNKNOWN = "UNKNOWN"
    CONTRADICTORY = "CONTRADICTORY"


class Source(StrEnum):
    DOCUMENT = "document"
    USER = "user"
    AI_INFERENCE = "ai_inference"
    EXAMPLE_FIT = "example_fit"
    ASSESSMENT_FEEDBACK = "assessment_feedback"


TRUSTED_SOURCES = (Source.DOCUMENT, Source.USER)


class Validation(StrEnum):
    PENDING = "pending"
    VALIDATED = "validated"
    REJECTED = "rejected"


class QuestionStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    DONT_KNOW = "dont_know"
    LATER = "later"
    SKIPPED = "skipped"


class ResponseKind(StrEnum):
    CHOICE = "choice"      # seçeneklerden biri
    TEXT = "text"          # doğal dilde açıklama
    DONT_KNOW = "dont_know"
    LATER = "later"
    SKIP = "skip"


class AssessmentStatus(StrEnum):
    PROVISIONAL = "PROVISIONAL"
    VERIFIED = "VERIFIED"
