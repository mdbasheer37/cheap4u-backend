
# admin.py — Full version with instant Paystack transfer for withdrawals
import logging
from flask import Blueprint, request, jsonify, current_app
from datetime import datetime, timedelta
from models import db, User, Transaction, Profit, WithdrawalRequest
from sqlalchemy import func
from flask_jwt_extended import jwt_required, get_jwt_identity
from functools import wraps
from admin_transfer import (
    resolve_account_number, get_bank_list,
    create_transfer_recipient, initiate_transfer
)

logger = logging.getLogger(__name__)

admin_bp = Blueprint('admin', __name__, url_prefix='/api/admin')

ADMIN_EMAILS = ['admin@cheap4u.com', 'muhammadibrahim376@gmail.com']


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
            'wallet_balance':   round(u.wallet_balance, 2),
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
    service_type = request.args.get('service_type')
    status       = request.args.get('status')
    query = Transaction.query.join(User, Transaction.user_id == User.id, isouter=True)
    if service_type:
        query = query.filter(Transaction.service_type == service_type)
    if status:
        query = query.filter(Transaction.status == status)
    txns = query.order_by(Transaction.created_at.desc()).limit(1000).all()
    return jsonify({
        'status': 'success',
        'data': [{
            'id': t.id,
            'user': {'id': t.user.id if t.user else None,
                      'name': t.user.name if t.user else 'Guest',
                      'email': t.user.email if t.user else None},
            'reference': t.reference, 'type': t.type, 'service_type': t.service_type,
            'amount': t.amount, 'profit': t.profit, 'status': t.status,
            'created_at': t.created_at.isoformat(), 'details': t.details,
        } for t in txns]
    })


@admin_bp.route('/profit', methods=['GET'])
@admin_required
def get_profit_summary():
    profit_table_total = db.session.query(func.sum(Profit.amount)).scalar() or 0.0
    txn_profit_total = db.session.query(func.sum(Transaction.profit)).filter(
        Transaction.status == 'success').scalar() or 0.0
    total_profit = max(float(profit_table_total), float(txn_profit_total))
    total_withdrawn = db.session.query(func.sum(WithdrawalRequest.amount)).filter(
        WithdrawalRequest.status == 'completed').scalar() or 0.0
    available = round(total_profit - float(total_withdrawn), 2)

    category_rows = db.session.query(Profit.category, func.sum(Profit.amount)).group_by(Profit.category).all()
    by_category = {'airtime': 0.0, 'data': 0.0, 'electricity': 0.0, 'cable_tv': 0.0, 'exam_pin': 0.0}
    for cat, amt in category_rows:
        if cat and cat in by_category:
            by_category[cat] = round(float(amt), 2)

    if profit_table_total == 0 and txn_profit_total > 0:
        svc_rows = db.session.query(Transaction.service_type, func.sum(Transaction.profit)).filter(
            Transaction.status == 'success', Transaction.profit > 0).group_by(Transaction.service_type).all()
        for svc, amt in svc_rows:
            if svc in by_category:
                by_category[svc] = round(float(amt), 2)

    return jsonify({
        'status': 'success',
        'data': {
            'total_profit': round(total_profit, 2),
            'total_withdrawn': round(float(total_withdrawn), 2),
            'available_balance': available,
            'total_available': available,
            'total_earned': round(total_profit, 2),
            'by_category': by_category,
            'profit_by_category': by_category,
        }
    })


@admin_bp.route('/wallet-summary', methods=['GET'])
@admin_required
def get_wallet_summary():
    total_user_wallet = db.session.query(func.sum(User.wallet_balance)).scalar() or 0.0
    total_profit = db.session.query(func.sum(Profit.amount)).scalar() or 0.0
    total_withdrawn = db.session.query(func.sum(WithdrawalRequest.amount)).filter(
        WithdrawalRequest.status == 'completed').scalar() or 0.0
    return jsonify({
        'status': 'success',
        'data': {
            'total_money_in_system': round(float(total_user_wallet), 2),
            'total_profit': round(float(total_profit), 2),
            'available_profit': round(float(total_profit) - float(total_withdrawn), 2),
        }
    })


@admin_bp.route('/stats', methods=['GET'])
@admin_required
def get_sales_stats():
    today = datetime.utcnow().date()

    def stats_for(start, end):
        rows = db.session.query(
            Transaction.service_type, func.sum(Transaction.amount), func.sum(Transaction.profit), func.count(Transaction.id)
        ).filter(Transaction.created_at >= start, Transaction.created_at < end,
                  Transaction.status == 'success').group_by(Transaction.service_type).all()
        result = {svc: {'sales': 0.0, 'profit': 0.0, 'count': 0}
                   for svc in ('airtime', 'data', 'electricity', 'cable_tv', 'exam_pin', 'wallet_funding')}
        for svc, sales, profit, count in rows:
            if svc in result:
                result[svc] = {'sales': round(float(sales or 0), 2), 'profit': round(float(profit or 0), 2), 'count': count}
        return result

    daily_start = datetime.combine(today, datetime.min.time())
    daily_end = daily_start + timedelta(days=1)
    weekly_start = daily_start - timedelta(days=6)
    monthly_start = datetime(today.year, today.month, 1)
    monthly_end = datetime(today.year + 1, 1, 1) if today.month == 12 else datetime(today.year, today.month + 1, 1)

    return jsonify({
        'status': 'success',
        'data': {
            'daily': stats_for(daily_start, daily_end),
            'weekly': stats_for(weekly_start, daily_end),
            'monthly': stats_for(monthly_start, monthly_end),
        }
    })


# ── INSTANT WITHDRAWAL VIA PAYSTACK TRANSFER ──────────────────────────
@admin_bp.route('/profit/withdraw', methods=['POST'])
@admin_required
def request_withdrawal():
    """Admin withdraws profit — sent INSTANTLY via Paystack Transfer."""
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
        return jsonify({'status': 'error', 'message': 'Maximum withdrawal is ₦500,000'}), 400

    total_profit     = float(db.session.query(func.sum(Profit.amount)).scalar() or 0)
    total_txn_profit = float(db.session.query(func.sum(Transaction.profit)).filter(
        Transaction.status == 'success').scalar() or 0)
    gross_profit    = max(total_profit, total_txn_profit)
    total_withdrawn = float(db.session.query(func.sum(WithdrawalRequest.amount)).filter(
        WithdrawalRequest.status == 'completed').scalar() or 0)
    available = gross_profit - total_withdrawn

    if amount > available:
        return jsonify({'status': 'error', 'message': f'Insufficient profit. Available: ₦{available:,.2f}'}), 400

    bank_details   = data.get('bank_details', {})
    account_number = bank_details.get('account_number', '').strip()
    bank_code      = bank_details.get('bank_code', '').strip()

    if not account_number or not bank_code:
        return jsonify({'status': 'error', 'message': 'Account number and bank are required'}), 400

    secret = current_app.config.get('PAYSTACK_SECRET_KEY', '').strip()

    resolved_name = resolve_account_number(secret, account_number, bank_code)
    if not resolved_name:
        return jsonify({'status': 'error', 'message': 'Could not verify account number. Check account number and bank.'}), 400

    withdrawal = WithdrawalRequest(
        user_id=user.id, amount=amount,
        bank_name=bank_details.get('bank_name', ''),
        account_number=account_number, account_name=resolved_name,
        status='pending',
    )
    db.session.add(withdrawal)
    db.session.flush()

    txn = Transaction(
        user_id=user.id,
        reference=f"WDL_{int(datetime.utcnow().timestamp())}_{user.id}",
        type='withdrawal', service_type='profit_withdrawal', amount=amount,
        status='pending', details={'withdrawal_id': withdrawal.id},
    )
    db.session.add(txn)
    db.session.commit()

    recipient_code = create_transfer_recipient(secret, account_number, bank_code, resolved_name)
    if not recipient_code:
        withdrawal.status = 'failed'
        txn.status = 'failed'
        db.session.commit()
        return jsonify({'status': 'error', 'message': 'Failed to set up bank account for transfer. Try again.'}), 500

    success, result = initiate_transfer(
        secret, recipient_code, amount,
        reason=f'Cheap4u profit withdrawal #{withdrawal.id}',
        reference=txn.reference,
    )

    if not success:
        withdrawal.status = 'failed'
        txn.status = 'failed'
        db.session.commit()
        return jsonify({'status': 'error', 'message': f'Transfer failed: {result}'}), 500

    transfer_status = result.get('status', 'pending')
    withdrawal.transfer_code = result.get('transfer_code', '')

    if transfer_status == 'success':
        withdrawal.status = 'completed'
        txn.status = 'success'
        message = f'₦{amount:,.2f} sent to {resolved_name} successfully!'
    else:
        withdrawal.status = 'processing'
        txn.status = 'processing'
        message = f'₦{amount:,.2f} transfer initiated to {resolved_name}. Processing...'

    withdrawal.processed_at = datetime.utcnow()
    db.session.commit()

    return jsonify({
        'status': 'success', 'message': message,
        'data': {'withdrawal_id': withdrawal.id, 'transfer_status': transfer_status, 'account_name': resolved_name},
    })


@admin_bp.route('/withdraw', methods=['POST'])
@admin_required
def request_withdrawal_legacy():
    return request_withdrawal()


@admin_bp.route('/banks', methods=['GET'])
@admin_required
def list_banks():
    """Get list of Nigerian banks with codes — used to populate bank dropdown."""
    secret = current_app.config.get('PAYSTACK_SECRET_KEY', '').strip()
    banks = get_bank_list(secret)
    return jsonify({'status': 'success', 'data': [{'name': b['name'], 'code': b['code']} for b in banks]})


@admin_bp.route('/withdraw/<int:withdrawal_id>/approve', methods=['POST'])
@admin_required
def approve_withdrawal(withdrawal_id):
    withdrawal = WithdrawalRequest.query.get_or_404(withdrawal_id)
    if withdrawal.status != 'pending':
        return jsonify({'status': 'error', 'message': 'Already processed'}), 400
    withdrawal.status = 'completed'
    withdrawal.processed_at = datetime.utcnow()
    txn = Transaction.query.filter(
        Transaction.user_id == withdrawal.user_id, Transaction.type == 'withdrawal',
        Transaction.details['withdrawal_id'].astext == str(withdrawal.id)
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
    withdrawals = query.order_by(WithdrawalRequest.created_at.desc()).all()
    return jsonify({
        'status': 'success',
        'data': [{
            'id': w.id,
            'user': {'id': w.user.id, 'name': w.user.name, 'email': w.user.email},
            'amount': round(w.amount, 2), 'bank_name': w.bank_name,
            'account_number': w.account_number, 'account_name': w.account_name,
            'status': w.status, 'created_at': w.created_at.isoformat(),
            'processed_at': w.processed_at.isoformat() if w.processed_at else None,
        } for w in withdrawals]
    })
