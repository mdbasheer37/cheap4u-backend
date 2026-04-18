# utils.py
import random
import string
import re
import requests
import logging
from flask import current_app

logger = logging.getLogger(__name__)


def generate_referral_code():
    """Generate a unique 8-character referral code."""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))


def generate_otp():
    """Generate a 6-digit OTP."""
    return ''.join(random.choices(string.digits, k=6))


def format_currency(amount):
    """Format amount as NGN currency."""
    return f"₦{amount:,.2f}"


def validate_email(email):
    """Validate email format."""
    pattern = r'^[\w\.-]+@[\w\.-]+\.\w+$'
    return re.match(pattern, email) is not None


def validate_phone(phone):
    """Validate Nigerian phone number — must be exactly 11 digits."""
    return len(str(phone)) == 11 and str(phone).isdigit()


def _to_international(phone):
    """
    Convert Nigerian phone number to Termii's required international format.
    '08012345678'    → '2348012345678'
    '2348012345678'  → '2348012345678'
    '+2348012345678' → '2348012345678'
    Returns None for unrecognised formats.
    """
    phone = str(phone).strip().replace(" ", "").replace("-", "")
    if phone.startswith("0") and len(phone) == 11 and phone.isdigit():
        return "234" + phone[1:]
    elif phone.startswith("234") and len(phone) == 13 and phone.isdigit():
        return phone
    elif phone.startswith("+234") and len(phone) == 14 and phone[1:].isdigit():
        return phone[1:]
    return None


def _try_send(api_key, phone_intl, sender_id, channel, message):
    """
    Make a single Termii SMS API call.
    Returns (True, data) on success, (False, data) on failure.
    """
    url = "https://api.ng.termii.com/api/sms/send"
    payload = {
        "api_key": api_key,
        "to": phone_intl,
        "from": sender_id,
        "sms": message,
        "type": "plain",
        "channel": channel,
    }
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        try:
            data = response.json()
        except Exception:
            data = {}
        logger.info(
            f"Termii [{channel}] sender={sender_id} → {phone_intl} "
            f"HTTP {response.status_code}: {data}"
        )
        if response.status_code == 200 and data.get("message") == "Successfully Sent":
            return True, data
        return False, data
    except requests.exceptions.Timeout:
        logger.error(f"Termii timeout [{channel}] → {phone_intl}")
        return False, {"error": "timeout"}
    except Exception as e:
        logger.error(f"Termii exception [{channel}] → {phone_intl}: {e}")
        return False, {"error": str(e)}


def send_sms(phone, message):
    """
    Send a real OTP SMS via Termii with automatic fallback strategy:

    Attempt 1: Custom sender ID (e.g. "Cheap4uApp") on DND channel
               — works if your Sender ID is approved for DND
    Attempt 2: "N-Alert" sender ID on DND channel
               — N-Alert is Termii's pre-approved universal transactional sender,
                 always works without any approval needed
    Attempt 3: "N-Alert" sender ID on generic channel
               — final fallback for non-DND numbers

    This means SMS will be delivered regardless of whether your custom
    Sender ID is approved or the number is on DND list.

    Returns True if any attempt succeeds, False if all fail.
    """
    api_key = current_app.config.get('TERMII_API_KEY', '').strip()
    custom_sender = current_app.config.get('TERMII_SENDER_ID', 'Cheap4uApp').strip()

    if not api_key:
        logger.error(
            "❌ TERMII_API_KEY is not set. "
            "Add it in Render Dashboard → Environment → TERMII_API_KEY"
        )
        return False

    phone_intl = _to_international(phone)
    if not phone_intl:
        logger.error(f"❌ Invalid phone number: {phone}")
        return False

    # Attempt 1: custom sender ID on DND route
    if custom_sender and custom_sender != 'N-Alert':
        ok, data = _try_send(api_key, phone_intl, custom_sender, "dnd", message)
        if ok:
            logger.info(f"✅ SMS sent via custom sender [{custom_sender}/dnd]")
            return True
        logger.warning(
            f"⚠️  Custom sender [{custom_sender}/dnd] failed: {data}. "
            f"Trying N-Alert/dnd fallback..."
        )

    # Attempt 2: N-Alert on DND route (always approved, works on all numbers)
    ok, data = _try_send(api_key, phone_intl, "N-Alert", "dnd", message)
    if ok:
        logger.info("✅ SMS sent via N-Alert/dnd")
        return True
    logger.warning(f"⚠️  N-Alert/dnd failed: {data}. Trying N-Alert/generic fallback...")

    # Attempt 3: N-Alert on generic route (last resort)
    ok, data = _try_send(api_key, phone_intl, "N-Alert", "generic", message)
    if ok:
        logger.info("✅ SMS sent via N-Alert/generic")
        return True

    logger.error(
        f"❌ All 3 SMS attempts failed for {phone_intl}. "
        f"Last error: {data}. "
        f"Check your TERMII_API_KEY balance and account status at accounts.termii.com"
    )
    return False
