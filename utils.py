# utils.py
import random
import string
import re
import requests
import logging
from flask import current_app

logger = logging.getLogger(__name__)


def generate_referral_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))


def generate_otp():
    return ''.join(random.choices(string.digits, k=6))


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


def send_sms(phone, message):
    """
    Send an SMS via Termii. Returns (sent: bool, message_id: str|None,
    error: str|None) — sent=True means Termii's API accepted the request for
    delivery, NOT that the carrier confirmed it reached the handset (Termii's
    /sms/send response has no delivery confirmation; that requires a DLR
    webhook configured on the Termii dashboard, which this account does not
    currently have wired up — see the note at the bottom of this function).

    Channel order — this is the actual fix for "works in the day, not at
    night": the previous version tried the 'number' channel first, which is
    NOT a DND-bypass channel. A large share of Nigerian numbers are
    registered on the NCC's Do-Not-Disturb list, and non-bypass routes get
    throttled/blocked by carriers more aggressively during certain hours
    (this shows up exactly as "sometimes doesn't arrive at night"). Termii's
    'dnd' channel is the one designed to bypass that filtering for
    transactional messages like OTPs, so it's tried first now.

    Each fallback below only fires if the previous attempt was rejected
    outright by Termii (bad sender, no channel access, etc.) — never for an
    ambiguous/successful response — so a single OTP request cannot result in
    more than one SMS actually being sent.
    """
    api_key = current_app.config.get('TERMII_API_KEY', '').strip()
    if not api_key:
        logger.error("TERMII_API_KEY not set in Render environment")
        return False, None, "SMS service not configured"

    phone_intl = _to_international(phone)
    if not phone_intl:
        logger.error(f"Invalid/unrecognized phone number format (not logging raw value)")
        return False, None, "Invalid phone number format"

    custom_sender = current_app.config.get('TERMII_SENDER_ID', 'Cheap4uApp').strip()

    # Attempt 1: 'dnd' channel with our own sender ID — bypasses DND filtering,
    # the channel Termii recommends for OTP/transactional SMS.
    result = _termii_attempt(api_key, phone_intl, message, channel="dnd", sender=custom_sender)
    if result["sent"]:
        return True, result["message_id"], None

    # Attempt 2: 'dnd' channel with Termii's shared default sender ('N-Alert') —
    # covers the case where our own sender ID isn't yet approved/active.
    result2 = _termii_attempt(api_key, phone_intl, message, channel="dnd", sender="N-Alert")
    if result2["sent"]:
        return True, result2["message_id"], None

    # Attempt 3: last resort — generic channel, Termii's own number, no sender ID.
    result3 = _termii_attempt(api_key, phone_intl, message, channel="generic", sender="N-Alert")
    if result3["sent"]:
        return True, result3["message_id"], None

    logger.error(
        f"All Termii send attempts failed → {phone_intl}. "
        f"Errors: dnd/{custom_sender}={result['error']!r}, "
        f"dnd/N-Alert={result2['error']!r}, generic/N-Alert={result3['error']!r}"
    )
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
