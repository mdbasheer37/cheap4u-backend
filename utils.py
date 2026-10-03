# utils.py
import random
import secrets
import string
import re
import time
import requests
import logging
from flask import current_app

logger = logging.getLogger(__name__)

# Hard cap on how long one send_sms() call may take in total, and on any single
# Termii request within it (see send_sms).
# 4 attempts x 8s = 32s: every route (incl. the Number API fallback that works
# without sender-ID approval) still gets a turn even if Termii is slow, but a
# Termii outage can no longer hold a request for the old 4 x 15s = 60s.
SMS_TOTAL_BUDGET_SECONDS    = 32
SMS_ATTEMPT_TIMEOUT_SECONDS = 8


def generate_referral_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))


def generate_otp():
    # `secrets`, not `random`: these codes guard account verification and
    # password/PIN resets, so they must come from a CSPRNG (random's output is
    # predictable once enough of it has been observed).
    return ''.join(secrets.choice(string.digits) for _ in range(6))


def format_currency(amount):
    return f"₦{amount:,.2f}"


def validate_email(email):
    pattern = r'^[\w\.-]+@[\w\.-]+\.\w+$'
    return re.match(pattern, email) is not None


def _clean_phone(phone):
    return str(phone).strip().replace(" ", "").replace("-", "")


def validate_phone(phone):
    """Accepts local Nigerian format: 11 digits starting with 0 (080..., 070..., 081..., 090..., 091...)."""
    phone = _clean_phone(phone)
    return len(phone) == 11 and phone.isdigit() and phone.startswith("0")


def _to_international(phone):
    """
    Normalize a Nigerian number to Termii's expected international format
    (234XXXXXXXXXX, no leading '+'). Handles the formats users actually type:
      - "08012345678"      (11 digits, local)
      - "2348012345678"    (13 digits, already international)
      - "+2348012345678"   (with plus)
      - "8012345678"       (10 digits, leading 0 dropped by autocorrect/paste)
    Returns None for anything that doesn't cleanly match one of these —
    callers must treat None as "do not send", never guess further.
    """
    phone = _clean_phone(phone)
    if phone.startswith("+"):
        phone = phone[1:]

    if phone.startswith("0") and len(phone) == 11 and phone.isdigit():
        return "234" + phone[1:]
    if phone.startswith("234") and len(phone) == 13 and phone.isdigit():
        return phone
    # Common paste/autocorrect artifact: leading 0 dropped, 10 digits left,
    # starting with a real NG mobile prefix digit (7/8/9).
    if len(phone) == 10 and phone.isdigit() and phone[0] in ("7", "8", "9"):
        return "234" + phone
    return None


def _termii_attempt(api_key, phone_intl, message, channel, sender=None, timeout=15):
    """
    One Termii send attempt. Returns a dict:
      {'sent': bool, 'message_id': str|None, 'raw_message': str|None, 'error': str|None}
    'sent' means Termii's API *accepted* the request — not that the carrier
    confirmed delivery (Termii's standard /sms/send response doesn't include
    delivery confirmation; only its DLR webhook does, which isn't wired up
    here — see the note in send_sms()).
    """
    payload = {
        "api_key": api_key,
        "to": phone_intl,
        "sms": message,
        "type": "plain",
        "channel": channel,
    }
    if sender:
        payload["from"] = sender
    try:
        r = requests.post(
            "https://api.ng.termii.com/api/sms/send",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        try:
            data = r.json()
        except Exception:
            data = {}
        # Never log the api_key or the OTP/message body — log only what's
        # needed to diagnose delivery problems.
        logger.info(
            f"Termii [channel={channel} sender={sender or '-'}] → {phone_intl}: "
            f"http={r.status_code} termii_message={data.get('message')!r} "
            f"message_id={data.get('message_id')!r}"
        )
        sent = (r.status_code == 200 and data.get("message") == "Successfully Sent")
        return {
            "sent": sent,
            "message_id": data.get("message_id"),
            "raw_message": data.get("message"),
            "error": None if sent else (data.get("message") or f"HTTP {r.status_code}"),
        }
    except requests.exceptions.Timeout:
        logger.error(f"Termii [channel={channel}] timed out after {timeout}s → {phone_intl}")
        return {"sent": False, "message_id": None, "raw_message": None, "error": "timeout"}
    except Exception as e:
        logger.error(f"Termii [channel={channel}] exception → {phone_intl}: {e}")
        return {"sent": False, "message_id": None, "raw_message": None, "error": str(e)}


def _termii_number_send(api_key, phone_intl, message, timeout=15):
    """
    Termii's "Number API" — a SEPARATE endpoint from the one used above, not
    a channel value on it. Auto-assigns a local-looking sending number per
    country, so unlike dnd/generic it needs no sender-ID approval or route
    activation. Payload is deliberately minimal: no 'from', no 'type', no
    'channel' — sending those extra fields to THIS endpoint is what caused
    "One or more fields failed validation" when this used to (incorrectly)
    call /api/sms/send with channel="number", which was never a valid
    channel value there (only "generic"/"dnd" are).
    """
    payload = {"api_key": api_key, "to": phone_intl, "sms": message}
    try:
        r = requests.post(
            "https://api.ng.termii.com/api/sms/number/send",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        try:
            data = r.json()
        except Exception:
            data = {}
        logger.info(
            f"Termii [number-api] → {phone_intl}: http={r.status_code} "
            f"termii_message={data.get('message')!r} message_id={data.get('message_id')!r}"
        )
        sent = (r.status_code == 200 and data.get("message") == "Successfully Sent")
        return {
            "sent": sent,
            "message_id": data.get("message_id"),
            "raw_message": data.get("message"),
            "error": None if sent else (data.get("message") or f"HTTP {r.status_code}"),
        }
    except requests.exceptions.Timeout:
        logger.error(f"Termii [number-api] timed out after {timeout}s → {phone_intl}")
        return {"sent": False, "message_id": None, "raw_message": None, "error": "timeout"}
    except Exception as e:
        logger.error(f"Termii [number-api] exception → {phone_intl}: {e}")
        return {"sent": False, "message_id": None, "raw_message": None, "error": str(e)}


def send_sms(phone, message, diagnostics=None):
    """
    Send an SMS via Termii. Returns (sent: bool, message_id: str|None,
    error: str|None) — sent=True means Termii's API accepted the request for
    delivery, NOT that the carrier confirmed it reached the handset (Termii's
    /sms/send response has no delivery confirmation; that requires a DLR
    webhook configured on the Termii dashboard, which this account does not
    currently have wired up — see the note at the bottom of this function).

    Route order. 'dnd' (transactional) with our own sender ID goes first: it is
    the route Termii recommends for OTPs and the only one that reaches numbers
    on Nigeria's DND list at any hour. It needs TWO things on the Termii side:
    the DND route activated on the workspace (Termii support) and the sender ID
    approved. 'generic' with the same sender ID is next: it needs only the
    sender ID, so it starts working the moment that ID is approved — but Termii
    says it does not reach DND numbers and that MTN holds it back between 8pm
    and 8am, so it is a stopgap, not a substitute for the DND route. The
    'N-Alert' attempts only work on workspaces where Termii has registered that
    shared sender ID. The Number API is kept last, but it answers 404 on
    current Termii (it is no longer in their docs), so it cannot be relied on.

    Each fallback below only fires if the previous attempt did not succeed
    (rejected by Termii — bad sender, no channel access, etc. — or timed out),
    and the whole chain is capped by SMS_TOTAL_BUDGET_SECONDS.

    `diagnostics`: optional dict; when given it is filled with the per-attempt
    outcome (used by the gated /api/debug/test-sms endpoint).
    """
    api_key = current_app.config.get('TERMII_API_KEY', '').strip()
    if not api_key:
        logger.error("TERMII_API_KEY not set in Render environment")
        return False, None, "SMS service not configured"

    phone_intl = _to_international(phone)
    if not phone_intl:
        logger.error(f"Invalid/unrecognized phone number format (not logging raw value)")
        return False, None, "Invalid phone number format"

    # `or` (not just a default): an env var that exists but is empty would
    # otherwise give "" here and every dnd attempt would go out with no sender.
    custom_sender = (current_app.config.get('TERMII_SENDER_ID') or 'Cheap4uApp').strip() or 'Cheap4uApp'

    # Overall time budget across ALL attempts. Previously each of the four
    # attempts could wait up to 15s on its own, i.e. a Termii slowdown could
    # keep the user (and, with a single gunicorn worker, every other request)
    # waiting ~60s for an OTP that then "failed" anyway. Now the whole chain
    # is capped, and the user can simply tap Resend.
    deadline = time.monotonic() + SMS_TOTAL_BUDGET_SECONDS

    def _next_timeout():
        left = deadline - time.monotonic()
        if left < 3:
            return None
        return min(SMS_ATTEMPT_TIMEOUT_SECONDS, left)

    # Attempt order (see the docstring above for why). Duplicates are dropped,
    # e.g. when TERMII_SENDER_ID is itself "N-Alert".
    attempts, seen = [], set()

    def _add(label, key, run):
        if key not in seen:
            seen.add(key)
            attempts.append((label, run))

    _add(f"dnd/{custom_sender}", ("dnd", custom_sender),
         lambda to: _termii_attempt(api_key, phone_intl, message, channel="dnd", sender=custom_sender, timeout=to))
    _add(f"generic/{custom_sender}", ("generic", custom_sender),
         lambda to: _termii_attempt(api_key, phone_intl, message, channel="generic", sender=custom_sender, timeout=to))
    _add("dnd/N-Alert", ("dnd", "N-Alert"),
         lambda to: _termii_attempt(api_key, phone_intl, message, channel="dnd", sender="N-Alert", timeout=to))
    _add("generic/N-Alert", ("generic", "N-Alert"),
         lambda to: _termii_attempt(api_key, phone_intl, message, channel="generic", sender="N-Alert", timeout=to))
    _add("number", ("number", None),
         lambda to: _termii_number_send(api_key, phone_intl, message, timeout=to))

    errors = {}
    for label, run in attempts:
        to = _next_timeout()
        if to is None:
            errors[label] = "skipped (time budget used up)"
            continue
        result = run(to)
        if result["sent"]:
            if diagnostics is not None:
                diagnostics.update(errors)
                diagnostics["sent_via"] = label
            return True, result["message_id"], None
        errors[label] = result["error"]

    logger.error(f"All Termii send attempts failed → {phone_intl}. Errors: {errors}")
    all_errors = " | ".join(str(v) for v in errors.values())
    causes = []
    if "SENDER_ID_NOT_APPROVED" in all_errors or "not registered for workspace" in all_errors:
        causes.append(f"a sender ID is not approved on Termii (register/approve '{custom_sender}' in the Termii dashboard)")
    if "Route not configured" in all_errors:
        causes.append("the DND route is not activated on the Termii workspace (ask Termii support to activate it)")
    if causes:
        logger.error("OTP SMS cannot be delivered until the Termii account is set up: " + "; and ".join(causes) + ".")
    if diagnostics is not None:
        diagnostics.update(errors)
    return False, None, "Could not send SMS at this time"

    # NOTE (delivery confirmation): Termii's /api/sms/send response only
    # confirms the API *accepted* the request (captured above as `sent`); it
    # does not confirm the carrier delivered it. Termii supports delivery
    # receipts (DLR) via a webhook URL configured on the Termii dashboard —
    # doing that would let this backend distinguish "accepted" from
    # "confirmed delivered" with certainty. That webhook is NOT currently
    # configured for this account (I don't have dashboard access to verify
    # or set it up), so this function — like the rest of the OTP flow —
    # necessarily reports "accepted by Termii", and the user-facing message
    # in auth.py is worded to reflect that honestly rather than promising
    # delivery it can't confirm.

    # NOTE (account setup — no code can substitute for this): live logs from
    # this deployment show Termii answering "Route not configured ... route=DND"
    # on the dnd channel, "SENDER_ID_NOT_APPROVED" for the shared N-Alert ID, and
    # 404 on the Number API. Reliable OTP delivery therefore needs (1) the sender
    # ID approved and (2) the DND route activated by Termii support. The log line
    # emitted when every attempt fails names whichever of the two is still missing.
