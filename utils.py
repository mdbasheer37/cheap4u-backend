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


def validate_phone(phone):
    return len(str(phone)) == 11 and str(phone).isdigit()


def _to_international(phone):
    phone = str(phone).strip().replace(" ", "").replace("-", "")
    if phone.startswith("0") and len(phone) == 11 and phone.isdigit():
        return "234" + phone[1:]
    elif phone.startswith("234") and len(phone) == 13 and phone.isdigit():
        return phone
    elif phone.startswith("+234") and len(phone) == 14 and phone[1:].isdigit():
        return phone[1:]
    return None


def send_sms(phone, message):
    """
    Send OTP SMS via Termii using the 'number' channel.

    The 'number' channel sends from a Termii-owned number (not a Sender ID),
    so it:
    - Works 24/7 with NO time restrictions
    - Does NOT need a registered Sender ID
    - Works on all networks (MTN, Airtel, Glo, 9mobile)
    - Works with generic accounts (no DND approval needed)

    Falls back to generic channel with 'talert' if number channel fails.
    """
    api_key = current_app.config.get('TERMII_API_KEY', '').strip()

    if not api_key:
        logger.error("❌ TERMII_API_KEY not set in Render environment")
        return False

    phone_intl = _to_international(phone)
    if not phone_intl:
        logger.error(f"❌ Invalid phone number: {phone}")
        return False

    headers = {"Content-Type": "application/json"}

    # Attempt 1: 'number' channel — uses Termii's own number, no sender ID needed,
    # works 24/7 without any time restriction
    try:
        payload = {
            "api_key": api_key,
            "to": phone_intl,
            "sms": message,
            "type": "plain",
            "channel": "number",
        }
        r = requests.post(
            "https://api.ng.termii.com/api/sms/send",
            json=payload,
            headers=headers,
            timeout=15
        )
        try:
            data = r.json()
        except Exception:
            data = {}
        logger.info(f"Termii [number] → {phone_intl}: {r.status_code} {data}")

        if r.status_code == 200 and data.get("message") == "Successfully Sent":
            logger.info(f"✅ OTP SMS sent via number channel to {phone_intl}")
            return True

        logger.warning(f"number channel failed: {data.get('message')} — trying generic")
    except Exception as e:
        logger.error(f"number channel exception: {e}")

    # Attempt 2: generic channel with 'talert'
    try:
        payload = {
            "api_key": api_key,
            "to": phone_intl,
            "from": "talert",
            "sms": message,
            "type": "plain",
            "channel": "generic",
        }
        r = requests.post(
            "https://api.ng.termii.com/api/sms/send",
            json=payload,
            headers=headers,
            timeout=15
        )
        try:
            data = r.json()
        except Exception:
            data = {}
        logger.info(f"Termii [talert/generic] → {phone_intl}: {r.status_code} {data}")

        if r.status_code == 200 and data.get("message") == "Successfully Sent":
            logger.info(f"✅ OTP SMS sent via talert/generic to {phone_intl}")
            return True

        logger.error(f"talert/generic failed: {data.get('message')}")
    except Exception as e:
        logger.error(f"talert/generic exception: {e}")

    # Attempt 3: generic channel with custom sender
    custom_sender = current_app.config.get('TERMII_SENDER_ID', 'Cheap4uApp').strip()
    try:
        payload = {
            "api_key": api_key,
            "to": phone_intl,
            "from": custom_sender,
            "sms": message,
            "type": "plain",
            "channel": "generic",
        }
        r = requests.post(
            "https://api.ng.termii.com/api/sms/send",
            json=payload,
            headers=headers,
            timeout=15
        )
        try:
            data = r.json()
        except Exception:
            data = {}
        logger.info(f"Termii [{custom_sender}/generic] → {phone_intl}: {r.status_code} {data}")

        if r.status_code == 200 and data.get("message") == "Successfully Sent":
            logger.info(f"✅ OTP SMS sent via {custom_sender}/generic to {phone_intl}")
            return True

        logger.error(f"All 3 SMS attempts failed for {phone_intl}. Last error: {data.get('message')}")
    except Exception as e:
        logger.error(f"{custom_sender}/generic exception: {e}")

    return False
