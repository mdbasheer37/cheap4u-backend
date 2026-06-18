# ═══════════════════════════════════════════════════════════════════════
# ADMIN.PY ADDITION — Instant Paystack Transfer for Withdrawals
#
# This REPLACES the /profit/withdraw route to send money instantly
# via Paystack Transfer API instead of just creating a pending record.
#
# IMPORTANT REQUIREMENT:
# You must disable OTP for transfers in Paystack dashboard:
# Settings → Preferences → uncheck "Confirm transfers before sending"
# Without this, Paystack will require manual OTP approval every time.
# ═══════════════════════════════════════════════════════════════════════

import requests
import logging

logger = logging.getLogger(__name__)


def _paystack_call(method, path, secret, data=None):
    """Generic Paystack API caller for transfers."""
    headers = {
        'Authorization': f'Bearer {secret}',
        'Content-Type':  'application/json',
    }
    url = f'https://api.paystack.co{path}'
    try:
        if method == 'GET':
            r = requests.get(url, headers=headers, timeout=30)
        else:
            r = requests.post(url, json=data, headers=headers, timeout=30)
        result = r.json()
        return result.get('status', False), result
    except Exception as e:
        logger.error(f'Paystack transfer API error [{path}]: {e}')
        return False, {'message': str(e)}


def resolve_account_number(secret, account_number, bank_code):
    """Verify account number + bank code, returns account name or None."""
    ok, result = _paystack_call(
        'GET',
        f'/bank/resolve?account_number={account_number}&bank_code={bank_code}',
        secret,
    )
    if ok:
        return result['data']['account_name']
    return None


def get_bank_list(secret):
    """Fetch list of Nigerian banks with their codes."""
    ok, result = _paystack_call('GET', '/bank?country=nigeria&perPage=100', secret)
    if ok:
        return result['data']
    return []


def create_transfer_recipient(secret, account_number, bank_code, account_name):
    """Create a transfer recipient. Returns recipient_code or None."""
    ok, result = _paystack_call('POST', '/transferrecipient', secret, {
        'type':           'nuban',
        'name':           account_name,
        'account_number': account_number,
        'bank_code':      bank_code,
        'currency':       'NGN',
    })
    if ok:
        return result['data']['recipient_code']
    logger.error(f'Recipient creation failed: {result.get("message")}')
    return None


def initiate_transfer(secret, recipient_code, amount, reason, reference):
    """
    Send money via Paystack. Amount in Naira (will be converted to kobo).
    Returns (success, transfer_data_or_error_message).
    """
    ok, result = _paystack_call('POST', '/transfer', secret, {
        'source':    'balance',
        'reason':    reason,
        'amount':    int(amount * 100),
        'recipient': recipient_code,
        'reference': reference,
    })
    if ok:
        return True, result['data']
    return False, result.get('message', 'Transfer failed')


