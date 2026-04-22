# payment.py  — Production-ready Paystack integration
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
    secret = current_app.config.get('PAYSTACK_SECRET_KEY', '')
    headers = {'Authorization': f'Bearer {secret}', 'Content-Type': 'application/json'}
    url = f'https://api.paystack.co{path}'
    try:
        r = requests.get(url, headers=headers, timeout=30) if method == 'GET' else \
            requests.post(url, json=data, headers=headers, timeout=30)
        result = r.json()
        return result.get('status', False), result
    except Exception as e:
        logger.error(f'Paystack API error {path}: {e}')
        return False, {'message': str(e)}


def create_paystack_customer(email, first_name, last_name, phone):
    """Create Paystack customer. Returns customer_code or raises."""
    ok, result = _paystack('POST', '/customer', {
        'email': email, 'first_name': first_name,
        'last_name': last_name, 'phone': phone,
    })
    if ok:
        return result['data']['customer_code']
    raise Exception(f"Customer creation failed: {result.get('message')}")


def create_dedicated_virtual_account(customer_code):
    """Create DVA. Returns dict with account details or raises."""
    for bank in ('wema-bank', 'titan-paystack'):
        ok, result = _paystack('POST', '/dedicated_account', {
            'customer': customer_code,
            'preferred_bank': bank,
        })
        if ok:
            acct = result['data']
            return {
                'account_number': acct['account_number'],
                'bank_name': acct['bank']['name'],
                'account_name': acct['account_name'],
            }
        logger.warning(f'DVA failed for {bank}: {result.get("message")}')
    raise Exception("Could not create virtual account with any bank")


@payment_bp.route('/initialize', methods=['POST'])
@jwt_required()
def initialize_payment():
    """Initialize a Paystack card/USSD payment for wallet funding."""
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    data = request.get_json() or {}
    amount = data.get('amount')
    if not amount or float(amount) < 100:
        return jsonify({'status': 'error', 'message': 'Minimum amount is ₦100'}), 400

    backend_url = current_app.config.get('BACKEND_URL', '')
    ok, result = _paystack('POST', '/transaction/initialize', {
        'email': user.email,
        'amount': int(float(amount) * 100),
        'callback_url': f'{backend_url}/api/payment/verify',
        'metadata': {'user_id': user.id, 'service_type': 'wallet_funding'},
    })

    if not ok:
        return jsonify({'status': 'error', 'message': result.get('message', 'Failed')}), 400

    ref = result['data']['reference']
    txn = Transaction(
        user_id=user.id, reference=ref, type='wallet_funding',
        service_type='card_payment', amount=float(amount),
        status='pending', details={'channel': 'card'},
    )
    db.session.add(txn)
    db.session.commit()

    return jsonify({
        'status': 'success',
        'message': 'Payment initialized',
        'data': {
            'authorization_url': result['data']['authorization_url'],
            'reference': ref,
            'amount': float(amount),
        },
    })


@payment_bp.route('/verify/<reference>', methods=['GET'])
@jwt_required()
def verify_payment(reference):
    """Verify a payment and credit wallet if successful."""
    ok, result = _paystack('GET', f'/transaction/verify/{reference}')
    if not ok:
        return jsonify({'status': 'error', 'message': result.get('message')}), 400

    pay_data = result['data']
    txn = Transaction.query.filter_by(reference=reference).first()
    if not txn:
        return jsonify({'status': 'error', 'message': 'Transaction not found'}), 404

    if txn.status == 'success':
        return jsonify({'status': 'success', 'message': 'Already credited',
                        'data': {'amount': txn.amount}})

    if pay_data.get('status') == 'success':
        amount = pay_data['amount'] / 100
        _credit_wallet(txn, amount, channel='card')
        return jsonify({'status': 'success',
                        'message': f'Wallet credited ₦{amount:,.2f}',
                        'data': {'amount': amount, 'reference': reference}})

    txn.status = 'failed'
    db.session.commit()
    return jsonify({'status': 'error', 'message': 'Payment not successful'}), 400


@payment_bp.route('/webhook', methods=['POST'])
def paystack_webhook():
    """
    Paystack webhook — handles DVA bank transfers + card payments.
    Set webhook URL in Paystack dashboard to:
    https://cheap4u-backend.onrender.com/api/payment/webhook
    """
    secret = current_app.config.get('PAYSTACK_SECRET_KEY', '')
    sig = request.headers.get('x-paystack-signature', '')
    body = request.get_data()

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
        _handle_charge_success(event.get('data', {}))

    return jsonify({'status': 'success'}), 200


def _handle_charge_success(pay_data):
    reference = pay_data.get('reference', '')
    amount = pay_data.get('amount', 0) / 100
    customer = pay_data.get('customer', {})
    customer_email = customer.get('email', '').lower()
    customer_code = customer.get('customer_code', '')
    channel = pay_data.get('channel', 'unknown')

    # Idempotency check
    existing = Transaction.query.filter_by(reference=reference).first()
    if existing and existing.status == 'success':
        logger.info(f'Webhook: {reference} already processed')
        return

    # Find user — by customer_code first (DVA), then by email
    user = None
    if customer_code:
        user = User.query.filter_by(paystack_customer_code=customer_code).first()
    if not user and customer_email:
        user = User.query.filter_by(email=customer_email).first()
    if not user:
        logger.error(f'Webhook: no user found for {customer_email} / {customer_code}')
        return

    txn = existing or Transaction(
        user_id=user.id, reference=reference, type='wallet_funding',
        service_type='bank_transfer' if channel == 'dedicated_nuban' else channel,
        amount=amount, status='pending', details={},
    )
    if not existing:
        db.session.add(txn)

    _credit_wallet(txn, amount, channel=channel, user=user)
    logger.info(f'✅ Credited ₦{amount:,.2f} to {user.email} [{channel}]')


def _credit_wallet(txn, amount, channel='unknown', user=None):
    if user is None:
        user = User.query.get(txn.user_id)
    if not user:
        return

    user.wallet_balance = round(user.wallet_balance + amount, 2)
    txn.status = 'success'
    txn.amount = amount
    txn.details = {**(txn.details or {}), 'channel': channel}

    # One-time signup referral bonus on first funding
    if not user.referral_bonus_claimed and user.referred_by_user_id:
        referrer = User.query.get(user.referred_by_user_id)
        if referrer:
            referrer.referral_earnings = round(referrer.referral_earnings + 10.0, 2)
            user.referral_bonus_claimed = True
            db.session.add(ReferralTransaction(
                referrer_id=referrer.id, referred_user_id=user.id,
                amount=10.0, type='signup_bonus',
            ))

    db.session.commit()


@payment_bp.route('/account-details', methods=['GET'])
@jwt_required()
def get_account_details():
    """Return logged-in user's virtual account + wallet balance."""
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    # Auto-create DVA if missing
    if not user.virtual_account_number and user.paystack_customer_code:
        try:
            dva = create_dedicated_virtual_account(user.paystack_customer_code)
            user.virtual_account_number = dva['account_number']
            user.virtual_bank_name = dva['bank_name']
            user.virtual_account_name = dva['account_name']
            db.session.commit()
        except Exception as e:
            logger.error(f'DVA retry failed user {user.id}: {e}')

    return jsonify({
        'status': 'success',
        'data': {
            'account_number': user.virtual_account_number or 'Not available',
            'bank_name': user.virtual_bank_name or 'Not available',
            'account_name': user.virtual_account_name or user.name,
            'wallet_balance': user.wallet_balance,
        },
    })


@payment_bp.route('/transactions', methods=['GET'])
@jwt_required()
def get_transactions():
    """Paginated transaction history."""
    user_id = get_jwt_identity()
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 20, type=int), 100)
    txns = (Transaction.query.filter_by(user_id=user_id)
            .order_by(Transaction.created_at.desc())
            .paginate(page=page, per_page=per_page, error_out=False))
    return jsonify({
        'status': 'success',
        'data': {
            'transactions': [t.to_dict() for t in txns.items],
            'total': txns.total,
            'page': page,
            'pages': txns.pages,
        },
    })
