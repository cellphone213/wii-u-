"""Table of lines: original, translation (editable), status."""

from __future__ import annotations

import re

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QBrush, QColor

from ..core import protect
from ..core.project import Project, Row

_ARABIC = re.compile(r"[\u0600-\u06ff]")

KIND_AR = {"dialogue": "حوار", "menu": "اختيار", "name": "اسم", "ui": "واجهة"}
STATUS_AR = {"new": "جديد", "machine": "ترجمة آلية", "edited": "معدّل يدوياً", "locked": "لا يُترجم", "error": "خطأ"}
STATUS_COLOR = {
    "new": None,
    "machine": QColor(60, 140, 255, 40),
    "edited": QColor(40, 190, 90, 50),
    "locked": QColor(128, 128, 128, 45),
    "error": QColor(230, 60, 60, 60),
}

COLUMNS = ["النوع", "المتكلم", "النص الأصلي", "الترجمة", "الحالة", "ملاحظة"]
COL_ORIGINAL, COL_TRANSLATION, COL_STATUS = 2, 3, 4


class LinesModel(QAbstractTableModel):
    def __init__(self, project: Project | None = None):
        super().__init__()
        self.project = project
        self.rows: list[Row] = []

    def reload(self) -> None:
        self.beginResetModel()
        self.rows = self.project.rows() if self.project else []
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return COLUMNS[section]
        return None

    def flags(self, index):
        f = Qt.ItemIsSelectable | Qt.ItemIsEnabled
        if index.column() == COL_TRANSLATION and self.rows[index.row()].status != "locked":
            f |= Qt.ItemIsEditable
        return f

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r = self.rows[index.row()]
        c = index.column()
        if role in (Qt.DisplayRole, Qt.EditRole, Qt.ToolTipRole):
            if c == 0:
                return KIND_AR.get(r.kind, r.kind)
            if c == 1:
                return r.speaker
            if c == 2:
                if role == Qt.ToolTipRole:
                    return f"{r.original}\n\n{r.file}:{r.line}  ×{r.count}"
                # Show non-Arabic source text left-to-right inside the RTL window.
                return r.original if role == Qt.EditRole or _ARABIC.search(r.original) else "\u202a" + r.original + "\u202c"
            if c == 3:
                return r.translation
            if c == 4:
                return STATUS_AR.get(r.status, r.status)
            if c == 5:
                return r.problem
        if role == Qt.BackgroundRole:
            col = STATUS_COLOR.get(r.status)
            return QBrush(col) if col else None
        if role == Qt.TextAlignmentRole and c in (2, 3):
            return int(Qt.AlignVCenter | (Qt.AlignRight if c == 3 else Qt.AlignLeft))
        if role == Qt.UserRole:
            return r
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or index.column() != COL_TRANSLATION:
            return False
        r = self.rows[index.row()]
        text = str(value)
        problems = protect.validate(r.original, text, self.project.markup) if text.strip() else []
        status = "error" if problems else ("edited" if text.strip() else "new")
        self.project.set_translation(r.id, text, status, "; ".join(problems), "manual")
        self.project.commit()
        r.translation, r.status, r.problem = text, status, "; ".join(problems)
        self.dataChanged.emit(self.index(index.row(), 0), self.index(index.row(), len(COLUMNS) - 1))
        return True


class LinesFilter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.text = ""
        self.status = ""
        self.kind = ""

    def set(self, text: str | None = None, status: str | None = None, kind: str | None = None) -> None:
        if text is not None:
            self.text = text.lower()
        if status is not None:
            self.status = status
        if kind is not None:
            self.kind = kind
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        r: Row = self.sourceModel().rows[source_row]
        if self.status and r.status != self.status:
            return False
        if self.kind and r.kind != self.kind:
            return False
        if self.text and self.text not in r.original.lower() and self.text not in r.translation.lower() and self.text not in r.speaker.lower():
            return False
        return True
