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

