"""Registry keyboards: callback payload budget and button wiring.

Telegram hard-caps callback_data at 64 bytes; the registry carries its whole
filter state there instead of in FSM, so the budget is asserted explicitly.
"""
from bot.keyboards.inline import RegistryCb, registry_detail_kb, registry_list_kb


class _I18n:
    def get(self, key, /, *args, **kwargs):
        return key


def _texts(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


def _datas(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def test_callback_payload_fits_telegram_64_byte_limit():
    worst = RegistryCb(
        type="corruption", status="in_progress", order="asc",
        page=999, open="ZZZZZZZZ",
    ).pack()
    assert len(worst.encode()) <= 64, worst


def test_list_keyboard_has_one_open_button_per_row_shown():
    kb = registry_list_kb(
        _I18n(), type_="appeal", status="all", order="desc", page=0,
        public_ids=["AAA111", "BBB222", "CCC333"], total_pages=2,
    )
    opens = [d for d in _datas(kb) if RegistryCb.unpack(d).open]
    assert len(opens) == 3
    assert RegistryCb.unpack(opens[0]).open == "AAA111"


def test_list_keyboard_preserves_filter_state_in_pagination():
    kb = registry_list_kb(
        _I18n(), type_="corruption", status="new", order="asc", page=1,
        public_ids=["AAA111"], total_pages=3,
    )
    for data in _datas(kb):
        cb = RegistryCb.unpack(data)
        assert cb.type == "corruption"  # type is fixed on entry, never changes


def test_list_keyboard_omits_pagination_when_single_page():
    kb = registry_list_kb(
        _I18n(), type_="appeal", status="all", order="desc", page=0,
        public_ids=["AAA111"], total_pages=1,
    )
    assert "registry-page" not in _texts(kb)


def test_filter_buttons_reset_to_first_page():
    """An offset valid for the old filter may not exist in the new result set."""
    kb = registry_list_kb(
        _I18n(), type_="appeal", status="all", order="desc", page=3,
        public_ids=["AAA111"], total_pages=5,
    )
    for data in _datas(kb):
        cb = RegistryCb.unpack(data)
        if cb.status != "all" or cb.order != "desc":
            assert cb.page == 0


def test_detail_keyboard_offers_take_for_new_submission():
    kb = registry_detail_kb(
        _I18n(), submission_id=7, type_="appeal", status="all", order="desc",
        page=0, status_value="new",
    )
    assert "btn-take" in _texts(kb)
    assert "btn-back-to-list" in _texts(kb)


def test_detail_keyboard_hides_take_once_claimed():
    kb = registry_detail_kb(
        _I18n(), submission_id=7, type_="appeal", status="all", order="desc",
        page=0, status_value="in_progress",
    )
    assert "btn-take" not in _texts(kb)
    assert "btn-reply" in _texts(kb)


def test_detail_keyboard_closed_submission_offers_only_back():
    kb = registry_detail_kb(
        _I18n(), submission_id=7, type_="appeal", status="all", order="desc",
        page=0, status_value="closed",
    )
    assert _texts(kb) == ["btn-back-to-list"]


def test_detail_back_button_returns_to_same_page_and_filter():
    kb = registry_detail_kb(
        _I18n(), submission_id=7, type_="corruption", status="new", order="asc",
        page=4, status_value="closed",
    )
    back = RegistryCb.unpack(_datas(kb)[-1])
    assert (back.type, back.status, back.order, back.page) == ("corruption", "new", "asc", 4)
    assert back.open == ""   # empty open == list view
