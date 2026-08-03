# coupon_models.py — Coupon / Promo Code System data models
#
# Uses the SAME `db` SQLAlchemy instance as models.py so these tables are
# created automatically by the existing `db.create_all()` call in app.py.

from datetime import datetime
from models import db


class Coupon(db.Model):
    """One promo code. Admin-managed end to end (create/edit/delete/toggle)
    — no code changes needed to run a promotion."""
    __tablename__ = 'coupons'

    id                     = db.Column(db.Integer, primary_key=True)
    code                   = db.Column(db.String(30), unique=True, nullable=False, index=True)

    # 'fixed'   → discount_value is a naira amount
    # 'percentage' → discount_value is 0-100, optionally capped by max_discount_amount
    discount_type          = db.Column(db.String(12), nullable=False, default='fixed')
    discount_value         = db.Column(db.Float, nullable=False, default=0.0)
    max_discount_amount    = db.Column(db.Float, nullable=True)   # cap for percentage coupons

    min_transaction_amount = db.Column(db.Float, default=0.0)     # purchase must be at least this

    # Comma-separated purchase categories this coupon applies to
    # (airtime,data,electricity,cable_tv,exam_pin). Empty/null = all categories.
    applicable_categories  = db.Column(db.String(200), nullable=True)

    usage_limit_total      = db.Column(db.Integer, nullable=True)   # None = unlimited
    usage_limit_per_user    = db.Column(db.Integer, default=1)      # 0/None = unlimited per user
    total_used              = db.Column(db.Integer, default=0)

    # ── restrictions ──
    new_users_only          = db.Column(db.Boolean, default=False)   # never made a successful purchase
    specific_user_id        = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    is_referral_coupon      = db.Column(db.Boolean, default=False)   # issued via the referral program

    is_active                = db.Column(db.Boolean, default=True)
    starts_at                = db.Column(db.DateTime, nullable=True)
    expires_at                = db.Column(db.DateTime, nullable=True)

    note                      = db.Column(db.String(255), nullable=True)   # internal admin note
    created_by_admin_id      = db.Column(db.Integer, nullable=True)

    created_at                = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at                = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id':                      self.id,
            'code':                    self.code,
            'discount_type':           self.discount_type,
            'discount_value':          self.discount_value,
            'max_discount_amount':     self.max_discount_amount,
            'min_transaction_amount':  self.min_transaction_amount,
            'applicable_categories':   (self.applicable_categories.split(',')
                                         if self.applicable_categories else []),
            'usage_limit_total':       self.usage_limit_total,
            'usage_limit_per_user':    self.usage_limit_per_user,
            'total_used':              self.total_used,
            'new_users_only':          self.new_users_only,
            'specific_user_id':        self.specific_user_id,
            'is_referral_coupon':      self.is_referral_coupon,
            'is_active':               self.is_active,
            'starts_at':               self.starts_at.isoformat() if self.starts_at else None,
            'expires_at':              self.expires_at.isoformat() if self.expires_at else None,
            'note':                    self.note,
            'created_at':              self.created_at.isoformat() if self.created_at else None,
        }


class CouponRedemption(db.Model):
    """Ledger of every time a coupon was actually applied to a purchase."""
    __tablename__ = 'coupon_redemptions'

    id              = db.Column(db.Integer, primary_key=True)
    coupon_id       = db.Column(db.Integer, db.ForeignKey('coupons.id'), nullable=False, index=True)
    user_id         = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    transaction_id  = db.Column(db.Integer, db.ForeignKey('transactions.id'), nullable=True)

    discount_amount = db.Column(db.Float, nullable=False)
    category        = db.Column(db.String(30), nullable=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    coupon = db.relationship('Coupon')
    user   = db.relationship('User')

    def to_dict(self):
        return {
            'id':               self.id,
            'coupon_code':      self.coupon.code if self.coupon else None,
            'user_id':          self.user_id,
            'discount_amount':  round(self.discount_amount, 2),
            'category':         self.category,
            'created_at':       self.created_at.isoformat() if self.created_at else None,
        }
