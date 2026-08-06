# Submissions Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give responsibles and admins a browsable, filterable registry of submissions so a missed push card no longer makes a submission permanently unreachable.

**Architecture:** A new read-only UI layer (`bot/handlers/registry.py`) over one new repository query. Filter/sort/page state travels inside `callback_data` (48 bytes worst case, limit 64) — no FSM, no Redis. Actions are NOT reimplemented: the detail screen renders the existing `ReactionCb` buttons handled by `responsible.py`, so authorization stays in exactly one place (`_authorize()`).

**Tech Stack:** Python 3.11+, aiogram 3.x, aiogram-i18n + Fluent, SQLAlchemy 2.0 async, pytest (asyncio_mode=auto), ruff.

**Spec:** [docs/superpowers/specs/2026-08-06-submissions-registry-design.md](../specs/2026-08-06-submissions-registry-design.md)

## Global Constraints

- **Registry grants no new rights.** Access reuses `can_handle_type(user, type_)`. A `resp_appeal`-only user must never see `corruption` rows. Verified by test, not by inspection.
- **Anonymity untouched.** The registry never reads `anon_delivery_refs.enc_chat_ref`. For anonymous rows it shows `🕵` instead of a name — exactly the parity of the push card.
- **All user text through `escape()`** — messages go out in HTML parse mode.
- **i18n parity is enforced by CI.** Every new key must be added to all 5 locales (`ru`, `kaa`, `uz_cyrl`, `uz_latn`, `en`), currently 84 keys each. `tests/test_i18n.py` fails the build otherwise.
- **Page size is 5.** `per_page=5` is the single source of truth in `list_for_registry`.
- **Ruff:** line-length 100, rules `E,F,I,UP,B,ASYNC`. Run `ruff check bot tests` before every commit.
- **Test runner:** `.venv\Scripts\python.exe -m pytest` (Windows). Baseline before this work: **86 passed**.
- **Русский — язык пользовательских строк**, английский — код и комментарии.

---

### Task 1: Registry query in the repository

**Files:**
- Modify: `bot/db/repositories.py` (add method to `SubmissionRepository`, after `try_claim`)
- Test: `tests/test_registry_query.py` (create)

**Interfaces:**
- Consumes: `SubmissionRepository(session)`, `SubmissionType`, `SubmissionStatus` from `bot/db/models.py`
- Produces: `SubmissionRepository.list_for_registry(*, type_, status=None, order="desc", page=0, per_page=5) -> tuple[list[Submission], int]` — returns `(rows_of_page, total_count)`

- [ ] **Step 1: Write the failing test**

Create `tests/test_registry_query.py`:

```python
"""Registry query: filtering, ordering, pagination, and the total count.

The registry must never widen visibility, so type filtering is asserted here
as well as in the access tests.
"""
from datetime import UTC, datetime, timedelta

import pytest

from bot.db.models import Submission, SubmissionStatus, SubmissionType
from bot.db.repositories import SubmissionRepository

_BASE = datetime(2026, 6, 1, tzinfo=UTC)


async def _seed(session, specs):
    """specs: list of (type_, status, days_offset). Returns created rows."""
    rows = []
    for i, (type_, status, offset) in enumerate(specs):
        sub = Submission(
            type=type_,
            text=f"text {i}",
            is_anonymous=False,
            public_id=f"PID{i:05d}",
            ticket_number=f"TKT-2026-{i:04d}",
            status=status,
            created_at=_BASE + timedelta(days=offset),
        )
        session.add(sub)
        rows.append(sub)
    await session.flush()
    return rows


@pytest.mark.asyncio
async def test_filters_by_type(session):
    await _seed(session, [
        (SubmissionType.appeal, SubmissionStatus.new, 0),
        (SubmissionType.corruption, SubmissionStatus.new, 1),
    ])
    rows, total = await SubmissionRepository(session).list_for_registry(
        type_=SubmissionType.appeal
    )
    assert total == 1
    assert [r.type for r in rows] == [SubmissionType.appeal]


@pytest.mark.asyncio
async def test_filters_by_status(session):
    await _seed(session, [
        (SubmissionType.appeal, SubmissionStatus.new, 0),
        (SubmissionType.appeal, SubmissionStatus.closed, 1),
    ])
    repo = SubmissionRepository(session)
    rows, total = await repo.list_for_registry(
        type_=SubmissionType.appeal, status=SubmissionStatus.closed
    )
    assert total == 1
    assert rows[0].status == SubmissionStatus.closed


@pytest.mark.asyncio
async def test_status_none_returns_all_statuses(session):
    await _seed(session, [
        (SubmissionType.appeal, SubmissionStatus.new, 0),
        (SubmissionType.appeal, SubmissionStatus.in_progress, 1),
        (SubmissionType.appeal, SubmissionStatus.closed, 2),
    ])
    rows, total = await SubmissionRepository(session).list_for_registry(
        type_=SubmissionType.appeal, status=None
    )
    assert total == 3


@pytest.mark.asyncio
async def test_order_desc_is_newest_first(session):
    await _seed(session, [
        (SubmissionType.appeal, SubmissionStatus.new, 0),   # oldest
        (SubmissionType.appeal, SubmissionStatus.new, 5),   # newest
    ])
    rows, _ = await SubmissionRepository(session).list_for_registry(
        type_=SubmissionType.appeal, order="desc"
    )
    assert rows[0].created_at > rows[1].created_at


@pytest.mark.asyncio
async def test_order_asc_is_oldest_first(session):
    await _seed(session, [
        (SubmissionType.appeal, SubmissionStatus.new, 0),
        (SubmissionType.appeal, SubmissionStatus.new, 5),
    ])
    rows, _ = await SubmissionRepository(session).list_for_registry(
        type_=SubmissionType.appeal, order="asc"
    )
    assert rows[0].created_at < rows[1].created_at


@pytest.mark.asyncio
async def test_pagination_slices_and_reports_full_total(session):
    await _seed(session, [
        (SubmissionType.appeal, SubmissionStatus.new, i) for i in range(12)
    ])
    repo = SubmissionRepository(session)
    page0, total = await repo.list_for_registry(type_=SubmissionType.appeal, page=0)
    page2, _ = await repo.list_for_registry(type_=SubmissionType.appeal, page=2)
    assert total == 12          # total is the FULL count, not the page length
    assert len(page0) == 5      # default per_page
    assert len(page2) == 2      # last partial page
    assert {r.id for r in page0}.isdisjoint({r.id for r in page2})


@pytest.mark.asyncio
async def test_page_beyond_end_is_empty_not_error(session):
    await _seed(session, [(SubmissionType.appeal, SubmissionStatus.new, 0)])
    rows, total = await SubmissionRepository(session).list_for_registry(
        type_=SubmissionType.appeal, page=99
    )
    assert rows == []
    assert total == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_registry_query.py -q`
Expected: FAIL — `AttributeError: 'SubmissionRepository' object has no attribute 'list_for_registry'`

- [ ] **Step 3: Write minimal implementation**

In `bot/db/repositories.py`, add to `SubmissionRepository` (place after `try_claim`, before `close`):

```python
    async def list_for_registry(
        self,
        *,
        type_: SubmissionType,
        status: SubmissionStatus | None = None,
        order: str = "desc",
        page: int = 0,
        per_page: int = 5,
    ) -> tuple[list[Submission], int]:
        """One page of the registry plus the TOTAL row count (for "1/4").

        ``type_`` is always applied: the caller has already checked the user may
        see this type, and widening it here would grant unintended access.
        """
        filters = [Submission.type == type_]
        if status is not None:
            filters.append(Submission.status == status)

        total = await self.session.scalar(
            select(func.count()).select_from(Submission).where(*filters)
        )
        col = Submission.created_at
        rows = list(
            await self.session.scalars(
                select(Submission)
                .where(*filters)
                .order_by(col.asc() if order == "asc" else col.desc())
                .offset(max(page, 0) * per_page)
                .limit(per_page)
            )
        )
        return rows, int(total or 0)
```

`func` and `select` are already imported at the top of the module.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_registry_query.py -q`
Expected: PASS — 7 passed

- [ ] **Step 5: Lint and commit**

```bash
ruff check bot tests
git add bot/db/repositories.py tests/test_registry_query.py
git commit -m "Add list_for_registry query with filtering, ordering and pagination"
```

---

### Task 2: Locale keys in all 5 languages

**Files:**
- Modify: `bot/locales/ru/LC_MESSAGES/bot.ftl`, `bot/locales/kaa/...`, `bot/locales/uz_cyrl/...`, `bot/locales/uz_latn/...`, `bot/locales/en/...`
- Test: `tests/test_i18n.py` (already exists — enforces parity, no changes needed)

**Interfaces:**
- Produces: keys `btn-registry-appeals`, `btn-registry-complaints`, `registry-title`, `registry-empty`, `registry-item`, `registry-page`, `registry-not-found`, `btn-filter-all`, `btn-filter-new`, `btn-filter-in-progress`, `btn-filter-closed`, `btn-sort-newest`, `btn-sort-oldest`, `btn-back-to-list`, `cmd-appeals`, `cmd-complaints`

Reused, already present — do NOT redefine: `status-new`, `status-in_progress`, `status-closed`, `type-appeal`, `type-corruption`, `btn-take`, `btn-reply`, `btn-close`, `admin-only`, `error-generic`.

- [ ] **Step 1: Run the parity test to confirm the current baseline**

Run: `.venv\Scripts\python.exe -m pytest tests/test_i18n.py -q`
Expected: PASS — this is the guard that will catch a missed locale in step 3.

- [ ] **Step 2: Append the block to `bot/locales/ru/LC_MESSAGES/bot.ftl`**

```
# ===== Реестр заявок =====
btn-registry-appeals = 🗂 Обращения
btn-registry-complaints = 🗂 Жалобы
registry-title = 🗂 { $type } · { $filter } · { $order }
registry-empty = 📭 Заявок по этому фильтру нет.
registry-item = { $n }. { $status } { $public_id } · { $author } · { $date }
registry-page = Стр. { $page } из { $pages }
registry-not-found = ⚠️ Заявка не найдена или была удалена.
btn-filter-all = Все
btn-filter-new = 🆕 Новые
btn-filter-in-progress = 🟡 В работе
btn-filter-closed = ✅ Закрытые
btn-sort-newest = ↓ Сначала новые
btn-sort-oldest = ↑ Сначала старые
btn-back-to-list = ◀️ К списку
cmd-appeals = Реестр обращений
cmd-complaints = Реестр жалоб
```

- [ ] **Step 3: Append the equivalent block to the other four locales**

`bot/locales/en/LC_MESSAGES/bot.ftl`:

```
# ===== Submissions registry =====
btn-registry-appeals = 🗂 Appeals
btn-registry-complaints = 🗂 Complaints
registry-title = 🗂 { $type } · { $filter } · { $order }
registry-empty = 📭 No submissions match this filter.
registry-item = { $n }. { $status } { $public_id } · { $author } · { $date }
registry-page = Page { $page } of { $pages }
registry-not-found = ⚠️ Submission not found or deleted.
btn-filter-all = All
btn-filter-new = 🆕 New
btn-filter-in-progress = 🟡 In progress
btn-filter-closed = ✅ Closed
btn-sort-newest = ↓ Newest first
btn-sort-oldest = ↑ Oldest first
btn-back-to-list = ◀️ Back to list
cmd-appeals = Appeals registry
cmd-complaints = Complaints registry
```

`bot/locales/uz_latn/LC_MESSAGES/bot.ftl`:

```
# ===== Murojaatlar reyestri =====
btn-registry-appeals = 🗂 Murojaatlar
btn-registry-complaints = 🗂 Shikoyatlar
registry-title = 🗂 { $type } · { $filter } · { $order }
registry-empty = 📭 Ushbu filtr boʻyicha murojaatlar yoʻq.
registry-item = { $n }. { $status } { $public_id } · { $author } · { $date }
registry-page = { $pages } dan { $page }-sahifa
registry-not-found = ⚠️ Murojaat topilmadi yoki oʻchirilgan.
btn-filter-all = Barchasi
btn-filter-new = 🆕 Yangi
btn-filter-in-progress = 🟡 Ishlanmoqda
btn-filter-closed = ✅ Yopilgan
btn-sort-newest = ↓ Avval yangilari
btn-sort-oldest = ↑ Avval eskilari
btn-back-to-list = ◀️ Roʻyxatga
cmd-appeals = Murojaatlar reyestri
cmd-complaints = Shikoyatlar reyestri
```

`bot/locales/uz_cyrl/LC_MESSAGES/bot.ftl`:

```
# ===== Мурожаатлар реестри =====
btn-registry-appeals = 🗂 Мурожаатлар
btn-registry-complaints = 🗂 Шикоятлар
registry-title = 🗂 { $type } · { $filter } · { $order }
registry-empty = 📭 Ушбу филтр бўйича мурожаатлар йўқ.
registry-item = { $n }. { $status } { $public_id } · { $author } · { $date }
registry-page = { $pages } дан { $page }-саҳифа
registry-not-found = ⚠️ Мурожаат топилмади ёки ўчирилган.
btn-filter-all = Барчаси
btn-filter-new = 🆕 Янги
btn-filter-in-progress = 🟡 Ишланмоқда
btn-filter-closed = ✅ Ёпилган
btn-sort-newest = ↓ Аввал янгилари
btn-sort-oldest = ↑ Аввал эскилари
btn-back-to-list = ◀️ Рўйхатга
cmd-appeals = Мурожаатлар реестри
cmd-complaints = Шикоятлар реестри
```

`bot/locales/kaa/LC_MESSAGES/bot.ftl` (Karakalpak — mark for native review, matching the existing convention in this file):

```
# ===== Múrájatlar reestri =====
# TODO: вычитка носителем
btn-registry-appeals = 🗂 Múrájatlar
btn-registry-complaints = 🗂 Shaǵımlar
registry-title = 🗂 { $type } · { $filter } · { $order }
registry-empty = 📭 Bul filtr boyınsha múrájatlar joq.
registry-item = { $n }. { $status } { $public_id } · { $author } · { $date }
registry-page = { $pages } den { $page }-bet
registry-not-found = ⚠️ Múrájat tabılmadı yamasa óshirilgen.
btn-filter-all = Barlıǵı
btn-filter-new = 🆕 Jańa
btn-filter-in-progress = 🟡 Islenbekte
btn-filter-closed = ✅ Jabılǵan
btn-sort-newest = ↓ Aldın jańaları
btn-sort-oldest = ↑ Aldın eskileri
btn-back-to-list = ◀️ Dizimge
cmd-appeals = Múrájatlar reestri
cmd-complaints = Shaǵımlar reestri
```

- [ ] **Step 4: Run the parity test**

Run: `.venv\Scripts\python.exe -m pytest tests/test_i18n.py -q`
Expected: PASS — 4 passed. A failure names the exact locale and missing key.

- [ ] **Step 5: Commit**

```bash
git add bot/locales
git commit -m "Add registry locale keys to all five locales"
```

---

### Task 3: RegistryCb and registry keyboards

**Files:**
- Modify: `bot/keyboards/inline.py` (append after `assign_type_keyboard`)
- Test: `tests/test_registry_keyboards.py` (create)

**Interfaces:**
- Consumes: `SubmissionType` from `bot/db/models.py`
- Produces:
  - `class RegistryCb(CallbackData, prefix="reg")` with fields `type: str`, `status: str`, `order: str`, `page: int`, `open: str`
  - `registry_list_kb(i18n, *, type_: str, status: str, order: str, page: int, public_ids: list[str], total_pages: int) -> InlineKeyboardMarkup`
  - `registry_detail_kb(i18n, *, submission_id: int, type_: str, status: str, order: str, page: int, status_value: str) -> InlineKeyboardMarkup`

`open=""` means "list view"; a non-empty `open` carries the `public_id` to display. Callback payload worst case is 48 bytes (limit 64).

- [ ] **Step 1: Write the failing test**

Create `tests/test_registry_keyboards.py`:

```python
"""Registry keyboards: callback payload budget and button wiring.

Telegram hard-caps callback_data at 64 bytes; the registry carries its whole
filter state there instead of in FSM, so the budget is asserted explicitly.
"""
import pytest

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_registry_keyboards.py -q`
Expected: FAIL — `ImportError: cannot import name 'RegistryCb'`

- [ ] **Step 3: Write minimal implementation**

Append to `bot/keyboards/inline.py`:

```python
class RegistryCb(CallbackData, prefix="reg"):
    """Registry navigation state.

    The whole filter/sort/page state rides in the payload (48 bytes worst case
    against Telegram's 64-byte cap), so the registry needs no FSM and an old
    keyboard keeps working after a bot restart.

    ``open`` empty means the list view; otherwise it is the public_id to show.
    """

    type: str  # appeal | corruption — fixed on entry, never changes
    status: str  # all | new | in_progress | closed
    order: str  # desc | asc
    page: int
    open: str


# Status filter buttons: (i18n key, status value carried in callback data).
_STATUS_FILTERS = [
    ("btn-filter-all", "all"),
    ("btn-filter-new", "new"),
    ("btn-filter-in-progress", "in_progress"),
    ("btn-filter-closed", "closed"),
]


def registry_list_kb(
    i18n,
    *,
    type_: str,
    status: str,
    order: str,
    page: int,
    public_ids: list[str],
    total_pages: int,
) -> InlineKeyboardMarkup:
    """List view: open-buttons per row, status filters, sort toggle, paging."""
    kb = InlineKeyboardBuilder()

    def cb(**over) -> RegistryCb:
        base = {
            "type": type_, "status": status, "order": order, "page": page, "open": "",
        }
        return RegistryCb(**{**base, **over})

    for n, public_id in enumerate(public_ids, start=1):
        kb.button(text=str(n), callback_data=cb(open=public_id))
    kb.adjust(len(public_ids) or 1)

    filters = InlineKeyboardBuilder()
    for key, value in _STATUS_FILTERS:
        # Switching a filter resets to the first page: the old offset may not
        # exist in the new result set.
        filters.button(text=i18n.get(key), callback_data=cb(status=value, page=0))
    filters.adjust(4)
    kb.attach(filters)

    sort = InlineKeyboardBuilder()
    sort.button(text=i18n.get("btn-sort-newest"), callback_data=cb(order="desc", page=0))
    sort.button(text=i18n.get("btn-sort-oldest"), callback_data=cb(order="asc", page=0))
    sort.adjust(2)
    kb.attach(sort)

    if total_pages > 1:
        nav = InlineKeyboardBuilder()
        if page > 0:
            nav.button(text="◀️", callback_data=cb(page=page - 1))
        nav.button(
            text=i18n.get("registry-page", page=page + 1, pages=total_pages),
            callback_data=cb(),  # no-op label; tapping re-renders the same page
        )
        if page < total_pages - 1:
            nav.button(text="▶️", callback_data=cb(page=page + 1))
        nav.adjust(3)
        kb.attach(nav)

    return kb.as_markup()


def registry_detail_kb(
    i18n,
    *,
    submission_id: int,
    type_: str,
    status: str,
    order: str,
    page: int,
    status_value: str,
) -> InlineKeyboardMarkup:
    """Detail view: the SAME ReactionCb actions as the push card, plus Back.

    Actions are deliberately not new callbacks — responsible.py already handles
    ReactionCb with the authorization checks, and duplicating them would split
    authz across two code paths.
    """
    kb = InlineKeyboardBuilder()
    if status_value != "closed":
        if status_value == "new":
            kb.button(
                text=i18n.get("btn-take"),
                callback_data=ReactionCb(action="take", submission_id=submission_id),
            )
        kb.button(
            text=i18n.get("btn-reply"),
            callback_data=ReactionCb(action="reply", submission_id=submission_id),
        )
        kb.button(
            text=i18n.get("btn-close"),
            callback_data=ReactionCb(action="close", submission_id=submission_id),
        )
    kb.button(
        text=i18n.get("btn-back-to-list"),
        callback_data=RegistryCb(
            type=type_, status=status, order=order, page=page, open=""
        ),
    )
    kb.adjust(1)
    return kb.as_markup()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_registry_keyboards.py -q`
Expected: PASS — 8 passed

- [ ] **Step 5: Lint and commit**

```bash
ruff check bot tests
git add bot/keyboards/inline.py tests/test_registry_keyboards.py
git commit -m "Add RegistryCb and registry list/detail keyboards"
```

---

### Task 4: Registry handler — list and detail rendering

**Files:**
- Create: `bot/handlers/registry.py`
- Modify: `bot/handlers/__init__.py` (include the new router)
- Test: `tests/test_registry_access.py` (create)

**Interfaces:**
- Consumes: `list_for_registry` (Task 1), `RegistryCb` / `registry_list_kb` / `registry_detail_kb` (Task 3), locale keys (Task 2), `can_handle_type` from `bot/filters/roles.py`
- Produces:
  - `router` (aiogram `Router`, name `"registry"`)
  - `async def open_registry(message, i18n, db_user, session, type_: SubmissionType) -> None` — entry point used by menu callbacks and commands (Task 6)
  - `async def refresh_detail(query, i18n, db_user, session, submission_id: int) -> bool` — used by Task 5; returns `True` if the message was a registry detail view and got redrawn

- [ ] **Step 1: Write the failing test**

Create `tests/test_registry_access.py`:

```python
"""Registry access control.

The registry must be a new ENTRY POINT to existing rights, never new rights.
The critical assertion: a user responsible only for appeals can never reach a
corruption row, no matter what callback data they craft by hand.
"""
import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from bot.db.base import Base
from bot.db.models import Submission, SubmissionStatus, SubmissionType, User
from bot.handlers import registry
from bot.keyboards.inline import RegistryCb


class _I18n:
    def get(self, key, /, *args, **kwargs):
        return key


class _Query:
    """Minimal CallbackQuery stand-in capturing what the handler produced."""

    def __init__(self):
        self.answers = []
        self.edits = []
        self.message = self
        self.bot = None

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))

    async def edit_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))


@pytest.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    pool = async_sessionmaker(engine, expire_on_commit=False)
    async with pool() as s:
        conn = await s.connection()
        await conn.run_sync(Base.metadata.create_all)
        yield s
    await engine.dispose()


async def _make(session, type_, public_id="PID001"):
    sub = Submission(
        type=type_, text="secret body", is_anonymous=False,
        public_id=public_id, ticket_number="TKT-2026-0001",
        status=SubmissionStatus.new,
    )
    session.add(sub)
    await session.flush()
    return sub


@pytest.mark.asyncio
async def test_appeal_responsible_cannot_list_corruption(db):
    """Hand-crafted callback data asking for the other type must be refused."""
    user = User(tg_id=1, resp_appeal=True, resp_corruption=False)
    db.add(user)
    await db.flush()
    await _make(db, SubmissionType.corruption)

    query = _Query()
    cb = RegistryCb(type="corruption", status="all", order="desc", page=0, open="")
    await registry.on_registry_nav(query, cb, _I18n(), user, db)

    assert query.edits == []                       # nothing rendered
    assert query.answers and query.answers[0][1]   # alert shown


@pytest.mark.asyncio
async def test_appeal_responsible_cannot_open_corruption_detail(db):
    user = User(tg_id=1, resp_appeal=True, resp_corruption=False)
    db.add(user)
    await db.flush()
    sub = await _make(db, SubmissionType.corruption, public_id="CORR01")

    query = _Query()
    cb = RegistryCb(type="corruption", status="all", order="desc", page=0, open="CORR01")
    await registry.on_registry_nav(query, cb, _I18n(), user, db)

    rendered = " ".join(t for t, _ in query.edits)
    assert "secret body" not in rendered
    assert query.answers and query.answers[0][1]


@pytest.mark.asyncio
async def test_plain_citizen_is_refused(db):
    user = User(tg_id=2, resp_appeal=False, resp_corruption=False, is_admin=False)
    db.add(user)
    await db.flush()
    await _make(db, SubmissionType.appeal)

    query = _Query()
    cb = RegistryCb(type="appeal", status="all", order="desc", page=0, open="")
    await registry.on_registry_nav(query, cb, _I18n(), user, db)

    assert query.edits == []
    assert query.answers and query.answers[0][1]


@pytest.mark.asyncio
async def test_admin_sees_both_types(db):
    user = User(tg_id=3, is_admin=True)
    db.add(user)
    await db.flush()
    await _make(db, SubmissionType.corruption, public_id="CORR01")

    query = _Query()
    cb = RegistryCb(type="corruption", status="all", order="desc", page=0, open="")
    await registry.on_registry_nav(query, cb, _I18n(), user, db)

    assert query.edits  # rendered something


@pytest.mark.asyncio
async def test_responsible_sees_own_type(db):
    user = User(tg_id=4, resp_corruption=True)
    db.add(user)
    await db.flush()
    await _make(db, SubmissionType.corruption, public_id="CORR01")

    query = _Query()
    cb = RegistryCb(type="corruption", status="all", order="desc", page=0, open="")
    await registry.on_registry_nav(query, cb, _I18n(), user, db)

    assert query.edits
    assert "CORR01" in query.edits[0][0]


@pytest.mark.asyncio
async def test_anonymous_row_shows_no_author_in_list(db):
    user = User(tg_id=5, resp_corruption=True)
    db.add(user)
    sub = Submission(
        type=SubmissionType.corruption, text="body", is_anonymous=True,
        public_id="ANON01", ticket_number="TKT-2026-0002",
        status=SubmissionStatus.new, full_name=None, phone=None,
    )
    db.add(sub)
    await db.flush()

    query = _Query()
    cb = RegistryCb(type="corruption", status="all", order="desc", page=0, open="")
    await registry.on_registry_nav(query, cb, _I18n(), user, db)

    text = query.edits[0][0]
    assert "🕵" in text


@pytest.mark.asyncio
async def test_missing_submission_reports_not_found(db):
    user = User(tg_id=6, resp_appeal=True)
    db.add(user)
    await db.flush()

    query = _Query()
    cb = RegistryCb(type="appeal", status="all", order="desc", page=0, open="NOPE99")
    await registry.on_registry_nav(query, cb, _I18n(), user, db)

    assert any("registry-not-found" in str(a[0]) for a in query.answers)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_registry_access.py -q`
Expected: FAIL — `ImportError: cannot import name 'registry' from 'bot.handlers'`

- [ ] **Step 3: Write minimal implementation**

Create `bot/handlers/registry.py`:

```python
"""Submissions registry: a browsable list for responsibles and admins.

This is a new ENTRY POINT to existing rights, not new rights. Visibility is
decided by ``can_handle_type`` — the same check that gates the push card — and
actions reuse ReactionCb handled by responsible.py, so authorization lives in
exactly one place.

Filter/sort/page state travels inside the callback payload rather than FSM, so
the registry never collides with the submission form's state and an old
keyboard still works after a restart.
"""
from __future__ import annotations

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message
from aiogram_i18n import I18nContext
from sqlalchemy import select

from bot.db.models import Submission, SubmissionStatus, SubmissionType, User
from bot.db.repositories import SubmissionRepository
from bot.filters.roles import can_handle_type
from bot.keyboards.inline import RegistryCb, registry_detail_kb, registry_list_kb
from bot.utils.text import escape, split_text

router = Router(name="registry")

PER_PAGE = 5


def _status_filter(value: str) -> SubmissionStatus | None:
    """'all' means no status filter; anything else maps to the enum."""
    if value == "all":
        return None
    try:
        return SubmissionStatus(value)
    except ValueError:
        return None


def _author_label(sub: Submission) -> str:
    """Author cell: never reveal an anonymous complainant.

    Mirrors the push card exactly — anonymous rows show the spy glyph, and a
    non-anonymous row with no name shows a dash rather than leaking a username
    or tg_id into a list that may be read over someone's shoulder.
    """
    if sub.is_anonymous:
        return "🕵"
    return escape(sub.full_name) if sub.full_name else "—"


def _render_list(i18n, type_: SubmissionType, status: str, order: str,
                 rows: list[Submission], page: int, total: int) -> str:
    type_label = i18n.get(f"type-{type_.value}")
    filter_label = i18n.get(
        "btn-filter-all" if status == "all" else f"btn-filter-{status.replace('_', '-')}"
    )
    order_label = i18n.get("btn-sort-newest" if order == "desc" else "btn-sort-oldest")
    lines = [i18n.get("registry-title", type=type_label, filter=filter_label,
                      order=order_label), ""]
    for n, sub in enumerate(rows, start=1):
        lines.append(
            i18n.get(
                "registry-item",
                n=n,
                status=i18n.get(f"status-{sub.status.value}"),
                public_id=sub.public_id,
                author=_author_label(sub),
                date=sub.created_at.strftime("%d.%m.%Y"),
            )
        )
    return "\n".join(lines)


def _render_detail(i18n, sub: Submission) -> str:
    lines = [i18n.get("card-title", public_id=sub.public_id)]
    lines.append(i18n.get("card-type", type=i18n.get(f"type-{sub.type.value}")))
    if sub.is_anonymous:
        lines.append(i18n.get("card-anonymous"))
    else:
        if sub.full_name:
            lines.append(i18n.get("card-from", name=escape(sub.full_name)))
        if sub.phone:
            lines.append(i18n.get("card-phone", phone=escape(sub.phone)))
    lines.append(i18n.get("card-text", text=escape(sub.text)))
    lines.append(i18n.get("card-status", status=i18n.get(f"status-{sub.status.value}")))
    return "\n".join(lines)


async def _edit(query: CallbackQuery, text: str, markup) -> None:
    """Edit in place, tolerating Telegram's 'not modified' on a no-op tap."""
    try:
        await query.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        pass


@router.callback_query(RegistryCb.filter())
async def on_registry_nav(
    query: CallbackQuery, callback_data: RegistryCb, i18n: I18nContext,
    db_user: User, session,
) -> None:
    """Single entry for every registry click: filter, page, open, back."""
    try:
        type_ = SubmissionType(callback_data.type)
    except ValueError:
        await query.answer(i18n.get("error-generic"), show_alert=True)
        return

    # Re-checked on EVERY click: the keyboard may outlive a role change, and
    # callback data is client-supplied.
    if not can_handle_type(db_user, type_):
        await query.answer(i18n.get("admin-only"), show_alert=True)
        return

    if callback_data.open:
        await _show_detail(query, callback_data, i18n, type_, session)
        return

    await _show_list(query, callback_data, i18n, type_, session)


async def _show_list(query, cb: RegistryCb, i18n, type_: SubmissionType, session) -> None:
    page = max(cb.page, 0)
    rows, total = await SubmissionRepository(session).list_for_registry(
        type_=type_, status=_status_filter(cb.status), order=cb.order,
        page=page, per_page=PER_PAGE,
    )
    if not rows and total and page > 0:
        # The filter changed under an out-of-range page: fall back to the first.
        page = 0
        rows, total = await SubmissionRepository(session).list_for_registry(
            type_=type_, status=_status_filter(cb.status), order=cb.order,
            page=0, per_page=PER_PAGE,
        )

    total_pages = max((total + PER_PAGE - 1) // PER_PAGE, 1)
    if not rows:
        text = i18n.get("registry-empty")
    else:
        text = _render_list(i18n, type_, cb.status, cb.order, rows, page, total)

    markup = registry_list_kb(
        i18n, type_=cb.type, status=cb.status, order=cb.order, page=page,
        public_ids=[r.public_id for r in rows], total_pages=total_pages,
    )
    await _edit(query, text, markup)
    await query.answer()


async def _show_detail(query, cb: RegistryCb, i18n, type_: SubmissionType, session) -> None:
    sub = await session.scalar(
        select(Submission).where(Submission.public_id == cb.open)
    )
    # Type is re-derived from the ROW, not from callback data: a crafted payload
    # must not smuggle a foreign-type submission past the check above.
    if sub is None or sub.type != type_:
        await query.answer(i18n.get("registry-not-found"), show_alert=True)
        return

    markup = registry_detail_kb(
        i18n, submission_id=sub.id, type_=cb.type, status=cb.status,
        order=cb.order, page=cb.page, status_value=sub.status.value,
    )
    await _edit(query, split_text(_render_detail(i18n, sub))[0], markup)
    await query.answer()


async def open_registry(
    message: Message, i18n: I18nContext, db_user: User, session,
    type_: SubmissionType,
) -> None:
    """Entry point from the main menu / a command: send the first page."""
    if not can_handle_type(db_user, type_):
        await message.answer(i18n.get("admin-only"))
        return
    rows, total = await SubmissionRepository(session).list_for_registry(
        type_=type_, status=None, order="desc", page=0, per_page=PER_PAGE,
    )
    total_pages = max((total + PER_PAGE - 1) // PER_PAGE, 1)
    text = (
        _render_list(i18n, type_, "all", "desc", rows, 0, total)
        if rows else i18n.get("registry-empty")
    )
    await message.answer(
        text,
        reply_markup=registry_list_kb(
            i18n, type_=type_.value, status="all", order="desc", page=0,
            public_ids=[r.public_id for r in rows], total_pages=total_pages,
        ),
    )


async def refresh_detail(
    query: CallbackQuery, i18n: I18nContext, session, submission_id: int
) -> bool:
    """Redraw the detail screen after an action taken from the registry.

    No-op (returns False) when the click came from a push card, which
    responsible.py updates through its own update_all_cards path.
    """
    markup = query.message.reply_markup if query.message else None
    if markup is None:
        return False
    back = None
    for row in markup.inline_keyboard:
        for button in row:
            if button.callback_data and button.callback_data.startswith("reg:"):
                back = RegistryCb.unpack(button.callback_data)
                break
    if back is None:
        return False

    sub = await session.get(Submission, submission_id)
    if sub is None:
        return False
    await session.refresh(sub)
    await _edit(
        query,
        split_text(_render_detail(i18n, sub))[0],
        registry_detail_kb(
            i18n, submission_id=sub.id, type_=back.type, status=back.status,
            order=back.order, page=back.page, status_value=sub.status.value,
        ),
    )
    return True
```

Then modify `bot/handlers/__init__.py` — add the import and include the router **before** `start`:

```python
from bot.handlers import admin, my_submissions, registry, responsible, start, submission
```

```python
router.include_router(admin.router)
router.include_router(responsible.router)
router.include_router(registry.router)
router.include_router(submission.router)
router.include_router(my_submissions.router)
router.include_router(start.router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_registry_access.py -q`
Expected: PASS — 7 passed

- [ ] **Step 5: Lint and commit**

```bash
ruff check bot tests
git add bot/handlers/registry.py bot/handlers/__init__.py tests/test_registry_access.py
git commit -m "Add registry handler with list, detail and access re-checks"
```

---

### Task 5: Redraw the detail view after an action

**Files:**
- Modify: `bot/handlers/responsible.py` (in `on_take` and `on_close`, after the existing card updates)
- Test: `tests/test_registry_refresh.py` (create)

**Interfaces:**
- Consumes: `registry.refresh_detail(query, i18n, session, submission_id)` (Task 4)
- Produces: no new public names — behavior change only

Why here and not a registry-side handler: duplicating `ReactionCb` handling would split `_authorize()` across two code paths, which is exactly the failure mode the spec forbids.

- [ ] **Step 1: Write the failing test**

Create `tests/test_registry_refresh.py`:

```python
"""After acting from the registry, the detail screen must reflect the new state.

A click from a push card must NOT be redrawn this way — that path is handled by
update_all_cards, and redrawing it would blank the card for other responsibles.
"""
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from bot.db.base import Base
from bot.db.models import Submission, SubmissionStatus, SubmissionType
from bot.handlers import registry
from bot.keyboards.inline import RegistryCb, ReactionCb


class _I18n:
    def get(self, key, /, *args, **kwargs):
        return key


class _Msg:
    def __init__(self, markup):
        self.reply_markup = markup
        self.edited = []

    async def edit_text(self, text, reply_markup=None):
        self.edited.append((text, reply_markup))


class _Query:
    def __init__(self, markup):
        self.message = _Msg(markup)

    async def answer(self, text=None, show_alert=False):
        pass


@pytest.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    pool = async_sessionmaker(engine, expire_on_commit=False)
    async with pool() as s:
        conn = await s.connection()
        await conn.run_sync(Base.metadata.create_all)
        yield s
    await engine.dispose()


async def _sub(session):
    sub = Submission(
        type=SubmissionType.appeal, text="body", is_anonymous=False,
        public_id="PID001", ticket_number="TKT-2026-0001",
        status=SubmissionStatus.new,
    )
    session.add(sub)
    await session.flush()
    return sub


@pytest.mark.asyncio
async def test_refresh_redraws_when_click_came_from_registry(db):
    sub = await _sub(db)
    from bot.keyboards.inline import registry_detail_kb

    markup = registry_detail_kb(
        _I18n(), submission_id=sub.id, type_="appeal", status="all",
        order="desc", page=0, status_value="new",
    )
    query = _Query(markup)
    did = await registry.refresh_detail(query, _I18n(), db, sub.id)
    assert did is True
    assert query.message.edited


@pytest.mark.asyncio
async def test_refresh_is_noop_for_push_card_click(db):
    """A push card carries only ReactionCb — no registry state to return to."""
    sub = await _sub(db)
    from bot.keyboards.inline import reaction_keyboard

    query = _Query(reaction_keyboard(_I18n(), sub.id))
    did = await registry.refresh_detail(query, _I18n(), db, sub.id)
    assert did is False
    assert query.message.edited == []


@pytest.mark.asyncio
async def test_refresh_is_noop_when_message_has_no_keyboard(db):
    sub = await _sub(db)
    query = _Query(None)
    assert await registry.refresh_detail(query, _I18n(), db, sub.id) is False


@pytest.mark.asyncio
async def test_refreshed_keyboard_drops_take_after_claim(db):
    sub = await _sub(db)
    from bot.keyboards.inline import registry_detail_kb

    markup = registry_detail_kb(
        _I18n(), submission_id=sub.id, type_="appeal", status="all",
        order="desc", page=0, status_value="new",
    )
    query = _Query(markup)
    sub.status = SubmissionStatus.in_progress
    await db.flush()

    await registry.refresh_detail(query, _I18n(), db, sub.id)
    _, new_markup = query.message.edited[-1]
    texts = [b.text for row in new_markup.inline_keyboard for b in row]
    assert "btn-take" not in texts
    assert "btn-back-to-list" in texts
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_registry_refresh.py -q`
Expected: FAIL — `test_refreshed_keyboard_drops_take_after_claim` fails because nothing calls the refresh yet, or the import of `registry.refresh_detail` resolves but tests exercising the wiring fail. (If Task 4 is complete these may already pass; the wiring assertions in Step 3 are what this task adds.)

- [ ] **Step 3: Wire the refresh into the action handlers**

In `bot/handlers/responsible.py`, add the import at the top of the module:

```python
from bot.handlers import registry
```

In `on_take`, after `await svc.update_all_cards(...)` and its `await session.commit()`, before `await query.answer(...)`:

```python
    # If the click came from the registry, redraw that screen too — the card
    # update above only touches the push cards.
    await registry.refresh_detail(query, i18n, session, sub.id)
```

In `on_close`, after `await session.commit()` at the end of the handler, before `await query.answer(i18n.get("cb-closed"))`:

```python
    await registry.refresh_detail(query, i18n, session, sub.id)
```

⚠️ `bot/handlers/registry.py` must not import `responsible.py` — the dependency runs one way only, or Python raises a circular-import error at startup.

- [ ] **Step 4: Run the tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_registry_refresh.py tests/test_responsible_admin.py -q`
Expected: PASS — the registry refresh tests pass and the existing responsible/admin suite is unbroken.

- [ ] **Step 5: Lint and commit**

```bash
ruff check bot tests
git add bot/handlers/responsible.py tests/test_registry_refresh.py
git commit -m "Redraw registry detail view after take/close actions"
```

---

### Task 6: Role-aware main menu and personal command scope

**Files:**
- Modify: `bot/keyboards/inline.py` (`main_menu_keyboard`)
- Modify: `bot/handlers/start.py` (`show_menu`, `cmd_start`, `on_language_chosen`)
- Modify: `bot/handlers/registry.py` (add the two command handlers)
- Modify: `bot/runners/commands.py` (add `set_personal_commands`)
- Modify: `bot/handlers/admin.py` (`on_assign_type`, `on_revoke`)
- Test: `tests/test_registry_menu.py` (create)

**Interfaces:**
- Consumes: `open_registry` (Task 4), locale keys (Task 2)
- Produces:
  - `main_menu_keyboard(i18n, db_user=None) -> InlineKeyboardMarkup` — `db_user=None` keeps the citizen menu (back-compatible with existing call sites)
  - `set_personal_commands(bot, core, user, default_locale: str) -> None` in `bot/runners/commands.py`
  - `MenuCb` gains two actions: `reg_appeal`, `reg_corruption`

- [ ] **Step 1: Write the failing test**

Create `tests/test_registry_menu.py`:

```python
"""Role-aware menu and per-chat command scope.

The personal command list REPLACES the global one for that chat, so it must
include the base commands or an assigned responsible loses /start from the menu.
"""
from unittest.mock import AsyncMock

import pytest

from bot.db.models import User
from bot.keyboards.inline import main_menu_keyboard
from bot.runners.commands import set_personal_commands


class _I18n:
    def get(self, key, /, *args, **kwargs):
        return key


class _Core:
    def get(self, key, locale=None, **kwargs):
        return f"{key}"


def _texts(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


def test_citizen_menu_has_no_registry_entries():
    user = User(tg_id=1)
    texts = _texts(main_menu_keyboard(_I18n(), user))
    assert "btn-registry-appeals" not in texts
    assert "btn-registry-complaints" not in texts


def test_menu_without_user_is_citizen_menu():
    """Back-compat: existing call sites pass no user."""
    texts = _texts(main_menu_keyboard(_I18n()))
    assert "btn-appeal" in texts
    assert "btn-registry-appeals" not in texts


def test_appeal_responsible_sees_only_appeals_registry():
    user = User(tg_id=2, resp_appeal=True)
    texts = _texts(main_menu_keyboard(_I18n(), user))
    assert "btn-registry-appeals" in texts
    assert "btn-registry-complaints" not in texts


def test_corruption_responsible_sees_only_complaints_registry():
    user = User(tg_id=3, resp_corruption=True)
    texts = _texts(main_menu_keyboard(_I18n(), user))
    assert "btn-registry-complaints" in texts
    assert "btn-registry-appeals" not in texts


def test_admin_sees_both_registries():
    user = User(tg_id=4, is_admin=True)
    texts = _texts(main_menu_keyboard(_I18n(), user))
    assert "btn-registry-appeals" in texts
    assert "btn-registry-complaints" in texts


@pytest.mark.asyncio
async def test_personal_commands_keep_base_commands():
    """Chat scope REPLACES the global list — base commands must survive."""
    bot = AsyncMock()
    user = User(tg_id=5, resp_appeal=True, language="ru")
    await set_personal_commands(bot, _Core(), user, default_locale="ru")

    bot.set_my_commands.assert_awaited()
    commands = bot.set_my_commands.await_args.args[0]
    names = [c.command for c in commands]
    assert "start" in names and "language" in names and "cancel" in names
    assert "appeals" in names
    assert "complaints" not in names


@pytest.mark.asyncio
async def test_personal_commands_for_admin_include_both():
    bot = AsyncMock()
    user = User(tg_id=6, is_admin=True, language="ru")
    await set_personal_commands(bot, _Core(), user, default_locale="ru")

    names = [c.command for c in bot.set_my_commands.await_args.args[0]]
    assert "appeals" in names and "complaints" in names


@pytest.mark.asyncio
async def test_citizen_scope_is_deleted_not_set():
    """Losing the last role must restore the global menu, not pin an empty one."""
    bot = AsyncMock()
    user = User(tg_id=7, language="ru")
    await set_personal_commands(bot, _Core(), user, default_locale="ru")

    bot.delete_my_commands.assert_awaited()
    bot.set_my_commands.assert_not_awaited()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_registry_menu.py -q`
Expected: FAIL — `ImportError: cannot import name 'set_personal_commands'`

- [ ] **Step 3: Write the implementation**

**3a.** In `bot/keyboards/inline.py`, replace `main_menu_keyboard`:

```python
def main_menu_keyboard(i18n, db_user=None) -> InlineKeyboardMarkup:
    """Main menu; registry entries appear only for the roles that may use them.

    ``db_user`` is optional so the citizen menu stays the default. Hiding a
    button is UX only — the handlers re-check the role on every click.
    """
    kb = InlineKeyboardBuilder()
    kb.button(text=i18n.get("btn-appeal"), callback_data=MenuCb(action="appeal"))
    kb.button(text=i18n.get("btn-corruption"), callback_data=MenuCb(action="corruption"))
    kb.button(text=i18n.get("btn-my-submissions"), callback_data=MenuCb(action="my"))
    if db_user is not None:
        if db_user.is_admin or db_user.resp_appeal:
            kb.button(
                text=i18n.get("btn-registry-appeals"),
                callback_data=MenuCb(action="reg_appeal"),
            )
        if db_user.is_admin or db_user.resp_corruption:
            kb.button(
                text=i18n.get("btn-registry-complaints"),
                callback_data=MenuCb(action="reg_corruption"),
            )
    kb.button(text=i18n.get("btn-change-language"), callback_data=MenuCb(action="language"))
    kb.adjust(1)
    return kb.as_markup()
```

Update the `MenuCb` docstring comment to list the new actions:

```python
class MenuCb(CallbackData, prefix="menu"):
    action: str  # appeal | corruption | my | language | reg_appeal | reg_corruption
```

**3b.** In `bot/handlers/start.py`, thread `db_user` through — replace the three call sites:

```python
async def show_menu(message: Message, i18n: I18nContext, db_user: User | None = None) -> None:
    await message.answer(i18n.get("main-menu"), reply_markup=main_menu_keyboard(i18n, db_user))
```

In `cmd_start`, change `await show_menu(message, i18n)` to:

```python
    await show_menu(message, i18n, db_user)
```

In `on_language_chosen`, change the menu send to:

```python
    await query.message.answer(
        i18n.get("main-menu"), reply_markup=main_menu_keyboard(i18n, db_user)
    )
```

**3c.** In `bot/handlers/registry.py`, append the menu/command entry points:

```python
@router.callback_query(MenuCb.filter(F.action.in_({"reg_appeal", "reg_corruption"})))
async def on_menu_registry(
    query: CallbackQuery, callback_data: MenuCb, i18n: I18nContext,
    db_user: User, session,
) -> None:
    type_ = (
        SubmissionType.appeal
        if callback_data.action == "reg_appeal"
        else SubmissionType.corruption
    )
    await open_registry(query.message, i18n, db_user, session, type_)
    await query.answer()


@router.message(Command("appeals"))
async def cmd_appeals(message: Message, i18n: I18nContext, db_user: User, session) -> None:
    await open_registry(message, i18n, db_user, session, SubmissionType.appeal)


@router.message(Command("complaints"))
async def cmd_complaints(message: Message, i18n: I18nContext, db_user: User, session) -> None:
    await open_registry(message, i18n, db_user, session, SubmissionType.corruption)
```

Extend the imports at the top of `bot/handlers/registry.py`:

```python
from aiogram import F, Router
from aiogram.filters import Command
```

and add `MenuCb` to the keyboards import:

```python
from bot.keyboards.inline import (
    MenuCb,
    RegistryCb,
    registry_detail_kb,
    registry_list_kb,
)
```

**3d.** In `bot/runners/commands.py`, append:

```python
# Registry commands and the roles that may see them, checked per user.
_REGISTRY_COMMANDS = [
    ("appeals", "cmd-appeals", "resp_appeal"),
    ("complaints", "cmd-complaints", "resp_corruption"),
]


async def set_personal_commands(bot: Bot, core, user, default_locale: str) -> None:
    """Set this user's private '/' menu according to their roles.

    A chat-scoped list REPLACES the global one for that chat, so the base
    commands are re-sent alongside the registry ones — otherwise assigning a
    role would strip /start from that user's menu. With no roles left we delete
    the scope so the global list applies again.
    """
    from aiogram.types import BotCommandScopeChat

    locale = getattr(user, "language", None) or default_locale
    scope = BotCommandScopeChat(chat_id=user.tg_id)

    extra = [
        (cmd, key)
        for cmd, key, flag in _REGISTRY_COMMANDS
        if user.is_admin or getattr(user, flag, False)
    ]
    if not extra:
        await bot.delete_my_commands(scope=scope)
        return

    commands = _commands_for(core, locale) + [
        BotCommand(command=cmd, description=core.get(key, locale)) for cmd, key in extra
    ]
    await bot.set_my_commands(commands, scope=scope)
```

**3e.** In `bot/handlers/admin.py`, refresh the target's `/` menu after a role change.

Add to the imports at the top of the module:

```python
from bot.config import Settings
from bot.runners.commands import set_personal_commands
```

Add this helper at the bottom of the file:

```python
async def _refresh_commands(query: CallbackQuery, target: User, core, default_locale: str) -> None:
    """Update the target user's personal '/' menu after a role change.

    Best-effort: a user who has never started the bot has no reachable chat
    scope, and that must never fail the role change itself.
    """
    from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

    try:
        await set_personal_commands(query.bot, core, target, default_locale)
    except (TelegramBadRequest, TelegramForbiddenError):
        pass
```

Add `settings: Settings` to the signatures of `on_assign_type` and `on_revoke`, then call the helper in each — in `on_assign_type` after `await session.commit()` and before `await query.message.edit_text(...)`; in `on_revoke` after `await session.commit()` and before `await query.answer(...)`:

```python
    await _refresh_commands(query, target, i18n.core, settings.default_locale)
```

- [ ] **Step 4: Run the tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_registry_menu.py tests/test_responsible_admin.py tests/test_commands.py -q`
Expected: PASS — new menu tests pass, existing command and admin suites unbroken.

- [ ] **Step 5: Lint and commit**

```bash
ruff check bot tests
git add bot/keyboards/inline.py bot/handlers/start.py bot/handlers/registry.py bot/runners/commands.py bot/handlers/admin.py tests/test_registry_menu.py
git commit -m "Add role-aware menu entries and personal command scope for the registry"
```

---

### Task 7: End-to-end flow test through the dispatcher

**Files:**
- Test: `tests/test_registry_flow.py` (create)

**Interfaces:**
- Consumes: everything from Tasks 1–6. No production code changes — this task proves the wiring holds through a real `Dispatcher.feed_update`.

Mirror the harness in `tests/test_handlers_flow.py`: module-level routers are singletons, so `importlib.reload` each handler module and build a fresh router tree per test, or the second test to run attaches a router to a second dispatcher and errors.

- [ ] **Step 1: Write the test**

Create `tests/test_registry_flow.py`:

```python
"""Registry end-to-end through Dispatcher.feed_update.

Proves the wiring holds: menu -> list -> filter -> page -> open -> back, with
the filter and page surviving the round trip.
"""
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from bot.config import Settings
from bot.db.base import Base
from bot.db.models import Submission, SubmissionStatus, SubmissionType, User
from bot.keyboards.inline import RegistryCb

_DT = datetime(2026, 6, 2, tzinfo=UTC)
_UID = 2000


class _StubI18n:
    def __init__(self):
        self.core = SimpleNamespace(get=lambda key, locale=None, **kw: key)

    def get(self, key, /, *args, **kwargs):
        return key

    async def set_locale(self, code, **kw):
        pass


class _StubI18nMiddleware:
    async def __call__(self, handler, event, data):
        data["i18n"] = _StubI18n()
        return await handler(event, data)


class _DbMiddleware:
    def __init__(self, pool):
        self.pool = pool

    async def __call__(self, handler, event, data):
        from sqlalchemy import select

        async with self.pool() as session:
            data["session"] = session
            user = await session.scalar(select(User).where(User.tg_id == _UID))
            data["db_user"] = user
            result = await handler(event, data)
            await session.commit()
            return result


@pytest_asyncio.fixture
async def harness():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    pool = async_sessionmaker(engine, expire_on_commit=False)
    async with pool() as s:
        conn = await s.connection()
        await conn.run_sync(Base.metadata.create_all)
        s.add(User(tg_id=_UID, language="ru", resp_appeal=True))
        for i in range(7):
            s.add(Submission(
                type=SubmissionType.appeal, text=f"body {i}", is_anonymous=False,
                public_id=f"PID{i:05d}", ticket_number=f"TKT-2026-{i:04d}",
                status=SubmissionStatus.new if i % 2 else SubmissionStatus.closed,
                full_name=f"Author {i}",
            ))
        await s.commit()

    settings = Settings(
        _env_file=None, bot_token="1:x", anon_enc_key=Fernet.generate_key().decode(),
    )

    import importlib

    from aiogram import Router

    from bot.handlers import (
        admin, errors, my_submissions, registry, responsible, start, submission,
    )

    for mod in (errors, admin, responsible, registry, submission, my_submissions, start):
        importlib.reload(mod)
    root = Router(name="root-test")
    root.include_router(admin.router)
    root.include_router(responsible.router)
    root.include_router(registry.router)
    root.include_router(submission.router)
    root.include_router(my_submissions.router)
    root.include_router(start.router)
    errors.register_errors(root)

    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(_DbMiddleware(pool))
    dp.update.outer_middleware(_StubI18nMiddleware())
    dp.include_router(root)
    dp["settings"] = settings

    bot = AsyncMock()
    bot.id = 1
    yield SimpleNamespace(dp=dp, bot=bot, pool=pool)
    await engine.dispose()


def _cb(data: str, msg_id: int = 1):
    from aiogram.types import CallbackQuery, Chat, Message, Update
    from aiogram.types import User as TgUser

    user = TgUser(id=_UID, is_bot=False, first_name="R")
    chat = Chat(id=_UID, type="private")
    msg = Message(message_id=msg_id, chat=chat, from_user=user, date=_DT)
    return Update(
        update_id=msg_id,
        callback_query=CallbackQuery(
            id=str(msg_id), from_user=user, chat_instance="ci", data=data, message=msg
        ),
    )


def _text(text: str):
    from aiogram.types import Chat, Message, Update
    from aiogram.types import User as TgUser

    user = TgUser(id=_UID, is_bot=False, first_name="R")
    chat = Chat(id=_UID, type="private")
    return Update(
        update_id=999,
        message=Message(message_id=999, chat=chat, from_user=user, date=_DT, text=text),
    )


The `_text`/`_cb` builders above produce real aiogram objects, but `Message.answer` and `edit_text` need a bot to call. Patch them to record instead, so the assertions can inspect what was rendered:

```python
@pytest.fixture
def sent(monkeypatch):
    """Capture what handlers render, without touching the network."""
    from aiogram.types import Message

    captured = []

    async def _answer(self, text, **kwargs):
        captured.append((text, kwargs.get("reply_markup")))
        return self

    async def _edit(self, text, **kwargs):
        captured.append((text, kwargs.get("reply_markup")))
        return self

    monkeypatch.setattr(Message, "answer", _answer, raising=False)
    monkeypatch.setattr(Message, "edit_text", _edit, raising=False)
    return captured


@pytest.mark.asyncio
async def test_command_opens_registry(harness, sent):
    await harness.dp.feed_update(harness.bot, _text("/appeals"))
    assert sent, "the /appeals command rendered nothing"
    text, markup = sent[-1]
    assert "registry-title" in text
    assert markup is not None


@pytest.mark.asyncio
async def test_status_filter_limits_the_rows(harness, sent):
    """7 seeded rows: 3 new (odd i), 4 closed. The 'new' filter must show 3."""
    data = RegistryCb(type="appeal", status="new", order="asc", page=0, open="").pack()
    await harness.dp.feed_update(harness.bot, _cb(data))
    text, _ = sent[-1]
    assert text.count("registry-item") == 3


@pytest.mark.asyncio
async def test_open_detail_then_back_restores_filter_and_page(harness, sent):
    open_cb = RegistryCb(
        type="appeal", status="new", order="desc", page=0, open="PID00001"
    ).pack()
    await harness.dp.feed_update(harness.bot, _cb(open_cb))
    detail_text, detail_kb = sent[-1]
    assert "body 1" in detail_text  # the detail view shows the body

    back = [
        b.callback_data
        for row in detail_kb.inline_keyboard
        for b in row
        if b.callback_data and b.callback_data.startswith("reg:")
    ][0]
    unpacked = RegistryCb.unpack(back)
    assert (unpacked.status, unpacked.order, unpacked.open) == ("new", "desc", "")

    await harness.dp.feed_update(harness.bot, _cb(back, msg_id=2))
    list_text, _ = sent[-1]
    assert "registry-title" in list_text


@pytest.mark.asyncio
async def test_citizen_command_is_refused(harness, sent):
    """A user with no roles must not reach the registry via the raw command."""
    from sqlalchemy import select

    async with harness.pool() as s:
        user = await s.scalar(select(User).where(User.tg_id == _UID))
        user.resp_appeal = False
        await s.commit()

    await harness.dp.feed_update(harness.bot, _text("/appeals"))
    assert sent, "expected a refusal message"
    text, _ = sent[-1]
    assert text == "admin-only"
    assert "registry-item" not in text
```

- [ ] **Step 2: Run the test**

Run: `.venv\Scripts\python.exe -m pytest tests/test_registry_flow.py -q`
Expected: PASS — 4 passed. If a router-reuse error appears (`Router is already attached`), the `importlib.reload` loop is missing a module.

- [ ] **Step 3: Run the complete suite**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: PASS — 86 baseline + the new tests, zero failures.

- [ ] **Step 4: Lint**

Run: `ruff check bot tests`
Expected: no output (clean)

- [ ] **Step 5: Commit**

```bash
git add tests/test_registry_flow.py
git commit -m "Add end-to-end registry flow tests through the dispatcher"
```

---

### Task 8: Documentation

**Files:**
- Modify: `README.md` (feature list and the responsible-person section)
- Modify: `CLAUDE.md` (registry section)

**Interfaces:** none — documentation only.

- [ ] **Step 1: Update `README.md`**

In the `## Возможности` list, after the «Ответственные лица…» bullet, add:

```markdown
- 🗂 **Реестр заявок** для ответственных и админов: список своих типов с фильтром по статусу, сортировкой и постраничной навигацией; из карточки списка доступны те же действия (взять / ответить / закрыть). Пункт меню и команда (`/appeals`, `/complaints`) видны только тем, у кого есть роль.
```

- [ ] **Step 2: Update `CLAUDE.md`**

Add after the "### Authorization" section:

```markdown
### Registry (bot/handlers/registry.py)

A browsable list for responsibles/admins — a new **entry point to existing rights**, never new rights. Two invariants hold it together:

- Visibility is `can_handle_type()`, the same check that gates the push card. It is re-checked on **every** callback, because a keyboard outlives a role change and callback data is client-supplied. In `_show_detail` the type is re-derived from the row, so a crafted `public_id` cannot smuggle a foreign-type submission past the gate.
- Actions are **not** reimplemented: the detail keyboard emits the existing `ReactionCb`, handled by `responsible.py` with its `_authorize()`. `registry.refresh_detail()` only redraws afterwards. Never add a competing `ReactionCb` handler — that splits authz in two.

Filter/sort/page state lives in `RegistryCb` (48 bytes worst case, Telegram's cap is 64) rather than FSM, so it never collides with `SubmissionForm` and survives a restart. `registry.py` must not import `responsible.py` — the dependency is one-way.

`set_personal_commands()` uses `BotCommandScopeChat`, which **replaces** the whole list for that chat: it re-sends the base commands with the registry ones, and deletes the scope when the last role is revoked.
```

- [ ] **Step 3: Verify nothing broke**

Run: `.venv\Scripts\python.exe -m pytest -q && ruff check bot tests`
Expected: all pass, lint clean

- [ ] **Step 4: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "Document the submissions registry"
```

---

## Done criteria

- [ ] `.venv\Scripts\python.exe -m pytest -q` — all green (86 baseline + ~37 new)
- [ ] `ruff check bot tests` — clean
- [ ] A `resp_appeal`-only user cannot reach a `corruption` row via crafted callback data (proved by `test_registry_access.py`)
- [ ] Anonymous rows show `🕵` and never a name or phone
- [ ] All 5 locales have identical key sets (`tests/test_i18n.py`)
- [ ] Assigning a role does not strip `/start` from the target's `/` menu
