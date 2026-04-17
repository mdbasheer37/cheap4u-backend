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
    Send SMS via Termii API.

    Fixes applied vs original:
    1. Phone number is converted to international format (234XXXXXXXXXX)
       — Termii rejects local 0XXXXXXXXXX format.
    2. channel changed from "generic" to "dnd" for OTP/transactional messages.
       Generic channel is promotional only and will silently fail for OTPs
       on many numbers, especially DND-registered numbers.
    3. Missing Content-Type header added — without it Termii returns a 400.
    4. Response check fixed: Termii returns {"message": "Successfully Sent"},
       not {"status": true}. Original code checked response.status_code == 200
       but never checked the body — so SMS failures were silently ignored.
    5. Proper logging added so failures appear in Render logs.
    """
    api_key = current_app.config.get('TERMII_API_KEY', '')
    sender_id = current_app.config.get('TERMII_SENDER_ID', 'Cheap4uApp')

    # No API key → dev/test mode: log OTP and return False so mock_otp is shown
    if not api_key:
        logger.warning(f"⚠️  No TERMII_API_KEY set. SMS not sent to {phone}. Message: {message}")
        return False

    # Fix 1: Convert Nigerian local number to international format
    # Termii requires "2348012345678", not "08012345678"
    phone_intl = _to_international(phone)
    if not phone_intl:
        logger.error(f"❌ Invalid phone number format: {phone}")
        return False

    url = "https://api.ng.termii.com/api/sms/send"

    payload = {
        "api_key": api_key,
        "to": phone_intl,
        "from": sender_id,
        "sms": message,
        "type": "plain",
        # Fix 2: Use "dnd" channel for transactional/OTP messages.
        # "generic" is promotional only and is blocked on DND numbers.
        "channel": "dnd",
    }

    # Fix 3: Add Content-Type header — required by Termii
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)

        # Fix 4: Check the actual response body, not just status code
        try:
            data = response.json()
        except Exception:
            data = {}

        logger.info(f"Termii response [{response.status_code}]: {data}")

        if response.status_code == 200 and data.get("message") == "Successfully Sent":
            logger.info(f"✅ SMS sent successfully to {phone_intl}")
            return True
        else:
            # Log the exact error so you can see it in Render logs
            logger.error(
                f"❌ Termii SMS failed. Status: {response.status_code}. "
                f"Response: {data}. Phone: {phone_intl}"
            )
            return False

    except requests.exceptions.Timeout:
        logger.error(f"❌ Termii request timed out for {phone_intl}")
        return False
    except requests.exceptions.ConnectionError as e:
        logger.error(f"❌ Termii connection error for {phone_intl}: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ SMS sending failed for {phone_intl}: {e}")
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
