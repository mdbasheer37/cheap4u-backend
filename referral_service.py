# referral_service.py
# Utility functions for awarding referral commissions.
# The webhook logic lives entirely in payment.py — do NOT define routes here.

from models import db, User, ReferralTransaction


def award_referral_commission(user, transaction_amount):
    """
    Award 2% commission to referrer for a transaction made by referred user.
    Call this BEFORE the final db.session.commit() in the calling function.
    """
    if not user or not user.referred_by_user_id:
        return

    commission_rate = 0.02
    commission = transaction_amount * commission_rate
    referrer = User.query.get(user.referred_by_user_id)
    if referrer:
        referrer.referral_earnings += commission
        ref_tx = ReferralTransaction(
            referrer_id=referrer.id,
            referred_user_id=user.id,
            amount=commission,
            type='commission'
        )
        db.session.add(ref_tx)
        # NOTE: commit is handled by the calling function

@payment_bp.route('/webhook/paystack', methods=['POST'])
def paystack_webhook():
    data = request.get_json()
    if data.get('event') == 'charge.success':
        reference = data['data']['reference']
        transaction = Transaction.query.filter_by(reference=reference).first()
        if transaction and transaction.status != 'success':
            transaction.status = 'success'
            if transaction.type == 'wallet_funding' and transaction.user_id:
                user = User.query.get(transaction.user_id)
                if user:
                    # Check if this is the user's FIRST successful funding
                    first_funding = not Transaction.query.filter(
                        Transaction.user_id == user.id,
                        Transaction.type == 'wallet_funding',
                        Transaction.status == 'success'
                    ).first()  # this query will return the current transaction as well? Need careful check.

                    # Better: check if user.wallet_balance was 0 before? Or add a flag 'has_received_referral_bonus' on User.
                    # We'll add a flag to User model: referral_bonus_claimed (boolean, default False)
                    
                    if not user.referral_bonus_claimed and user.referred_by_user_id:
                        # Grant signup bonus
                        referrer = User.query.get(user.referred_by_user_id)
                        if referrer:
                            bonus_amount = 10.0  # configurable
                            referrer.referral_earnings += bonus_amount
                            user.referral_bonus_claimed = True
                            
                            # Log the bonus transaction
                            ref_tx = ReferralTransaction(
                                referrer_id=referrer.id,
                                referred_user_id=user.id,
                                amount=bonus_amount,
                                type='signup_bonus'
                            )
                            db.session.add(ref_tx)
                    
                    user.wallet_balance += transaction.amount
            db.session.commit()
    return jsonify({'status': 'success'}), 200 
 
