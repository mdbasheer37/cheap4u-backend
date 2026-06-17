# admin.py — Production-ready admin backend
import hmac
import hashlib
from flask import Blueprint, request, jsonify
from datetime import datetime, timedelta
from models import db, User, Transaction, Profit, WithdrawalRequest
from sqlalchemy import func
from flask_jwt_extended import jwt_required, get_jwt_identity
from functools import wraps

admin_bp = Blueprint('admin', __name__, url_prefix='/api/admin')

ADMIN_EMAILS = ['admin@cheap4u.com', 'muhammadibrahim3766@gmail.com']


# ── Admin guard decorator ─────────────────────────────────────────────────
def admin_required(f):
    @wraps(f)
    @jwt_required()
    def decorated(*args, **kwargs):
        # Fix: get_jwt_identity returns string, convert to int
        user_id = int(get_jwt_identity())
        user = User.query.get(user_id)
        if not user:
            return jsonify({'status': 'error', 'message': 'User not found'}), 404
        # Allow by role OR by email (covers cases where role not set yet)
        if user.role != 'admin' and user.email not in ADMIN_EMAILS:
            return jsonify({'status': 'error', 'message': 'Admin access required'}), 403
        if not user.is_active:
            return jsonify({'status': 'error', 'message': 'Account is blocked'}), 403
        return f(*args, **kwargs)
    return decorated


# ── Promote user to admin (one-time setup) ────────────────────────────────
@admin_bp.route('/promote', methods=['POST'])
def promote_to_admin():
    """
    Promote a user to admin by email.
    Protected by a secret key — only for initial setup.
    POST /api/admin/promote
    Body: {"email": "...", "secret": "cheap4u-admin-setup-2024"}
    """
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


# ── USER MANAGEMENT ───────────────────────────────────────────────────────
@admin_bp.route('/users', methods=['GET'])
@admin_required
def get_all_users():
    users = User.query.order_by(User.created_at.desc()).all()
    return jsonify({
        'status': 'success',
        'data': [{
            'id':               u.id,
            'name':             u.name,
            'email':            u.email,
            'phone':            u.phone,
            'wallet_balance':   round(u.wallet_balance, 2),
            'referral_balance': round(u.referral_balance, 2),
            'is_active':        u.is_active,
            'is_verified':      u.is_verified,
            'role':             u.role,
            'created_at':       u.created_at.isoformat() if u.created_at else None,
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


# ── TRANSACTIONS ──────────────────────────────────────────────────────────
@admin_bp.route('/transactions', methods=['GET'])
@admin_required
def get_all_transactions():
    service_type = request.args.get('service_type')
    status       = request.args.get('status')
    start_date   = request.args.get('start_date')
    end_date     = request.args.get('end_date')

    query = Transaction.query.join(User, Transaction.user_id == User.id, isouter=True)

    if service_type:
        query = query.filter(Transaction.service_type == service_type)
    if status:
        query = query.filter(Transaction.status == status)
    if start_date:
        try:
            query = query.filter(Transaction.created_at >= datetime.strptime(start_date, '%Y-%m-%d'))
        except ValueError:
            pass
    if end_date:
        try:
            end = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(Transaction.created_at < end)
        except ValueError:
            pass

    txns = query.order_by(Transaction.created_at.desc()).limit(1000).all()
    return jsonify({
        'status': 'success',
        'data': [{
            'id':           t.id,
            'user': {
                'id':    t.user.id    if t.user else None,
                'name':  t.user.name  if t.user else 'Guest',
                'email': t.user.email if t.user else None,
            },
            'reference':    t.reference,
            'type':         t.type,
            'service_type': t.service_type,
            'amount':       t.amount,
            'profit':       t.profit,
            'status':       t.status,
            'created_at':   t.created_at.isoformat(),
            'details':      t.details,
        } for t in txns]
    })


# ── PROFIT SUMMARY ────────────────────────────────────────────────────────
@admin_bp.route('/profit', methods=['GET'])
@admin_required
def get_profit_summary():
    """
    Returns profit from two sources:
    1. Profit table — populated by VTU service purchases
    2. Transaction.profit field — fallback if Profit rows are missing
    Uses whichever is higher to avoid showing ₦0 due to missing Profit rows.
    """
    # Source 1: dedicated Profit table
    profit_table_total = db.session.query(func.sum(Profit.amount)).scalar() or 0.0

    # Source 2: sum of profit column on successful transactions
    txn_profit_total = db.session.query(
        func.sum(Transaction.profit)
    ).filter(Transaction.status == 'success').scalar() or 0.0

    # Use the larger of the two
    total_profit = max(float(profit_table_total), float(txn_profit_total))

    total_withdrawn = db.session.query(func.sum(WithdrawalRequest.amount)).filter(
        WithdrawalRequest.status == 'completed'
    ).scalar() or 0.0

    available = round(total_profit - float(total_withdrawn), 2)

    # By category from Profit table
    category_rows = db.session.query(
        Profit.category, func.sum(Profit.amount)
    ).group_by(Profit.category).all()

    by_category = {
        'airtime': 0.0, 'data': 0.0, 'electricity': 0.0,
        'cable_tv': 0.0, 'exam_pin': 0.0,
    }
    for cat, amt in category_rows:
        if cat and cat in by_category:
            by_category[cat] = round(float(amt), 2)

    # If Profit table is empty, calculate from Transaction.profit per service_type
    if profit_table_total == 0 and txn_profit_total > 0:
        svc_rows = db.session.query(
            Transaction.service_type,
            func.sum(Transaction.profit)
        ).filter(
            Transaction.status == 'success',
            Transaction.profit > 0
        ).group_by(Transaction.service_type).all()

        for svc, amt in svc_rows:
            if svc in by_category:
                by_category[svc] = round(float(amt), 2)

    return jsonify({
        'status': 'success',
        'data': {
            'total_profit':      round(total_profit, 2),
            'total_withdrawn':   round(float(total_withdrawn), 2),
            'available_balance': available,
            'total_available':   available,   # alias for Kivy app
            'total_earned':      round(total_profit, 2),  # alias for Kivy app
            'by_category':       by_category,
            'profit_by_category': by_category,  # alias
        }
    })


# ── WALLET SUMMARY ────────────────────────────────────────────────────────
@admin_bp.route('/wallet-summary', methods=['GET'])
@admin_required
def get_wallet_summary():
    total_user_wallet = db.session.query(func.sum(User.wallet_balance)).scalar() or 0.0
    total_profit      = db.session.query(func.sum(Profit.amount)).scalar() or 0.0
    total_withdrawn   = db.session.query(func.sum(WithdrawalRequest.amount)).filter(
        WithdrawalRequest.status == 'completed'
    ).scalar() or 0.0

    return jsonify({
        'status': 'success',
        'data': {
            'total_money_in_system': round(float(total_user_wallet), 2),
            'total_profit':          round(float(total_profit), 2),
            'available_profit':      round(float(total_profit) - float(total_withdrawn), 2),
        }
    })


# ── SALES STATS ───────────────────────────────────────────────────────────
@admin_bp.route('/stats', methods=['GET'])
@admin_required
def get_sales_stats():
    today = datetime.utcnow().date()

    def stats_for(start, end):
        rows = db.session.query(
            Transaction.service_type,
            func.sum(Transaction.amount).label('sales'),
            func.sum(Transaction.profit).label('profit'),
            func.count(Transaction.id).label('count'),
        ).filter(
            Transaction.created_at >= start,
            Transaction.created_at < end,
            Transaction.status == 'success',
        ).group_by(Transaction.service_type).all()

        result = {
            svc: {'sales': 0.0, 'profit': 0.0, 'count': 0}
            for svc in ('airtime', 'data', 'electricity', 'cable_tv', 'exam_pin', 'wallet_funding')
        }
        for svc, sales, profit, count in rows:
            key = svc if svc in result else svc
            result[key] = {
                'sales':  round(float(sales or 0), 2),
                'profit': round(float(profit or 0), 2),
                'count':  count,
            }
        return result

    daily_start   = datetime.combine(today, datetime.min.time())
    daily_end     = daily_start + timedelta(days=1)
    weekly_start  = daily_start - timedelta(days=6)
    monthly_start = datetime(today.year, today.month, 1)
    monthly_end   = (
        datetime(today.year + 1, 1, 1) if today.month == 12
        else datetime(today.year, today.month + 1, 1)
    )

    return jsonify({
        'status': 'success',
        'data': {
            'daily':   stats_for(daily_start, daily_end),
            'weekly':  stats_for(weekly_start, daily_end),
            'monthly': stats_for(monthly_start, monthly_end),
        }
    })


# ── REQUEST WITHDRAWAL ────────────────────────────────────────────────────
@admin_bp.route('/profit/withdraw', methods=['POST'])
@admin_required
def request_withdrawal():
    """Admin requests a profit withdrawal."""
    user_id = int(get_jwt_identity())
    user    = User.query.get(user_id)
    data    = request.get_json() or {}

    try:
        amount = float(data.get('amount', 0))
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'Invalid amount'}), 400

    if amount < 50:
        return jsonify({'status': 'error', 'message': 'Minimum withdrawal is ₦50'}), 400
    if amount > 500_000:
        return jsonify({'status': 'error', 'message': 'Maximum withdrawal is ₦500,000'}), 400

    # Check available profit
    total_profit    = float(db.session.query(func.sum(Profit.amount)).scalar() or 0)
    total_txn_profit = float(db.session.query(func.sum(Transaction.profit)).filter(
        Transaction.status == 'success').scalar() or 0)
    gross_profit    = max(total_profit, total_txn_profit)
    total_withdrawn = float(db.session.query(func.sum(WithdrawalRequest.amount)).filter(
        WithdrawalRequest.status == 'completed').scalar() or 0)
    available = gross_profit - total_withdrawn

    if amount > available:
        return jsonify({
            'status': 'error',
            'message': f'Insufficient profit. Available: ₦{available:,.2f}'
        }), 400

    bank_details = data.get('bank_details', {})

    withdrawal = WithdrawalRequest(
        user_id        = user.id,
        amount         = amount,
        bank_name      = bank_details.get('bank_name'),
        account_number = bank_details.get('account_number'),
        account_name   = bank_details.get('account_name'),
        status         = 'pending',
    )
    db.session.add(withdrawal)
    db.session.flush()  # get withdrawal.id before creating transaction

    txn = Transaction(
        user_id      = user.id,
        reference    = f"WDL_{datetime.utcnow().timestamp()}_{user.id}",
        type         = 'withdrawal',
        service_type = 'profit_withdrawal',
        amount       = amount,
        status       = 'pending',
        details      = {'withdrawal_id': withdrawal.id},
    )
    db.session.add(txn)
    db.session.commit()

    return jsonify({
        'status':  'success',
        'message': 'Withdrawal request submitted',
        'data':    {'withdrawal_id': withdrawal.id},
    })


# Keep old route as alias so Kivy app still works
@admin_bp.route('/withdraw', methods=['POST'])
@admin_required
def request_withdrawal_legacy():
    return request_withdrawal()


# ── APPROVE WITHDRAWAL ────────────────────────────────────────────────────
@admin_bp.route('/withdraw/<int:withdrawal_id>/approve', methods=['POST'])
@admin_required
def approve_withdrawal(withdrawal_id):
    withdrawal = WithdrawalRequest.query.get_or_404(withdrawal_id)
    if withdrawal.status != 'pending':
        return jsonify({'status': 'error', 'message': 'Already processed'}), 400

    withdrawal.status       = 'completed'
    withdrawal.processed_at = datetime.utcnow()

    # Fix: can't filter JSON with dict — use text cast instead
    txn = Transaction.query.filter(
        Transaction.user_id      == withdrawal.user_id,
        Transaction.type         == 'withdrawal',
        Transaction.details['withdrawal_id'].astext == str(withdrawal.id)
    ).first()
    if txn:
        txn.status = 'success'

    db.session.commit()
    return jsonify({'status': 'success', 'message': f'Withdrawal #{withdrawal_id} approved'})


# ── LIST WITHDRAWALS ──────────────────────────────────────────────────────
@admin_bp.route('/withdrawals', methods=['GET'])
@admin_required
def list_withdrawals():
    status = request.args.get('status')
    query  = WithdrawalRequest.query.join(User)
    if status:
        query = query.filter(WithdrawalRequest.status == status)
    withdrawals = query.order_by(WithdrawalRequest.created_at.desc()).all()

    return jsonify({
        'status': 'success',
        'data': [{
            'id':             w.id,
            'user': {
                'id':    w.user.id,
                'name':  w.user.name,
                'email': w.user.email,
            },
            'amount':         round(w.amount, 2),
            'bank_name':      w.bank_name,
            'account_number': w.account_number,
            'account_name':   w.account_name,
            'status':         w.status,
            'created_at':     w.created_at.isoformat(),
            'processed_at':   w.processed_at.isoformat() if w.processed_at else None,
        } for w in withdrawals]
    })
