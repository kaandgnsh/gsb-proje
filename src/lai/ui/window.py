"""Türkçe masaüstü: hizmetler işçide, widget güncellemeleri GUI iş parçacığında."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .backend import Backend
from .tasks import Task
from .theme import apply_theme
from .widgets import (
    ResultDialog,
    RuleEditor,
    label,
    rows,
    table,
    text,
    tree_value,
)


class MainWindow(QMainWindow):
    operation_finished = Signal(str)

    def __init__(self, db, *, backend=None):
        super().__init__()
        apply_theme(QApplication.instance())
        self.db = Path(db)
        self.backend = backend or Backend(db)
        self.task = None
        self.closing = False
        self.controls = []
        self.inputs = []
        self.dialogs = []
        self.preview = None
        self.batch_preview = None
        self.learning_data = {}
        self.catalog_data = {}
        self.loaded_version = None
        self.operation = ""
        self.followup = None
        self.job_id = None
        self.history_offset = 0
        self.history_total = 0
        self.preferences = self._read_preferences()
        self.setWindowTitle("Yerel Envanter • Değerlendirme ve Öğrenme")
        self.resize(1280, 850)
        self.setMinimumSize(960, 680)
        self.setObjectName("main_window")
        root = QWidget()
        outer = QVBoxLayout(root)
        self.setCentralWidget(root)
        title = QHBoxLayout()
        title.addWidget(label("YEREL ENVANTER  |  Değerlendirme ve Öğrenme"), 1)
        self.inventory_label = label("&Envanter:")
        title.addWidget(self.inventory_label)
        self.inventory = QComboBox()
        self.inventory.setObjectName("inventory")
        self.inventory_label.setBuddy(self.inventory)
        title.addWidget(self.inventory, 1)
        title.addWidget(
            self.button("&Yenile", lambda: self.start("refresh", {}), "refresh")
        )
        outer.addLayout(title)
        body = QHBoxLayout()
        self.nav = QListWidget()
        self.nav.setObjectName("navigation")
        self.nav.setMaximumWidth(205)
        self.nav.addItems(
            [
                "Belgeler",
                "Öğrenme Merkezi",
                "Tekil Değerlendirme",
                "Toplu İşler",
                "Geçmiş",
                "Katılımcılar",
                "Ayarlar",
            ]
        )
        self.stack = QStackedWidget()
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)
        for builder in (
            self.documents_page,
            self.learning_page,
            self.single_page,
            self.batch_page,
            self.history_page,
            self.participants_page,
            self.settings_page,
        ):
            builder()
        status = QHBoxLayout()
        self.status = label("Yerel veritabanı açılıyor…")
        self.status.setObjectName("status")
        self.status.setMaximumHeight(100)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setMaximumWidth(200)
        self.cancel_button = QPushButton("İşlemi &iptal et")
        self.cancel_button.setObjectName("cancel_task")
        self.cancel_button.clicked.connect(lambda: self.cancel_active("pause"))
        self.cancel_button.setEnabled(False)
        status.addWidget(self.status, 1)
        status.addWidget(self.progress)
        status.addWidget(self.cancel_button)
        outer.addLayout(status)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(self.preferences.get("page", 0))
        self.inventory.currentIndexChanged.connect(self.inventory_changed)
        for i in range(7):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self)
            shortcut.activated.connect(lambda i=i: self.nav.setCurrentRow(i))
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(
            lambda: self.start("refresh", {})
        )
        QShortcut(QKeySequence("Escape"), self).activated.connect(
            lambda: self.cancel_active("pause")
        )
        self.setStyleSheet(
            "QMainWindow {background:#f3f5f9;} QGroupBox {font-weight:600;margin-top:10px;padding-top:12px;} QListWidget {background:#16283e;color:white;padding:8px;} QListWidget::item {padding:13px 7px;} QListWidget::item:selected {background:#315a80;} QPushButton {padding:7px 12px;} QPushButton:focus,QComboBox:focus,QLineEdit:focus {border:2px solid #2777b8;} QTableWidget,QTreeWidget,QPlainTextEdit {background:white;}"
        )
        QTimer.singleShot(0, lambda: self.start("refresh", {}))

    def button(self, title, fn, name):
        w = QPushButton(title)
        w.setObjectName(name)

        def invoke():
            try:
                fn()
            except (ValueError, KeyError, OSError) as e:
                self.error(str(e))

        w.clicked.connect(invoke)
        self.controls.append(w)
        return w

    def page(self, title):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(label(title))
        self.stack.addWidget(w)
        return layout

    def choose_file(self, target, title, filters):
        dialog = QFileDialog(self, title)
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        dialog.setFileMode(QFileDialog.ExistingFile)
        dialog.setDirectory(str(self.db.parent))
        dialog.setNameFilter(filters)
        dialog.setLabelText(QFileDialog.Accept, "Seç")
        dialog.setLabelText(QFileDialog.Reject, "Vazgeç")
        dialog.setLabelText(QFileDialog.FileName, "Dosya adı:")
        dialog.setLabelText(QFileDialog.FileType, "Dosya türü:")
        dialog.setLabelText(QFileDialog.LookIn, "Konum:")
        if dialog.exec():
            selected = dialog.selectedFiles()
            if selected:
                target.setText(selected[0])

    def file_row(self, layout, title, name, filters):
        h = QHBoxLayout()
        field = QLineEdit()
        field.setObjectName(name)
        field.setAccessibleName(title)
        h.addWidget(field, 1)
        h.addWidget(
            self.button(
                "Dosya &seç…",
                lambda: self.choose_file(field, title, filters),
                name + "_choose",
            )
        )
        layout.addLayout(h)
        self.inputs.append(field)
        return field

    def documents_page(self):
        layout = self.page(
            "Belgeleri kaydetmeden önce soru, anahtar ve kaynak konumlarını kontrol edin."
        )
        h = QHBoxLayout()
        self.new_name = QLineEdit()
        self.new_name.setPlaceholderText("Yeni envanter adı")
        self.new_name.setObjectName("new_inventory_name")
        h.addWidget(self.new_name)
        h.addWidget(
            self.button(
                "Envanter &oluştur",
                lambda: self.start("create_inventory", {"name": self.new_name.text()}),
                "create_inventory",
            )
        )
        layout.addLayout(h)
        self.doc_role = QComboBox()
        self.doc_role.setObjectName("document_role")
        for title, role in [
            ("Envanter soruları (varsa anahtarla)", "inventory"),
            ("Ayrı cevap anahtarı", "answer_key"),
            ("Puanlama yönergesi / başvuru belgesi", "reference"),
        ]:
            self.doc_role.addItem(title, role)
        layout.addWidget(self.doc_role)
        self.doc_path = self.file_row(
            layout,
            "Envanter / anahtar / yönerge seçin",
            "document_path",
            "Belgeler (*.pdf *.docx *.xlsx *.csv *.json)",
        )
        h = QHBoxLayout()
        h.addWidget(self.button("Ön&izle", self.preview_document, "preview_document"))
        self.save_doc = self.button(
            "Kontrol edilen belgeyi &kaydet",
            lambda: self.start("save_document", {"preview": self.preview}),
            "save_document",
        )
        h.addWidget(self.save_doc)
        layout.addLayout(h)
        self.doc_summary = label("Önizleme yapılmadı.")
        layout.addWidget(self.doc_summary)
        self.doc_records = table(["Soru", "Metin", "Kaynak konumu"], "document_records")
        layout.addWidget(self.doc_records, 2)
        self.doc_issues = table(
            ["Düzey", "Soru", "Sayfa / satır", "Açıklama"], "document_issues"
        )
        layout.addWidget(self.doc_issues, 1)
        self.doc_text = QPlainTextEdit()
        self.doc_text.setReadOnly(True)
        self.doc_text.setMaximumHeight(100)
        self.doc_text.setAccessibleName("Belgeden metin örneği")
        layout.addWidget(self.doc_text)
        self.doc_path.textChanged.connect(self.invalidate_document)
        self.doc_role.currentIndexChanged.connect(self.invalidate_document)
        self.inputs.extend([self.new_name, self.doc_role])

    def learning_page(self):
        layout = self.page(
            "Kaynakları inceleyin; model önerisi açık onayınız olmadan doğrulanmış sayılmaz."
        )
        self.learning_model_notice = label(
            "Yerel model isteğe bağlıdır; manuel bilgi öğretme model olmadan kullanılabilir."
        )
        self.learning_model_notice.setObjectName("learning_model_notice")
        layout.addWidget(self.learning_model_notice)
        h = QHBoxLayout()
        h.addWidget(
            self.button(
                "Eksikleri ve kuralları &incele", self.load_learning, "learning_refresh"
            )
        )
        h.addWidget(
            self.button(
                "Yerel kural &analizi",
                lambda: self.start(
                    "discover", self.model_args() | {"inventory_id": self.inv()}
                ),
                "discover",
            )
        )
        h.addWidget(
            self.button(
                "Yeni profil sürümü &yayımla",
                lambda: self.start("publish", {"inventory_id": self.inv()}),
                "publish",
            )
        )
        layout.addLayout(h)
        split = QSplitter()
        left = QWidget()
        v = QVBoxLayout(left)
        self.gap_filter = QComboBox()
        self.gap_filter.setObjectName("gap_filter")
        for title, code in [
            ("Yanıt bekleyen sorular", "active"),
            ("Ertelenen / bilinmeyen yanıtlı sorular", "deferred"),
            ("Bütün sorular ve durumlar", "all"),
        ]:
            self.gap_filter.addItem(title, code)
        self.gap_filter.setCurrentIndex(
            max(
                0,
                self.gap_filter.findData(self.preferences.get("gap_filter", "active")),
            )
        )
        self.gap_filter.currentIndexChanged.connect(
            lambda: (
                self.show_learning(self.learning_data) if self.learning_data else None
            )
        )
        v.addWidget(self.gap_filter)
        self.gap_table = table(["Kimlik", "Öncelik", "Durum", "Eksik bilgi"], "gaps")
        self.gap_table.itemSelectionChanged.connect(self.select_gap)
        v.addWidget(self.gap_table, 2)
        self.gap_detail = QPlainTextEdit()
        self.gap_detail.setReadOnly(True)
        self.gap_detail.setObjectName("gap_detail")
        v.addWidget(self.gap_detail, 1)
        self.answer_text = QPlainTextEdit()
        self.answer_text.setObjectName("answer_text")
        self.answer_text.setPlaceholderText("Açıklamanız veya düzeltme gerekçesi")
        self.answer_text.setMaximumHeight(90)
        v.addWidget(self.answer_text)
        h = QHBoxLayout()
        for title, action, name in [
            ("&Doğrula", "yes", "confirm"),
            ("&Bilmiyorum", "unknown", "dont_know"),
            ("Daha &sonra", "later", "later"),
        ]:
            h.addWidget(
                self.button(
                    title, lambda checked=False, a=action: self.answer_gap(a), name
                )
            )
        v.addLayout(h)
        h = QHBoxLayout()
        for title, action, name in [
            ("Bu bilgiyi &kullanma", "reject", "reject"),
            ("Açıklamayı kaydet", "text", "answer_text_save"),
            ("Yeniden sor", "resume", "resume_question"),
        ]:
            h.addWidget(
                self.button(
                    title, lambda checked=False, a=action: self.answer_gap(a), name
                )
            )
        v.addLayout(h)
        v.addWidget(
            self.button(
                "Açıklamayı yerel modelle yorumla",
                lambda: self.answer_gap("interpret"),
                "interpret",
            )
        )
        right = QWidget()
        r = QVBoxLayout(right)
        self.rule_editor = RuleEditor()
        r.addWidget(self.rule_editor)
        h = QHBoxLayout()
        h.addWidget(
            self.button("Açık kuralı &öğret", lambda: self.answer_gap("teach"), "teach")
        )
        h.addWidget(
            self.button(
                "Önceki bilgiyi &düzelt", lambda: self.answer_gap("correct"), "correct"
            )
        )
        r.addLayout(h)
        self.rule_table = table(["Bilgi / kural", "Durum", "Kaynak", "Sürüm"], "rules")
        r.addWidget(self.rule_table, 1)
        self.sources = QTreeWidget()
        self.sources.setHeaderLabels(["Bilgi kaynağı", "Kanıt / konum"])
        self.sources.setObjectName("sources")
        r.addWidget(self.sources, 1)
        split.addWidget(left)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(right)
        split.addWidget(scroll)
        layout.addWidget(split, 1)
        self.inputs.extend([self.answer_text, self.rule_editor])

    def version_row(self, layout, prefix):
        h = QHBoxLayout()
        exam = QComboBox()
        exam.setObjectName(prefix + "_inventory_version")
        profile = QComboBox()
        profile.setObjectName(prefix + "_profile_version")
        h.addWidget(label("Sınav sürümü:"))
        h.addWidget(exam, 1)
        h.addWidget(label("Puanlama profili:"))
        h.addWidget(profile, 2)
        layout.addLayout(h)
        self.inputs.extend([exam, profile])
        return exam, profile

    def single_page(self):
        layout = self.page(
            "Yanıt yok seçimi boş cevap olarak saklanır; sonuç M7 politikalarıyla hesaplanır."
        )
        self.exam, self.profile = self.version_row(layout, "single")
        h = QHBoxLayout()
        self.code = QLineEdit()
        self.code.setPlaceholderText("Katılımcı kodu (veya takma ad)")
        self.code.setObjectName("participant_code")
        self.alias = QLineEdit()
        self.alias.setPlaceholderText("Takma ad")
        self.alias.setObjectName("participant_alias")
        h.addWidget(self.code)
        h.addWidget(self.alias)
        layout.addLayout(h)
        layout.addWidget(
            self.button(
                "Seçilen sürümün &sorularını yükle",
                lambda: self.start("items", {"version_id": self.required(self.exam)}),
                "load_items",
            )
        )
        self.answer_table = table(["Soru", "Soru metni", "Cevap"], "single_answers")
        layout.addWidget(self.answer_table, 1)
        self.evaluate_button = self.button(
            "Değerlendir ve &kaydet", self.evaluate_single, "evaluate"
        )
        layout.addWidget(self.evaluate_button)
        self.exam.currentIndexChanged.connect(self.invalidate_answers)
        self.inputs.extend([self.code, self.alias])

    def batch_page(self):
        layout = self.page(
            "Sütun ve satırları önizleyin. Kaydedilmiş iş, sabit sürümleriyle devam eder."
        )
        self.batch_exam, self.batch_profile = self.version_row(layout, "batch")
        self.batch_path = self.file_row(
            layout,
            "CSV / XLSX cevap dosyası seçin",
            "batch_path",
            "Cevap dosyaları (*.csv *.xlsx)",
        )
        self.batch_sheet = QLineEdit()
        self.batch_sheet.setPlaceholderText("XLSX sayfa adı (boş: bütün dolu sayfalar)")
        layout.addWidget(self.batch_sheet)
        self.mapping_path = self.file_row(
            layout,
            "Özel sütun eşlemesi seçin (isteğe bağlı)",
            "mapping_path",
            "Eşleme dosyası (*.json)",
        )
        h = QHBoxLayout()
        h.addWidget(
            self.button(
                "Sütunları ve cevapları &önizle",
                self.preview_responses,
                "preview_batch",
            )
        )
        self.prepare_button = self.button(
            "Önizlenen &işi kaydet",
            lambda: self.start("prepare_job", {"preview": self.batch_preview}),
            "prepare_job",
        )
        h.addWidget(self.prepare_button)
        layout.addLayout(h)
        self.batch_summary = label("Önizleme yapılmadı.")
        layout.addWidget(self.batch_summary)
        split = QSplitter()
        self.mapping_tree = QTreeWidget()
        self.mapping_tree.setHeaderLabels(["Sayfa / sütun", "Eşleme"])
        self.mapping_tree.setObjectName("batch_mapping")
        split.addWidget(self.mapping_tree)
        self.batch_errors = table(
            ["Sayfa", "Satır", "Katılımcı", "Soru", "Açıklama"], "batch_errors"
        )
        split.addWidget(self.batch_errors)
        layout.addWidget(split, 1)
        self.jobs = table(
            [
                "İş",
                "Durum",
                "Sınav / profil",
                "Başarılı",
                "Kısmi",
                "Hatalı",
                "Bekleyen",
            ],
            "jobs",
        )
        self.jobs.itemDoubleClicked.connect(lambda: self.open_job())
        layout.addWidget(self.jobs, 1)
        h = QHBoxLayout()
        h.addWidget(self.button("İşi &aç", self.open_job, "open_job"))
        h.addWidget(self.button("&Başlat / devam et", self.run_selected_job, "run_job"))
        self.pause_job = QPushButton("&Duraklat")
        self.pause_job.setObjectName("pause_job")
        self.pause_job.clicked.connect(lambda: self.job_control("pause"))
        h.addWidget(self.pause_job)
        self.cancel_job = QPushButton("İşi i&ptal et")
        self.cancel_job.setObjectName("cancel_job")
        self.cancel_job.clicked.connect(lambda: self.job_control("cancel"))
        h.addWidget(self.cancel_job)
        layout.addLayout(h)
        self.job_summary = label("İş seçilmedi.")
        self.job_summary.setObjectName("job_summary")
        layout.addWidget(self.job_summary)
        self.job_rows = table(
            ["Katılımcı", "Durum", "Sayfa / satır", "Sonuç", "Açıklama"], "job_rows"
        )
        self.job_rows.itemDoubleClicked.connect(self.open_job_result)
        layout.addWidget(self.job_rows, 1)
        for w in (self.batch_path, self.mapping_path, self.batch_sheet):
            w.textChanged.connect(self.invalidate_batch)
        self.batch_exam.currentIndexChanged.connect(self.invalidate_batch)
        self.batch_profile.currentIndexChanged.connect(self.invalidate_batch)
        self.inputs.append(self.batch_sheet)

    def history_page(self):
        layout = self.page(
            "Geçmiş sonuç kendi sınav/profil sürümü ve değişmez hesap dökümüyle açılır. 500 kayıtlı sayfalarla bütün geçmişe erişebilirsiniz."
        )
        paging = QHBoxLayout()
        self.history_page_label = label("Geçmiş yükleniyor…")
        paging.addWidget(
            self.button(
                "Önceki sayfa", lambda: self.page_history(-1), "history_previous"
            )
        )
        paging.addWidget(self.history_page_label, 1)
        paging.addWidget(
            self.button("Sonraki sayfa", lambda: self.page_history(1), "history_next")
        )
        layout.addLayout(paging)
        self.history = table(
            ["Kayıt", "Katılımcı", "Durum", "Sınav sürümü", "Profil sürümü", "Tarih"],
            "history",
        )
        self.history.itemDoubleClicked.connect(self.open_history)
        layout.addWidget(self.history)
        h = QHBoxLayout()
        h.addWidget(self.button("Seçilen sonucu &aç", self.open_history, "open_result"))
        h.addWidget(
            self.button(
                "Tekil ekrandaki profille yeniden değerlendir",
                self.rescore_history,
                "rescore_history",
            )
        )
        layout.addLayout(h)

    def participants_page(self):
        layout = self.page(
            "Katılımcı kodu veya takma ad yeterlidir; ad ve iletişim bilgileri isteğe bağlıdır."
        )
        self.participants = table(
            ["Kod", "Takma ad", "Ad", "İletişim bilgileri"], "participants"
        )
        layout.addWidget(self.participants)

    def settings_page(self):
        layout = self.page(
            "Yerel model isteğe bağlıdır. Bu ekran servis başlatmaz, model indirmez veya sistem ayarlarını değiştirmez."
        )
        form = QFormLayout()
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(self.preferences.get("port", 11435))
        self.port.setObjectName("model_port")
        self.model = QLineEdit(self.preferences.get("model", "qwen3.5:4b"))
        self.model.setObjectName("model_name")
        form.addRow("Uygulamanın yerel model portu (11434 kullanılamaz)", self.port)
        form.addRow("Yerel model adı", self.model)
        layout.addLayout(form)
        layout.addWidget(
            self.button(
                "Yerel model durumunu &kontrol et",
                lambda: self.start("model_status", self.model_args()),
                "model_status",
            )
        )
        self.model_message = label(
            "Model durumu henüz kontrol edilmedi. Belgeler, manuel öğrenme ve hazır profille puanlama model olmadan çalışır."
        )
        self.model_message.setObjectName("model_message")
        layout.addWidget(self.model_message)
        layout.addWidget(label(f"Yerel veritabanı: {self.db}"))
        layout.addStretch()
        self.inputs.extend([self.port, self.model])

    def required(self, combo):
        value = combo.currentData()
        if value is None:
            raise ValueError("Önce ilgili envanter / sürümü seçin.")
        return value

    def inv(self):
        return self.required(self.inventory)

    def model_args(self):
        return {"port": self.port.value(), "model": self.model.text().strip()}

    def start(self, op, args):
        if self.task is not None:
            return
        args = dict(args, _history_offset=self.history_offset)
        self.operation = op
        self.followup = None
        self.progress.setRange(0, 0)
        self.status.setText("İşlem sürüyor…")
        self.task = Task(self.backend, op, args, self)
        self.task.done.connect(self.receive)
        self.task.failed.connect(self.error)
        self.task.progress.connect(self.update_progress)
        self.task.finished.connect(self.finished)
        self.set_busy(True)
        self.task.start()

    def error(self, message):
        if isinstance(message, dict):
            issues = message.get("issues", [])
            if self.operation == "preview_batch" and issues:
                rows(
                    self.batch_errors,
                    [
                        [
                            i.get("sheet"),
                            i.get("row"),
                            i.get("participant"),
                            i.get("question"),
                            i.get("message"),
                        ]
                        for i in issues
                        if isinstance(i, dict)
                    ],
                )
                self.batch_summary.setText(
                    "Dosya / sütun doğrulaması başarısız; iş kaydedilmedi."
                )
            message = message["message"]
        self.status.setText("İşlem tamamlanamadı: " + message[:600])
        self.status.setToolTip(message)
        self.followup = None
        if self.operation in ("discover", "interpret", "model_status"):
            self.learning_model_notice.setText(
                message + "\nManuel öğrenme ve hazır profille puanlama kullanılabilir."
            )
            self.model_message.setText(
                message + "\nBelge aktarımı ve hazır profille puanlama kullanılabilir."
            )

    def set_busy(self, busy):
        for w in self.controls + self.inputs + [self.inventory]:
            w.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        self.pause_job.setEnabled(not busy or self.operation == "run_job")
        self.cancel_job.setEnabled(not busy or self.operation == "run_job")
        if not busy:
            self.save_doc.setEnabled(
                self.preview is not None
                and not any(i["severity"] == "error" for i in self.preview["issues"])
            )
            self.prepare_button.setEnabled(self.batch_preview is not None)
            self.evaluate_button.setEnabled(
                self.loaded_version is not None
                and self.loaded_version == self.exam.currentData()
            )

    def receive(self, data):
        if "inventory_id" in data:
            self.preferences["inventory_id"] = data["inventory_id"]
        self.apply_catalog(data.get("catalog", {}))
        self.status.setText("İşlem tamamlandı.")
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        if "preview" in data:
            self.preview = data["preview"]
            p = self.preview
            self.doc_summary.setText(
                f"{Path(p['path']).name}: {p['questions']} soru, {p['answer_keys']} anahtar, {len(p['issues'])} bildirim. Kayıt öncesi inceleyin."
            )
            rows(
                self.doc_records,
                [[i["number"], i["text"], i["location"]] for i in p["sample"]],
            )
            rows(
                self.doc_issues,
                [
                    [
                        {"error": "Hata", "warning": "Uyarı", "info": "Bilgi"}.get(
                            i["severity"]
                        ),
                        i["item"],
                        i["location"],
                        i["message"],
                    ]
                    for i in p["issues"]
                ],
            )
            self.doc_text.setPlainText(p["text_sample"])
        if "imported" in data:
            self.status.setText(
                "Belge kaydedildi. Öğrenme Merkezi ile eksikleri inceleyebilirsiniz."
            )
        if "gaps" in data:
            self.show_learning(data)
        if "answer" in data:
            if data["answer"].get("model_error"):
                notice = "Yanıt kaydedildi; yerel model kullanılamadı. Manuel bilgi öğretme ve hazır profille puanlama kullanılabilir."
                self.learning_model_notice.setText(notice)
                self.model_message.setText(notice)
            self.status.setText(
                "Yanıt kalıcı olarak kaydedildi: " + text(data["answer"]["state"])
            )
            self.followup = ("learning", {"inventory_id": self.inv()})
        if "discovery" in data:
            self.status.setText(
                "Yerel analiz tamamlandı; öneriler ve kaynaklar incelenebilir."
            )
            self.followup = ("learning", {"inventory_id": self.inv()})
        if "items" in data:
            self.loaded_version = data["version_id"]
            self.answer_table.setRowCount(len(data["items"]))
            for j, item in enumerate(data["items"]):
                self.answer_table.setItem(j, 0, QTableWidgetItem(str(item["number"])))
                self.answer_table.setItem(j, 1, QTableWidgetItem(item["text"]))
                choice = QComboBox()
                choice.setAccessibleName(f"{item['number']}. soru cevabı")
                choice.addItem("Yanıt yok", None)
                for code, title in item["options"]:
                    choice.addItem(f"{code} — {title}", code)
                self.answer_table.setCellWidget(j, 2, choice)
            self.answer_table.setColumnWidth(1, 450)
        if "result" in data:
            self.show_result({"id": data["assessment_id"], "result": data["result"]})
        if "assessment" in data:
            self.show_result(data["assessment"])
        if "batch_preview" in data:
            self.show_batch_preview(data["batch_preview"])
        if "job" in data:
            self.job_id = data["job"]["id"]
            self.show_job(data["job"])
            for n, r in enumerate(self.catalog_data.get("jobs", [])):
                if r["id"] == self.job_id:
                    self.jobs.selectRow(n)
        if (
            self.operation == "refresh"
            and self.nav.currentRow() == 1
            and self.inventory.currentData() is not None
        ):
            self.followup = ("learning", {"inventory_id": self.inv()})
        if "model_status" in data:
            self.model_message.setText(data["model_status"]["message"])
            self.learning_model_notice.setText(data["model_status"]["message"])

    def finished(self):
        op = self.operation
        task = self.task
        self.task = None
        task.deleteLater()
        self.set_busy(False)
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
        self.operation_finished.emit(op)
        if self.closing:
            QTimer.singleShot(0, self.close)
        elif self.followup:
            op, args = self.followup
            self.followup = None
            QTimer.singleShot(0, lambda: self.start(op, args))

    def update_progress(self, data):
        self.status.setText(data.get("message", "İşlem sürüyor…"))
        if "value" in data:
            self.progress.setRange(0, 100)
            self.progress.setValue(data["value"])
        if "job" in data:
            self.show_job(data["job"])

    def cancel_active(self, action):
        if self.task:
            self.task.request_cancel(action)
            self.status.setText(
                "İptal / duraklatma istendi; güvenli işlem sınırı bekleniyor…"
            )

    def inventory_changed(self):
        if not hasattr(self, "batch_exam"):
            return
        if self.inventory.currentData() is not None:
            self.preferences["inventory_id"] = self.inventory.currentData()
        self.invalidate_document()
        self.invalidate_batch()
        self.invalidate_answers()
        self.learning_data = {}
        self.gap_table.setRowCount(0)
        self.populate_versions()
        if self.nav.currentRow() == 1:
            self.load_learning()

    def fill_combo(self, w, values, preferred=None):
        old = preferred if preferred is not None else w.currentData()
        w.blockSignals(True)
        w.clear()
        for title, value in values:
            w.addItem(title, value)
        idx = w.findData(old)
        if idx >= 0:
            w.setCurrentIndex(idx)
        w.blockSignals(False)

    def apply_catalog(self, data):
        if not data:
            return
        self.catalog_data = data
        old = self.inventory.currentData()
        preferred = old if old is not None else self.preferences.get("inventory_id")
        if self.operation == "create_inventory":
            preferred = self.preferences["inventory_id"]
        self.fill_combo(
            self.inventory,
            [(f"{r['name']} (#{r['id']})", r["id"]) for r in data["inventories"]],
            preferred,
        )
        if old != self.inventory.currentData():
            self.invalidate_document()
            self.invalidate_answers()
            self.invalidate_batch()
        self.populate_versions()
        self.history_total = data["history_total"]
        self.history_page_label.setText(
            f"{self.history_offset + 1 if self.history_total else 0}–{self.history_offset + len(data['history'])} / {self.history_total} kayıt"
        )
        self.history_data = data["history"]
        rows(
            self.history,
            [
                [
                    r["id"],
                    r["alias"] or r["code"],
                    r["status"],
                    r["inventory_version_id"],
                    r["profile_version_id"],
                    r["created_at"],
                ]
                for r in data["history"]
            ],
        )
        self.job_data = data["jobs"]
        rows(
            self.jobs,
            [
                [
                    r["id"],
                    r["state"],
                    f"{r['inventory_version_id']} / {r['profile_version_id']}",
                    r["counts"]["succeeded"],
                    r["counts"]["partial"],
                    r["counts"]["failed"],
                    r["counts"]["pending"],
                ]
                for r in data["jobs"]
            ],
        )
        rows(
            self.participants,
            [
                [r["code"], r["alias"], r["full_name"], r["attributes"]]
                for r in data["participants"]
            ],
        )

    def populate_versions(self):
        inv = self.inventory.currentData()
        d = self.catalog_data
        versions = [
            (f"v{v['version']} — #{v['id']} {v['label'] or ''}", v["id"])
            for v in d.get("versions", [])
            if v["inventory_id"] == inv
        ]
        profiles = [
            (
                f"v{p['version']} — #{p['id']} ({'Geçici' if p['provisional'] else 'Doğrulanmış'}), sınav #{p['inventory_version_id']}",
                p["id"],
            )
            for p in d.get("profiles", [])
            if p["inventory_id"] == inv
        ]
        for w, name in ((self.exam, "exam"), (self.batch_exam, "batch_exam")):
            self.fill_combo(
                w,
                versions,
                w.currentData()
                if w.currentData() is not None
                else self.preferences.get(name),
            )
        for w, name in (
            (self.profile, "profile"),
            (self.batch_profile, "batch_profile"),
        ):
            self.fill_combo(
                w,
                profiles,
                w.currentData()
                if w.currentData() is not None
                else self.preferences.get(name),
            )

    def invalidate_document(self):
        self.preview = None
        self.save_doc.setEnabled(False)

    def invalidate_batch(self):
        self.batch_preview = None
        self.prepare_button.setEnabled(False)

    def invalidate_answers(self):
        self.loaded_version = None
        self.evaluate_button.setEnabled(False)
        self.answer_table.setRowCount(0)

    def preview_document(self):
        self.invalidate_document()
        try:
            self.start(
                "preview_document",
                {
                    "path": self.doc_path.text(),
                    "role": self.doc_role.currentData(),
                    "inventory_id": self.inv(),
                },
            )
        except ValueError as e:
            self.error(str(e))

    def load_learning(self):
        try:
            self.start("learning", {"inventory_id": self.inv()})
        except ValueError as e:
            self.error(str(e))

    def show_learning(self, data):
        self.learning_data = data
        mode = self.gap_filter.currentData()
        self.gap_data = [
            g
            for g in data["gaps"]
            if mode == "all"
            or (
                mode == "deferred"
                and (g["state"] == "DEFERRED" or g["payload"].get("dont_know"))
            )
            or (
                mode == "active"
                and g["state"] not in ("CONFIRMED", "REJECTED", "DEFERRED")
                and not g["payload"].get("dont_know")
            )
        ]
        rows(
            self.gap_table,
            [
                [
                    g["id"],
                    {
                        "CRITICAL": "Kritik",
                        "HIGH": "Yüksek",
                        "MEDIUM": "Orta",
                        "LOW": "Düşük",
                    }.get(g["severity"]),
                    g["state"],
                    g["payload"]["missing_info"],
                ]
                for g in self.gap_data
            ],
        )
        rows(
            self.rule_table,
            [
                [
                    e["summary"],
                    "REJECTED" if e["validation"] == "rejected" else e["confidence"],
                    {
                        "user": "Kullanıcı",
                        "document": "Belge",
                        "ai_inference": "Model çıkarımı",
                        "example_fit": "Örnek uyumu",
                    }.get(e["source"], e["source"]),
                    e["version"],
                ]
                for e in data["rules"]
            ],
        )
        self.sources.clear()
        for e in data["evidence"]:
            tree_value(
                self.sources,
                e.get("file_name") or "Kullanıcı açıklaması",
                {
                    "Sayfa": e.get("page"),
                    "Alıntı": e.get("quote"),
                    "Not": e.get("note"),
                    "Konum": e.get("locator"),
                },
            )
        for key in data.get("answer_key_sources", []):
            tree_value(
                self.sources,
                "Cevap anahtarı: " + key["file_name"],
                {
                    "Soru": key["item_number"],
                    "Değer": key["value"],
                    "Konum": key["locator"],
                },
            )
        if self.gap_data:
            self.gap_table.selectRow(0)

    def select_gap(self):
        index = self.gap_table.currentRow()
        if index < 0 or index >= len(getattr(self, "gap_data", [])):
            return
        g = self.gap_data[index]
        p = g["payload"]
        self.gap_detail.setPlainText(
            f"{p['question']}\n\nNeden: {p['why']}\nBoyutlar: {p['dimensions']}\nSorular: {p['items']}\nKanıtlar: {p['evidence']}\nÖnerilen bilgi: {p.get('proposal') or p.get('candidate_rules') or 'Henüz yok'}\nDurum: {text(g['state'])}"
        )
        self.rule_editor.dimension.setText(
            p["dimensions"][0] if p["dimensions"][0] != "*" else ""
        )
        aliases = {
            "zero_policy": "zero_score",
            "confirmation": (p.get("proposal") or {}).get("rule_type"),
        }
        idx = self.rule_editor.kind.findData(aliases.get(g["category"], g["category"]))
        if idx >= 0:
            self.rule_editor.kind.setCurrentIndex(idx)

    def answer_gap(self, action):
        try:
            i = self.gap_table.currentRow()
            if i < 0:
                raise ValueError("Önce bir soru seçin.")
            g = self.gap_data[i]
            a = {
                "gap_id": g["id"],
                "action": action,
                "text": self.answer_text.toPlainText(),
            }
            if action in ("teach", "correct"):
                a["structured"] = self.rule_editor.proposal()
            if action == "resume" and not a["text"]:
                a["text"] = "Kullanıcı bu soruyu yeniden açtı."
            if action == "interpret":
                a.update(self.model_args())
                self.start("interpret", a)
            else:
                self.start("answer", a)
        except (ValueError, KeyError) as e:
            self.error(str(e))

    def evaluate_single(self):
        try:
            if self.loaded_version != self.required(self.exam):
                raise ValueError("Seçilen sürümün sorularını yükleyin.")
            responses = [
                [
                    int(self.answer_table.item(n, 0).text()),
                    self.answer_table.cellWidget(n, 2).currentData(),
                ]
                for n in range(self.answer_table.rowCount())
            ]
            self.start(
                "single",
                {
                    "inventory_version_id": self.required(self.exam),
                    "profile_version_id": self.required(self.profile),
                    "participant_code": self.code.text() or None,
                    "alias": self.alias.text() or None,
                    "responses": responses,
                },
            )
        except ValueError as e:
            self.error(str(e))

    def show_result(self, assessment):
        dialog = ResultDialog(assessment, self)
        self.dialogs.append(dialog)
        dialog.show()
        self.last_result = assessment

    def preview_responses(self):
        self.invalidate_batch()
        self.mapping_tree.clear()
        self.batch_errors.setRowCount(0)
        try:
            self.start(
                "preview_batch",
                {
                    "path": self.batch_path.text(),
                    "inventory_version_id": self.required(self.batch_exam),
                    "profile_version_id": self.required(self.batch_profile),
                    "mapping_path": self.mapping_path.text(),
                    "sheets": [self.batch_sheet.text().strip()]
                    if self.batch_sheet.text().strip()
                    else None,
                },
            )
        except (ValueError, OSError) as e:
            self.error(str(e))

    def show_batch_preview(self, p):
        self.batch_preview = p
        self.batch_summary.setText(
            f"{p['total']} katılımcı; {p['failed']} hatalı satır. Önizleme ilk 500 kayıtla sınırlıdır."
        )
        self.mapping_tree.clear()
        for sheet, m in p["mapping"].items():
            node = QTreeWidgetItem(self.mapping_tree, [sheet, ""])
            for role, index in m["roles"].items():
                QTreeWidgetItem(
                    node,
                    [
                        m["headers"][index],
                        {
                            "participant_code": "Katılımcı kodu",
                            "alias": "Takma ad",
                            "full_name": "Ad",
                            "email": "E-posta",
                            "phone": "Telefon",
                        }.get(role, role),
                    ],
                )
            for q, index in m["questions"].items():
                QTreeWidgetItem(node, [m["headers"][index], f"{q}. soru"])
            for ignored in m["ignored"]:
                QTreeWidgetItem(node, [ignored, "Eşleme dışında"])
        errors = p.get("issues", [e for r in p["rows"] for e in r["errors"]])
        rows(
            self.batch_errors,
            [
                [e["sheet"], e["row"], e["participant"], e["question"], e["message"]]
                for e in errors
            ],
        )

    def selected_job(self):
        n = self.jobs.currentRow()
        if n < 0:
            raise ValueError("Önce bir iş seçin.")
        return self.job_data[n]["id"]

    def open_job(self):
        try:
            self.start("job", {"job_id": self.selected_job()})
        except ValueError as e:
            self.error(str(e))

    def run_selected_job(self):
        try:
            self.job_id = self.selected_job()
            self.start("run_job", {"job_id": self.job_id, "cancel_action": "pause"})
        except ValueError as e:
            self.error(str(e))

    def job_control(self, action):
        if self.task and self.operation == "run_job":
            self.cancel_active(action)
        elif self.task is None:
            try:
                self.start(
                    "control_job", {"job_id": self.selected_job(), "action": action}
                )
            except ValueError as e:
                self.error(str(e))

    def show_job(self, job):
        c = job["counts"]
        self.job_summary.setText(
            f"İş #{job['id']} · {text(job['state'])} · sabit sınav #{job['inventory_version_id']} / profil #{job['profile_version_id']} · {job['done']}/{job['total']} · başarılı {c['succeeded']}, kısmi {c['partial']}, hatalı {c['failed']}, bekleyen {c['pending']}"
        )
        if "rows" in job:
            self.job_row_data = job["rows"][:500]
            rows(
                self.job_rows,
                [
                    [
                        r["identity"].get("participant_code")
                        or r["identity"].get("alias"),
                        r["state"],
                        f"{r['sheet_name'] or 'CSV'} / {r['row_number']}",
                        r.get("full_profile") or r.get("assessment_id"),
                        "; ".join(r["limitations"])
                        or "; ".join(e["message"] for e in r["errors"]),
                    ]
                    for r in self.job_row_data
                ],
            )

    def open_job_result(self):
        n = self.job_rows.currentRow()
        if n >= 0 and self.job_row_data[n]["assessment_id"]:
            self.start(
                "result", {"assessment_id": self.job_row_data[n]["assessment_id"]}
            )

    def page_history(self, direction):
        offset = self.history_offset + direction * 500
        if offset < 0 or offset >= self.history_total:
            return
        self.history_offset = offset
        self.start("refresh", {})

    def open_history(self):
        n = self.history.currentRow()
        if n >= 0:
            self.start("result", {"assessment_id": self.history_data[n]["id"]})

    def rescore_history(self):
        try:
            n = self.history.currentRow()
            if n < 0:
                raise ValueError("Önce geçmişten bir sonuç seçin.")
            self.start(
                "rescore",
                {
                    "assessment_id": self.history_data[n]["id"],
                    "profile_version_id": self.required(self.profile),
                },
            )
        except ValueError as e:
            self.error(str(e))

    def _read_preferences(self):
        try:
            d = json.loads(self.db.with_suffix(".ui.json").read_text(encoding="utf-8"))
            if not isinstance(d, dict):
                return {}
            valid = {}
            if type(d.get("page")) is int and 0 <= d["page"] < 7:
                valid["page"] = d["page"]
            if type(d.get("inventory_id")) is int and d["inventory_id"] > 0:
                valid["inventory_id"] = d["inventory_id"]
            if (
                type(d.get("port")) is int
                and 1024 <= d["port"] <= 65535
                and d["port"] != 11434
            ):
                valid["port"] = d["port"]
            if isinstance(d.get("model"), str) and 0 < len(d["model"]) <= 128:
                valid["model"] = d["model"]
            for key in ("exam", "profile", "batch_exam", "batch_profile"):
                if type(d.get(key)) is int and d[key] > 0:
                    valid[key] = d[key]
            if d.get("gap_filter") in ("active", "deferred", "all"):
                valid["gap_filter"] = d["gap_filter"]
            return valid
        except (OSError, ValueError):
            return {}

    def closeEvent(self, event):
        if self.task:
            self.closing = True
            self.cancel_active("pause")
            self.status.setText(
                "Kapanış bekleniyor: işlem güvenli sınırda tamamlanıyor; toplu iş daha sonra sürdürülebilir."
            )
            event.ignore()
            return
        try:
            data = {
                "page": self.nav.currentRow(),
                "inventory_id": self.inventory.currentData(),
                "port": self.port.value(),
                "model": self.model.text(),
                "exam": self.exam.currentData(),
                "profile": self.profile.currentData(),
                "batch_exam": self.batch_exam.currentData(),
                "batch_profile": self.batch_profile.currentData(),
                "gap_filter": self.gap_filter.currentData(),
            }
            path = self.db.with_suffix(".ui.json")
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            temp.replace(path)
        except OSError:
            pass
        for dialog in self.dialogs:
            dialog.close()
        event.accept()
