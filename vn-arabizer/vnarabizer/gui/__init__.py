def main() -> int:
    import os
    import sys

    # The windowed build has no console; keep stray prints from crashing.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont, QFontDatabase, QIcon
    from PySide6.QtWidgets import QApplication

    from ..core.paths import assets_dir
    from .main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("VN Arabizer")
    app.setLayoutDirection(Qt.RightToLeft)
    app.setWindowIcon(QIcon(str(assets_dir() / "icon.png")))
    fid = QFontDatabase.addApplicationFont(str(assets_dir() / "fonts" / "VNArabic-Regular.ttf"))
    QFontDatabase.addApplicationFont(str(assets_dir() / "fonts" / "VNArabic-Bold.ttf"))
    if fid >= 0:
        fam = QFontDatabase.applicationFontFamilies(fid)
        if fam:
            app.setFont(QFont(fam[0], 10))
    w = MainWindow()
    w.show()
    return app.exec()
