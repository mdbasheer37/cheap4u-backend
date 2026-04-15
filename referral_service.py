# referral_service.py (new file)

from models import db, User, ReferralTransaction

def award_referral_commission(user, transaction_amount):
    """Award commission to referrer for a transaction made by referred user."""
    if not user or not user.referred_by_user_id:
        return
    
    commission_rate = 0.02  # 2%
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
        # Note: commit should be handled by the calling function 
