"""Settings and glossary dialogs."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core.project import Project
from ..core.settings import Settings
from ..translators import cuda


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None, on_gpu_download=None):
        super().__init__(parent)
        self.setWindowTitle("الإعدادات")
        self.s = settings
        lay = QVBoxLayout(self)

        g1 = QGroupBox("الترجمة بدون إنترنت (NLLB)")
        f1 = QFormLayout(g1)
        self.device = QComboBox()
        for key, label in (("auto", "تلقائي (كرت الشاشة إن وُجد)"), ("cuda", "كرت الشاشة NVIDIA"), ("cpu", "المعالج فقط")):
            self.device.addItem(label, key)
        self.device.setCurrentIndex(max(0, self.device.findData(settings.device)))
        f1.addRow("الجهاز:", self.device)
        self.beam = QSpinBox()
        self.beam.setRange(1, 8)
        self.beam.setValue(settings.beam_size)
        self.beam.setToolTip("أعلى = أدق قليلاً وأبطأ")
        f1.addRow("دقة البحث (beam):", self.beam)
        self.repo = QLineEdit(settings.model_repo)
        self.repo.setPlaceholderText("اختياري: اسم مستودع على Hugging Face لنموذج CTranslate2")
        f1.addRow("مصدر النموذج:", self.repo)
        self.hf = QLineEdit(settings.hf_token)
        self.hf.setEchoMode(QLineEdit.Password)
        self.hf.setPlaceholderText("اختياري")
        f1.addRow("رمز Hugging Face:", self.hf)
        gpu_row = QHBoxLayout()
        self.gpu_label = QLabel("مكتبات كرت الشاشة: " + ("منزّلة ✓" if cuda.installed() else "غير منزّلة"))
        gpu_btn = QPushButton("تنزيل دعم كرت الشاشة (~550MB)")
        gpu_btn.setToolTip("مطلوب مرة واحدة لتشغيل الترجمة على كرت NVIDIA. يُنزَّل من NVIDIA عبر PyPI.")
        gpu_btn.clicked.connect(lambda: (on_gpu_download() if on_gpu_download else None, self.accept()))
        gpu_row.addWidget(self.gpu_label)
        gpu_row.addWidget(gpu_btn)
        f1.addRow(gpu_row)
        lay.addWidget(g1)

        g2 = QGroupBox("الترجمة عبر الإنترنت")
        f2 = QFormLayout(g2)
        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.5, 10)
        self.interval.setSingleStep(0.5)
        self.interval.setValue(settings.google_interval)
        f2.addRow("Google: ثوانٍ بين الطلبات:", self.interval)
        self.deepl = QLineEdit(settings.deepl_key)
        self.deepl.setEchoMode(QLineEdit.Password)
        f2.addRow("مفتاح DeepL:", self.deepl)
        lay.addWidget(g2)

        g3 = QGroupBox("التعريب داخل اللعبة")
        v3 = QVBoxLayout(g3)
        self.align = QCheckBox("محاذاة الحوار لليمين")
        self.align.setChecked(settings.align_right)
        self.force = QCheckBox("تشغيل اللعبة بالعربي مباشرة")
        self.force.setChecked(settings.force_language)
        self.fonts = QCheckBox("استبدال خطوط اللعبة بخط عربي")
        self.fonts.setChecked(settings.replace_fonts)
        for w in (self.align, self.force, self.fonts):
            v3.addWidget(w)
        row = QHBoxLayout()
        row.addWidget(QLabel("طول سطر الرسالة في المحركات اليابانية (حروف، 0 = تلقائي):"))
        self.line_em = QSpinBox()
        self.line_em.setRange(0, 80)
        self.line_em.setValue(int(settings.legacy_line_em))
        self.line_em.setToolTip("CatSystem2 و KiriKiri وغيرها: يُقسَّم النص العربي إلى أسطر بهذا الطول. قلّله إذا خرج النص من النافذة.")
        row.addWidget(self.line_em)
        v3.addLayout(row)
        lay.addWidget(g3)

        g4 = QGroupBox("الخصوصية")
        v4 = QVBoxLayout(g4)
        self.private = QCheckBox("وضع التخفي: لا يُحفظ أي مشروع أو سجل على القرص (يُطبَّق عند فتح لعبة)")
        self.private.setChecked(settings.private_mode)
        v4.addWidget(self.private)
        lay.addWidget(g4)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def accept(self):
        s = self.s
        s.device = self.device.currentData()
        s.beam_size = self.beam.value()
        s.model_repo = self.repo.text().strip()
        s.hf_token = self.hf.text().strip()
        s.google_interval = self.interval.value()
        s.deepl_key = self.deepl.text().strip()
        s.align_right = self.align.isChecked()
        s.force_language = self.force.isChecked()
        s.replace_fonts = self.fonts.isChecked()
        s.legacy_line_em = self.line_em.value()
        s.private_mode = self.private.isChecked()
        s.save()
        super().accept()


class GlossaryDialog(QDialog):
    """Fix how character names (and any other terms) are written in Arabic."""

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.setWindowTitle("قاموس الأسماء")
        self.resize(560, 520)
        self.project = project
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("اكتب الاسم بالعربي مرة واحدة، ويُستخدم نفسه في كل الحوارات."))
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["الأصل", "بالعربي"])
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table)
        glossary = project.glossary()
        names = [r.original for r in project.rows("kind='name'")]
        for src in list(dict.fromkeys(names + list(glossary))):
            self._add(src, glossary.get(src, ""))
        row = QHBoxLayout()
        self.new_src = QLineEdit()
        self.new_src.setPlaceholderText("مصطلح جديد")
        add = QPushButton("إضافة")
        add.clicked.connect(lambda: (self._add(self.new_src.text().strip(), ""), self.new_src.clear()) if self.new_src.text().strip() else None)
        row.addWidget(self.new_src)
        row.addWidget(add)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _add(self, src: str, dst: str) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        it = QTableWidgetItem(src)
        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(r, 0, it)
        self.table.setItem(r, 1, QTableWidgetItem(dst))

    def accept(self):
        for r in range(self.table.rowCount()):
            src = self.table.item(r, 0).text()
            dst = self.table.item(r, 1).text() if self.table.item(r, 1) else ""
            self.project.set_glossary(src, dst.strip())
        QMessageBox.information(self, "قاموس الأسماء", "تم الحفظ. الأسماء تُطبَّق في الترجمة القادمة.")
        super().accept()
