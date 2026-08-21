"""Qt translation bootstrap for incremental UI internationalization."""
from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QLocale, QTranslator

from .paths import resource_path


def tr(context: str, source_text: str) -> str:
    return QCoreApplication.translate(context, source_text)


def install_translator(app, locale_name: str | None = None) -> QTranslator | None:
    """Load a bundled QM file when available; Chinese source text remains fallback."""
    locale = str(locale_name or QLocale.system().name() or "zh_CN")
    candidates = (
        resource_path(f"resources/i18n/SerialX_{locale}.qm"),
        resource_path(f"resources/i18n/SerialX_{locale.split('_', 1)[0]}.qm"),
    )
    translator = QTranslator(app)
    for candidate in candidates:
        if candidate.is_file() and translator.load(str(candidate)):
            app.installTranslator(translator)
            return translator
    return None
