"""İçe aktarma veri modeli. Ayrıştırma çıktısı saf veridir; veritabanına `service` yazar."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields

PARSER_VERSION = "1"

ERROR, WARNING, INFO = "error", "warning", "info"


class IngestError(Exception):
    """Belgenin hiç okunamadığı durumlar (desteklenmeyen biçim, bozuk dosya, …)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Locator:
    """Bir kaydın kaynaktaki yeri. Sayfa/satır/paragraf 1'den, karakter aralığı 0'dan başlar;
    `char_start`/`char_end` sayfa (PDF) ya da paragraf (DOCX) metni içindeki aralıktır."""
    page: int | None = None
    page_end: int | None = None
    line: int | None = None
    paragraph: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    sheet: str | None = None
    row: int | None = None
    column: str | None = None
    json_path: str | None = None
    bbox: tuple[float, float, float, float] | None = None

    def to_dict(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if v is not None}
        if "bbox" in d:
            d["bbox"] = [round(x, 2) for x in d["bbox"]]
        return d

    @classmethod
    def from_dict(cls, d: dict | None) -> Locator:
        d = dict(d or {})
        if "bbox" in d:
            d["bbox"] = tuple(d["bbox"])
        return cls(**{k: v for k, v in d.items() if k in {f.name for f in fields(cls)}})

    def describe(self) -> str:
        if self.json_path:
            return self.json_path
        if self.sheet is not None:
            if self.row is not None and self.column:
                return f"{self.sheet}!{self.column}{self.row}"
            return f"{self.sheet}, satır {self.row}" if self.row is not None else self.sheet
        parts = []
        if self.page is not None:
            same = self.page_end is None or self.page_end == self.page
            parts.append(f"sayfa {self.page}" if same else f"sayfa {self.page}–{self.page_end}")
        if self.paragraph is not None:
            parts.append(f"paragraf {self.paragraph}")
        if self.line is not None or (self.row is not None and not parts):
            parts.append(f"satır {self.line if self.line is not None else self.row}")
            if self.line is None and self.column:
                parts.append(f"sütun {self.column}")
        return ", ".join(parts) or "konum bilinmiyor"

    def span_to(self, other: Locator) -> Locator:
        """Bu konumdan `other`'a kadar uzanan konum (çok satırlı kayıtlar için)."""
        if self.page is None or other.page is None or other.page == self.page:
            end = other.char_end if other.page == self.page or other.page is None else None
            return Locator(page=self.page, line=self.line, paragraph=self.paragraph,
                           char_start=self.char_start,
                           char_end=end if end is not None else self.char_end,
                           sheet=self.sheet, row=self.row, column=self.column,
                           json_path=self.json_path, bbox=self.bbox)
        return Locator(page=self.page, page_end=other.page, line=self.line,
                       char_start=self.char_start, char_end=None, bbox=self.bbox)


@dataclass(frozen=True)
class RawUnit:
    index: int
    text: str
    locator: Locator
    cells: tuple[str, ...] | None = None   # tablo satırlarında hücreler (metin = JSON dizisi)


@dataclass
class ParsedOption:
    code: str
    text: str
    locator: Locator


@dataclass
class ParsedItem:
    seq: int
    number: int | None
    label: str | None
    text: str
    locator: Locator
    options: list[ParsedOption] = field(default_factory=list)
    raw_first: int | None = None
    raw_last: int | None = None


@dataclass
class AnswerKeyEntry:
    seq: int
    item_number: int
    value: str
    locator: Locator


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    locator: Locator | None = None
    item_seq: int | None = None
    item_number: int | None = None


@dataclass
class ParseResult:
    kind: str
    raw_units: list[RawUnit] = field(default_factory=list)
    items: list[ParsedItem] = field(default_factory=list)
    answer_keys: list[AnswerKeyEntry] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def count(self, severity: str) -> int:
        return sum(1 for i in self.issues if i.severity == severity)

    @property
    def has_errors(self) -> bool:
        return self.count(ERROR) > 0
