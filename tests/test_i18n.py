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
    assert i18n.tr("Live risk monitor") == "Theo dõi rủi ro trực tiếp"
    assert i18n.tr("Exposure snapshot") == "Ảnh chụp mức phơi nhiễm"
    assert i18n.tr("Today action center") == "Trung tâm hành động hôm nay"
    assert i18n.tr("Today's reviewed issues") == "Vấn đề đã đánh giá hôm nay"
    assert i18n.tr("Resolved today") == "Đã hoàn tất hôm nay"
    assert i18n.tr("Edge quality") == "Chất lượng lợi thế"
    assert i18n.tr("Edge summary") == "Tóm tắt lợi thế"
    assert i18n.tr("Outcome distribution") == "Phân bố kết quả"
    assert i18n.tr("Winning trades") == "Giao dịch thắng"
    assert i18n.tr("Losing trades") == "Giao dịch thua"
    assert i18n.tr("Win rate details") == "Chi tiết tỷ lệ thắng"
    assert i18n.tr(
        "Excluding breakevens: {rate} = {wins} wins ÷ {decisive} wins and losses.",
        rate="57,7%",
        wins="236",
        decisive="409",
    ) == "Không tính hòa vốn: 57,7% = 236 lệnh thắng ÷ 409 lệnh thắng và thua."
    # Only the shortfall caption is rendered now; full coverage says nothing.
    assert i18n.tr(
        "R coverage: :orange[**{covered} / {total}**] logical trades can be normalized "
        "using the account's current standard 1R.",
        covered="0",
        total="462",
    ) == (
        "Độ phủ R: :orange[**0 / 462**] giao dịch logic có thể được chuẩn hóa "
        "bằng 1R tiêu chuẩn hiện tại của tài khoản."
    )
    assert i18n.tr("Daily result range") == "Biên độ kết quả theo ngày"
    assert i18n.tr("Select at least one trading mistake when a criterion fails") == "Chọn ít nhất một lỗi giao dịch khi một tiêu chí không đạt"


def test_vietnamese_translates_oversized_revenge_coaching_action(monkeypatch) -> None:
    monkeypatch.setattr(i18n, "language", lambda: "vi")

    assert i18n.tr(
        "After a loss, pause before re-entering and keep position size within the written risk plan."
    ) == (
        "Sau lệnh lỗ, hãy tạm dừng trước khi vào lại và giữ khối lượng trong giới hạn "
        "của kế hoạch rủi ro đã ghi."
    )
