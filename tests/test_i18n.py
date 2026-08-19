from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

from trading_journal.presentation import i18n


def test_translation_catalogs_do_not_repeat_source_keys() -> None:
    tree = ast.parse(Path(i18n.__file__).read_text(encoding="utf-8"))
    catalogs: dict[str, list[str]] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        if node.target.id not in {"VI", "_PHRASES"} or not isinstance(node.value, ast.Dict):
            continue
        catalogs[node.target.id] = [
            key.value
            for key in node.value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        ]

    for name, keys in catalogs.items():
        duplicates = sorted(key for key, count in Counter(keys).items() if count > 1)
        assert duplicates == [], f"{name} contains duplicate source keys: {duplicates}"


def test_vietnamese_keeps_familiar_trading_terms(monkeypatch) -> None:
    monkeypatch.setattr(i18n, "language", lambda: "vi")

    assert i18n.tr("Three-pillar framework") == "Framework ba trụ cột"
    assert i18n.tr("Maximum open risk (R)") == "Open risk tối đa (R)"
    assert i18n.tr("Report period") == "Kỳ báo cáo"
    assert i18n.tr("Ongoing") == "Đang diễn ra"
    assert i18n.tr("Outcome mix") == "Cơ cấu kết quả"
    assert i18n.tr("Daily result range") == "Biên độ kết quả theo ngày"
    assert i18n.tr("Net P&L by {dimension}", dimension=i18n.tr("Direction").lower()) == "P&L ròng theo hướng"
