from flask import Blueprint, request, jsonify, current_app
from datetime import datetime
from models import db, User, Referral, Transaction
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import func
from models import ReferralTransaction

referral_bp = Blueprint('referral', __name__, url_prefix='/api/referral')

@referral_bp.route('/info', methods=['GET'])
def get_referral_info():
    """Get user's referral information"""
    email = request.args.get('email')
    
    if not email:
        return jsonify({'status': 'error', 'message': 'Email required'}), 400
    
    user = User.query.filter_by(email=email).first()
    
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404
    
    # Get total referrals
    total_referrals = Referral.query.filter_by(referrer_id=user.id).count()
    
    # Get pending referrals (those who haven't completed first transaction)
    pending_referrals = Referral.query.filter_by(
        referrer_id=user.id,
        first_transaction_completed=False
    ).count()
    
    # Calculate next bonus threshold
    next_bonus = 200 - user.referral_balance if user.referral_balance < 200 else 0
    
    return jsonify({
        'status': 'success',
        'data': {
            'referral_balance': user.referral_balance,
            'total_referrals': total_referrals,
            'referral_code': user.referral_code,
            'referral_link': f"https://cheap4u.technology/register?ref={user.referral_code}",
            'pending_referrals_count': pending_referrals,
            'can_use_bonus': user.referral_balance >= 200,
            'next_bonus_threshold': next_bonus
        }
    })

@referral_bp.route('/process-first-transaction', methods=['POST'])
def process_first_transaction():
    """Process referral bonus when user completes first transaction"""
    data = request.get_json()
    
    user_email = data.get('user_email')
    amount = data.get('amount', 0)
    
    if not user_email:
        return jsonify({'status': 'error', 'message': 'User email required'}), 400
    
    user = User.query.filter_by(email=user_email).first()
    
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404
    
    # Check if this is first transaction
    transaction_count = Transaction.query.filter_by(
        user_id=user.id,
        status='success'
    ).count()
    
    if transaction_count == 1 and user.referred_by:
        # This is first transaction, process referral bonus
        referrer = User.query.filter_by(referral_code=user.referred_by).first()
        
        if referrer:
            # Add bonus to referrer
            bonus_amount = 50.00
            referrer.referral_balance += bonus_amount
            
            # Update referral record
            referral = Referral.query.filter_by(
                referrer_id=referrer.id,
                referred_id=user.id
            ).first()
            
            if referral:
                referral.first_transaction_completed = True
                referral.bonus_paid = True
            
            db.session.commit()
            
            return jsonify({
                'status': 'success',
                'message': f'Referral bonus of ₦{bonus_amount} added'
            })
    
    return jsonify({'status': 'success', 'message': 'No referral bonus to process'})

@referral_bp.route('/use-bonus', methods=['POST'])
def use_referral_bonus():
    """Use referral bonus to fund wallet"""
    data = request.get_json()
    
    user_email = data.get('user_email')
    amount = data.get('amount')
    
    if not user_email or not amount:
        return jsonify({'status': 'error', 'message': 'Email and amount required'}), 400
    
    user = User.query.filter_by(email=user_email).first()
    
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404
    
    if user.referral_balance < amount:
        return jsonify({'status': 'error', 'message': 'Insufficient referral balance'}), 400
    
    if amount < 200:
        return jsonify({'status': 'error', 'message': 'Minimum bonus usage is ₦200'}), 400
    
    # Transfer from referral balance to wallet
    user.referral_balance -= amount
    user.wallet_balance += amount
    
    # Record transaction
    transaction = Transaction(
        user_id=user.id,
        reference=f"BONUS_{datetime.utcnow().timestamp()}",
        type='wallet_funding',
        service_type='referral_bonus',
        amount=amount,
        status='success',
        details={'source': 'referral_bonus'}
    )
    db.session.add(transaction)
    db.session.commit()
    
    return jsonify({
        'status': 'success',
        'message': f'₦{amount:,.2f} added to wallet from referral bonus',
        'data': {
            'wallet_balance': user.wallet_balance,
            'referral_balance': user.referral_balance
        }
    }) 

# referral.py (additions)

@referral_bp.route('/stats', methods=['GET'])
@jwt_required()
def referral_stats():
    """Get referral statistics for the logged-in user."""
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    return jsonify({
        'status': 'success',
        'data': {
            'total_referrals': user.total_referrals,
            'total_earnings': user.referral_earnings,
            'referral_code': user.referral_code,
            'referral_link': f"https://cheap4u.technology/register?ref={user.referral_code}"
        }
    })

@referral_bp.route('/history', methods=['GET'])
@jwt_required()
def referral_history():
    """Get list of all referral transactions (bonus/commission) for the user."""
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    transactions = ReferralTransaction.query.filter_by(referrer_id=user_id)\
                     .order_by(ReferralTransaction.created_at.desc()).all()

    history = []
    for tx in transactions:
        referred_user = User.query.get(tx.referred_user_id)
        history.append({
            'id': tx.id,
            'amount': tx.amount,
            'type': tx.type,
            'referred_user_name': referred_user.name if referred_user else 'Unknown',
            'created_at': tx.created_at.strftime('%Y-%m-%d %H:%M:%S')
        })

    return jsonify({'status': 'success', 'data': history}) 
