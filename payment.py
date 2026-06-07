# payment.py — Full live Paystack integration with debug endpoint
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


def _paystack(method, path, data=None):
    """Make a Paystack API call. Returns (ok, response_dict)."""
    secret = current_app.config.get('PAYSTACK_SECRET_KEY', '').strip()
    if not secret:
        logger.error('PAYSTACK_SECRET_KEY is not set!')
        return False, {'message': 'Paystack key not configured'}

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
        logger.info(f'Paystack {method} {path} → {r.status_code}: {result}')
        return result.get('status', False), result
    except Exception as e:
        logger.error(f'Paystack API error [{path}]: {e}')
        return False, {'message': str(e)}


def create_paystack_customer(email, first_name, last_name, phone):
    """Create Paystack customer. Returns customer_code or raises."""
    ok, result = _paystack('POST', '/customer', {
        'email':      email,
        'first_name': first_name,
        'last_name':  last_name,
        'phone':      phone,
    })
    if ok:
        return result['data']['customer_code']
    raise Exception(f"Paystack customer creation failed: {result.get('message')}")


def create_dedicated_virtual_account(customer_code):
    """
    Create DVA for customer.
    Returns dict with account details, or None if unavailable.
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
        if any(x in msg for x in (
            'live', 'not enabled', 'not available',
            'upgrade', 'feature', 'contact', 'invalid key'
        )):
            return None
    return None


# ── Debug: test Paystack key ──────────────────────────────────────────
@payment_bp.route('/test-key', methods=['GET'])
def test_paystack_key():
    """
    GET /api/payment/test-key
    Tests if Paystack secret key is valid by calling /balance.
    """
    secret = current_app.config.get('PAYSTACK_SECRET_KEY', '').strip()
    if not secret:
        return jsonify({
            'key_set':    False,
            'key_works':  False,
            'error':      'PAYSTACK_SECRET_KEY not set in Render environment',
        }), 500

    try:
        r = requests.get(
            'https://api.paystack.co/balance',
            headers={'Authorization': f'Bearer {secret}'},
            timeout=15,
        )
        body = r.json()
        key_works = r.status_code == 200 and body.get('status') is True

        return jsonify({
            'key_set':      True,
            'key_length':   len(secret),
            'key_preview':  secret[:12] + '...' + secret[-4:],
            'key_works':    key_works,
            'http_status':  r.status_code,
            'paystack_response': body,
        })
    except Exception as e:
        return jsonify({'key_set': True, 'key_works': False, 'error': str(e)}), 500


# ── Initialize card payment ───────────────────────────────────────────
@payment_bp.route('/initialize', methods=['POST'])
@jwt_required()
def initialize_payment():
    user_id = int(get_jwt_identity())
    user    = User.query.get(user_id)
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
        return jsonify({'status': 'error',
                        'message': 'Minimum funding amount is ₦100'}), 400
    if amount > 1_000_000:
        return jsonify({'status': 'error',
                        'message': 'Maximum funding amount is ₦1,000,000'}), 400

    backend_url = current_app.config.get('BACKEND_URL', '')
    ok, result = _paystack('POST', '/transaction/initialize', {
        'email':        user.email,
        'amount':       int(amount * 100),
        'currency':     'NGN',
        'callback_url': f'{backend_url}/api/payment/callback',
        'metadata': {
            'user_id':      user.id,
            'service_type': 'wallet_funding',
        },
    })

    if not ok:
        logger.error(f'Payment init failed for user {user.id}: {result}')
        return jsonify({
            'status':  'error',
            'message': result.get('message', 'Payment initialization failed'),
        }), 400

    ref = result['data']['reference']
    txn = Transaction(
        user_id      = user.id,
        reference    = ref,
        type         = 'wallet_funding',
        service_type = 'card_payment',
        amount       = amount,
        status       = 'pending',
        details      = {'channel': 'card'},
    )
    db.session.add(txn)
    db.session.commit()

    return jsonify({
        'status':  'success',
        'message': 'Payment initialized',
        'data': {
            'authorization_url': result['data']['authorization_url'],
            'reference':         ref,
            'amount':            amount,
        },
    })


# ── Verify card payment ───────────────────────────────────────────────
@payment_bp.route('/verify/<reference>', methods=['GET'])
@jwt_required()
def verify_payment(reference):
    ok, result = _paystack('GET', f'/transaction/verify/{reference}')
    if not ok:
        return jsonify({'status': 'error',
                        'message': result.get('message', 'Verification failed')}), 400

    pay_data = result['data']
    txn      = Transaction.query.filter_by(reference=reference).first()

    if not txn:
        user = User.query.filter_by(
            email=pay_data.get('customer', {}).get('email', '').lower()
        ).first()
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

    if txn.status == 'success':
        user = User.query.get(txn.user_id)
        return jsonify({
            'status':  'success',
            'message': 'Already credited',
            'data': {
                'amount':         txn.amount,
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

    txn.status = 'failed'
    db.session.commit()
    return jsonify({'status': 'error',
                    'message': f'Payment {pay_data.get("status", "not completed")}'}), 400


# ── Callback page (browser redirect after card payment) ───────────────
@payment_bp.route('/callback', methods=['GET'])
def payment_callback():
    reference = request.args.get('trxref') or request.args.get('reference')
    if not reference:
        return '''<html><body style="font-family:sans-serif;text-align:center;padding:50px;background:#fef2f2">
            <h2 style="color:#dc2626">❌ Payment reference missing</h2>
            <p>Please go back to the app and check your transaction history.</p>
        </body></html>'''

    ok, result = _paystack('GET', f'/transaction/verify/{reference}')
    if ok and result.get('data', {}).get('status') == 'success':
        amount = result['data']['amount'] / 100
        txn = Transaction.query.filter_by(reference=reference).first()
        if txn and txn.status != 'success':
            _credit_wallet(txn, amount, channel='card')
        return f'''<html><body style="font-family:sans-serif;text-align:center;padding:50px;background:#f0fdf4">
            <h2 style="color:#16a34a">✅ Payment Successful!</h2>
            <p style="font-size:18px">₦{amount:,.2f} has been added to your Cheap4u wallet.</p>
            <p>You can close this page and return to the app.</p>
        </body></html>'''

    return '''<html><body style="font-family:sans-serif;text-align:center;padding:50px;background:#fef2f2">
        <h2 style="color:#dc2626">❌ Payment Not Completed</h2>
        <p>Your wallet was not funded. Please try again from the app.</p>
    </body></html>'''


# ── Webhook ───────────────────────────────────────────────────────────
@payment_bp.route('/webhook', methods=['POST'])
def paystack_webhook():
    secret = current_app.config.get('PAYSTACK_SECRET_KEY', '').strip()
    sig    = request.headers.get('x-paystack-signature', '')
    body   = request.get_data()

    expected = hmac.new(secret.encode('utf-8'), body, hashlib.sha512).hexdigest()
    if not hmac.compare_digest(sig, expected):
        logger.warning('Webhook: invalid signature rejected')
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

    return jsonify({'status': 'success'}), 200


def _handle_charge_success(pay_data):
    reference    = pay_data.get('reference', '')
    amount       = pay_data.get('amount', 0) / 100
    channel      = pay_data.get('channel', 'unknown')
    customer     = pay_data.get('customer', {})
    cust_email   = customer.get('email', '').lower()
    cust_code    = customer.get('customer_code', '')

    if not reference or amount <= 0:
        return

    existing = Transaction.query.filter_by(reference=reference).first()
    if existing and existing.status == 'success':
        logger.info(f'Webhook: {reference} already credited')
        return

    user = None
    if cust_code:
        user = User.query.filter_by(paystack_customer_code=cust_code).first()
    if not user and cust_email:
        user = User.query.filter_by(email=cust_email).first()
    if not user:
        logger.error(f'Webhook: user not found for {cust_email} / {cust_code}')
        return

    service_type_map = {
        'dedicated_nuban': 'bank_transfer',
        'card':            'card_payment',
        'bank':            'bank_payment',
        'ussd':            'ussd_payment',
    }
    service_type = service_type_map.get(channel, channel)

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
    logger.info(f'✅ Webhook credited ₦{amount:,.2f} to {user.email} via {channel}')


def _credit_wallet(txn, amount, channel='unknown', user=None):
    if user is None:
        user = User.query.get(txn.user_id)
    if not user:
        return None

    user.wallet_balance = round(user.wallet_balance + amount, 2)
    txn.status          = 'success'
    txn.amount          = amount
    txn.details         = {**(txn.details or {}), 'channel': channel}

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

    db.session.commit()
    return user


# ── Account details ───────────────────────────────────────────────────
@payment_bp.route('/account-details', methods=['GET'])
@jwt_required()
def get_account_details():
    user_id = int(get_jwt_identity())
    user    = User.query.get(user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    # Auto-create DVA if missing
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
                f'Transfer to {user.virtual_bank_name} — '
                f'{user.virtual_account_number} to fund your wallet instantly.'
                if has_dva else
                'Fund your wallet via card payment. Bank transfer coming soon.'
            ),
        },
    })


# ── Transaction history ───────────────────────────────────────────────
@payment_bp.route('/transactions', methods=['GET'])
@jwt_required()
def get_transactions():
    user_id  = int(get_jwt_identity())
    page     = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 100)

    txns  = (Transaction.query.filter_by(user_id=user_id)
             .order_by(Transaction.created_at.desc())
             .limit(per_page).offset((page - 1) * per_page).all())
    total = Transaction.query.filter_by(user_id=user_id).count()

    return jsonify({
        'status': 'success',
        'data': {
            'transactions': [t.to_dict() for t in txns],
            'total':        total,
            'page':         page,
            'pages':        (total + per_page - 1) // per_page,
        },
    })
