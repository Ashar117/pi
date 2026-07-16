# Telegram smoke checklist (T-261)

Run this by hand after touching `tools/tools_telegram.py`. The T-244..T-248
bug cluster (context loss, quote-reply miss, literal-empty message, HTML 400,
empty-photo crash) all shipped to production because nothing exercised the
real handlers before Ash hit them live — this is the checklist that would
have caught each one.

Automated coverage for the send/callback plumbing lives in
`testing/test_telegram_handlers.py` (drives the real registered handlers via
a fake bot) and `testing/test_telegram_fixes_t245_t246_t247.py`. This
document is for the parts that genuinely need a live Telegram client: real
network round-trips, real message rendering, real button taps.

## The 5 steps

1. **Plain text message.** Send "hello" (or any short question).
   *Expected:* a normal reply arrives, formatted correctly (bold/italic/links
   render, no raw `<b>` or `**` visible). *Would have caught:* T-246 (empty
   response sent literally).

2. **Photo with caption.** Send a photo with a short caption/question.
   *Expected:* Pi analyzes and replies referencing the image content.
   *Would have caught:* T-248 (`IndexError` on an empty `message.photo` list).

3. **Quote-reply.** Long-press an earlier message from Pi and reply to it
   with something short ("what did you mean by that?").
   *Expected:* the reply shows Pi understood which message was quoted.
   *Would have caught:* T-245 (quote-reply context not injected).

4. **Inline-button tap.** Trigger any flow that sends buttons (e.g. an
   approval request, or the T-258 email-triage alert if a watcher is
   configured) and tap one.
   *Expected:* the tap produces a real follow-up action, not silence.
   *Would have caught:* T-220 wiring regressions.

5. **Long or HTML-heavy response.** Ask something that produces a long,
   formatted answer (e.g. "give me a detailed comparison of X and Y with
   code examples").
   *Expected:* delivered in full across multiple messages if >4096 chars, no
   400 error, no visible raw HTML tags even if a chunk boundary lands mid-tag.
   *Would have caught:* T-247 (malformed HTML entity 400).

## If something fails

File a ticket with: which step, the exact Telegram error text (if any), and
whether it reproduces twice in a row (rules out a transient Telegram API
hiccup vs. a real regression).
