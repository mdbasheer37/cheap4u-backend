# referral.py — Fixed production version
from flask import Blueprint, request, jsonify
from datetime import datetime
from models import db, User, Referral, Transaction, ReferralTransaction
from flask_jwt_extended import jwt_required, get_jwt_identity

referral_bp = Blueprint('referral', __name__, url_prefix='/api/referral')


def _get_user_by_email_or_jwt():
    """
    Try JWT first, fall back to email query param.
    Returns (user, error_response) tuple.
    """
    # Try JWT token
    try:
        from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity
        verify_jwt_in_request(optional=True)
        user_id = get_jwt_identity()
        if user_id:
            user = User.query.get(int(user_id))
            if user:
                return user, None
    except Exception:
        pass

    # Fall back to email param (legacy Kivy app calls)
    email = request.args.get('email') or (request.get_json() or {}).get('user_email')
    if email:
        user = User.query.filter_by(email=email.lower()).first()
        if user:
            return user, None
        return None, (jsonify({'status': 'error', 'message': 'User not found'}), 404)

    return None, (jsonify({'status': 'error', 'message': 'Authentication required'}), 401)


# ── GET /api/referral/info ────────────────────────────────────────────────
@referral_bp.route('/info', methods=['GET'])
def get_referral_info():
    """Return user's referral balance, code, link and stats."""
    user, err = _get_user_by_email_or_jwt()
    if err:
        return err

    # Count referrals — use User.referred_by_user_id (reliable)
    total_referrals = User.query.filter_by(referred_by_user_id=user.id).count()

    # Pending = referred users who haven't funded wallet yet
    pending_referrals = User.query.filter_by(
        referred_by_user_id=user.id,
        referral_bonus_claimed=False
    ).count()

    can_use_bonus = user.referral_balance >= 200
    next_threshold = max(0, 200 - user.referral_balance)

    return jsonify({
        'status': 'success',
        'data': {
            'referral_balance':      round(user.referral_balance, 2),
            'referral_earnings':     round(user.referral_earnings, 2),
            'total_referrals':       total_referrals,
            'pending_referrals_count': pending_referrals,
            'referral_code':         user.referral_code,
            'referral_link':         f"https://cheap4u.technology/register?ref={user.referral_code}",
            'can_use_bonus':         can_use_bonus,
            'next_bonus_threshold':  round(next_threshold, 2),
        }
    })


# ── POST /api/referral/process-first-transaction ─────────────────────────
@referral_bp.route('/process-first-transaction', methods=['POST'])
def process_first_transaction():
    """
    Award ₦50 signup bonus to referrer when a referred user
    completes their first successful wallet funding.
    Called automatically by the webhook — idempotent.
    """
    data = request.get_json() or {}
    user_email = data.get('user_email', '').lower()

    if not user_email:
        return jsonify({'status': 'error', 'message': 'user_email required'}), 400

    user = User.query.filter_by(email=user_email).first()
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    # Idempotency — already claimed
    if user.referral_bonus_claimed:
        return jsonify({'status': 'success', 'message': 'Bonus already claimed'})

    if not user.referred_by_user_id:
        return jsonify({'status': 'success', 'message': 'No referrer for this user'})

    # Check this is first successful transaction
    success_count = Transaction.query.filter_by(
        user_id=user.id,
        status='success'
    ).count()

    if success_count < 1:
        return jsonify({'status': 'success', 'message': 'No successful transactions yet'})

    referrer = User.query.get(user.referred_by_user_id)
    if not referrer:
        return jsonify({'status': 'success', 'message': 'Referrer not found'})

    bonus_amount = 50.0
    referrer.referral_balance  = round(referrer.referral_balance + bonus_amount, 2)
    referrer.referral_earnings = round(referrer.referral_earnings + bonus_amount, 2)
    user.referral_bonus_claimed = True

    # Update Referral record if it exists
    referral = Referral.query.filter_by(
        referrer_id=referrer.id,
        referred_id=user.id
    ).first()
    if referral:
        referral.bonus_paid = True
        referral.status = 'completed'

    # Log the bonus transaction
    db.session.add(ReferralTransaction(
        referrer_id=referrer.id,
        referred_user_id=user.id,
        amount=bonus_amount,
        type='signup_bonus',
    ))

    db.session.commit()

    return jsonify({
        'status': 'success',
        'message': f'Referral bonus of ₦{bonus_amount:,.2f} awarded to {referrer.email}',
        'data': {'bonus_amount': bonus_amount}
    })


# ── POST /api/referral/use-bonus ─────────────────────────────────────────
@referral_bp.route('/use-bonus', methods=['POST'])
def use_referral_bonus():
    """Transfer referral balance → wallet balance."""
    data = request.get_json() or {}
    user_email = data.get('user_email', '').lower()
    amount = data.get('amount')

    if not user_email or not amount:
        return jsonify({'status': 'error', 'message': 'user_email and amount required'}), 400

    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'Invalid amount'}), 400

    if amount < 200:
        return jsonify({'status': 'error', 'message': 'Minimum bonus usage is ₦200'}), 400

    user = User.query.filter_by(email=user_email).first()
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    if user.referral_balance < amount:
        return jsonify({
            'status': 'error',
            'message': f'Insufficient referral balance. Available: ₦{user.referral_balance:,.2f}'
        }), 400

    user.referral_balance = round(user.referral_balance - amount, 2)
    user.wallet_balance   = round(user.wallet_balance + amount, 2)

    txn = Transaction(
        user_id      = user.id,
        reference    = f"BONUS_{datetime.utcnow().timestamp()}_{user.id}",
        type         = 'wallet_funding',
        service_type = 'referral_bonus',
        amount       = amount,
        status       = 'success',
        details      = {'source': 'referral_bonus'},
    )
    db.session.add(txn)
    db.session.commit()

    return jsonify({
        'status': 'success',
        'message': f'₦{amount:,.2f} moved from referral bonus to wallet',
        'data': {
            'wallet_balance':   round(user.wallet_balance, 2),
            'referral_balance': round(user.referral_balance, 2),
        }
    })


# ── GET /api/referral/stats ───────────────────────────────────────────────
@referral_bp.route('/stats', methods=['GET'])
@jwt_required()
def referral_stats():
    """Detailed referral stats for logged-in user."""
    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    total   = User.query.filter_by(referred_by_user_id=user.id).count()
    pending = User.query.filter_by(referred_by_user_id=user.id, referral_bonus_claimed=False).count()
    paid    = total - pending

    return jsonify({
        'status': 'success',
        'data': {
            'total_referrals':    total,
            'pending_referrals':  pending,
            'completed_referrals': paid,
            'total_earnings':     round(user.referral_earnings, 2),
            'referral_balance':   round(user.referral_balance, 2),
            'referral_code':      user.referral_code,
            'referral_link':      f"https://cheap4u.technology/register?ref={user.referral_code}",
        }
    })


# ── GET /api/referral/history ─────────────────────────────────────────────
@referral_bp.route('/history', methods=['GET'])
@jwt_required()
def referral_history():
    """List all referral bonus/commission transactions for the logged-in user."""
    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    txns = (ReferralTransaction.query
            .filter_by(referrer_id=user_id)
            .order_by(ReferralTransaction.created_at.desc())
            .all())

    history = []
    for tx in txns:
        referred_user = User.query.get(tx.referred_user_id)
        history.append({
            'id':                  tx.id,
            'amount':              round(tx.amount, 2),
            'type':                tx.type,
            'referred_user_name':  referred_user.name if referred_user else 'Unknown',
            'created_at':          tx.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        })

    return jsonify({'status': 'success', 'data': history})


# ── GET /api/referral/referred-users ─────────────────────────────────────
@referral_bp.route('/referred-users', methods=['GET'])
@jwt_required()
def referred_users():
    """List all users referred by the logged-in user."""
    user_id = int(get_jwt_identity())
    users = User.query.filter_by(referred_by_user_id=user_id).all()

    return jsonify({
        'status': 'success',
        'data': [{
            'name':          u.name,
            'joined_date':   u.created_at.strftime('%Y-%m-%d'),
            'bonus_claimed': u.referral_bonus_claimed,
            'status':        'completed' if u.referral_bonus_claimed else 'pending',
        } for u in users]
    })
