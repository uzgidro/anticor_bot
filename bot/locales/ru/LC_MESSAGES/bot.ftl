# ===== Общее =====
choose-language = 🌐 Выберите язык / Тилни танланг / Tilni tanlang / Tildi saylañ / Choose a language
language-set = ✅ Язык установлен: русский
error-generic = ⚠️ Произошла ошибка. Попробуйте позже.
btn-back = ◀️ Назад
btn-cancel = ❌ Отмена
btn-skip = ⏭️ Пропустить
btn-done = ✅ Готово
cancelled = ❌ Действие отменено.
throttled = ⏳ Слишком много запросов. Подождите немного.

# ===== Главное меню =====
main-menu = 👋 Здравствуйте! Это бот АО «Узбекгидроэнерго». Выберите, что вы хотите сделать:
btn-appeal = 📝 Обращение граждан
btn-corruption = 🛡 Жалоба на коррупцию
btn-my-submissions = 📋 Мои обращения
btn-change-language = 🌐 Сменить язык

# ===== Форма =====
form-anonymous-ask = 🕵 Хотите подать жалобу анонимно?
form-anonymous-warning = ⚠️ Внимание: вложения (фото/документы) могут содержать данные, раскрывающие вашу личность (например, метаданные/EXIF). Анонимность означает, что бот не сохранит и не покажет ваши имя и контакты — но не указывайте их и в тексте обращения.
btn-yes = ✅ Да
btn-no = ❌ Нет
form-ask-name = 👤 Укажите ваше ФИО:
form-ask-phone = 📞 Укажите ваш номер телефона или поделитесь контактом:
btn-share-contact = 📲 Поделиться контактом
form-ask-text = ✍️ Опишите ваше обращение:
form-text-anon-hint = 🔒 Не указывайте в тексте своё имя, телефон или иные данные, по которым вас можно опознать.
form-ask-attachments = 📎 Прикрепите фото или документы (по желанию) и нажмите «Готово», либо «Пропустить».
form-attachment-added = 📎 Вложение добавлено ({ $count }). Можно добавить ещё или нажать «Готово».
form-invalid-text = ⚠️ Пожалуйста, отправьте текстом.
form-empty-text = ⚠️ Текст не может быть пустым. Попробуйте снова.
form-invalid-phone = ⚠️ Похоже, это не телефон. Введите номер или поделитесь контактом.
form-too-long = ⚠️ Слишком длинный текст (макс. { $max } символов). Сократите, пожалуйста.

# ===== Подтверждение =====
form-summary = 📋 Проверьте ваше обращение:
form-summary-type = Тип: { $type }
form-summary-name = ФИО: { $name }
form-summary-phone = Телефон: { $phone }
form-summary-anonymous = 🕵 Анонимно
form-summary-text = Текст: { $text }
form-summary-attachments = Вложений: { $count }
btn-submit = ✅ Отправить
form-confirm-cancel = Вы уверены, что хотите отменить? Введённые данные будут потеряны.

# ===== После отправки =====
submission-accepted = ✅ Спасибо за ваше обращение!
submission-accepted-ticket = Номер вашего обращения: { $public_id }
submission-accepted-note = Мы рассмотрим его в ближайшее время.
submission-accepted-no-responsible = ✅ Спасибо за ваше обращение! Номер: { $public_id }. Оно принято и в ближайшее время будет назначено ответственному.

# ===== Мои обращения =====
my-submissions-empty = 📭 У вас пока нет обращений.
my-submissions-title = 📋 Ваши обращения:
my-submission-item = { $public_id } — { $type } — { $status }

# ===== Типы и статусы =====
type-appeal = Обращение граждан
type-corruption = Жалоба на коррупцию
status-new = 🆕 Новое
status-in_progress = 🟡 В работе
status-closed = ✅ Закрыто

# ===== Смена языка в форме =====
language-locked-in-form = ⚠️ Сначала завершите или отмените текущее обращение.

# ===== Карточка ответственного =====
card-title = 📨 Новое обращение { $public_id }
card-type = Тип: { $type }
card-from = От: { $name }
card-phone = Телефон: { $phone }
card-anonymous = 🕵 Анонимное обращение
card-text = Текст: { $text }
card-status = Статус: { $status }
card-assigned = 🟡 В работе у { $name }
btn-take = 🟡 Взять в работу
btn-reply = ✍️ Ответить
btn-close = ✅ Закрыть
cb-already-taken = ⚠️ Уже в работе у { $name }
cb-taken = ✅ Вы взяли обращение в работу.
cb-closed = ✅ Обращение закрыто.
reply-ask = ✍️ Введите текст ответа заявителю:
reply-sent = ✅ Ответ отправлен заявителю.

# ===== Ответ заявителю =====
reply-to-author = 📩 Ответ по вашему обращению { $public_id }:
reply-to-author-body = { $text }
submission-closed-notify = ✅ Ваше обращение { $public_id } закрыто.

# ===== Админ =====
admin-only = ⛔ Команда доступна только администраторам.
admin-assign-usage = Использование: перешлите сообщение пользователя или укажите его ID, затем выберите тип.
admin-assign-choose-type = Выберите тип ответственности для пользователя:
btn-resp-appeal = 📝 Ответственный за обращения
btn-resp-corruption = 🛡 Ответственный за жалобы
admin-assigned = ✅ Роль назначена пользователю { $user }.
admin-no-responsibles = ⚠️ Нет ответственных для типа «{ $type }». Назначьте их командой /assign.
admin-responsibles-title = 👥 Ответственные лица:
admin-responsibles-empty = 📭 Ответственные ещё не назначены.
btn-revoke = 🗑 Снять роль
admin-revoked = ✅ Роль снята.
new-submission-admin-alert = ⚠️ Поступило обращение { $public_id } ({ $type }), но нет назначенных ответственных.

# ===== Команды меню =====
cmd-start = Главное меню
cmd-language = Сменить язык
cmd-cancel = Отменить текущее действие

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

## Matrix (Element) room
mx-attachments = 📎 Вложения: { $count }
mx-attachment-failed = ⚠️ Не удалось загрузить вложений: { $count } (больше 20 МБ или ошибка)
mx-assignee = 👤 Исполнитель: { $name }
mx-hint-take = ▶️ Взять в работу: ответьте на это сообщение <code>!olish</code>
mx-hint-reply = ✍️ Ответ заявителю: ответьте на это сообщение текстом
mx-hint-close = ✅ Закрыть: ответьте на это сообщение <code>!yopish</code>
mx-hint-card = 🔄 Карточка: <code>!karta</code>
mx-not-owner = ⚠️ Заявку ведёт { $name } — закрыть или ответить может только он
mx-already-closed = ℹ️ Заявка уже закрыта
mx-forbidden = ⚠️ У вас нет прав на заявки этого типа
mx-reply-undeliverable = ⚠️ Не удалось доставить заявителю (возможно, бот заблокирован). Ответ сохранён.
mx-help = <b>Команды</b> (ответом на карточку):<br/>• текст — ответ заявителю<br/>• <code>!olish</code> — взять в работу<br/>• <code>!yopish</code> — закрыть<br/>• <code>!karta</code> — показать карточку заново<br/>• <code>!yordam</code> — эта справка
