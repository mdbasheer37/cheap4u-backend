# utils.py
import random
import string
import re
import requests
import logging
from datetime import datetime, timedelta
from flask import current_app

logger = logging.getLogger(__name__)


def generate_referral_code():
    """Generate a unique referral code."""
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
    return len(phone) == 11 and phone.isdigit()


def send_sms(phone, message):
    """
    Send a real SMS via the Termii API.

    Returns True on success, False on any failure.
    Errors are logged so they appear in Render logs.

    Key fixes vs original code:
    - Phone converted to international format (Termii rejects 0XXXXXXXXXX)
    - channel = "dnd"  (transactional/OTP route — generic is promotional only)
    - Content-Type header included (required by Termii)
    - Response checked by body message, not just HTTP status code
    - No mock OTP is ever returned — SMS must succeed for verification to proceed
    """
    api_key = current_app.config.get('TERMII_API_KEY', '').strip()
    sender_id = current_app.config.get('TERMII_SENDER_ID', 'Cheap4uApp').strip()

    if not api_key:
        logger.error(
            "❌ TERMII_API_KEY is not set in environment variables. "
            "Go to Render Dashboard → Your Service → Environment → Add TERMII_API_KEY"
        )
        return False

    phone_intl = _to_international(phone)
    if not phone_intl:
        logger.error(f"❌ Cannot convert phone number to international format: {phone}")
        return False

    url = "https://api.ng.termii.com/api/sms/send"
    payload = {
        "api_key": api_key,
        "to": phone_intl,
        "from": sender_id,
        "sms": message,
        "type": "plain",
        "channel": "dnd",   # MUST be "dnd" for OTP/transactional messages
    }
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)

        try:
            data = response.json()
        except Exception:
            data = {}

        logger.info(f"Termii response [{response.status_code}] to {phone_intl}: {data}")

        if response.status_code == 200 and data.get("message") == "Successfully Sent":
            logger.info(f"✅ OTP SMS sent to {phone_intl}")
            return True

        logger.error(
            f"❌ Termii SMS failed for {phone_intl}. "
            f"HTTP {response.status_code}. Body: {data}"
        )
        return False

    except requests.exceptions.Timeout:
        logger.error(f"❌ Termii request timed out for {phone_intl}")
        return False
    except requests.exceptions.ConnectionError as e:
        logger.error(f"❌ Termii connection error for {phone_intl}: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Unexpected error sending SMS to {phone_intl}: {e}")
        return False


def _to_international(phone):
    """
    Convert a Nigerian phone number to international format.
    '08012345678'  → '2348012345678'
    '2348012345678' → '2348012345678'  (already correct)
    Returns None if the number is invalid.
    """
    phone = str(phone).strip().replace(" ", "").replace("-", "")

    if phone.startswith("0") and len(phone) == 11 and phone.isdigit():
        return "234" + phone[1:]  # strip leading 0, prepend 234
    elif phone.startswith("234") and len(phone) == 13 and phone.isdigit():
        return phone  # already in international format
    elif phone.startswith("+234") and len(phone) == 14 and phone[1:].isdigit():
        return phone[1:]  # strip the + sign
    else:
        return None  # unrecognised format
