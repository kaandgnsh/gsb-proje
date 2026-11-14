"""CSV/XLSX cevap dosyalarının kaynak konumlu, deterministik ön doğrulaması."""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
import unicodedata
import zipfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

MAX_BYTES = 50 * 1024 * 1024
MAX_ROWS = 10000
MAX_COLUMNS = 4096
ROLES = {
    "participant_code": {
        "katılımcı",
        "katilimci",
        "participant",
        "id",
        "participant_id",
        "participant_code",
        "code",
        "kimlik",
        "katılımcı_kodu",
        "katilimci_kodu",
    },
    "alias": {"alias", "takma_ad", "takma ad"},
    "full_name": {"full_name", "ad", "ad_soyad"},
    "email": {"email", "e-posta", "eposta"},
    "phone": {"phone", "telefon"},
}


@dataclass
class Issue:
    file: str
    sheet: str | None
    row: int
    participant: str | None
    question: int | None
    column: str | None
    message: str
    stage: str = "validation"

    def text(self):
        return f"{self.file} | sayfa={self.sheet or 'CSV'} | satır={self.row} | katılımcı={self.participant or '?'} | soru={self.question or '-'} | sütun={self.column or '-'}: {self.message}"


class ResponseFileError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__("\n".join(i.text() for i in issues))


@dataclass
class InputRow:
    sheet: str | None
    row: int
    identity: dict
    answers: list
    errors: list[dict] = field(default_factory=list)


@dataclass
class ResponseFile:
    file_name: str
    sha256: str
    mapping: dict
    rows: list[InputRow]


def _text(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        raise ValueError("Doğru/yanlış hücresi metin kodu değildir")  # noqa: TRY004
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Sonlu olmayan hücre değeri")
        if value.is_integer():
            value = int(value)
    if not isinstance(value, (str, int, float)):
        raise ValueError("Metin veya sayısal kod gerekli")  # noqa: TRY004
    return unicodedata.normalize("NFC", str(value)).strip()


def identity_values(identity):
    if not isinstance(identity, dict) or set(identity) - set(ROLES):
        raise ValueError("Katılımcı bilgisi geçersiz")
    out = {k: _text(v) for k, v in identity.items()}
    if not (out.get("participant_code") or out.get("alias")):
        raise ValueError("Katılımcı kimliği veya takma ad gerekli")
    if any(len(v) > 256 for v in out.values()):
        raise ValueError("Katılımcı alanı 256 karakterden uzun olamaz")
    return out


def normalize_responses(responses):
    """Tekil ve dosya girdisinde boşluk/boş hücre semantiği aynıdır; tekrarlar korunur."""
    from collections.abc import Mapping

    pairs = list(responses.items()) if isinstance(responses, Mapping) else responses
    if not isinstance(pairs, (list, tuple)):
        return pairs
    out = []
    for pair in pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            return pairs  # M7, zarf biçimini kendi Türkçe hatasıyla reddeder.
        n, value = pair
        try:
            out.append([n, _text(value) or None])
        except ValueError as e:
            raise ValueError(f"{n}. soru: {e}") from None
    return out


def _label(v):
    return _text(v).casefold().replace("\u0307", "")


def _mapping(headers, items, explicit, file, sheet):
    def fail(message, col=None):
        raise ResponseFileError([Issue(file, sheet, 1, None, None, col, message)])

    try:
        names = [_text(h) for h in headers]
    except ValueError as e:
        fail(str(e))
    if len(names) > MAX_COLUMNS:
        fail("Sütun sayısı 4096 sınırını aşıyor")
    if not names or any(not n for n in names):
        fail("Boş sütun başlığı")
    labels = [_label(n) for n in names]
    if len(set(labels)) != len(labels):
        fail("Yinelenen veya belirsiz sütun başlıkları")
    roles = {}
    questions = {}
    used = set()
    if explicit is not None:
        if (
            not isinstance(explicit, dict)
            or set(explicit) - set(ROLES) - {"questions"}
            or not isinstance(explicit.get("questions"), dict)
        ):
            fail("Sütun eşlemesi rol alanları ve questions nesnesi içermeli")

        def column(name):
            if not isinstance(name, str) or name not in names:
                fail("Eşlenen sütun bulunamadı", str(name))
            index = names.index(name)
            if index in used:
                fail("Bir sütun birden fazla alana eşlenmiş", name)
            used.add(index)
            return index

        for role in ROLES:
            if role in explicit:
                roles[role] = column(explicit[role])
        for number, name in explicit["questions"].items():
            if not isinstance(number, str) or not number.isdigit() or int(number) < 1:
                fail("Soru eşlemesi pozitif madde numarası olmalı")
            n = int(number)
            if n in questions:
                fail("Bir soruya birden fazla sütun eşlenmiş")
            questions[n] = column(name)
    else:
        for index, label in enumerate(labels):
            role = next((r for r, aliases in ROLES.items() if label in aliases), None)
            if role:
                if role in roles:
                    fail(
                        "Aynı katılımcı alanına birden fazla sütun eşleniyor",
                        names[index],
                    )
                roles[role] = index
            else:
                hit = re.fullmatch(r"(?:q[ _-]?|soru[ _-]?|s[ _-]?)?(\d+)", label)
                if not hit or int(hit[1]) < 1:
                    fail("Sütun eşleşmesi belirsiz; açık eşleme verin", names[index])
                n = int(hit[1])
                if n in questions:
                    fail("Aynı soruya birden fazla sütun eşleniyor", names[index])
                questions[n] = index
            used.add(index)
    if not ({"participant_code", "alias"} & roles.keys()):
        fail("Katılımcı kimliği veya takma ad sütunu bulunamadı")
    if set(questions) != set(items):
        missing = sorted(set(items) - set(questions))
        extra = sorted(set(questions) - set(items))
        fail(f"Soru sütunları envanterle uyuşmuyor; eksik={missing}, tanımsız={extra}")
    return (
        names,
        roles,
        questions,
        [names[j] for j in range(len(names)) if j not in used],
    )


def _tables(data, suffix, sheets, delimiter):
    if suffix == ".csv":
        if sheets:
            raise ValueError("CSV için sayfa seçilemez")
        text = data.decode("utf-8-sig")
        if delimiter is not None and delimiter not in (",", ";", "\t"):
            raise ValueError("Ayraç virgül, noktalı virgül veya sekme olmalı")
        if delimiter is None:
            try:
                delimiter = (
                    csv.Sniffer().sniff(text[:65536], delimiters=",;\t").delimiter
                )
            except csv.Error:
                delimiter = ","
        yield (
            None,
            (
                (n, list(row), set())
                for n, row in enumerate(
                    csv.reader(io.StringIO(text), delimiter=delimiter, strict=True), 1
                )
            ),
        )
        return
    from openpyxl import load_workbook
    from openpyxl.utils.exceptions import InvalidFileException

    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if sum(i.file_size for i in z.infolist()) > 200 * 1024 * 1024:
            raise ValueError("XLSX açılmış içerik sınırını aşıyor")
    try:
        wb = load_workbook(
            io.BytesIO(data), read_only=True, data_only=False, keep_links=False
        )
    except (InvalidFileException, KeyError, SyntaxError, TypeError) as e:
        raise ValueError(f"XLSX çalışma kitabı bozuk: {e}") from None
    try:
        selected = sheets or wb.sheetnames
        if len(set(selected)) != len(selected) or any(
            s not in wb.sheetnames for s in selected
        ):
            raise ValueError("Sayfa seçimi geçersiz veya yinelenmiş")
        for name in selected:
            ws = wb[name]
            if (ws.max_column or 0) > MAX_COLUMNS:
                raise ValueError("Sayfa 4096 sütun sınırını aşıyor")

            def rows(ws=ws):
                for n, row in enumerate(ws.iter_rows(), 1):
                    yield (
                        n,
                        [c.value for c in row],
                        {j for j, c in enumerate(row) if c.data_type == "f"},
                    )

            yield name, rows()
    finally:
        wb.close()


def _parse_row(file, sheet, n, values, formula_cols, names, roles, questions, items):
    issues = []
    identity = {}
    answers = []

    def issue(message, question=None, col=None):
        issues.append(
            asdict(
                Issue(
                    file,
                    sheet,
                    n,
                    identity.get("participant_code") or identity.get("alias"),
                    question,
                    col,
                    message,
                )
            )
        )

    if len(values) != len(names):
        issue("Satırdaki hücre sayısı başlık sayısıyla uyuşmuyor")

    def cell(index, question=None):
        if index in formula_cols:
            issue(
                "Formül hücresi cevap veya kimlik olarak kullanılamaz",
                question,
                names[index],
            )
            return ""
        try:
            return _text(values[index] if index < len(values) else None)
        except ValueError as e:
            issue(str(e), question, names[index])
            return ""

    for role, index in roles.items():
        identity[role] = cell(index)
    try:
        identity = identity_values(identity)
    except ValueError as e:
        issue(str(e))
    for q, index in sorted(questions.items()):
        value = cell(index, q) or None
        if value is not None and value not in items[q]:
            issue(f"Geçersiz seçenek: {value!r}", q, names[index])
        answers.append([q, value])
    for error in issues:
        if error["participant"] is None:
            error["participant"] = identity.get("participant_code") or identity.get(
                "alias"
            )
    return InputRow(sheet, n, identity, answers, issues)


def read_responses(
    path: Path, items: dict[int, set[str]], *, mapping=None, sheets=None, delimiter=None
):
    path = Path(path)
    if path.suffix.lower() not in (".csv", ".xlsx"):
        raise ResponseFileError(
            [
                Issue(
                    path.name,
                    None,
                    0,
                    None,
                    None,
                    None,
                    "Yalnız CSV ve XLSX desteklenir",
                )
            ]
        )
    try:
        with path.open("rb") as f:
            data = f.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("Dosya 50 MiB sınırını aşıyor")
        parsed = []
        mappings = {}
        for sheet, rows in _tables(data, path.suffix.lower(), sheets, delimiter):
            iterator = iter(rows)
            first = next(iterator, None)
            if first is None:
                continue
            _, headers, formulas = first
            if all(h is None for h in headers) and next(iterator, None) is None:
                continue
            if formulas:
                raise ResponseFileError(
                    [
                        Issue(
                            path.name,
                            sheet,
                            1,
                            None,
                            None,
                            None,
                            "Sütun başlığında formül kullanılamaz",
                        )
                    ]
                )
            names, roles, questions, ignored = _mapping(
                headers, items, mapping, path.name, sheet
            )
            mappings[sheet or "CSV"] = {
                "headers": names,
                "roles": roles,
                "questions": questions,
                "ignored": ignored,
            }
            for n, values, formula_cols in iterator:
                if n > MAX_ROWS + 1:
                    raise ValueError("Sayfa 10000 veri satırı sınırını aşıyor")
                if not any(v is not None and v != "" for v in values):
                    continue
                if len(parsed) >= MAX_ROWS:
                    raise ValueError("Dosya 10000 katılımcı sınırını aşıyor")
                parsed.append(
                    _parse_row(
                        path.name,
                        sheet,
                        n,
                        values,
                        formula_cols,
                        names,
                        roles,
                        questions,
                        items,
                    )
                )
        if not mappings or not parsed:
            raise ValueError("Cevap dosyasında katılımcı kaydı yok")
        keys = [
            ("code", r.identity.get("participant_code"))
            if r.identity.get("participant_code")
            else ("alias", r.identity.get("alias"))
            for r in parsed
        ]
        duplicates = Counter(keys)
        aliases = Counter(
            r.identity.get("alias") for r in parsed if r.identity.get("alias")
        )
        for row, key in zip(parsed, keys, strict=True):
            if (key[1] and duplicates[key] > 1) or (
                not row.identity.get("participant_code")
                and aliases[row.identity.get("alias")] > 1
            ):
                row.errors.append(
                    asdict(
                        Issue(
                            path.name,
                            row.sheet,
                            row.row,
                            key[1],
                            None,
                            None,
                            "Yinelenen veya belirsiz katılımcı kaydı",
                        )
                    )
                )
        return ResponseFile(
            path.name, hashlib.sha256(data).hexdigest(), mappings, parsed
        )
    except ResponseFileError:
        raise
    except (
        OSError,
        ValueError,
        UnicodeError,
        csv.Error,
        zipfile.BadZipFile,
        SyntaxError,
        KeyError,
    ) as e:
        raise ResponseFileError(
            [Issue(path.name, None, 0, None, None, None, f"Dosya okunamadı: {e}")]
        ) from None
