import os

# Muss vor dem Erzeugen der QApplication (durch pytest-qt) gesetzt sein – GUI-Tests laufen ohne Display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
