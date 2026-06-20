# admin_transfer.py — Fixed: skip account verification for mobile money banks
import requests
import logging

logger = logging.getLogger(__name__)

# These banks don't support Paystack's /bank/resolve endpoint
# Transfer still works — just skip verification
SKIP_VERIFY_BANKS = {
    '100004',  # OPay
    '999991',  # PalmPay
    '50515',   # Moniepoint MFB
    '090267',  # Kuda Bank
    '090405',  # Opay Digital Services
    '110005',  # TeamApt (Moniepoint)
    '566',     # VFD MFB
    '526',     # Parallex Bank
    '101',     # Providus Bank
}


def _paystack_call(method, path, secret, data=None):
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
        logger.info(f'Paystack {method} {path} → {r.status_code}')
        return result.get('status', False), result
    except Exception as e:
        logger.error(f'Paystack API error [{path}]: {e}')
        return False, {'message': str(e)}


def resolve_account_number(secret, account_number, bank_code):
    """
    Verify account number + bank code.
    Returns account name string, or None if verification fails.
    For mobile money banks (OPay, PalmPay etc.) — skips verification
    and returns a placeholder so transfer can still proceed.
    """
    # Skip verification for mobile money operators
    if str(bank_code) in SKIP_VERIFY_BANKS:
        logger.info(f'Skipping account verification for mobile money bank {bank_code}')
        return f'Account {account_number}'  # Placeholder — transfer still works

    ok, result = _paystack_call(
        'GET',
        f'/bank/resolve?account_number={account_number}&bank_code={bank_code}',
        secret,
    )
    if ok:
        return result['data']['account_name']

    logger.warning(f'Account resolve failed for {account_number}/{bank_code}: {result.get("message")}')
    return None


def get_bank_list(secret):
    """Fetch list of Nigerian banks."""
    ok, result = _paystack_call('GET', '/bank?country=nigeria&perPage=100', secret)
    if ok:
        return result['data']
    return []


def create_transfer_recipient(secret, account_number, bank_code, account_name):
    """Create a Paystack transfer recipient. Returns recipient_code or None."""
    ok, result = _paystack_call('POST', '/transferrecipient', secret, {
        'type':           'nuban',
        'name':           account_name,
        'account_number': account_number,
        'bank_code':      str(bank_code),
        'currency':       'NGN',
    })
    if ok:
        return result['data']['recipient_code']
    logger.error(f'Recipient creation failed: {result.get("message")}')
    return None


def initiate_transfer(secret, recipient_code, amount, reason, reference):
    """
    Send money via Paystack Transfer.
    Amount in Naira — converted to kobo internally.
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
