# debug_routes.py
# TEMPORARY diagnostic file — delete after SMS is working
from flask import Blueprint, request, jsonify, current_app
import requests

debug_bp = Blueprint('debug', __name__, url_prefix='/api/debug')


@debug_bp.route('/test-sms', methods=['POST'])
def test_sms():
    """
    Test Termii SMS directly and return the full raw response.
    POST /api/debug/test-sms
    Body: { "phone": "08012345678" }

    DELETE THIS ENDPOINT once SMS is confirmed working.
    """
    data = request.get_json() or {}
    phone_raw = data.get('phone', '')

    api_key = current_app.config.get('TERMII_API_KEY', '').strip()
    sender_id = current_app.config.get('TERMII_SENDER_ID', 'Cheap4uApp').strip()

    # --- Diagnostics ---
    diag = {
        'api_key_set': bool(api_key),
        'api_key_length': len(api_key),
        'api_key_preview': api_key[:6] + '...' if api_key else 'NOT SET',
        'sender_id': sender_id,
        'phone_raw': phone_raw,
        'attempts': []
    }

    if not api_key:
        diag['error'] = 'TERMII_API_KEY is not set in Render environment variables'
        return jsonify(diag), 500

    # Convert phone
    phone_intl = _to_international(phone_raw)
    diag['phone_international'] = phone_intl

    if not phone_intl:
        diag['error'] = f'Cannot convert "{phone_raw}" to international format'
        return jsonify(diag), 400

    # Try all 3 combinations and record full Termii response for each
    test_cases = [
        (sender_id, 'dnd'),
        ('N-Alert', 'dnd'),
        ('N-Alert', 'generic'),
    ]

    for sender, channel in test_cases:
        attempt = {'sender': sender, 'channel': channel}
        try:
            resp = requests.post(
                'https://api.ng.termii.com/api/sms/send',
                json={
                    'api_key': api_key,
                    'to': phone_intl,
                    'from': sender,
                    'sms': 'Cheap4u test OTP: 123456. Do not share.',
                    'type': 'plain',
                    'channel': channel,
                },
                headers={'Content-Type': 'application/json'},
                timeout=15
            )
            attempt['http_status'] = resp.status_code
            try:
                attempt['termii_response'] = resp.json()
            except Exception:
                attempt['termii_response'] = resp.text

            attempt['success'] = (
                resp.status_code == 200 and
                resp.json().get('message') == 'Successfully Sent'
            )
        except Exception as e:
            attempt['error'] = str(e)
            attempt['success'] = False

        diag['attempts'].append(attempt)

        # Stop on first success
        if attempt.get('success'):
            diag['result'] = f'SMS SENT via sender={sender} channel={channel}'
            return jsonify(diag), 200

    diag['result'] = 'ALL ATTEMPTS FAILED — see attempts[] above for exact Termii errors'
    return jsonify(diag), 500


def _to_international(phone):
    phone = str(phone).strip().replace(' ', '').replace('-', '')
    if phone.startswith('0') and len(phone) == 11 and phone.isdigit():
        return '234' + phone[1:]
    elif phone.startswith('234') and len(phone) == 13 and phone.isdigit():
        return phone
    elif phone.startswith('+234') and len(phone) == 14 and phone[1:].isdigit():
        return phone[1:]
    return None


@debug_bp.route('/check-config', methods=['GET'])
def check_config():
    """
    GET /api/debug/check-config
    Shows all SMS-related config values (keys masked).
    """
    api_key = current_app.config.get('TERMII_API_KEY', '').strip()
    return jsonify({
        'TERMII_API_KEY': (api_key[:6] + '...' + api_key[-4:]) if len(api_key) > 10 else ('SET but short: ' + api_key if api_key else 'NOT SET'),
        'TERMII_SENDER_ID': current_app.config.get('TERMII_SENDER_ID', 'NOT SET'),
        'DEBUG': current_app.config.get('DEBUG'),
        'BACKEND_URL': current_app.config.get('BACKEND_URL'),
    })
