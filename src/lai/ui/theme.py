"""Sistem teması karanlık olsa da tablolar ve diyaloglar okunabilir kalır."""

from PySide6.QtGui import QColor, QPalette


def apply_theme(app):
    if app.property("lai_theme"):
        return
    app.setStyle("Fusion")
    palette = QPalette()
    colors = {
        "Window": "#f3f5f9",
        "WindowText": "#142438",
        "Base": "#ffffff",
        "AlternateBase": "#edf2f7",
        "Text": "#142438",
        "Button": "#e4ecf3",
        "ButtonText": "#142438",
        "Highlight": "#246d9d",
        "HighlightedText": "#ffffff",
        "ToolTipBase": "#ffffff",
        "ToolTipText": "#142438",
        "PlaceholderText": "#596b7c",
    }
    for role, color in colors.items():
        palette.setColor(getattr(QPalette, role), QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor("#65758a"))
    app.setPalette(palette)
    app.setProperty("lai_theme", True)
