# ===== Common =====
choose-language = 🌐 Выберите язык / Тилни танланг / Tilni tanlang / Tildi saylañ / Choose a language
language-set = ✅ Language set: English
error-generic = ⚠️ An error occurred. Please try again later.
btn-back = ◀️ Back
btn-cancel = ❌ Cancel
btn-skip = ⏭️ Skip
btn-done = ✅ Done
cancelled = ❌ Action cancelled.
throttled = ⏳ Too many requests. Please wait a moment.

# ===== Main menu =====
main-menu = 👋 Hello! This is the bot of JSC Uzbekgidroenergo. Please choose what you would like to do:
btn-appeal = 📝 Citizen appeal
btn-corruption = 🛡 Corruption complaint
btn-my-submissions = 📋 My submissions
btn-change-language = 🌐 Change language

# ===== Form =====
form-anonymous-ask = 🕵 Would you like to submit the complaint anonymously?
form-anonymous-warning = ⚠️ Note: attachments (photos/documents) may contain data that reveals your identity (e.g. metadata/EXIF). Anonymous means the bot won't store or show your name and contacts — but do not include them in the text either.
btn-yes = ✅ Yes
btn-no = ❌ No
form-ask-name = 👤 Please enter your full name:
form-ask-phone = 📞 Please enter your phone number or share your contact:
btn-share-contact = 📲 Share contact
form-ask-text = ✍️ Please describe your submission:
form-text-anon-hint = 🔒 Do not include your name, phone or any data that could identify you in the text.
form-ask-attachments = 📎 Attach photos or documents (optional) and tap "Done", or "Skip".
form-attachment-added = 📎 Attachment added ({ $count }). Add more or tap "Done".
form-invalid-text = ⚠️ Please send it as text.
form-empty-text = ⚠️ The text cannot be empty. Please try again.
form-invalid-phone = ⚠️ That doesn't look like a phone number. Enter it or share your contact.
form-too-long = ⚠️ The text is too long (max { $max } characters). Please shorten it.

# ===== Confirmation =====
form-summary = 📋 Please review your submission:
form-summary-type = Type: { $type }
form-summary-name = Full name: { $name }
form-summary-phone = Phone: { $phone }
form-summary-anonymous = 🕵 Anonymous
form-summary-text = Text: { $text }
form-summary-attachments = Attachments: { $count }
btn-submit = ✅ Submit
form-confirm-cancel = Are you sure you want to cancel? Your entered data will be lost.

# ===== After submit =====
submission-accepted = ✅ Thank you for your submission!
submission-accepted-ticket = Your submission number: { $public_id }
submission-accepted-note = We will review it shortly.
submission-accepted-no-responsible = ✅ Thank you for your submission! Number: { $public_id }. It has been accepted and will be assigned to a responsible officer soon.

# ===== My submissions =====
my-submissions-empty = 📭 You have no submissions yet.
my-submissions-title = 📋 Your submissions:
my-submission-item = { $public_id } — { $type } — { $status }

# ===== Types and statuses =====
type-appeal = Citizen appeal
type-corruption = Corruption complaint
status-new = 🆕 New
status-in_progress = 🟡 In progress
status-closed = ✅ Closed

# ===== Language change inside form =====
language-locked-in-form = ⚠️ Please finish or cancel your current submission first.

# ===== Responsible person card =====
card-title = 📨 New submission { $public_id }
card-type = Type: { $type }
card-from = From: { $name }
card-phone = Phone: { $phone }
card-anonymous = 🕵 Anonymous submission
card-text = Text: { $text }
card-status = Status: { $status }
card-assigned = 🟡 In progress by { $name }
btn-take = 🟡 Take in progress
btn-reply = ✍️ Reply
btn-close = ✅ Close
cb-already-taken = ⚠️ Already in progress by { $name }
cb-taken = ✅ You took this submission.
cb-closed = ✅ Submission closed.
reply-ask = ✍️ Enter the reply text for the applicant:
reply-sent = ✅ The reply has been sent to the applicant.

# ===== Reply to applicant =====
reply-to-author = 📩 Reply to your submission { $public_id }:
reply-to-author-body = { $text }
submission-closed-notify = ✅ Your submission { $public_id } has been closed.

# ===== Admin =====
admin-only = ⛔ This command is for administrators only.
admin-assign-usage = Usage: forward the user's message, or give their Telegram ID or Matrix ID (@user:server), then pick the type.
admin-assign-choose-type = Choose the responsibility type for the user:
btn-resp-appeal = 📝 Responsible for appeals
btn-resp-corruption = 🛡 Responsible for complaints
admin-assigned = ✅ Role assigned to user { $user }.
admin-no-responsibles = ⚠️ No responsible persons for type "{ $type }". Assign them with /assign.
admin-responsibles-title = 👥 Responsible persons:
admin-responsibles-empty = 📭 No responsible persons assigned yet.
btn-revoke = 🗑 Revoke role
admin-revoked = ✅ Role revoked.
new-submission-admin-alert = ⚠️ Submission { $public_id } ({ $type }) arrived, but there are no assigned responsible persons.

# ===== Command menu =====
cmd-start = Main menu
cmd-language = Change language
cmd-cancel = Cancel the current action

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

## Matrix (Element) room
mx-attachments = 📎 Attachments: { $count }
mx-attachment-failed = ⚠️ { $count } attachment(s) could not be uploaded (over 20 MB or an error)
mx-assignee = 👤 Assignee: { $name }
mx-hint-take = ▶️ Take: reply to this message with <code>!olish</code>
mx-hint-reply = ✍️ Answer the applicant: reply to this message with text
mx-hint-close = ✅ Close: reply to this message with <code>!yopish</code>
mx-hint-card = 🔄 Card: <code>!karta</code>
mx-not-owner = ⚠️ { $name } is handling this — only they can close or answer it
mx-already-closed = ℹ️ Already closed
mx-forbidden = ⚠️ You are not allowed to handle this type of submission
mx-reply-undeliverable = ⚠️ Could not deliver to the applicant (they may have blocked the bot). The answer was saved.
mx-help = <b>Commands</b> (as a reply to a card):<br/>• text — answer the applicant<br/>• <code>!olish</code> — take<br/>• <code>!yopish</code> — close<br/>• <code>!karta</code> — show the card again<br/>• <code>!yordam</code> — this help
mx-dm-welcome = 👋 Your Matrix ID: <code>{ $id }</code><br/>An administrator must grant you a role with <code>/assign { $id }</code> in Telegram. Submissions will then arrive here.
