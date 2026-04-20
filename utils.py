
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
    Send OTP SMS via Termii.
    Your account has GENERIC channel only (no DND).
    We use 'talert' as sender — it is Termii's own built-in sender
    that works on all accounts without any registration needed.
    """
    api_key = current_app.config.get('TERMII_API_KEY', '').strip()

    if not api_key:
        logger.error("❌ TERMII_API_KEY not set in Render environment")
        return False

    phone_intl = _to_international(phone)
    if not phone_intl:
        logger.error(f"❌ Invalid phone number: {phone}")
        return False

    url = "https://api.ng.termii.com/api/sms/send"
    headers = {"Content-Type": "application/json"}

    # Try senders in order — 'talert' is Termii's default that needs no registration
    senders = ["talert", "Termii"]

    for sender in senders:
        payload = {
            "api_key": api_key,
            "to": phone_intl,
            "from": sender,
            "sms": message,
            "type": "plain",
            "channel": "generic",  # Your account only has GENERIC channel
        }
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=15)
            try:
                data = r.json()
            except Exception:
                data = {}

            logger.info(f"Termii [{sender}/generic] → {phone_intl}: {r.status_code} {data}")

            if r.status_code == 200 and data.get("message") == "Successfully Sent":
                logger.info(f"✅ OTP SMS sent to {phone_intl} via {sender}")
                return True

            logger.warning(f"⚠️ Sender '{sender}' failed: {data.get('message')}")

        except Exception as e:
            logger.error(f"❌ Termii error with sender {sender}: {e}")

    logger.error(f"❌ All SMS attempts failed for {phone_intl}")
    return False
