"""Application entry point: logging, crash handling, theme, main window."""

from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import multiprocessing
import os
import sys
import traceback

from traffic_snapshot import APP_NAME, APP_ORG, __version__


def setup_logging() -> None:
    from traffic_snapshot.core.paths import logs_dir

    log_file = logs_dir() / "traffic_snapshot.log"
    handler = logging.handlers.RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stderr is not None:  # windowed (frozen) builds have no console
        root.addHandler(logging.StreamHandler())
    try:  # native crashes (e.g. in a codec) end up in a separate file for diagnosis
        faulthandler.enable(open(logs_dir() / "crash.log", "a", encoding="utf-8"))  # noqa: SIM115
    except OSError:
        pass
    logging.getLogger(__name__).info("%s %s starting (Python %s)", APP_NAME, __version__, sys.version.split()[0])


def install_excepthook() -> None:
    def hook(exc_type, exc, tb) -> None:
        logging.getLogger("uncaught").error("Unhandled exception", exc_info=(exc_type, exc, tb))
        try:
            from PySide6.QtWidgets import QApplication

            from traffic_snapshot.core.errors import AppError
            from traffic_snapshot.ui.dialogs import show_error

            if QApplication.instance() is not None:
                err = exc if isinstance(exc, AppError) else AppError(
                    "Er is een onverwachte fout opgetreden. Uw projecten en analyses zijn bewaard.",
                    hint="Probeer de handeling opnieuw. Blijft het probleem bestaan, bekijk dan de logbestanden "
                    "via Help → Logbestanden openen.",
                    detail="".join(traceback.format_exception(exc_type, exc, tb))[-4000:],
                )
                show_error(err, QApplication.activeWindow())
        except Exception:  # noqa: BLE001 - never raise from the hook
            pass

    sys.excepthook = hook


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
    setup_logging()
    install_excepthook()

    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    QCoreApplication.setOrganizationName(APP_ORG)
    QCoreApplication.setApplicationName(APP_NAME)
    QCoreApplication.setApplicationVersion(__version__)
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    # Create the QApplication before importing modules that load matplotlib/OpenCV:
    # initialising Qt afterwards can crash at shutdown on some platforms.
    app = QApplication(argv if argv is not None else sys.argv)

    from traffic_snapshot.core.paths import projects_root
    from traffic_snapshot.core.project_store import ProjectStore
    from traffic_snapshot.ui.main_window import MainWindow
    from traffic_snapshot.ui.theme import apply_theme

    apply_theme(app)
    from PySide6.QtGui import QIcon

    from traffic_snapshot.core.paths import resource_root

    icon = resource_root() / "traffic_snapshot" / "resources" / "app.png"
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))
    if sys.platform == "win32":
        try:  # own taskbar icon/group instead of python.exe
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_ORG}.{APP_NAME}.{__version__}")
        except Exception:  # noqa: BLE001
            pass

    store = ProjectStore(projects_root())
    store.recover_stale_states()
    win = MainWindow(store)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
