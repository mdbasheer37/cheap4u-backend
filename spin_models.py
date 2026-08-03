# spin_models.py — Spin & Win data models
#
# Uses the SAME `db` SQLAlchemy instance as models.py so these tables are
# created automatically by the existing `db.create_all()` call in app.py.
# Mirrors the pattern used by challenge_models.py / cashback_models.py.

from datetime import datetime
from models import db


class SpinConfig(db.Model):
    """Singleton settings row (id is always 1) controlling the whole
    Spin & Win feature. Editable from the admin panel."""
    __tablename__ = 'spin_config'

    id                    = db.Column(db.Integer, primary_key=True)
    is_enabled            = db.Column(db.Boolean, default=True)

    # How many spins are free per calendar day (UTC).
    free_spins_per_day    = db.Column(db.Integer, default=1)

    # Hard ceiling on total spins (free + paid) a single user can do in one
    # day, regardless of wallet balance — the primary anti-cheat control.
    max_spins_per_day     = db.Column(db.Integer, default=3)

    # Naira cost to buy one spin beyond the free daily allowance.
    # 0 = buying extra spins is disabled (only free spins allowed).
    extra_spin_cost       = db.Column(db.Float, default=50.0)

    updated_at            = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'is_enabled':         self.is_enabled,
            'free_spins_per_day': self.free_spins_per_day,
            'max_spins_per_day':  self.max_spins_per_day,
            'extra_spin_cost':    self.extra_spin_cost,
        }


class SpinSegment(db.Model):
    """One slice of the wheel = one possible reward. Fully admin-managed
    (create/edit/delete/enable/disable) so promotions can be run without a
    code change. `weight` sets relative odds — a segment with weight=20
    is twice as likely to be picked as one with weight=10."""
    __tablename__ = 'spin_segments'

    id             = db.Column(db.Integer, primary_key=True)
    label          = db.Column(db.String(60), nullable=False)
    # 'airtime' | 'data' | 'cashback' | 'wallet_balance' | 'coupon' | 'bonus_points'
    reward_type    = db.Column(db.String(20), nullable=False)
    # Meaning depends on reward_type: naira amount (airtime/data/wallet_balance/
    # cashback/coupon discount) or point count (bonus_points).
    reward_value   = db.Column(db.Float, nullable=False, default=0.0)

    weight         = db.Column(db.Integer, default=10)     # relative odds
    is_active      = db.Column(db.Boolean, default=True)
    color          = db.Column(db.String(9), default='#2196F3')   # hex, for the wheel UI
    display_order  = db.Column(db.Integer, default=0)

    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at     = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id':             self.id,
            'label':          self.label,
            'reward_type':    self.reward_type,
            'reward_value':   self.reward_value,
            'weight':         self.weight,
            'is_active':      self.is_active,
            'color':          self.color,
            'display_order':  self.display_order,
        }


class SpinDailyCounter(db.Model):
    """One row per user per calendar day — tracks how many free/paid spins
    have been consumed. Locked with SELECT ... FOR UPDATE when spinning so
    two rapid-fire requests (double-tap, scripted abuse) can't both slip
    through before the count is incremented — the core anti-cheat guard."""
    __tablename__ = 'spin_daily_counters'

    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    spin_date   = db.Column(db.Date, nullable=False)
    free_used   = db.Column(db.Integer, default=0)
    paid_used   = db.Column(db.Integer, default=0)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'spin_date', name='uq_spin_counter_user_date'),
    )


class SpinEntry(db.Model):
    """Permanent history of every spin — what was won, whether it was free
    or paid, and a snapshot of the segment at spin-time (so history stays
    accurate even if the admin edits/removes that segment later)."""
    __tablename__ = 'spin_entries'

    id             = db.Column(db.Integer, primary_key=True)
    user_id        = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    segment_id     = db.Column(db.Integer, db.ForeignKey('spin_segments.id'), nullable=True)

    label          = db.Column(db.String(60))
    reward_type    = db.Column(db.String(20))
    reward_value   = db.Column(db.Float, default=0.0)

    is_free_spin   = db.Column(db.Boolean, default=True)
    cost_paid      = db.Column(db.Float, default=0.0)

    ip_address     = db.Column(db.String(64), nullable=True)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    user = db.relationship('User')

    def to_dict(self):
        return {
            'id':            self.id,
            'label':         self.label,
            'reward_type':   self.reward_type,
            'reward_value':  round(self.reward_value or 0, 2),
            'is_free_spin':  self.is_free_spin,
            'cost_paid':     round(self.cost_paid or 0, 2),
            'created_at':    self.created_at.isoformat() if self.created_at else None,
        }


class UserPoints(db.Model):
    """Lightweight bonus-points wallet. Deliberately generic (not spin-
    specific) so the upcoming full Gamification/XP system can adopt this
    same table instead of starting a second, competing points ledger."""
    __tablename__ = 'user_points'

    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True)
    balance     = db.Column(db.Integer, default=0)
    updated_at  = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {'points_balance': self.balance or 0}


class SpinCouponAward(db.Model):
    """A single-use fixed-discount coupon personally awarded to one user by
    the wheel. Kept in its own table (rather than bolted onto a shared
    'coupons' table) so it doesn't collide with the standalone Coupon
    System feature when that's built — the two can be merged later without
    a data migration risk today."""
    __tablename__ = 'spin_coupon_awards'

    id             = db.Column(db.Integer, primary_key=True)
    user_id        = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    code           = db.Column(db.String(20), unique=True, nullable=False)
    discount_amount = db.Column(db.Float, nullable=False)
    is_used        = db.Column(db.Boolean, default=False)
    expires_at     = db.Column(db.DateTime, nullable=True)
    used_at        = db.Column(db.DateTime, nullable=True)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'code':            self.code,
            'discount_amount': round(self.discount_amount, 2),
            'is_used':         self.is_used,
            'expires_at':      self.expires_at.isoformat() if self.expires_at else None,
            'created_at':      self.created_at.isoformat() if self.created_at else None,
        }
