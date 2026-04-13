from flask import current_app
from flask import Blueprint, request, jsonify
from datetime import datetime, timedelta
from models import db, User, Transaction, Profit, WithdrawalRequest
from sqlalchemy import func
from flask_jwt_extended import jwt_required, get_jwt_identity

admin_bp = Blueprint('admin', __name__, url_prefix='/api/admin')

def is_admin(user_id):
    """Check if user is admin"""
    user = User.query.get(user_id)
    if not user:
        return False
    return user.email in current_app.config['ADMIN_EMAILS']

@admin_bp.route('/profit', methods=['GET'])
def get_profit():
    """Get admin profit data"""
    user_email = request.args.get('user_email')
    
    if not user_email:
        return jsonify({'status': 'error', 'message': 'User email required'}), 400
    
    user = User.query.filter_by(email=user_email).first()
    
    if not user or user.email not in current_app.config['ADMIN_EMAILS']:
        return jsonify({'status': 'error', 'message': 'Admin access required'}), 403
    
    # Get total profit
    total_profit = db.session.query(func.sum(Profit.amount)).scalar() or 0
    
    # Get profit by category
    profit_by_category = db.session.query(
        Profit.category,
        func.sum(Profit.amount).label('total'),
        func.count(Profit.id).label('count')
    ).group_by(Profit.category).all()
    
    # Format category data
    categories = {}
    for cat in profit_by_category:
        categories[cat.category] = {
            'amount': float(cat.total),
            'count': cat.count
        }
    
    return jsonify({
        'status': 'success',
        'data': {
            'total_available': float(total_profit),
            'total_earned': float(total_profit),
            'by_category': categories
        }
    })

@admin_bp.route('/profit/withdraw', methods=['POST'])
def withdraw_profit():
    """Process profit withdrawal"""
    data = request.get_json()
    
    user_email = data.get('user_email')
    amount = data.get('amount')
    bank_details = data.get('bank_details', {})
    
    if not user_email or not amount:
        return jsonify({'status': 'error', 'message': 'Email and amount required'}), 400
    
    user = User.query.filter_by(email=user_email).first()
    
    if not user or user.email not in current_app.config['ADMIN_EMAILS']:
        return jsonify({'status': 'error', 'message': 'Admin access required'}), 403
    
    # Check if enough profit available
    total_profit = db.session.query(func.sum(Profit.amount)).scalar() or 0
    
    if amount > total_profit:
        return jsonify({'status': 'error', 'message': 'Insufficient profit balance'}), 400
    
    if amount < 1000:
        return jsonify({'status': 'error', 'message': 'Minimum withdrawal is ₦1,000'}), 400
    
    # Create withdrawal request
    withdrawal = WithdrawalRequest(
        user_id=user.id,
        amount=amount,
        bank_name=bank_details.get('bank_name'),
        account_number=bank_details.get('account_number'),
        account_name=bank_details.get('account_name'),
        status='pending'
    )
    db.session.add(withdrawal)
    db.session.commit()
    
    # Record transaction
    transaction = Transaction(
        user_id=user.id,
        reference=f"WDL_{datetime.utcnow().timestamp()}",
        type='withdrawal',
        service_type='profit_withdrawal',
        amount=amount,
        status='pending',
        details={'withdrawal_id': withdrawal.id}
    )
    db.session.add(transaction)
    db.session.commit()
    
    return jsonify({
        'status': 'success',
        'message': 'Withdrawal request submitted successfully',
        'data': {'withdrawal_id': withdrawal.id}
    })

@admin_bp.route('/withdrawals', methods=['GET'])
def get_withdrawals():
    """Get withdrawal history"""
    user_email = request.args.get('user_email')
    
    if not user_email:
        return jsonify({'status': 'error', 'message': 'User email required'}), 400
    
    user = User.query.filter_by(email=user_email).first()
    
    if not user or user.email not in current_app.config['ADMIN_EMAILS']:
        return jsonify({'status': 'error', 'message': 'Admin access required'}), 403
    
    withdrawals = WithdrawalRequest.query.filter_by(user_id=user.id).order_by(
        WithdrawalRequest.created_at.desc()
    ).all()
    
    return jsonify({
        'status': 'success',
        'data': [{
            'id': w.id,
            'amount': w.amount,
            'status': w.status,
            'bank_name': w.bank_name,
            'account_number': w.account_number,
            'account_name': w.account_name,
            'created_at': w.created_at.strftime('%Y-%m-%d %H:%M:%S')
        } for w in withdrawals]
    }) 
