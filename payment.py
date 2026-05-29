# payment.py — Full live Paystack integration (DVA + card + webhook)
import hmac
import hashlib
import requests
import logging
from flask import Blueprint, request, jsonify, current_app
from datetime import datetime
from models import db, User, Transaction, ReferralTransaction
from flask_jwt_extended import jwt_required, get_jwt_identity

payment_bp = Blueprint('payment', __name__, url_prefix='/api/payment')
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# INTERNAL: Paystack API wrapper
# ─────────────────────────────────────────
def _paystack(method, path, data=None):
    secret = current_app.config.get('PAYSTACK_SECRET_KEY', '')
    headers = {
        'Authorization': f'Bearer {secret}',
        'Content-Type': 'application/json',
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


# ─────────────────────────────────────────
# INTERNAL: Create Paystack customer
# ─────────────────────────────────────────
def create_paystack_customer(email, first_name, last_name, phone):
    """Called during registration. Returns customer_code."""
    ok, result = _paystack('POST', '/customer', {
        'email':      email,
        'first_name': first_name,
        'last_name':  last_name,
        'phone':      phone,
    })
    if ok:
        return result['data']['customer_code']
    raise Exception(f"Paystack customer creation failed: {result.get('message')}")


# ─────────────────────────────────────────
# INTERNAL: Create DVA
# ─────────────────────────────────────────
def create_dedicated_virtual_account(customer_code):
    """
    Creates a Dedicated Virtual Account for a customer.
    Tries Wema Bank first, then Titan Trust.
    Returns dict with account_number/bank_name/account_name, or None if unavailable.
    """
    for bank in ('wema-bank', 'titan-paystack'):
        ok, result = _paystack('POST', '/dedicated_account', {
            'customer':       customer_code,
            'preferred_bank': bank,
        })
        if ok:
            acct = result['data']
            return {
                'account_number': acct['account_number'],
                'bank_name':      acct['bank']['name'],
                'account_name':   acct['account_name'],
            }
        msg = result.get('message', '').lower()
        logger.warning(f'DVA [{bank}] failed: {result.get("message")}')
        # Not available on this account — no point retrying other banks
        if any(x in msg for x in ('live', 'not enabled', 'not available',
                                   'upgrade', 'feature', 'contact')):
            return None
    return None


# ─────────────────────────────────────────
# INTERNAL: Credit wallet (used by verify + webhook)
# ─────────────────────────────────────────
def _credit_wallet(txn, amount, channel='unknown', user=None):
    if user is None:
        user = User.query.get(txn.user_id)
    if not user:
        logger.error(f'_credit_wallet: user {txn.user_id} not found')
        return None

    user.wallet_balance = round(user.wallet_balance + amount, 2)
    txn.status  = 'success'
    txn.amount  = amount
    txn.details = {**(txn.details or {}), 'channel': channel}

    # One-time ₦10 signup referral bonus on first ever wallet funding
    if not user.referral_bonus_claimed and user.referred_by_user_id:
        referrer = User.query.get(user.referred_by_user_id)
        if referrer:
            referrer.referral_earnings = round(referrer.referral_earnings + 10.0, 2)
            user.referral_bonus_claimed = True
            db.session.add(ReferralTransaction(
                referrer_id      = referrer.id,
                referred_user_id = user.id,
                amount           = 10.0,
                type             = 'signup_bonus',
            ))
            logger.info(f'Referral bonus ₦10 awarded to user {referrer.id}')

    db.session.commit()
    return user


# ─────────────────────────────────────────
# ROUTE: GET /api/payment/account-details
# ─────────────────────────────────────────
@payment_bp.route('/account-details', methods=['GET'])
@jwt_required()
def get_account_details():
    """
    Returns the user's virtual account details + wallet balance.
    If DVA doesn't exist yet, tries to create it automatically.
    """
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    # Auto-create DVA if missing (works once account is live)
    if not user.virtual_account_number and user.paystack_customer_code:
        try:
            dva = create_dedicated_virtual_account(user.paystack_customer_code)
            if dva:
                user.virtual_account_number = dva['account_number']
                user.virtual_bank_name      = dva['bank_name']
                user.virtual_account_name   = dva['account_name']
                db.session.commit()
                logger.info(f'DVA auto-created for user {user.id}: {dva["account_number"]}')
        except Exception as e:
            logger.warning(f'DVA auto-create failed for user {user.id}: {e}')

    has_dva = bool(user.virtual_account_number)

    return jsonify({
        'status': 'success',
        'data': {
            'wallet_balance':         round(user.wallet_balance, 2),
            'has_virtual_account':    has_dva,
            'account_number':         user.virtual_account_number or None,
            'bank_name':              user.virtual_bank_name or None,
            'account_name':           user.virtual_account_name or None,
            'card_funding_available': True,
            'funding_message': (
                f'Transfer to {user.virtual_bank_name} — {user.virtual_account_number} to fund your wallet instantly.'
                if has_dva else
                'Fund your wallet via card payment below.'
            ),
        },
    })


# ─────────────────────────────────────────
# ROUTE: POST /api/payment/initialize
# ─────────────────────────────────────────
@payment_bp.route('/initialize', methods=['POST'])
@jwt_required()
def initialize_payment():
    """Initialize card/USSD wallet funding via Paystack."""
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404
    if not user.is_active:
        return jsonify({'status': 'error', 'message': 'Account is blocked'}), 403

    data = request.get_json() or {}
    try:
        amount = float(data.get('amount', 0))
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'Invalid amount'}), 400

    if amount < 100:
        return jsonify({'status': 'error', 'message': 'Minimum funding amount is ₦100'}), 400
    if amount > 1_000_000:
        return jsonify({'status': 'error', 'message': 'Maximum funding amount is ₦1,000,000'}), 400

    backend_url = current_app.config.get('BACKEND_URL', '')
    ok, result = _paystack('POST', '/transaction/initialize', {
        'email':        user.email,
        'amount':       int(amount * 100),          # Naira → kobo
        'currency':     'NGN',
        'callback_url': f'{backend_url}/api/payment/callback',
        'metadata': {
            'user_id':      user.id,
            'service_type': 'wallet_funding',
            'custom_fields': [{
                'display_name': 'Customer Name',
                'variable_name': 'customer_name',
                'value': user.name,
            }],
        },
    })

    if not ok:
        logger.error(f'Payment init failed for user {user.id}: {result.get("message")}')
        return jsonify({'status': 'error',
                        'message': result.get('message', 'Payment initialization failed')}), 400

    ref = result['data']['reference']

    # Record pending transaction
    txn = Transaction(
        user_id      = user.id,
        reference    = ref,
        type         = 'wallet_funding',
        service_type = 'card_payment',
        amount       = amount,
        status       = 'pending',
        details      = {'channel': 'card', 'initiated_at': datetime.utcnow().isoformat()},
    )
    db.session.add(txn)
    db.session.commit()

    return jsonify({
        'status': 'success',
        'message': 'Payment initialized',
        'data': {
            'authorization_url': result['data']['authorization_url'],
            'reference':         ref,
            'amount':            amount,
        },
    })


# ─────────────────────────────────────────
# ROUTE: GET /api/payment/verify/<reference>
# ─────────────────────────────────────────
@payment_bp.route('/verify/<reference>', methods=['GET'])
@jwt_required()
def verify_payment(reference):
    """Verify a card payment and credit wallet."""
    ok, result = _paystack('GET', f'/transaction/verify/{reference}')
    if not ok:
        return jsonify({'status': 'error',
                        'message': result.get('message', 'Verification failed')}), 400

    pay_data = result['data']

    # Find transaction
    txn = Transaction.query.filter_by(reference=reference).first()
    if not txn:
        # Create if webhook beat us to it
        user = User.query.filter_by(email=pay_data.get('customer', {}).get('email', '').lower()).first()
        if not user:
            return jsonify({'status': 'error', 'message': 'Transaction not found'}), 404
        txn = Transaction(
            user_id      = user.id,
            reference    = reference,
            type         = 'wallet_funding',
            service_type = 'card_payment',
            amount       = pay_data.get('amount', 0) / 100,
            status       = 'pending',
            details      = {},
        )
        db.session.add(txn)
        db.session.flush()

    # Idempotency
    if txn.status == 'success':
        user = User.query.get(txn.user_id)
        return jsonify({
            'status':  'success',
            'message': 'Payment already credited',
            'data': {
                'amount':         txn.amount,
                'reference':      reference,
                'wallet_balance': round(user.wallet_balance, 2) if user else 0,
            },
        })

    if pay_data.get('status') == 'success':
        amount = pay_data['amount'] / 100
        user   = _credit_wallet(txn, amount, channel='card')
        return jsonify({
            'status':  'success',
            'message': f'Wallet funded with ₦{amount:,.2f}',
            'data': {
                'amount':         amount,
                'reference':      reference,
                'wallet_balance': round(user.wallet_balance, 2) if user else 0,
            },
        })

    # Payment not completed
    txn.status = 'failed'
    db.session.commit()
    return jsonify({
        'status':  'error',
        'message': f'Payment status: {pay_data.get("status", "unknown")}',
    }), 400


# ─────────────────────────────────────────
# ROUTE: GET /api/payment/callback
# Paystack redirects here after card payment
# ─────────────────────────────────────────
@payment_bp.route('/callback', methods=['GET'])
def payment_callback():
    """
    Paystack redirects the user here after card payment.
    We verify the payment and return a simple success/failure page.
    The webhook handles the actual wallet credit — this is just UX.
    """
    reference = request.args.get('trxref') or request.args.get('reference')
    if not reference:
        return '''<html><body style="font-family:sans-serif;text-align:center;padding:50px">
            <h2>❌ Payment reference missing</h2>
            <p>Please go back to the app and check your transaction history.</p>
        </body></html>'''

    ok, result = _paystack('GET', f'/transaction/verify/{reference}')
    if ok and result.get('data', {}).get('status') == 'success':
        amount = result['data']['amount'] / 100
        # Credit wallet if not already done by webhook
        txn = Transaction.query.filter_by(reference=reference).first()
        if txn and txn.status != 'success':
            _credit_wallet(txn, amount, channel='card')
        return f'''<html><body style="font-family:sans-serif;text-align:center;padding:50px;background:#f0fdf4">
            <h2 style="color:#16a34a">✅ Payment Successful!</h2>
            <p>₦{amount:,.2f} has been added to your Cheap4u wallet.</p>
            <p>You can close this page and return to the app.</p>
        </body></html>'''

    return '''<html><body style="font-family:sans-serif;text-align:center;padding:50px;background:#fef2f2">
        <h2 style="color:#dc2626">❌ Payment Not Completed</h2>
        <p>Your wallet was not funded. Please try again from the app.</p>
    </body></html>'''


# ─────────────────────────────────────────
# ROUTE: POST /api/payment/webhook
# ─────────────────────────────────────────
@payment_bp.route('/webhook', methods=['POST'])
def paystack_webhook():
    """
    Paystack webhook endpoint.
    Set this URL in Paystack Dashboard → Settings → API & Webhooks:
    https://cheap4u-backend.onrender.com/api/payment/webhook
    """
    secret = current_app.config.get('PAYSTACK_SECRET_KEY', '')
    sig    = request.headers.get('x-paystack-signature', '')
    body   = request.get_data()

    # Verify HMAC signature
    expected = hmac.new(secret.encode('utf-8'), body, hashlib.sha512).hexdigest()
    if not hmac.compare_digest(sig, expected):
        logger.warning('Webhook rejected: invalid signature')
        return jsonify({'status': 'error', 'message': 'Invalid signature'}), 401

    try:
        event = request.get_json(force=True)
    except Exception:
        return jsonify({'status': 'error', 'message': 'Bad JSON'}), 400

    event_type = event.get('event', '')
    logger.info(f'Paystack webhook: {event_type}')

    if event_type == 'charge.success':
        try:
            _handle_charge_success(event.get('data', {}))
        except Exception as e:
            logger.error(f'Webhook handler error: {e}')
            # Still return 200 so Paystack doesn't keep retrying
            return jsonify({'status': 'success'}), 200

    # Always return 200 to Paystack
    return jsonify({'status': 'success'}), 200


def _handle_charge_success(pay_data):
    """Process a successful charge from webhook."""
    reference    = pay_data.get('reference', '')
    amount       = pay_data.get('amount', 0) / 100       # kobo → naira
    channel      = pay_data.get('channel', 'unknown')
    customer     = pay_data.get('customer', {})
    cust_email   = customer.get('email', '').lower()
    cust_code    = customer.get('customer_code', '')

    if not reference or amount <= 0:
        logger.warning(f'Webhook: bad data ref={reference} amount={amount}')
        return

    # Idempotency — skip if already processed
    existing = Transaction.query.filter_by(reference=reference).first()
    if existing and existing.status == 'success':
        logger.info(f'Webhook: {reference} already credited, skipping')
        return

    # Find user — customer_code is more reliable for DVA transfers
    user = None
    if cust_code:
        user = User.query.filter_by(paystack_customer_code=cust_code).first()
    if not user and cust_email:
        user = User.query.filter_by(email=cust_email).first()
    if not user:
        logger.error(f'Webhook: user not found for email={cust_email} code={cust_code}')
        return

    # Map Paystack channel to service_type
    service_type_map = {
        'dedicated_nuban': 'bank_transfer',
        'card':            'card_payment',
        'bank':            'bank_payment',
        'ussd':            'ussd_payment',
        'qr':              'qr_payment',
        'bank_transfer':   'bank_transfer',
    }
    service_type = service_type_map.get(channel, channel)

    # Use existing transaction or create new one
    txn = existing or Transaction(
        user_id      = user.id,
        reference    = reference,
        type         = 'wallet_funding',
        service_type = service_type,
        amount       = amount,
        status       = 'pending',
        details      = {},
    )
    if not existing:
        db.session.add(txn)
        db.session.flush()

    _credit_wallet(txn, amount, channel=channel, user=user)
    logger.info(f'✅ Webhook: credited ₦{amount:,.2f} to {user.email} via {channel}')


# ─────────────────────────────────────────
# ROUTE: GET /api/payment/transactions
# ─────────────────────────────────────────
@payment_bp.route('/transactions', methods=['GET'])
@jwt_required()
def get_transactions():
    """Return paginated transaction history for the logged-in user."""
    user_id  = get_jwt_identity()
    page     = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 20, type=int), 100)

    txns = (
        Transaction.query
        .filter_by(user_id=user_id)
        .order_by(Transaction.created_at.desc())
        .limit(per_page).offset((page - 1) * per_page)
        .all()
    )
    total = Transaction.query.filter_by(user_id=user_id).count()

    return jsonify({
        'status': 'success',
        'data': {
            'transactions': [t.to_dict() for t in txns],
            'total':        total,
            'page':         page,
            'pages':        (total + per_page - 1) // per_page,
            'has_next':     (page * per_page) < total,
        },
    })
