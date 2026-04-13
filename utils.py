import random
import string
from datetime import datetime, timedelta
import requests
from flask import current_app

def generate_referral_code():
    """Generate a unique referral code"""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

def generate_otp():
    """Generate a 6-digit OTP"""
    return ''.join(random.choices(string.digits, k=6))

def send_sms(phone, message):
    """Send SMS via Termii API"""
    api_key = current_app.config['TERMII_API_KEY']
    sender_id = current_app.config['TERMII_SENDER_ID']
    
    if not api_key or api_key == '':
        print(f"⚠️ SMS would be sent to {phone}: {message}")
        return True
    
    url = "https://api.ng.termii.com/api/sms/send"
    payload = {
        "to": phone,
        "from": sender_id,
        "sms": message,
        "type": "plain",
        "channel": "generic",
        "api_key": api_key
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ SMS sending failed: {e}")
        return False

def format_currency(amount):
    """Format amount as NGN currency"""
    return f"₦{amount:,.2f}"

def validate_email(email):
    """Validate email format"""
    import re
    pattern = r'^[\w\.-]+@[\w\.-]+\.\w+$'
    return re.match(pattern, email) is not None

def validate_phone(phone):
    """Validate Nigerian phone number"""
    return len(phone) == 11 and phone.isdigit()
