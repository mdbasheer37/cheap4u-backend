# admin.py — Self-contained: transfer logic built-in, no external imports needed
import logging
import requests as _requests
from flask import Blueprint, request, jsonify, current_app
from datetime import datetime, timedelta
from models import db, User, Transaction, Profit, WithdrawalRequest
from sqlalchemy import func
from flask_jwt_extended import jwt_required, get_jwt_identity
from functools import wraps

logger = logging.getLogger(__name__)
admin_bp = Blueprint('admin', __name__, url_prefix='/api/admin')
ADMIN_EMAILS = ['admin@cheap4u.com', 'muhammadibrahim376@gmail.com']

# Mobile money banks — skip Paystack account resolve for these
SKIP_VERIFY_BANKS = {
    '100004', '999991', '50515', '090267', '566', '526', '101', '110005'
}


def admin_required(f):
    @wraps(f)
    @jwt_required()
    def decorated(*args, **kwargs):
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            return jsonify({'status': 'error', 'message': 'User not found'}), 404
        if user.role != 'admin' and user.email not in ADMIN_EMAILS:
            return jsonify({'status': 'error', 'message': 'Admin access required'}), 403
        if not user.is_active:
            return jsonify({'status': 'error', 'message': 'Account is blocked'}), 403
        return f(*args, **kwargs)
    return decorated


# ── Paystack transfer helpers (built-in — no external file needed) ────
def _ps(method, path, secret, data=None):
    headers = {'Authorization': f'Bearer {secret}', 'Content-Type': 'application/json'}
    url = f'https://api.paystack.co{path}'
    try:
        r = _requests.get(url, headers=headers, timeout=30) if method == 'GET' \
            else _requests.post(url, json=data, headers=headers, timeout=30)
        result = r.json()
        return result.get('status', False), result
    except Exception as e:
        return False, {'message': str(e)}


def _resolve_account(secret, account_number, bank_code):
    if str(bank_code) in SKIP_VERIFY_BANKS:
        return f'Account {account_number}'
    ok, result = _ps('GET',
        f'/bank/resolve?account_number={account_number}&bank_code={bank_code}', secret)
    return result['data']['account_name'] if ok else None


def _create_recipient(secret, account_number, bank_code, account_name):
    ok, result = _ps('POST', '/transferrecipient', secret, {
        'type': 'nuban', 'name': account_name,
        'account_number': account_number,
        'bank_code': str(bank_code), 'currency': 'NGN',
    })
    if ok:
        return result['data']['recipient_code']
    logger.error(f'Recipient failed [{bank_code}/{account_number}]: {result}')
    return None, result.get('message', 'Unknown error')


def _do_transfer(secret, recipient_code, amount, reason, reference):
    ok, result = _ps('POST', '/transfer', secret, {
        'source': 'balance', 'reason': reason,
        'amount': int(amount * 100),
        'recipient': recipient_code, 'reference': reference,
    })
    return (True, result['data']) if ok else (False, result.get('message', 'Transfer failed'))


# ── Routes ─────────────────────────────────────────────────────────────
@admin_bp.route('/promote', methods=['POST'])
def promote_to_admin():
    data = request.get_json() or {}
    if data.get('secret') != 'cheap4u-admin-setup-2024':
        return jsonify({'status': 'error', 'message': 'Invalid secret'}), 403
    email = (data.get('email') or '').lower()
    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404
    user.role = 'admin'
    db.session.commit()
    return jsonify({'status': 'success', 'message': f'{email} is now admin'})


@admin_bp.route('/users', methods=['GET'])
@admin_required
def get_all_users():
    users = User.query.order_by(User.created_at.desc()).all()
    return jsonify({
        'status': 'success',
        'data': [{
            'id': u.id, 'name': u.name, 'email': u.email, 'phone': u.phone,
            'wallet_balance': round(u.wallet_balance, 2),
            'referral_balance': round(u.referral_balance, 2),
            'is_active': u.is_active, 'is_verified': u.is_verified, 'role': u.role,
            'created_at': u.created_at.isoformat() if u.created_at else None,
        } for u in users]
    })


@admin_bp.route('/users/<int:user_id>/block', methods=['POST'])
@admin_required
def block_user(user_id):
    user = User.query.get_or_404(user_id)
    user.is_active = False
    db.session.commit()
    return jsonify({'status': 'success', 'message': f'User {user.email} blocked'})


@admin_bp.route('/users/<int:user_id>/unblock', methods=['POST'])
@admin_required
def unblock_user(user_id):
    user = User.query.get_or_404(user_id)
    user.is_active = True
    db.session.commit()
    return jsonify({'status': 'success', 'message': f'User {user.email} unblocked'})


@admin_bp.route('/transactions', methods=['GET'])
@admin_required
def get_all_transactions():
    query = Transaction.query.join(User, Transaction.user_id == User.id, isouter=True)
    if request.args.get('service_type'):
        query = query.filter(Transaction.service_type == request.args['service_type'])
    if request.args.get('status'):
        query = query.filter(Transaction.status == request.args['status'])
    txns = query.order_by(Transaction.created_at.desc()).limit(1000).all()
    return jsonify({
        'status': 'success',
        'data': [{
            'id': t.id,
            'user': {'id': t.user.id if t.user else None,
                     'name': t.user.name if t.user else 'Guest',
                     'email': t.user.email if t.user else None},
            'reference': t.reference, 'type': t.type,
            'service_type': t.service_type, 'amount': t.amount,
            'profit': t.profit, 'status': t.status,
            'created_at': t.created_at.isoformat(), 'details': t.details,
        } for t in txns]
    })


@admin_bp.route('/profit', methods=['GET'])
@admin_required
def get_profit_summary():
    pt = float(db.session.query(func.sum(Profit.amount)).scalar() or 0)
    tt = float(db.session.query(func.sum(Transaction.profit)).filter(
        Transaction.status == 'success').scalar() or 0)
    total = max(pt, tt)
    withdrawn = float(db.session.query(func.sum(WithdrawalRequest.amount)).filter(
        WithdrawalRequest.status == 'completed').scalar() or 0)
    available = round(total - withdrawn, 2)

    cats = {'airtime': 0.0, 'data': 0.0, 'electricity': 0.0, 'cable_tv': 0.0, 'exam_pin': 0.0}
    for cat, amt in db.session.query(Profit.category, func.sum(Profit.amount)).group_by(Profit.category).all():
        if cat in cats:
            cats[cat] = round(float(amt), 2)
    if pt == 0 and tt > 0:
        for svc, amt in db.session.query(Transaction.service_type, func.sum(Transaction.profit)).filter(
            Transaction.status == 'success', Transaction.profit > 0).group_by(Transaction.service_type).all():
            if svc in cats:
                cats[svc] = round(float(amt), 2)

    return jsonify({'status': 'success', 'data': {
        'total_profit': round(total, 2), 'total_withdrawn': round(withdrawn, 2),
        'available_balance': available, 'total_available': available,
        'total_earned': round(total, 2),
        'by_category': cats, 'profit_by_category': cats,
    }})


@admin_bp.route('/wallet-summary', methods=['GET'])
@admin_required
def get_wallet_summary():
    return jsonify({'status': 'success', 'data': {
        'total_money_in_system': round(float(db.session.query(func.sum(User.wallet_balance)).scalar() or 0), 2),
        'total_profit': round(float(db.session.query(func.sum(Profit.amount)).scalar() or 0), 2),
        'available_profit': round(float(db.session.query(func.sum(Profit.amount)).scalar() or 0) -
            float(db.session.query(func.sum(WithdrawalRequest.amount)).filter(
                WithdrawalRequest.status == 'completed').scalar() or 0), 2),
    }})


@admin_bp.route('/stats', methods=['GET'])
@admin_required
def get_sales_stats():
    today = datetime.utcnow().date()
    def stats_for(start, end):
        rows = db.session.query(
            Transaction.service_type, func.sum(Transaction.amount),
            func.sum(Transaction.profit), func.count(Transaction.id)
        ).filter(Transaction.created_at >= start, Transaction.created_at < end,
                 Transaction.status == 'success').group_by(Transaction.service_type).all()
        result = {s: {'sales': 0.0, 'profit': 0.0, 'count': 0}
                  for s in ('airtime','data','electricity','cable_tv','exam_pin','wallet_funding')}
        for svc, sales, profit, count in rows:
            if svc in result:
                result[svc] = {'sales': round(float(sales or 0), 2),
                               'profit': round(float(profit or 0), 2), 'count': count}
        return result

    ds = datetime.combine(today, datetime.min.time())
    return jsonify({'status': 'success', 'data': {
        'daily':   stats_for(ds, ds + timedelta(days=1)),
        'weekly':  stats_for(ds - timedelta(days=6), ds + timedelta(days=1)),
        'monthly': stats_for(datetime(today.year, today.month, 1),
                             datetime(today.year + (1 if today.month == 12 else 0),
                                      1 if today.month == 12 else today.month + 1, 1)),
    }})


@admin_bp.route('/profit/withdraw', methods=['POST'])
@admin_required
def request_withdrawal():
    user_id = int(get_jwt_identity())
    user    = User.query.get(user_id)
    data    = request.get_json() or {}

    try:
        amount = float(data.get('amount', 0))
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'Invalid amount'}), 400

    if amount < 100:
        return jsonify({'status': 'error', 'message': 'Minimum withdrawal is ₦100'}), 400
    if amount > 500_000:
        return jsonify({'status': 'error', 'message': 'Maximum is ₦500,000'}), 400

    pt = float(db.session.query(func.sum(Profit.amount)).scalar() or 0)
    tt = float(db.session.query(func.sum(Transaction.profit)).filter(
        Transaction.status == 'success').scalar() or 0)
    gross = max(pt, tt)
    withdrawn = float(db.session.query(func.sum(WithdrawalRequest.amount)).filter(
        WithdrawalRequest.status == 'completed').scalar() or 0)
    available = gross - withdrawn

    if amount > available:
        return jsonify({'status': 'error',
                        'message': f'Insufficient profit. Available: ₦{available:,.2f}'}), 400

    bd             = data.get('bank_details', {})
    account_number = bd.get('account_number', '').strip()
    bank_code      = bd.get('bank_code', '').strip()

    if not account_number or not bank_code:
        return jsonify({'status': 'error',
                        'message': 'Account number and bank are required'}), 400

    secret = current_app.config.get('PAYSTACK_SECRET_KEY', '').strip()

    # Step 1: Resolve account name (skip for mobile money banks)
    account_name = _resolve_account(secret, account_number, bank_code)
    if not account_name:
        return jsonify({'status': 'error',
                        'message': 'Could not verify account number. Check details and try again.'}), 400

    # Step 2: Create withdrawal record
    withdrawal = WithdrawalRequest(
        user_id=user.id, amount=amount,
        bank_name=bd.get('bank_name', ''),
        account_number=account_number,
        account_name=account_name,
        status='pending',
    )
    db.session.add(withdrawal)
    db.session.flush()

    ref = f"WDL_{int(datetime.utcnow().timestamp())}_{user.id}"
    txn = Transaction(
        user_id=user.id, reference=ref, type='withdrawal',
        service_type='profit_withdrawal', amount=amount,
        status='pending', details={'withdrawal_id': withdrawal.id},
    )
    db.session.add(txn)
    db.session.commit()

    # Step 3: Create transfer recipient
    recipient_result = _create_recipient(secret, account_number, bank_code, account_name)
    if isinstance(recipient_result, tuple):
        recipient_code, recipient_err = recipient_result
        withdrawal.status = 'failed'
        txn.status = 'failed'
        db.session.commit()
        return jsonify({'status': 'error',
                        'message': f'Bank recipient error: {recipient_err}'}), 500
    recipient_code = recipient_result
    if not recipient_code:
        withdrawal.status = 'failed'
        txn.status = 'failed'
        db.session.commit()
        return jsonify({'status': 'error',
                        'message': 'Failed to set up bank recipient. Try again.'}), 500

    # Step 4: Send the money
    success, result = _do_transfer(secret, recipient_code, amount,
                                   reason=f'Cheap4u profit withdrawal #{withdrawal.id}',
                                   reference=ref)
    if not success:
        withdrawal.status = 'failed'
        txn.status = 'failed'
        db.session.commit()
        return jsonify({'status': 'error', 'message': f'Transfer failed: {result}'}), 500

    transfer_status = result.get('status', 'pending')
    if transfer_status == 'success':
        withdrawal.status = 'completed'
        txn.status = 'success'
        msg = f'₦{amount:,.2f} sent to {account_name} successfully!'
    else:
        withdrawal.status = 'processing'
        txn.status = 'processing'
        msg = f'₦{amount:,.2f} transfer initiated to {account_name}. Processing...'

    withdrawal.processed_at = datetime.utcnow()
    db.session.commit()

    return jsonify({'status': 'success', 'message': msg,
                    'data': {'withdrawal_id': withdrawal.id,
                             'transfer_status': transfer_status,
                             'account_name': account_name}})


@admin_bp.route('/withdraw', methods=['POST'])
@admin_required
def request_withdrawal_legacy():
    return request_withdrawal()


@admin_bp.route('/banks', methods=['GET'])
@admin_required
def list_banks():
    secret = current_app.config.get('PAYSTACK_SECRET_KEY', '').strip()
    ok, result = _ps('GET', '/bank?country=nigeria&perPage=100', secret)
    banks = result['data'] if ok else []
    return jsonify({'status': 'success',
                    'data': [{'name': b['name'], 'code': b['code']} for b in banks]})


@admin_bp.route('/withdraw/<int:withdrawal_id>/approve', methods=['POST'])
@admin_required
def approve_withdrawal(withdrawal_id):
    w = WithdrawalRequest.query.get_or_404(withdrawal_id)
    if w.status != 'pending':
        return jsonify({'status': 'error', 'message': 'Already processed'}), 400
    w.status = 'completed'
    w.processed_at = datetime.utcnow()
    txn = Transaction.query.filter(
        Transaction.user_id == w.user_id,
        Transaction.type == 'withdrawal',
        Transaction.details['withdrawal_id'].astext == str(w.id)
    ).first()
    if txn:
        txn.status = 'success'
    db.session.commit()
    return jsonify({'status': 'success', 'message': f'Withdrawal #{withdrawal_id} approved'})


@admin_bp.route('/withdrawals', methods=['GET'])
@admin_required
def list_withdrawals():
    status = request.args.get('status')
    query = WithdrawalRequest.query.join(User)
    if status:
        query = query.filter(WithdrawalRequest.status == status)
    ws = query.order_by(WithdrawalRequest.created_at.desc()).all()
    return jsonify({'status': 'success', 'data': [{
        'id': w.id,
        'user': {'id': w.user.id, 'name': w.user.name, 'email': w.user.email},
        'amount': round(w.amount, 2), 'bank_name': w.bank_name,
        'account_number': w.account_number, 'account_name': w.account_name,
        'status': w.status, 'created_at': w.created_at.isoformat(),
        'processed_at': w.processed_at.isoformat() if w.processed_at else None,
    } for w in ws]})
