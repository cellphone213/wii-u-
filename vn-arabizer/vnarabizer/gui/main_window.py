"""Main window."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__
from ..core import pipeline
from ..core.project import Project
from ..core.settings import Settings
from ..engines import detect
from ..engines.base import Engine
from ..translators import LANGUAGES, TRANSLATORS, cuda, models
from ..translators.download import Cancelled
from .dialogs import GlossaryDialog, SettingsDialog
from .table import KIND_AR, STATUS_AR, LinesFilter, LinesModel
from .workers import Job


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:,.0f} MB"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = Settings.load()
        self.engine: Engine | None = None
        self.project: Project | None = None
        self.job: Job | None = None
        self._gpu_declined = False
        self.pool = QThreadPool.globalInstance()

        self.setWindowTitle(f"{APP_NAME} {__version__} — معرّب الروايات المرئية")
        self.resize(1280, 820)
        self._build()
        self._refresh_model_state()
        self._update_buttons()
        if self.settings.last_game and Path(self.settings.last_game).exists() and not self.settings.private_mode:
            QTimer.singleShot(0, lambda: self.open_game(self.settings.last_game))

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)

        # Row 1: game
        r1 = QHBoxLayout()
        self.btn_open = QPushButton("📂 فتح لعبة")
        self.btn_open.clicked.connect(self.choose_game)
        self.game_label = QLabel("لم تُفتح لعبة بعد. اختر مجلد اللعبة (المجلد الذي فيه ملف التشغيل).")
        self.game_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.btn_extract = QPushButton("① استخراج النصوص")
        self.btn_extract.clicked.connect(self.extract)
        self.btn_glossary = QPushButton("قاموس الأسماء")
        self.btn_glossary.clicked.connect(self.edit_glossary)
        self.btn_settings = QPushButton("⚙ الإعدادات")
        self.btn_settings.clicked.connect(self.open_settings)
        for w in (self.btn_open, self.game_label):
            r1.addWidget(w)
        r1.addStretch(1)
        for w in (self.btn_extract, self.btn_glossary, self.btn_settings):
            r1.addWidget(w)
        root.addLayout(r1)

        # Row 2: translation engine
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("المترجم:"))
        self.cmb_translator = QComboBox()
        for tid, cls in TRANSLATORS.items():
            self.cmb_translator.addItem(cls.name, tid)
        self.cmb_translator.setCurrentIndex(max(0, self.cmb_translator.findData(self.settings.translator)))
        self.cmb_translator.currentIndexChanged.connect(self._translator_changed)
        r2.addWidget(self.cmb_translator)

        self.cmb_model = QComboBox()
        for key, spec in models.MODELS.items():
            self.cmb_model.addItem(f"{spec.title}  {spec.size_hint}", key)
        self.cmb_model.setCurrentIndex(max(0, self.cmb_model.findData(self.settings.model)))
        self.cmb_model.currentIndexChanged.connect(self._model_changed)
        r2.addWidget(self.cmb_model)
        self.btn_download = QPushButton("⬇ تنزيل النموذج")
        self.btn_download.clicked.connect(self.download_model)
        r2.addWidget(self.btn_download)
        self.model_state = QLabel()
        r2.addWidget(self.model_state)

        r2.addSpacing(16)
        r2.addWidget(QLabel("لغة اللعبة:"))
        self.cmb_source = QComboBox()
        for key, (label, _) in LANGUAGES.items():
            self.cmb_source.addItem(label, key)
        self.cmb_source.setCurrentIndex(max(0, self.cmb_source.findData(self.settings.source_language)))
        r2.addWidget(self.cmb_source)
        self.chk_redo = QCheckBox("إعادة ترجمة الأسطر الآلية")
        r2.addWidget(self.chk_redo)
        r2.addStretch(1)
        self.btn_translate = QPushButton("② ترجمة")
        self.btn_translate.clicked.connect(self.translate)
        self.btn_apply = QPushButton("③ تطبيق على اللعبة")
        self.btn_apply.clicked.connect(self.apply_patch)
        self.btn_remove = QPushButton("إزالة التعريب")
        self.btn_remove.clicked.connect(self.remove_patch)
        self.btn_run = QPushButton("▶ تشغيل اللعبة")
        self.btn_run.clicked.connect(self.run_game)
        for w in (self.btn_translate, self.btn_apply, self.btn_remove, self.btn_run):
            r2.addWidget(w)
        root.addLayout(r2)

        # Row 3: filters
        r3 = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔎 بحث في الأصل أو الترجمة أو المتكلم")
        self.cmb_status = QComboBox()
        self.cmb_status.addItem("كل الحالات", "")
        for k, v in STATUS_AR.items():
            self.cmb_status.addItem(v, k)
        self.cmb_kind = QComboBox()
        self.cmb_kind.addItem("كل الأنواع", "")
        for k, v in KIND_AR.items():
            self.cmb_kind.addItem(v, k)
        self.stats_label = QLabel()
        r3.addWidget(self.search, 2)
        r3.addWidget(self.cmb_status)
        r3.addWidget(self.cmb_kind)
        r3.addWidget(self.stats_label, 1)
        root.addLayout(r3)

        # Table + log
        self.model = LinesModel()
        self.proxy = LinesFilter()
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setDefaultSectionSize(28)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        for col, w in ((0, 60), (1, 110), (2, 420), (3, 420), (4, 100), (5, 160)):
            self.table.setColumnWidth(col, w)
        hh.setStretchLastSection(True)
        self.search.textChanged.connect(lambda t: self.proxy.set(text=t))
        self.cmb_status.currentIndexChanged.connect(lambda: self.proxy.set(status=self.cmb_status.currentData()))
        self.cmb_kind.currentIndexChanged.connect(lambda: self.proxy.set(kind=self.cmb_kind.currentData()))
        self.model.dataChanged.connect(lambda *a: self._update_stats())

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        split = QSplitter(Qt.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.log_view)
        split.setSizes([640, 140])
        root.addWidget(split, 1)

        # Progress
        r4 = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setFormat("%v / %m")
        self.btn_cancel = QPushButton("إيقاف")
        self.btn_cancel.clicked.connect(self.cancel_job)
        r4.addWidget(self.progress, 1)
        r4.addWidget(self.btn_cancel)
        root.addLayout(r4)

        self.setCentralWidget(central)
        self._translator_changed()

    # ----------------------------------------------------------------- helpers
    def log(self, msg: str) -> None:
        self.log_view.appendPlainText(msg.rstrip())

    def _busy(self) -> bool:
        return self.job is not None

    def _update_buttons(self) -> None:
        busy = self._busy()
        has_game = self.engine is not None
        has_rows = bool(self.project and self.project.stats()["total"])
        self.btn_open.setEnabled(not busy)
        self.btn_extract.setEnabled(has_game and not busy)
        self.btn_glossary.setEnabled(has_rows and not busy)
        self.btn_translate.setEnabled(has_rows and not busy)
        self.btn_apply.setEnabled(has_rows and not busy)
        self.btn_remove.setEnabled(has_game and not busy and self.engine.is_applied())
        self.btn_run.setEnabled(has_game and not busy)
        self.btn_download.setEnabled(not busy)
        self.btn_settings.setEnabled(not busy)
        self.btn_cancel.setEnabled(busy)
        # Lines cannot be edited while a job writes to the project.
        self.table.setEditTriggers(
            QAbstractItemView.NoEditTriggers
            if busy
            else QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed
        )

    def _update_stats(self) -> None:
        if not self.project:
            self.stats_label.setText("")
            return
        s = self.project.stats()
        self.stats_label.setText(
            f"المجموع {s['total']:,} · مترجم {s['machine'] + s['edited']:,} · جديد {s['new']:,} · أخطاء {s['error']:,} · لا يُترجم {s['locked']:,}"
        )

    def _refresh_model_state(self) -> None:
        key = self.cmb_model.currentData()
        if models.is_installed(key):
            repo = models.installed_info(key).get("repo", "")
            self.model_state.setText("✓ منزّل")
            self.model_state.setToolTip(repo)
            self.btn_download.setText("حذف النموذج")
        else:
            self.model_state.setText("غير منزّل")
            self.btn_download.setText("⬇ تنزيل النموذج")

    def _translator_changed(self) -> None:
        tid = self.cmb_translator.currentData()
        nllb = tid == "nllb"
        for w in (self.cmb_model, self.btn_download, self.model_state):
            w.setVisible(nllb)
        self.settings.translator = tid
        self.settings.save()
        if tid == "google":
            self.log("تنبيه خصوصية: Google يرى النصوص وعنوان الـ IP. للخصوصية الكاملة استخدم NLLB.")

    def _model_changed(self) -> None:
        self.settings.model = self.cmb_model.currentData()
        self.settings.save()
        self._refresh_model_state()

    def _start(self, fn, on_done, title: str) -> Job:
        job = Job(fn)
        self.job = job
        job.signals.log.connect(self.log)
        job.signals.progress.connect(self._on_progress)

        def done(result):
            self.job = None
            self.progress.setFormat("%v / %m")
            on_done(result)
            self._update_buttons()

        def failed(msg):
            self.job = None
            self._update_buttons()
            if msg != "cancelled":
                QMessageBox.warning(self, title, msg)
            self.log(f"✗ {title}: {msg}")

        job.signals.finished.connect(done)
        job.signals.failed.connect(failed)
        self.log(f"… {title}")
        self._update_buttons()
        self.pool.start(job)
        return job

    def _on_progress(self, done: int, total: int, msg: str) -> None:
        self.progress.setMaximum(max(1, total))
        self.progress.setValue(done)
        if msg:
            self.progress.setFormat(msg)

    def cancel_job(self) -> None:
        if self.job:
            self.job.cancel()
            self.log("جارٍ الإيقاف…")

    def _apply_engine_options(self, eng: Engine) -> None:
        opts = getattr(eng, "options", None)  # Ren'Py patch options; other engines have none
        if opts is not None:
            opts.align_right = self.settings.align_right
            opts.force_language = self.settings.force_language
            opts.replace_fonts = self.settings.replace_fonts

    # ------------------------------------------------------------------ actions
    def choose_game(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "اختر مجلد اللعبة", self.settings.last_game or str(Path.home()))
        if d:
            self.open_game(d)

    def open_game(self, path: str) -> None:
        eng = detect(path)
        if eng is None:
            QMessageBox.warning(
                self,
                "محرك غير مدعوم",
                "لم أتعرف على محرك هذه اللعبة. حالياً يدعم البرنامج Ren'Py وRPG Maker MV/MZ وألعاب Twine (HTML)، والمحركات الأخرى قادمة.\n"
                "تأكد أنك اخترت المجلد الرئيسي للعبة (الذي فيه ملف التشغيل).",
            )
            return
        if self.project:
            self.project.close()
        self.engine = eng
        self._apply_engine_options(eng)
        self.project = Project(eng.root, private=self.settings.private_mode)
        self.project.markup = eng.markup
        self.model.project = self.project
        self.model.reload()
        self.game_label.setText(f"🎮 {eng.root.name} — {eng.describe()}" + ("  · وضع التخفي" if self.settings.private_mode else ""))
        self.settings.last_game = str(eng.root)
        self.settings.save()
        self.log(f"فُتحت اللعبة: {eng.root} ({eng.describe()})")
        self._update_stats()
        self._update_buttons()
        if not self.project.stats()["total"]:
            self.extract()

    def extract(self) -> None:
        eng, project = self.engine, self.project

        def work(job: Job):
            entries = eng.extract(lambda m, f: job.signals.progress.emit(int(f * 1000), 1000, m))
            for w in eng.warnings:
                job.signals.log.emit("تحذير: " + w)
            return project.merge_entries(entries)

        def done(result):
            added, total = result
            self.model.reload()
            self._update_stats()
            self.log(f"✓ استُخرج {total:,} سطر ({added:,} جديد).")

        self._start(work, done, "استخراج النصوص")

    def translate(self) -> None:
        tid = self.cmb_translator.currentData()
        s = self.settings
        if tid == "nllb":
            translator = TRANSLATORS[tid](model_key=self.cmb_model.currentData(), device=s.device, beam_size=s.beam_size)
        elif tid == "google":
            translator = TRANSLATORS[tid](min_interval=s.google_interval)
        else:
            translator = TRANSLATORS[tid](api_key=s.deepl_key)
        ok, msg = translator.ready()
        if not ok:
            QMessageBox.information(self, "المترجم غير جاهز", msg)
            return
        if tid == "nllb" and self._offer_gpu_download():
            return
        source = self.cmb_source.currentData()
        s.source_language = source
        s.save()
        project = self.project
        rows = project.pending(retranslate_machine=self.chk_redo.isChecked())
        if not rows:
            QMessageBox.information(self, "لا شيء للترجمة", "كل الأسطر مترجمة. فعّل «إعادة ترجمة الأسطر الآلية» لإعادتها.")
            return

        def work(job: Job):
            translator.log = job.signals.log.emit
            st = pipeline.run(
                project,
                translator,
                source,
                rows,
                progress=lambda d, t, m: job.signals.progress.emit(d, t, f"ترجمة {d:,}/{t:,}"),
                cancel=lambda: job.cancelled,
            )
            if getattr(translator, "device", None):
                job.signals.log.emit(f"الجهاز المستخدم: {translator.device}")
            problem = getattr(translator, "gpu_problem", "")
            if problem == "missing_libs":
                job.signals.log.emit("كرت الشاشة لم يُستخدم لأن مكتباته غير منزّلة: الإعدادات ← «تنزيل دعم كرت الشاشة».")
            elif problem:
                job.signals.log.emit(
                    "كرت الشاشة لم يُستخدم: " + problem + "\nحدّث تعريف NVIDIA (يحتاج إصداراً يدعم CUDA 12) ثم أعد المحاولة."
                )
            translator.close()
            return st

        def done(st):
            self.model.reload()
            self._update_stats()
            rate = (st.translated / st.seconds * 60) if st.seconds else 0
            self.log(f"✓ تُرجم {st.translated:,} سطر، أخطاء {st.errors:,}، خلال {st.seconds:.0f} ثانية (~{rate:,.0f} سطر/دقيقة).")
            if st.errors:
                self.log("الأسطر التي فيها خطأ لن تُكتب في اللعبة. صحّحها يدوياً أو أعد الترجمة.")

        self._start(work, done, "الترجمة")

    def apply_patch(self) -> None:
        eng, project = self.engine, self.project
        self._apply_engine_options(eng)
        stats = project.stats()
        if stats["error"]:
            self.log(f"تنبيه: {stats['error']} سطر فيه خطأ وسيبقى بلغته الأصلية.")

        def work(job: Job):
            files = eng.apply(project.translations(), lambda m, f: job.signals.progress.emit(int(f * 100), 100, m))
            for w in eng.warnings:
                job.signals.log.emit("تحذير: " + w)
            return files

        def done(files):
            self.log("✓ طُبّق التعريب. الملفات المضافة:\n  " + "\n  ".join(files))
            if eng.markup in ("rpgm", "sugarcube", "harlowe"):
                self.log("نُسخت ملفات اللعبة الأصلية إلى مجلد vnarabizer_backup. «إزالة التعريب» يرجعها كما كانت تماماً.")
            else:
                self.log("ملفات اللعبة الأصلية لم تُعدَّل. للتراجع اضغط «إزالة التعريب».")

        self._start(work, done, "تطبيق التعريب")

    def remove_patch(self) -> None:
        if QMessageBox.question(self, "إزالة التعريب", "إزالة التعريب وإرجاع اللعبة كما كانت؟ (ترجماتك تبقى محفوظة في البرنامج)") != QMessageBox.Yes:
            return
        removed = self.engine.remove()
        self.log("✓ أُزيل التعريب: " + ", ".join(removed))
        self._update_buttons()

    def run_game(self) -> None:
        root = self.engine.root
        exes = []
        if sys.platform == "win32":
            skip = ("-32", "helper", "crash", "unins", "setup", "dxwebsetup", "vcredist")
            exes = [p for p in root.glob("*.exe") if not any(k in p.stem.lower() for k in skip)]
            exes.sort(key=lambda p: (p.stem.lower() != "game", p.stem.lower() != root.name.lower(), p.name))
        if sys.platform == "win32" and exes:
            os.startfile(str(exes[0]))  # type: ignore[attr-defined]
        else:
            sh = list(root.glob("*.sh"))
            if sh:
                subprocess.Popen(["/bin/sh", str(sh[0])], cwd=str(root))
            else:
                QMessageBox.information(self, "تشغيل", "لم أجد ملف تشغيل اللعبة.")

    def download_model(self) -> None:
        key = self.cmb_model.currentData()
        if models.is_installed(key):
            if QMessageBox.question(self, "حذف النموذج", "حذف النموذج من الجهاز لتوفير المساحة؟") == QMessageBox.Yes:
                models.remove(key)
                self._refresh_model_state()
            return
        s = self.settings

        def work(job: Job):
            def prog(name, done, total):
                job.signals.progress.emit(done // 1024, max(1, total // 1024), f"{name}: {_mb(done)} / {_mb(total)}")

            try:
                return models.install(
                    key, prog, lambda: job.cancelled, s.hf_token or None, s.model_repo or None, log=job.signals.log.emit
                )
            except Cancelled:
                raise RuntimeError("cancelled")

        def done(path):
            self._refresh_model_state()
            self.log(f"✓ تم تنزيل النموذج إلى {path}")

        self._start(work, done, "تنزيل النموذج")

    def _offer_gpu_download(self) -> bool:
        """If an NVIDIA card is present but its libraries are not, offer to get them.

        Returns True when a download was started (translation resumes after it).
        """
        if sys.platform != "win32" or self.settings.device == "cpu" or self._gpu_declined or cuda.installed():
            return False
        if not cuda.gpu_present():
            return False
        box = QMessageBox(self)
        box.setWindowTitle("كرت الشاشة")
        box.setText(
            "وجدتُ كرت شاشة NVIDIA. لتسريع الترجمة عليه يحتاج البرنامج مكتبات NVIDIA (~550MB، مرة واحدة فقط).\n"
            "بدونها تعمل الترجمة على المعالج، لكنها أبطأ بكثير."
        )
        dl = box.addButton("تنزيل الآن ثم الترجمة", QMessageBox.AcceptRole)
        cpu = box.addButton("متابعة على المعالج", QMessageBox.RejectRole)
        box.setDefaultButton(dl)
        box.exec()
        if box.clickedButton() is dl:
            self.download_gpu(then=self.translate)
            return True
        if box.clickedButton() is cpu:
            self._gpu_declined = True
        return False

    def download_gpu(self, then=None) -> None:
        def work(job: Job):
            def prog(name, done, total):
                job.signals.progress.emit(done // 1024, max(1, total // 1024), f"{name}: {_mb(done)} / {_mb(total)}")

            try:
                return cuda.install(prog, lambda: job.cancelled)
            except Cancelled:
                raise RuntimeError("cancelled")

        def done(p):
            self.log(f"✓ نُزّلت مكتبات كرت الشاشة إلى {p}")
            if then:
                QTimer.singleShot(0, then)

        self._start(work, done, "تنزيل دعم كرت الشاشة")

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.settings, self, on_gpu_download=lambda: QTimer.singleShot(0, self.download_gpu))
        dlg.exec()

    def edit_glossary(self) -> None:
        GlossaryDialog(self.project, self).exec()

    def closeEvent(self, event):
        if self.job:
            self.job.cancel()
            self.pool.waitForDone(5000)
        if self.project:
            self.project.close()
        super().closeEvent(event)
