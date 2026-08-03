# cashback_models.py — Cashback System data models
#
# Uses the SAME `db` SQLAlchemy instance as models.py so these tables are
# created automatically by the existing `db.create_all()` call in app.py.
# No changes to models.py are required — just import this module once
# (done in cashback.py) so SQLAlchemy registers the tables before
# create_all() runs. Mirrors the pattern used by challenge_models.py.

from datetime import datetime
from models import db


class CashbackConfig(db.Model):
    """
    Singleton settings row (id is always 1) controlling the whole
    Cashback System. Editable from the admin panel.
    """
    __tablename__ = 'cashback_config'

    id                            = db.Column(db.Integer, primary_key=True)
    is_enabled                    = db.Column(db.Boolean, default=True)

    # Cashback percentage per purchase category — independently configurable
    # by admin so promotions can be run per-service (e.g. boost data cashback
    # during a promo week without touching airtime).
    percent_airtime               = db.Column(db.Float, default=1.0)
    percent_data                  = db.Column(db.Float, default=1.0)
    percent_electricity           = db.Column(db.Float, default=0.5)
    percent_cable_tv              = db.Column(db.Float, default=0.5)
    percent_exam_pin              = db.Column(db.Float, default=0.5)

    # A purchase must be at least this much to earn any cashback (0 = no floor)
    min_transaction_amount        = db.Column(db.Float, default=0.0)
    # Cap on cashback earned from a single transaction (None/0 = uncapped)
    max_cashback_per_transaction  = db.Column(db.Float, nullable=True)

    # How many days after being earned a cashback amount expires.
    # None = cashback never expires.
    expiry_days                   = db.Column(db.Integer, nullable=True, default=90)

    # Minimum amount a user must redeem at once into their main wallet
    # (redeeming the full remaining balance is always allowed regardless).
    min_redeem_amount             = db.Column(db.Float, default=100.0)

    updated_at                    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'is_enabled':                   self.is_enabled,
            'percent_airtime':               self.percent_airtime,
            'percent_data':                  self.percent_data,
            'percent_electricity':           self.percent_electricity,
            'percent_cable_tv':              self.percent_cable_tv,
            'percent_exam_pin':              self.percent_exam_pin,
            'min_transaction_amount':        self.min_transaction_amount,
            'max_cashback_per_transaction':  self.max_cashback_per_transaction,
            'expiry_days':                   self.expiry_days,
            'min_redeem_amount':             self.min_redeem_amount,
        }


class CashbackWallet(db.Model):
    """
    One row per user — the running cashback balance. Kept separate from
    User.wallet_balance (the spendable naira wallet) so cashback has its
    own visible balance, history and expiry rules, and redeeming moves
    money from here into the main wallet on request.
    """
    __tablename__ = 'cashback_wallets'

    id             = db.Column(db.Integer, primary_key=True)
    user_id        = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True, index=True)

    balance        = db.Column(db.Float, default=0.0)   # currently available, unexpired
    total_earned   = db.Column(db.Float, default=0.0)   # lifetime, never decreases
    total_redeemed = db.Column(db.Float, default=0.0)   # lifetime, never decreases
    total_expired  = db.Column(db.Float, default=0.0)   # lifetime, never decreases

    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at     = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User')

    def to_dict(self):
        return {
            'balance':        round(self.balance or 0, 2),
            'total_earned':   round(self.total_earned or 0, 2),
            'total_redeemed': round(self.total_redeemed or 0, 2),
            'total_expired':  round(self.total_expired or 0, 2),
        }


class CashbackEntry(db.Model):
    """
    Full ledger of every cashback event: earned, redeemed, expired, or an
    admin adjustment. `remaining_amount` is only meaningful on 'earned' and
    'admin_credit' rows — it starts equal to `amount` and is drawn down
    (oldest-first / FIFO) as the user redeems or as the lot expires. This
    keeps expiry accurate per earned lot instead of guessing at the whole
    wallet balance.
    """
    __tablename__ = 'cashback_entries'

    id                = db.Column(db.Integer, primary_key=True)
    user_id           = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    transaction_id    = db.Column(db.Integer, db.ForeignKey('transactions.id'), nullable=True, index=True)

    # 'earned' | 'redeemed' | 'expired' | 'admin_credit' | 'admin_debit'
    type              = db.Column(db.String(20), nullable=False, index=True)
    # purchase category this cashback came from: airtime/data/electricity/cable_tv/exam_pin
    category          = db.Column(db.String(30), nullable=True)

    amount            = db.Column(db.Float, nullable=False)          # always positive
    remaining_amount  = db.Column(db.Float, nullable=True)           # only for 'earned'/'admin_credit'
    balance_after     = db.Column(db.Float, default=0.0)

    source_amount     = db.Column(db.Float, nullable=True)           # original purchase amount (earned rows)
    percent_applied   = db.Column(db.Float, nullable=True)           # % used to compute this earn

    expires_at        = db.Column(db.DateTime, nullable=True, index=True)
    is_expired        = db.Column(db.Boolean, default=False, index=True)

    note              = db.Column(db.String(255), nullable=True)
    created_at        = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    user = db.relationship('User')

    def to_dict(self):
        return {
            'id':               self.id,
            'type':             self.type,
            'category':         self.category,
            'amount':           round(self.amount or 0, 2),
            'source_amount':    round(self.source_amount, 2) if self.source_amount is not None else None,
            'percent_applied':  self.percent_applied,
            'balance_after':    round(self.balance_after or 0, 2),
            'expires_at':       self.expires_at.isoformat() if self.expires_at else None,
            'is_expired':       self.is_expired,
            'note':             self.note,
            'created_at':       self.created_at.isoformat() if self.created_at else None,
        }
