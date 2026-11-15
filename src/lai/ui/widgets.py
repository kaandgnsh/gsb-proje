"""Türkçe formlar ve açılabilir, salt okunur hesap dökümü."""

import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

STATE = {
    "CONFIRMED": "Doğrulandı",
    "STRONGLY_INFERRED": "Güçlü çıkarım",
    "WEAKLY_INFERRED": "Zayıf çıkarım",
    "UNKNOWN": "Bilinmiyor",
    "CONTRADICTORY": "Çelişkili",
    "REJECTED": "Reddedildi",
    "DEFERRED": "Ertelendi",
    "VERIFIED": "Doğrulanmış",
    "PROVISIONAL": "Geçici",
    "NOT_EVALUABLE": "Hesaplanamadı",
    "ready": "Hazır",
    "running": "Çalışıyor",
    "paused": "Duraklatıldı",
    "cancelled": "İptal edildi",
    "completed": "Tamamlandı",
    "pending": "Bekliyor",
    "succeeded": "Başarılı",
    "partial": "Kısmi",
    "failed": "Başarısız",
}

LABELS = {
    "item": "Soru",
    "answer": "Cevap",
    "points": "Puanlar",
    "rules": "Uygulanan kurallar",
    "transformations": "Dönüşümler",
    "type": "Aşama",
    "method": "Yöntem",
    "value": "Değer",
    "body": "Kural içeriği",
    "content": "İçerik",
    "dimension": "Boyut",
    "selected_pole": "Seçilen kutup",
    "profile_version_id": "Profil sürümü",
    "unrounded": "Yuvarlanmamış puan",
    "entry_id": "Bilgi kimliği",
    "entry_key": "Kural kimliği",
    "version": "Sürüm",
    "confidence": "Güven durumu",
    "source": "Kaynak",
    "validation": "Doğrulama",
    "confirmation_ref": "Onay kaynağı",
    "evidence_ids": "Kanıt kimlikleri",
    "rule_type": "Kural türü",
    "count": "Katkı sayısı",
    "places": "Ondalık basamak",
    "rounding": "Yuvarlama",
    "affects_decision": "Kararı etkiler",
    "options": "Seçenekler",
    "weights": "Katkılar",
    "poles": "Kutuplar",
    "items": "Sorular",
    "parameters": "Parametreler",
    "policy": "Politika",
    "pole": "Kutup",
    "denominator": "Payda",
    "factor": "Çarpan",
    "offset": "Sabit",
    "threshold": "Eşik kuralı",
}
STATE.update(
    {
        "user": "Kullanıcı",
        "document": "Belge",
        "ai_inference": "Model çıkarımı",
        "example_fit": "Örnek uyumu",
        "validated": "Doğrulandı",
        "rejected": "Reddedildi",
        "sum": "Toplam",
        "mean": "Ortalama",
        "none": "Uygulanmaz",
        "scaled": "Çarpan ve sabit",
        "percentage": "Yüzde",
        "higher_wins": "Yüksek puan",
        "min_points": "En az puan",
        "half_even": "Çift basamağa yuvarlama",
        "half_up": "Yarımları yukarı yuvarlama",
        "display_rounding": "Gösterim yuvarlaması",
        "aggregation": "Toplulaştırma",
        "normalization": "Normalizasyon",
        "dimension_weight": "Boyut ağırlığı",
    }
)


def text(value):
    if isinstance(value, bool):
        return "Evet" if value else "Hayır"
    return STATE.get(str(value), str(value)) if value is not None else "—"


def label(value):
    w = QLabel(value)
    w.setTextFormat(Qt.PlainText)
    w.setWordWrap(True)
    return w


def table(headers, name):
    w = QTableWidget(0, len(headers))
    w.setObjectName(name)
    w.setHorizontalHeaderLabels(headers)
    w.setSelectionBehavior(QTableWidget.SelectRows)
    w.setSelectionMode(QTableWidget.SingleSelection)
    w.setEditTriggers(QTableWidget.NoEditTriggers)
    w.horizontalHeader().setStretchLastSection(True)
    w.setAlternatingRowColors(True)
    return w


def rows(widget, values):
    widget.setRowCount(len(values))
    for i, row in enumerate(values):
        for j, value in enumerate(row):
            widget.setItem(i, j, QTableWidgetItem(text(value)))
    widget.resizeColumnsToContents()


def tree_value(parent, key, value):
    node = QTreeWidgetItem(
        parent,
        [
            LABELS.get(str(key), str(key)),
            "" if isinstance(value, (dict, list)) else text(value),
        ],
    )
    if isinstance(value, dict):
        for k, v in value.items():
            tree_value(node, k, v)
    elif isinstance(value, list):
        for i, v in enumerate(value, 1):
            tree_value(node, str(i), v)
    return node


class ResultDialog(QDialog):
    export_requested = Signal(int, str)
    commentary_requested = Signal(int)

    def __init__(self, assessment, parent=None):
        super().__init__(parent)
        self.assessment_id = assessment["id"]
        self.setWindowTitle("Değerlendirme sonucu ve hesap dökümü")
        self.resize(1050, 740)
        self.setObjectName("result_dialog")
        layout = QVBoxLayout(self)
        result = assessment.get("results", assessment.get("result", {}))
        layout.addWidget(
            label(
                f"Profil sürümü #{result.get('profile_version_id')} · Envanter sürümü #{result.get('inventory_version_id')} · {text(result.get('status'))} · Sonuç: {result.get('full_profile') or 'Tam profil hesaplanamadı'}"
            )
        )
        summary = table(
            ["Boyut", "Ham puan", "Nihai puan", "Seçilen harf", "Durum / açıklama"],
            "result_summary",
        )
        rows(
            summary,
            [
                [
                    d["dimension"],
                    " · ".join(f"{p}: {v}" for p, v in (d.get("raw") or {}).items())
                    or "—",
                    " · ".join(f"{p}: {v}" for p, v in (d.get("final") or {}).items())
                    or "—",
                    d.get("selected_pole"),
                    text(d["status"]) + "; " + "; ".join(d["limitations"]),
                ]
                for d in result.get("dimensions", [])
            ],
        )
        layout.addWidget(summary, 1)
        self.trace = QTreeWidget()
        self.trace.setObjectName("calculation_trace")
        self.trace.setHeaderLabels(["Hesap dökümü — açmak için sağ ok", "Değer"])
        for d in result.get("dimensions", []):
            node = QTreeWidgetItem(self.trace, [d["dimension"], text(d["status"])])
            for k, title in [
                ("contributions", "Soru katkıları"),
                ("transformations", "Uygulanan dönüşümler"),
                ("decision", "Karar kuralı"),
                ("rules", "Kural sürümleri ve kaynaklar"),
                ("limitations", "Hesaplanamama nedenleri"),
            ]:
                tree_value(node, title, d.get(k))
        self.trace.setColumnWidth(0, 430)
        layout.addWidget(self.trace, 3)
        self.export_format = QComboBox()
        self.export_format.addItems(["PDF", "XLSX", "CSV", "JSON"])
        self.export_format.setAccessibleName("Rapor biçimi")
        layout.addWidget(self.export_format)
        export = QPushButton("Raporu dışa &aktar")
        export.setObjectName("export_result")
        export.clicked.connect(
            lambda: self.export_requested.emit(
                assessment["id"], self.export_format.currentText().lower()
            )
        )
        layout.addWidget(export)
        self.commentary = QPlainTextEdit()
        self.commentary.setObjectName("model_commentary")
        self.commentary.setReadOnly(True)
        self.commentary.setAccessibleName("Yerel model açıklaması")
        self.commentary.setPlaceholderText(
            "İsteğe bağlı: yerel model kayıtlı sonucu Türkçe açıklar. Puan ve seçilen "
            "harf değişmez; açıklama kaydedilir ve sonraki dışa aktarmalara eklenir."
        )
        self.commentary.setMaximumHeight(150)
        layout.addWidget(self.commentary)
        explain = QPushButton("Yerel modelle &açıklama üret")
        explain.setObjectName("generate_commentary")
        explain.clicked.connect(
            lambda: self.commentary_requested.emit(self.assessment_id)
        )
        layout.addWidget(explain)
        close = QPushButton("&Kapat")
        close.clicked.connect(self.close)
        layout.addWidget(close)


    def show_commentary(self, body):
        self.commentary.setPlainText(
            "\n\n".join(
                [body["summary"]]
                + [f"{d['dimension']}: {d['text']}" for d in body["dimensions"]]
                + [f"Model: {body['model']} · {body['created_at']}"]
            )
        )


class ReportPreview(QDialog):
    """Yerel dosyayı salt okunur açar; çalışma sayfası formülü yürütmez."""

    def __init__(self, preview, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Rapor önizleme — " + preview["path"])
        self.setObjectName("report_preview")
        self.resize(1100, 800)
        layout = QVBoxLayout(self)
        if preview["kind"] == "pdf":
            from PySide6.QtPdf import QPdfDocument
            from PySide6.QtPdfWidgets import QPdfView

            self.document = QPdfDocument(self)
            error = self.document.load(preview["path"])
            if error != QPdfDocument.Error.None_:
                raise ValueError("PDF görüntüleyicide açılamadı: " + str(error))
            layout.addWidget(
                label(f"PDF · {self.document.pageCount()} sayfa · salt okunur")
            )
            self.view = QPdfView(self)
            self.view.setDocument(self.document)
            self.view.setPageMode(QPdfView.PageMode.MultiPage)
            self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
            layout.addWidget(self.view)
        else:
            layout.addWidget(
                label(
                    "Excel — salt okunur; her sayfanın ilk 500 satırı. Formüller yürütülmez."
                )
            )
            self.tabs = QTabWidget()
            for sheet in preview["sheets"]:
                data = sheet["rows"]
                width = max((len(r) for r in data), default=0)
                widget = table(data[0] if data else [], "report_sheet")
                widget.setColumnCount(width)
                rows(widget, data[1:])
                self.tabs.addTab(widget, sheet["name"])
            layout.addWidget(self.tabs)
        close = QPushButton("&Kapat")
        close.clicked.connect(self.close)
        layout.addWidget(close)


RULE_NAMES = {
    "dimension_def": "Boyut ve kutuplar",
    "dimension_items": "Boyutun soruları",
    "default_option_map": "Ortak seçenek eşlemesi",
    "item_pole_map": "Sorunun seçenek eşlemesi",
    "option_weight": "Seçenek puan katkıları",
    "reverse": "Ters soru",
    "threshold": "Eşik / karşılaştırma",
    "tie_break": "Eşitlik kararı",
    "missing_answer_policy": "Eksik cevap politikası",
    "normalization": "Normalizasyon",
    "aggregation": "Toplam / ortalama",
    "dimension_weight": "Boyut ağırlığı",
    "zero_score": "Sıfır puan kararı",
    "precision": "Gösterim hassasiyeti",
}


class RuleEditor(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("Açık puanlama bilgisini öğret / düzelt", parent)
        form = QFormLayout(self)
        self.kind = QComboBox()
        self.kind.setObjectName("rule_kind")
        for code, title in RULE_NAMES.items():
            self.kind.addItem(title, code)
        self.dimension = QLineEdit()
        self.dimension.setObjectName("rule_dimension")
        self.poles = QLineEdit()
        self.poles.setPlaceholderText("Örnek: E,I")
        self.members = QLineEdit()
        self.members.setPlaceholderText("Örnek: 1,5,9 (yalnız açık soru numaraları)")
        self.item = QSpinBox()
        self.item.setRange(1, 100000)
        self.pole = QLineEdit()
        self.pole.setPlaceholderText("Örnek: I")
        self.method = QComboBox()
        self.method.setObjectName("rule_method")
        self.number = QDoubleSpinBox()
        self.number.setRange(-1000000000, 1000000000)
        self.number.setDecimals(12)
        self.offset = QDoubleSpinBox()
        self.offset.setRange(-1000000, 1000000)
        self.offset.setDecimals(12)
        self.places = QSpinBox()
        self.places.setRange(0, 12)
        self.places.setValue(2)
        self.map = QTableWidget(2, 3)
        self.map.setHorizontalHeaderLabels(["Seçenek", "Kutup", "Puan"])
        self.map.setObjectName("rule_map")
        self.map.setMaximumHeight(125)
        add = QPushButton("Katkı satırı &ekle")
        add.clicked.connect(lambda: self.map.insertRow(self.map.rowCount()))
        self.mapping_box = QGroupBox("Seçenek eşlemesi / katkıları")
        v = QVBoxLayout(self.mapping_box)
        v.addWidget(self.map)
        v.addWidget(add)
        for title, w in [
            ("Kural &türü", self.kind),
            ("&Boyut kodu", self.dimension),
            ("Kutuplar (virgülle)", self.poles),
            ("Soru numaraları", self.members),
            ("Soru no", self.item),
            ("Seçilen kutup", self.pole),
            ("Yöntem / politika", self.method),
            ("Değer / çarpan / payda", self.number),
            ("Normalizasyon sabiti", self.offset),
            ("Ondalık basamak", self.places),
            ("Seçenekler", self.mapping_box),
        ]:
            form.addRow(title, w)
        self.kind.currentIndexChanged.connect(self.update_fields)
        self.update_fields()

    def update_fields(self):
        t = self.kind.currentData()
        form = self.layout()
        visible = {self.dimension}
        if t == "dimension_def":
            visible.add(self.poles)
        if t == "dimension_items":
            visible.add(self.members)
        if t in ("item_pole_map", "option_weight", "reverse"):
            visible.add(self.item)
        if t in ("default_option_map", "item_pole_map", "option_weight"):
            visible.add(self.mapping_box)
        if t in ("threshold", "tie_break", "missing_answer_policy", "zero_score"):
            visible.add(self.pole)
        options = {
            "threshold": [
                ("Puanı yüksek kutup", "higher_wins"),
                ("En az puan", "min_points"),
            ],
            "missing_answer_policy": [
                ("Atla", "skip"),
                ("Kutba bir puan say", "count_as_pole"),
                ("Geçersiz say", "invalidate"),
            ],
            "normalization": [
                ("Uygulanmaz", "none"),
                ("Yüzde (açık payda)", "percentage"),
                ("Çarpan ve sabit", "scaled"),
            ],
            "aggregation": [("Toplam", "sum"), ("Ortalama", "mean")],
            "zero_score": [
                ("Kutup seç", "select_pole"),
                ("Geçersiz say", "invalidate"),
                ("Eşik kuralını kullan", "use_threshold"),
            ],
            "precision": [
                ("Çift basamağa yuvarla", "half_even"),
                ("Yarımları yukarı yuvarla", "half_up"),
            ],
        }
        self.method.clear()
        for name, code in options.get(t, []):
            self.method.addItem(name, code)
        if t in options:
            visible.add(self.method)
        if t in ("threshold", "dimension_weight", "normalization"):
            visible.add(self.number)
        if t == "normalization":
            visible.add(self.offset)
        if t == "precision":
            visible.add(self.places)
        for w in (
            self.dimension,
            self.poles,
            self.members,
            self.item,
            self.pole,
            self.method,
            self.number,
            self.offset,
            self.places,
            self.mapping_box,
        ):
            w.setVisible(w in visible)
            if form.labelForField(w):
                form.labelForField(w).setVisible(w in visible)
        self.map.setColumnHidden(2, t != "option_weight")

    def proposal(self):
        t = self.kind.currentData()
        b = {"dimension": self.dimension.text().strip()}
        method = self.method.currentData()
        if t == "dimension_def":
            b["poles"] = [p.strip() for p in self.poles.text().split(",")]
        if t == "dimension_items":
            parts = self.members.text().split(",")
            if any(not p.strip().isdigit() for p in parts):
                raise ValueError("Soru numaralarını virgülle ayırın.")
            b["items"] = [int(p.strip()) for p in parts]
        if t in ("item_pole_map", "option_weight", "reverse"):
            b["item"] = self.item.value()
        if t in ("default_option_map", "item_pole_map", "option_weight"):
            data = {}
            seen = set()
            for n in range(self.map.rowCount()):
                values = [
                    self.map.item(n, j).text().strip() if self.map.item(n, j) else ""
                    for j in range(3)
                ]
                code, pole, weight = values
                if not any(values):
                    continue
                k = (code, pole) if t == "option_weight" else code
                if k in seen:
                    raise ValueError("Seçenek/kutup katkısı yineleniyor.")
                seen.add(k)
                if t == "option_weight":
                    try:
                        number = float(weight)
                    except ValueError:
                        raise ValueError(
                            "Puan alanına geçerli bir sayı girin."
                        ) from None
                    if not math.isfinite(number):
                        raise ValueError("Puan sonlu olmalı.")
                    data.setdefault(code, {})[pole] = number
                else:
                    data[code] = pole
            b["weights" if t == "option_weight" else "options"] = data
        if t == "threshold":
            b["method"] = method
            if method == "min_points":
                b.update(pole=self.pole.text().strip(), value=self.number.value())
        if t == "tie_break":
            b["pole"] = self.pole.text().strip()
        if t in ("missing_answer_policy", "zero_score"):
            b["policy"] = method
            if method in ("count_as_pole", "select_pole"):
                b["pole"] = self.pole.text().strip()
        if t == "normalization":
            b["method"] = method
            b["parameters"] = {}
            if method == "percentage":
                if self.number.value() <= 0:
                    raise ValueError("Normalizasyon paydası sıfırdan büyük olmalı.")
                b["parameters"] = {"denominator": self.number.value()}
            if method == "scaled":
                b["parameters"] = {
                    "factor": self.number.value(),
                    "offset": self.offset.value(),
                }
        if t == "aggregation":
            b["method"] = method
        if t == "dimension_weight":
            b["value"] = self.number.value()
        if t == "precision":
            b.update(places=self.places.value(), rounding=method)
        return {"rule_type": t, "body": b}
