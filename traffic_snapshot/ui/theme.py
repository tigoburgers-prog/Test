"""Visual theme: one calm, professional light palette used everywhere."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

PRIMARY = "#1F4E79"
PRIMARY_HOVER = "#173D60"
PRIMARY_SOFT = "#E8F0F8"
ACCENT = "#0E7C86"
BG = "#F4F6F9"
SURFACE = "#FFFFFF"
BORDER = "#DCE1E8"
TEXT = "#1F2933"
TEXT_MUTED = "#5F6B7A"
DANGER = "#B42318"
WARNING = "#B54708"
SUCCESS = "#067647"

STATUS_COLORS = {
    "created": "#5F6B7A",
    "analyzing": "#1F4E79",
    "interrupted": "#B54708",
    "completed": "#067647",
    "failed": "#B42318",
}

QSS = f"""
QWidget {{ color: {TEXT}; font-size: 10pt; }}
QMainWindow, QStackedWidget > QWidget#page {{ background: {BG}; }}
QWidget#page {{ background: {BG}; }}
QFrame#card, QWidget#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; }}
QFrame#headerBar {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
QLabel#appTitle {{ font-size: 13pt; font-weight: 600; color: {PRIMARY}; }}
QLabel#breadcrumb {{ color: {TEXT_MUTED}; }}
QLabel#pageTitle {{ font-size: 17pt; font-weight: 600; }}
QLabel#sectionTitle {{ font-size: 12pt; font-weight: 600; }}
QLabel#muted {{ color: {TEXT_MUTED}; }}
QLabel#kpiValue {{ font-size: 18pt; font-weight: 600; color: {PRIMARY}; }}
QLabel#kpiLabel {{ color: {TEXT_MUTED}; font-size: 9pt; }}
QLabel#kpiHint {{ color: {TEXT_MUTED}; font-size: 8pt; }}
QLabel#disclaimer {{ color: {TEXT_MUTED}; font-size: 8.5pt; }}
QLabel#banner {{ background: #FEF6E7; border: 1px solid #F5D9A8; border-radius: 6px; padding: 8px 10px; color: {WARNING}; }}
QLabel#infoBanner {{ background: {PRIMARY_SOFT}; border: 1px solid #C9DAEC; border-radius: 6px; padding: 8px 10px; color: {PRIMARY}; }}
QLabel#errorText {{ color: {DANGER}; }}

QPushButton {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px;
    padding: 6px 14px; min-height: 22px;
}}
QPushButton:hover {{ background: #EEF2F6; }}
QPushButton:pressed {{ background: #E3E8EE; }}
QPushButton:disabled {{ color: #A0A8B3; background: #F3F4F6; }}
QPushButton#primary {{ background: {PRIMARY}; color: white; border: 1px solid {PRIMARY}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {PRIMARY_HOVER}; }}
QPushButton#primary:disabled {{ background: #9DB3C8; border-color: #9DB3C8; }}
QPushButton#danger {{ color: {DANGER}; }}
QPushButton#link {{ border: none; background: transparent; color: {PRIMARY}; padding: 2px 4px; }}
QPushButton#link:hover {{ text-decoration: underline; }}
QToolButton {{ border: 1px solid {BORDER}; border-radius: 6px; background: {SURFACE}; padding: 4px 8px; }}
QToolButton:hover {{ background: #EEF2F6; }}
QToolButton:checked {{ background: {PRIMARY_SOFT}; border-color: {PRIMARY}; color: {PRIMARY}; }}

QLineEdit, QComboBox, QSpinBox {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 8px;
}}
QLineEdit:focus, QComboBox:focus {{ border-color: {PRIMARY}; }}
QTableView, QTreeView, QListWidget {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px;
    alternate-background-color: #F8FAFC; selection-background-color: {PRIMARY_SOFT}; selection-color: {TEXT};
}}
QHeaderView::section {{
    background: #F1F4F8; border: none; border-bottom: 1px solid {BORDER}; padding: 6px 8px; font-weight: 600;
}}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 6px; background: {SURFACE}; top: -1px; }}
QTabBar::tab {{ background: transparent; padding: 7px 14px; margin-right: 2px; color: {TEXT_MUTED}; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {PRIMARY}; border-bottom: 2px solid {PRIMARY}; font-weight: 600; }}
QProgressBar {{ border: 1px solid {BORDER}; border-radius: 6px; background: #EEF1F5; text-align: center; height: 20px; }}
QProgressBar::chunk {{ background: {PRIMARY}; border-radius: 5px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}
QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {PRIMARY}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QSlider::sub-page:horizontal {{ background: {PRIMARY}; border-radius: 2px; }}
QScrollArea {{ border: none; background: transparent; }}
QToolTip {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 8px; margin-top: 14px; padding: 12px; background: {SURFACE}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; font-weight: 600; }}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    font = QFont("Segoe UI", 10)
    font.setStyleHint(QFont.SansSerif)
    app.setFont(font)
    pal = app.palette()
    pal.setColor(QPalette.Window, QColor(BG))
    pal.setColor(QPalette.Base, QColor(SURFACE))
    pal.setColor(QPalette.AlternateBase, QColor("#F8FAFC"))
    pal.setColor(QPalette.Text, QColor(TEXT))
    pal.setColor(QPalette.WindowText, QColor(TEXT))
    pal.setColor(QPalette.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.Highlight, QColor(PRIMARY))
    pal.setColor(QPalette.HighlightedText, QColor("white"))
    app.setPalette(pal)
    app.setStyleSheet(QSS)
