# card_models.py — Virtual Dollar Card data models
#
# Uses the SAME `db` SQLAlchemy instance as models.py so these tables are
# created automatically by the existing `db.create_all()` call in app.py.
#
# IMPORTANT: no real card issuer is wired up here (see card_provider.py for
# why and how to plug one in). Only masked card data is ever stored — full
# PAN/CVV never touch this database, exactly as they wouldn't with a real
# provider either (the provider's hosted vault holds that, not us).

from datetime import datetime
from models import db


class CardConfig(db.Model):
    """Singleton settings row (id is always 1) controlling the whole
    Virtual Dollar Card feature. Editable from the admin panel."""
    __tablename__ = 'card_config'

    id                    = db.Column(db.Integer, primary_key=True)
    is_enabled            = db.Column(db.Boolean, default=True)
    provider_name         = db.Column(db.String(30), default='mock')   # see card_provider.PROVIDERS

    usd_to_ngn_rate        = db.Column(db.Float, default=1600.0)        # admin-adjustable FX rate
    card_creation_fee_usd  = db.Column(db.Float, default=2.0)           # one-time issuance fee
    min_funding_usd        = db.Column(db.Float, default=5.0)
    max_card_balance_usd   = db.Column(db.Float, default=2000.0)

    updated_at              = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'is_enabled':            self.is_enabled,
            'provider_name':         self.provider_name,
            'usd_to_ngn_rate':       self.usd_to_ngn_rate,
            'card_creation_fee_usd': self.card_creation_fee_usd,
            'min_funding_usd':       self.min_funding_usd,
            'max_card_balance_usd':  self.max_card_balance_usd,
        }


class VirtualCard(db.Model):
    """One virtual dollar card. `provider_card_id` is the issuer's own
    reference for this card (opaque to us) so any provider-specific lookups
    happen through card_provider.py, never by us guessing at their ID
    format here."""
    __tablename__ = 'virtual_cards'

    id                 = db.Column(db.Integer, primary_key=True)
    user_id            = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)

    provider            = db.Column(db.String(30), nullable=False)
    provider_card_id    = db.Column(db.String(100), nullable=True, index=True)

    card_number_masked  = db.Column(db.String(25))     # e.g. "4111 **** **** 1234"
    card_brand          = db.Column(db.String(20), default='Visa')
    expiry_month         = db.Column(db.String(2))
    expiry_year          = db.Column(db.String(4))
    cardholder_name       = db.Column(db.String(100))

    currency             = db.Column(db.String(3), default='USD')
    balance               = db.Column(db.Float, default=0.0)

    # 'active' | 'frozen' | 'terminated'
    status                = db.Column(db.String(15), default='active', index=True)

    created_at             = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at             = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    terminated_at           = db.Column(db.DateTime, nullable=True)

    user = db.relationship('User')

    def to_dict(self, reveal=False):
        """`reveal` is intentionally unused today — no full PAN/CVV is ever
        stored, so there is nothing to reveal even for the owner. Kept as a
        parameter so a future provider integration that supports one-time
        reveal-via-provider-API can slot in without changing every caller."""
        return {
            'id':                 self.id,
            'provider':           self.provider,
            'card_number_masked': self.card_number_masked,
            'card_brand':         self.card_brand,
            'expiry_month':       self.expiry_month,
            'expiry_year':        self.expiry_year,
            'cardholder_name':    self.cardholder_name,
            'currency':           self.currency,
            'balance':            round(self.balance or 0, 2),
            'status':             self.status,
            'created_at':         self.created_at.isoformat() if self.created_at else None,
        }


class CardTransaction(db.Model):
    """Ledger of everything that happened on a card: funding, purchases,
    withdrawals/refunds on deletion. Kept separate from the main
    `transactions` table since these are USD-denominated card events, not
    NGN wallet events — the NGN side of a funding/withdrawal is logged in
    the main Transaction table via card.py so wallet history stays intact."""
    __tablename__ = 'card_transactions'

    id           = db.Column(db.Integer, primary_key=True)
    card_id      = db.Column(db.Integer, db.ForeignKey('virtual_cards.id'), nullable=False, index=True)
    user_id      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)

    # 'funding' | 'purchase' | 'withdrawal' | 'fee'
    type         = db.Column(db.String(15), nullable=False)
    amount       = db.Column(db.Float, nullable=False)     # USD, always positive
    balance_after = db.Column(db.Float, default=0.0)
    description   = db.Column(db.String(255), nullable=True)

    created_at    = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def to_dict(self):
        return {
            'id':             self.id,
            'type':           self.type,
            'amount':         round(self.amount, 2),
            'balance_after':  round(self.balance_after or 0, 2),
            'description':    self.description,
            'created_at':     self.created_at.isoformat() if self.created_at else None,
        }
