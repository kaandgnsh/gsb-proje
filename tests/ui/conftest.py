import os
import time

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QLocale
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from lai.ui.window import MainWindow


def until(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("Arayüz işlemi zamanında tamamlanmadı")
        QTest.qWait(10)
    QTest.qWait(10)


def idle(window):
    QTest.qWait(10)
    until(lambda: window.task is None)
    QTest.qWait(20)
    until(lambda: window.task is None)


@pytest.fixture(scope="session")
def app():
    instance = QApplication.instance() or QApplication([])
    instance.setQuitOnLastWindowClosed(False)
    QLocale.setDefault(QLocale("tr_TR"))
    yield instance


@pytest.fixture
def windows(app):
    opened = []

    def make(path, backend=None):
        w = MainWindow(path, backend=backend)
        opened.append(w)
        w.show()
        app.processEvents()
        idle(w)
        return w

    yield make
    for w in opened:
        w.close()
        until(lambda w=w: w.task is None, 35)
        app.processEvents()
