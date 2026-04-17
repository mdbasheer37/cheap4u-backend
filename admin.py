# admin.py
from flask import Blueprint, request, jsonify, current_app
from datetime import datetime, timedelta
from models import db, User, Transaction, Profit, WithdrawalRequest
from sqlalchemy import func, cast
from sqlalchemy.dialects.postgresql import JSONB
from flask_jwt_extended import jwt_required, get_jwt_identity
from functools import wraps

admin_bp = Blueprint('admin', __name__, url_prefix='/api/admin')


def admin_required(f):
    """Decorator to verify JWT and admin role."""
    @wraps(f)
    @jwt_required()
    def decorated(*args, **kwargs):
        user_id = get_jwt_identity()
        user = User.query.get(user_id)
        if not user or user.role != 'admin':
            return jsonify({'status': 'error', 'message': 'Admin access required'}), 403
        if not user.is_active:
            return jsonify({'status': 'error', 'message': 'Account is blocked'}), 403
        return f(*args, **kwargs)
    return decorated


# ==================================================
# USER MANAGEMENT
# ==================================================
@admin_bp.route('/users', methods=['GET'])
@admin_required
def get_all_users():
    """Return all users with essential fields."""
    users = User.query.all()
    return jsonify({
        'status': 'success',
        'data': [{
            'id': u.id,
            'name': u.name,
            'email': u.email,
            'phone': u.phone,
            'wallet_balance': u.wallet_balance,
            'referral_balance': u.referral_balance,
            'is_active': u.is_active,
            'is_verified': u.is_verified,
            'role': u.role,
            'created_at': u.created_at.isoformat() if u.created_at else None
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


# ==================================================
# TRANSACTIONS OVERVIEW
# ==================================================
@admin_bp.route('/transactions', methods=['GET'])
@admin_required
def get_all_transactions():
    """Return all transactions with filtering."""
    service_type = request.args.get('service_type')
    status = request.args.get('status')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    query = Transaction.query.join(User, Transaction.user_id == User.id, isouter=True)

    if service_type:
        query = query.filter(Transaction.service_type == service_type)
    if status:
        query = query.filter(Transaction.status == status)
    if start_date:
        try:
            start = datetime.strptime(start_date, '%Y-%m-%d')
            query = query.filter(Transaction.created_at >= start)
        except ValueError:
            pass
    if end_date:
        try:
            end = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(Transaction.created_at < end)
        except ValueError:
            pass

    transactions = query.order_by(Transaction.created_at.desc()).limit(1000).all()

    return jsonify({
        'status': 'success',
        'data': [{
            'id': t.id,
            'user': {
                'id': t.user.id if t.user else None,
                'name': t.user.name if t.user else 'Guest',
                'email': t.user.email if t.user else None
            },
            'reference': t.reference,
            'type': t.type,
            'service_type': t.service_type,
            'amount': t.amount,
            'profit': t.profit,
            'status': t.status,
            'created_at': t.created_at.isoformat(),
            'details': t.details
        } for t in transactions]
    })


# ==================================================
# PROFIT TRACKING
# ==================================================
@admin_bp.route('/profit', methods=['GET'])
@admin_required
def get_profit_summary():
    total_profit = db.session.query(func.sum(Profit.amount)).scalar() or 0.0
    total_withdrawn = db.session.query(func.sum(WithdrawalRequest.amount)).filter(
        WithdrawalRequest.status == 'completed'
    ).scalar() or 0.0
    available_balance = total_profit - total_withdrawn

    category_profits = db.session.query(
        Profit.category,
        func.sum(Profit.amount).label('total')
    ).group_by(Profit.category).all()

    profit_by_category = {
        'airtime': 0.0, 'data': 0.0, 'electricity': 0.0,
        'cable_tv': 0.0, 'exam_pin': 0.0
    }
    for cat, amount in category_profits:
        if cat in profit_by_category:
            profit_by_category[cat] = float(amount)

    return jsonify({
        'status': 'success',
        'data': {
            'total_profit': float(total_profit),
            'total_withdrawn': float(total_withdrawn),
            'available_balance': float(available_balance),
            'profit_by_category': profit_by_category
        }
    })


# ==================================================
# WALLET / SYSTEM BALANCE
# ==================================================
@admin_bp.route('/wallet-summary', methods=['GET'])
@admin_required
def get_wallet_summary():
    total_user_wallet = db.session.query(func.sum(User.wallet_balance)).scalar() or 0.0
    total_profit = db.session.query(func.sum(Profit.amount)).scalar() or 0.0
    total_withdrawn = db.session.query(func.sum(WithdrawalRequest.amount)).filter(
        WithdrawalRequest.status == 'completed'
    ).scalar() or 0.0
    available_profit = total_profit - total_withdrawn

    return jsonify({
        'status': 'success',
        'data': {
            'total_money_in_system': float(total_user_wallet),
            'total_profit': float(total_profit),
            'available_profit': float(available_profit)
        }
    })


# ==================================================
# STATISTICS (ANALYTICS)
# ==================================================
@admin_bp.route('/stats', methods=['GET'])
@admin_required
def get_sales_stats():
    today = datetime.utcnow().date()

    def get_stats_for_period(start_date, end_date):
        results = db.session.query(
            Transaction.service_type,
            func.sum(Transaction.amount).label('total_sales'),
            func.count(Transaction.id).label('count')
        ).filter(
            Transaction.created_at >= start_date,
            Transaction.created_at < end_date,
            Transaction.status == 'success'
        ).group_by(Transaction.service_type).all()

        stats = {
            'airtime': {'sales': 0, 'count': 0},
            'data': {'sales': 0, 'count': 0},
            'electricity': {'sales': 0, 'count': 0},
            'cable_tv': {'sales': 0, 'count': 0},
            'exam_pin': {'sales': 0, 'count': 0}
        }
        for service, sales, count in results:
            if service in stats:
                stats[service] = {'sales': float(sales), 'count': count}
        return stats

    daily_start = datetime.combine(today, datetime.min.time())
    daily_end = daily_start + timedelta(days=1)
    weekly_start = daily_start - timedelta(days=6)
    monthly_start = datetime(today.year, today.month, 1)
    if today.month == 12:
        monthly_end = datetime(today.year + 1, 1, 1)
    else:
        monthly_end = datetime(today.year, today.month + 1, 1)

    return jsonify({
        'status': 'success',
        'data': {
            'daily': get_stats_for_period(daily_start, daily_end),
            'weekly': get_stats_for_period(weekly_start, daily_end),
            'monthly': get_stats_for_period(monthly_start, monthly_end)
        }
    })


# ==================================================
# WITHDRAWAL SYSTEM (ADMIN)
# ==================================================
@admin_bp.route('/withdraw', methods=['POST'])
@admin_required
def request_withdrawal():
    """Create withdrawal request (admin only)."""
    data = request.get_json()
    user_id = get_jwt_identity()
    user = User.query.get(user_id)

    amount = data.get('amount')
    bank_details = data.get('bank_details', {})

    if not amount:
        return jsonify({'status': 'error', 'message': 'Amount required'}), 400

    total_profit = db.session.query(func.sum(Profit.amount)).scalar() or 0.0
    total_withdrawn = db.session.query(func.sum(WithdrawalRequest.amount)).filter(
        WithdrawalRequest.status == 'completed'
    ).scalar() or 0.0
    available = total_profit - total_withdrawn

    if float(amount) > available:
        return jsonify({'status': 'error', 'message': 'Insufficient available profit'}), 400
    if float(amount) < 1000:
        return jsonify({'status': 'error', 'message': 'Minimum withdrawal is ₦1,000'}), 400

    withdrawal = WithdrawalRequest(
        user_id=user.id,
        amount=float(amount),
        bank_name=bank_details.get('bank_name'),
        account_number=bank_details.get('account_number'),
        account_name=bank_details.get('account_name'),
        status='pending'
    )
    db.session.add(withdrawal)
    db.session.flush()  # get withdrawal.id before creating transaction

    transaction = Transaction(
        user_id=user.id,
        reference=f"WDL_{datetime.utcnow().timestamp()}",
        type='withdrawal',
        service_type='profit_withdrawal',
        amount=float(amount),
        status='pending',
        details={'withdrawal_id': withdrawal.id}
    )
    db.session.add(transaction)
    db.session.commit()

    return jsonify({
        'status': 'success',
        'message': 'Withdrawal request submitted',
        'data': {'withdrawal_id': withdrawal.id}
    })


@admin_bp.route('/withdraw/<int:withdrawal_id>/approve', methods=['POST'])
@admin_required
def approve_withdrawal(withdrawal_id):
    """Approve withdrawal and mark as completed."""
    withdrawal = WithdrawalRequest.query.get_or_404(withdrawal_id)
    if withdrawal.status != 'pending':
        return jsonify({'status': 'error', 'message': 'Withdrawal already processed'}), 400

    withdrawal.status = 'completed'
    withdrawal.processed_at = datetime.utcnow()

    # Fixed: query JSON field correctly instead of filter_by with dict
    transaction = Transaction.query.filter(
        Transaction.user_id == withdrawal.user_id,
        Transaction.type == 'withdrawal',
        Transaction.details['withdrawal_id'].astext == str(withdrawal.id)
    ).first()
    if transaction:
        transaction.status = 'success'

    db.session.commit()
    return jsonify({'status': 'success', 'message': f'Withdrawal #{withdrawal_id} approved'})


@admin_bp.route('/withdrawals', methods=['GET'])
@admin_required
def list_withdrawals():
    """List all withdrawal requests with filters."""
    status = request.args.get('status')
    query = WithdrawalRequest.query.join(User)
    if status:
        query = query.filter(WithdrawalRequest.status == status)
    withdrawals = query.order_by(WithdrawalRequest.created_at.desc()).all()

    return jsonify({
        'status': 'success',
        'data': [{
            'id': w.id,
            'user': {
                'id': w.user.id,
                'name': w.user.name,
                'email': w.user.email
            },
            'amount': w.amount,
            'bank_name': w.bank_name,
            'account_number': w.account_number,
            'account_name': w.account_name,
            'status': w.status,
            'created_at': w.created_at.isoformat(),
            'processed_at': w.processed_at.isoformat() if w.processed_at else None
        } for w in withdrawals]
    }) 
