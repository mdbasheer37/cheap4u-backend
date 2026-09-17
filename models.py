# models.py — Cheap4U SQLAlchemy models
# Changes from original:
#   1. DataPlan.plan_id:  added unique=True (prevents duplicate plan IDs)
#   2. CablePlan.plan_id: added unique=True (prevents duplicate plan IDs)
#   3. ElectricityProvider.provider_id: added unique=True
#   4. Transaction.to_dict: added created_at ISO field (used by frontend history sort)
# Everything else is identical to your original.

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.ext.mutable import MutableDict
from datetime import datetime, timedelta
import bcrypt

db = SQLAlchemy()

# PIN brute-force protection: after this many wrong attempts in a row,
# the PIN is locked for this many minutes. Shared by login PIN and
# transaction PIN (tracked separately per user).
PIN_MAX_ATTEMPTS    = 5
PIN_LOCKOUT_MINUTES = 5


class User(db.Model):
    __tablename__ = 'users'
    id                      = db.Column(db.Integer, primary_key=True)
    name                    = db.Column(db.String(100), nullable=False)
    email                   = db.Column(db.String(100), unique=True, nullable=False)
    phone                   = db.Column(db.String(20), unique=True, nullable=False)
    password_hash           = db.Column(db.String(200), nullable=False)
    role                    = db.Column(db.String(20), default='user')
    is_active               = db.Column(db.Boolean, default=True)
    is_verified             = db.Column(db.Boolean, default=False)
    is_premium              = db.Column(db.Boolean, default=False)
    wallet_balance          = db.Column(db.Float, default=0.0)
    referral_code           = db.Column(db.String(20), unique=True)
    referred_by             = db.Column(db.String(20), nullable=True)
    referred_by_user_id     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    referral_balance        = db.Column(db.Float, default=0.0)
    referral_earnings       = db.Column(db.Float, default=0.0)
    total_referrals         = db.Column(db.Integer, default=0)
    referral_bonus_claimed  = db.Column(db.Boolean, default=False)
    paystack_customer_code  = db.Column(db.String(100), nullable=True)
    virtual_account_number  = db.Column(db.String(20), nullable=True)
    virtual_bank_name       = db.Column(db.String(100), nullable=True)
    virtual_account_name    = db.Column(db.String(100), nullable=True)
    transaction_pin_hash    = db.Column(db.String(200), nullable=True)

    # ── Login PIN (server-side — NEW) ──────────────────────────────
    # Previously the "quick PIN" login was stored ONLY on-device
    # (quick_pin.json in the app's local storage), so it vanished on
    # reinstall / new phone / cleared app data. These columns make the
    # backend the source of truth, exactly like transaction_pin_hash
    # already was.
    login_pin_hash                  = db.Column(db.String(200), nullable=True)
    login_pin_set_at                = db.Column(db.DateTime, nullable=True)
    login_pin_failed_attempts       = db.Column(db.Integer, default=0, nullable=False)
    login_pin_locked_until          = db.Column(db.DateTime, nullable=True)

    # ── Transaction PIN — added lockout/audit fields (hash already existed) ──
    transaction_pin_set_at          = db.Column(db.DateTime, nullable=True)
    transaction_pin_failed_attempts = db.Column(db.Integer, default=0, nullable=False)
    transaction_pin_locked_until    = db.Column(db.DateTime, nullable=True)

    created_at              = db.Column(db.DateTime, default=datetime.utcnow)
    last_login              = db.Column(db.DateTime, default=datetime.utcnow)

    referrer = db.relationship('User', remote_side=[id], backref='referred_users')

    def set_password(self, password):
        self.password_hash = bcrypt.hashpw(
            password.encode('utf-8'), bcrypt.gensalt()
        ).decode('utf-8')

    def check_password(self, password):
        return bcrypt.checkpw(
            password.encode('utf-8'), self.password_hash.encode('utf-8')
        )

    # ── Login PIN helpers ────────────────────────────────────────────
    def set_login_pin(self, pin):
        self.login_pin_hash = bcrypt.hashpw(pin.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        self.login_pin_set_at = datetime.utcnow()
        self.login_pin_failed_attempts = 0
        self.login_pin_locked_until = None

    def login_pin_lock_remaining(self):
        """Seconds left on an active lockout, or 0 if not locked."""
        if self.login_pin_locked_until and self.login_pin_locked_until > datetime.utcnow():
            return int((self.login_pin_locked_until - datetime.utcnow()).total_seconds())
        return 0

    def check_login_pin(self, pin):
        """
        Verify `pin` against the stored hash, tracking failed attempts and
        lockout. Returns one of: 'ok', 'locked', 'not_set', 'wrong'.
        Caller is responsible for db.session.commit() afterwards.
        """
        if self.login_pin_locked_until and self.login_pin_locked_until > datetime.utcnow():
            return 'locked'
        if not self.login_pin_hash:
            return 'not_set'
        try:
            matched = bcrypt.checkpw(pin.encode('utf-8'), self.login_pin_hash.encode('utf-8'))
        except Exception:
            matched = False
        if matched:
            self.login_pin_failed_attempts = 0
            self.login_pin_locked_until = None
            return 'ok'
        self.login_pin_failed_attempts = (self.login_pin_failed_attempts or 0) + 1
        if self.login_pin_failed_attempts >= PIN_MAX_ATTEMPTS:
            self.login_pin_locked_until = datetime.utcnow() + timedelta(minutes=PIN_LOCKOUT_MINUTES)
            self.login_pin_failed_attempts = 0
        return 'wrong'

    # ── Transaction PIN helpers ──────────────────────────────────────
    def set_transaction_pin(self, pin):
        self.transaction_pin_hash = bcrypt.hashpw(pin.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        self.transaction_pin_set_at = datetime.utcnow()
        self.transaction_pin_failed_attempts = 0
        self.transaction_pin_locked_until = None

    def transaction_pin_lock_remaining(self):
        if self.transaction_pin_locked_until and self.transaction_pin_locked_until > datetime.utcnow():
            return int((self.transaction_pin_locked_until - datetime.utcnow()).total_seconds())
        return 0

    def check_transaction_pin(self, pin):
        """Same semantics as check_login_pin(), for the transaction PIN."""
        if self.transaction_pin_locked_until and self.transaction_pin_locked_until > datetime.utcnow():
            return 'locked'
        if not self.transaction_pin_hash:
            return 'not_set'
        try:
            matched = bcrypt.checkpw(pin.encode('utf-8'), self.transaction_pin_hash.encode('utf-8'))
        except Exception:
            matched = False
        if matched:
            self.transaction_pin_failed_attempts = 0
            self.transaction_pin_locked_until = None
            return 'ok'
        self.transaction_pin_failed_attempts = (self.transaction_pin_failed_attempts or 0) + 1
        if self.transaction_pin_failed_attempts >= PIN_MAX_ATTEMPTS:
            self.transaction_pin_locked_until = datetime.utcnow() + timedelta(minutes=PIN_LOCKOUT_MINUTES)
            self.transaction_pin_failed_attempts = 0
        return 'wrong'

    def to_dict(self):
        return {
            'id':                     self.id,
            'name':                   self.name,
            'email':                  self.email,
            'phone':                  self.phone,
            'wallet_balance':         round(self.wallet_balance, 2),
            'referral_balance':       round(self.referral_balance, 2),
            'referral_earnings':      round(self.referral_earnings, 2),
            'referral_code':          self.referral_code,
            'is_verified':            self.is_verified,
            'is_premium':             self.is_premium,
            'role':                   self.role,
            'has_virtual_account':    bool(self.virtual_account_number),
            'virtual_account_number': self.virtual_account_number,
            'virtual_bank_name':      self.virtual_bank_name,
            'virtual_account_name':   self.virtual_account_name,
            'joined_date':            self.created_at.strftime('%Y-%m-%d'),
            'last_login':             self.last_login.strftime('%Y-%m-%d %H:%M:%S') if self.last_login else None,
            # NEW: server-side PIN state. The frontend must use these — not
            # whether a local PIN file exists on the device — to decide
            # whether to show "Create PIN" or skip straight to normal login.
            'login_pin_set':          bool(self.login_pin_hash),
            'transaction_pin_set':    bool(self.transaction_pin_hash),
        }


class Transaction(db.Model):
    __tablename__ = 'transactions'
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    reference    = db.Column(db.String(100), unique=True)
    type         = db.Column(db.String(50))
    service_type = db.Column(db.String(50))
    amount       = db.Column(db.Float, nullable=False)
    profit       = db.Column(db.Float, default=0.0)
    status       = db.Column(db.String(20), default='pending')
    # MutableDict wrapper: without this, SQLAlchemy has no way to notice
    # in-place changes like transaction.details.update({...}) or
    # transaction.details["error"] = x — every purchase function (airtime,
    # data, electricity, cable, exam pins, AirtimeToCash) does exactly that
    # AFTER the initial insert, to add the token/api_reference/cost_price/
    # error once the provider responds. Without this wrapper, none of that
    # ever actually reached the database: the transaction still correctly
    # shows "success" (status is a separate, properly-tracked column), but
    # looking up an old transaction later — e.g. a customer needing to
    # re-check an electricity token — would find it missing. This one
    # change fixes it everywhere `.details` is mutated, with no other file
    # needing to change.
    details      = db.Column(MutableDict.as_mutable(db.JSON), default=dict)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='transactions')

    def to_dict(self):
        return {
            'id':           self.id,
            'reference':    self.reference,
            'type':         self.type,
            'service_type': self.service_type,
            'amount':       self.amount,
            'profit':       self.profit,
            'status':       self.status,
            'details':      self.details,
            'date':         self.created_at.strftime('%B %d, %Y %I:%M:%S %p') if self.created_at else None,
            'created_at':   self.created_at.isoformat() if self.created_at else None,
        }


class OTP(db.Model):
    __tablename__ = 'otps'
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    email      = db.Column(db.String(100))
    phone      = db.Column(db.String(20))
    code       = db.Column(db.String(6), nullable=False)
    purpose    = db.Column(db.String(50), default='registration')
    is_used    = db.Column(db.Boolean, default=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # NEW: Termii's message_id for the SMS that carried this OTP, when
    # Termii's response included one. Lets support staff trace a specific
    # OTP back to a specific Termii send without ever logging the code
    # itself in plaintext logs long-term.
    provider_message_id = db.Column(db.String(100), nullable=True)


class Referral(db.Model):
    __tablename__ = 'referrals'
    id          = db.Column(db.Integer, primary_key=True)
    referrer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    referred_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    status      = db.Column(db.String(20), default='pending')
    bonus_paid  = db.Column(db.Boolean, default=False)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)


class ReferralTransaction(db.Model):
    __tablename__ = 'referral_transactions'
    id               = db.Column(db.Integer, primary_key=True)
    referrer_id      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    referred_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    amount           = db.Column(db.Float, nullable=False)
    type             = db.Column(db.String(20), nullable=False)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)


class Profit(db.Model):
    __tablename__ = 'profits'
    id             = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id'), nullable=True)
    user_id        = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    category       = db.Column(db.String(50))
    amount         = db.Column(db.Float, nullable=False)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)


class WithdrawalRequest(db.Model):
    __tablename__ = 'withdrawal_requests'
    id             = db.Column(db.Integer, primary_key=True)
    user_id        = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    amount         = db.Column(db.Float, nullable=False)
    bank_name      = db.Column(db.String(100))
    account_number = db.Column(db.String(20))
    account_name   = db.Column(db.String(100))
    status         = db.Column(db.String(20), default='pending')
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
    processed_at   = db.Column(db.DateTime)
    user = db.relationship('User', backref='withdrawals')
 

class DataPlan(db.Model):
    __tablename__ = 'data_plans'
    id            = db.Column(db.Integer, primary_key=True)
    # FIX: added unique=True — prevents duplicate plan IDs that cause wrong price lookups
    plan_id       = db.Column(db.Integer, nullable=False, unique=True)
    provider      = db.Column(db.String(50), nullable=False)
    size          = db.Column(db.String(50))
    duration      = db.Column(db.String(50))
    selling_price = db.Column(db.Float, nullable=False)
    cost_price    = db.Column(db.Float, nullable=False)
    # NEW: tags each plan as SME / Gifting / Corporate / SME2 / CG / Regular etc, so the
    # frontend's data-type tabs actually filter to different plans instead of all
    # showing the same list. Defaults to "Gifting" for existing rows since that's the
    # standard tier all current plans were seeded as.
    plan_type     = db.Column(db.String(30), nullable=False, default='Gifting')
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)


class CablePlan(db.Model):
    __tablename__ = 'cable_plans'
    id            = db.Column(db.Integer, primary_key=True)
    # FIX: added unique=True — prevents duplicate plan IDs that cause wrong price lookups
    plan_id       = db.Column(db.Integer, nullable=False, unique=True)
    provider      = db.Column(db.String(50), nullable=False)
    plan_name     = db.Column(db.String(100), nullable=False)
    selling_price = db.Column(db.Float, nullable=False)
    cost_price    = db.Column(db.Float, nullable=False)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)


class ElectricityProvider(db.Model):
    __tablename__ = 'electricity_providers'
    id               = db.Column(db.Integer, primary_key=True)
    # FIX: added unique=True
    provider_id      = db.Column(db.Integer, nullable=False, unique=True)
    name             = db.Column(db.String(100), nullable=False)
    discount_percent = db.Column(db.Float, default=0.0)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)


# ─────────────────────────────────────────────────────────────────────
# SUPPORT CENTER — AI Chat Assistant
# Added to support the in-app "Support Center" feature (replaces the
# old WhatsApp redirect button). Stores every chat turn so users see
# their chat history when they reopen the AI Assistant, and so support
# staff can audit conversations if a user has to be escalated to a
# human agent (phone / email).
# ─────────────────────────────────────────────────────────────────────
class SupportChatMessage(db.Model):
    __tablename__ = 'support_chat_messages'

    id         = db.Column(db.Integer, primary_key=True)
    # nullable — kept nullable for backward compatibility with any old
    # guest rows; the AI Assistant now requires login (JWT), so new
    # rows always have a user_id.
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    # groups messages into one conversation (one per logged-in user)
    session_id = db.Column(db.String(64), nullable=False, index=True)
    role       = db.Column(db.String(20), nullable=False)   # 'user' | 'assistant'
    content    = db.Column(db.Text, nullable=False)
    # smart-action code detected in a user message (e.g. 'data_purchase'),
    # stored on the assistant's reply row so the app can re-offer the
    # shortcut button even when history is reloaded later
    action     = db.Column(db.String(40), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def to_dict(self):
        return {
            'id':         self.id,
            'role':       self.role,
            'content':    self.content,
            'action':     self.action,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class ChatFeedback(db.Model):
    """Thumbs up/down on an AI Assistant reply, submitted via POST /api/chat/feedback."""
    __tablename__ = 'chat_feedback'

    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    message_id = db.Column(db.Integer, db.ForeignKey('support_chat_messages.id'), nullable=False, index=True)
    rating     = db.Column(db.String(10), nullable=False)   # 'up' | 'down'
    comment    = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id':         self.id,
            'message_id': self.message_id,
            'rating':     self.rating,
            'comment':    self.comment,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


# ─────────────────────────────────────────────────────────────────────
# GOOGLE PLAY — Delete Account requirement
# Stores requests submitted from the public /delete-account web page.
# Not linked to the User table by foreign key on purpose — the person
# submitting may not be logged in / may have forgotten their exact
# account email, so this is a support queue admins process manually,
# the same way the page tells the user to expect ("contact us").
# ─────────────────────────────────────────────────────────────────────
class AccountDeletionRequest(db.Model):
    __tablename__ = 'account_deletion_requests'

    id         = db.Column(db.Integer, primary_key=True)
    full_name  = db.Column(db.String(100), nullable=False)
    email      = db.Column(db.String(100), nullable=False, index=True)
    phone      = db.Column(db.String(20), nullable=False)
    reason     = db.Column(db.Text, nullable=True)
    # 'pending' | 'processing' | 'completed' | 'rejected'
    status     = db.Column(db.String(20), default='pending', nullable=False)
    ip_address = db.Column(db.String(100), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    processed_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            'id':           self.id,
            'full_name':    self.full_name,
            'email':        self.email,
            'phone':        self.phone,
            'reason':       self.reason,
            'status':       self.status,
            'created_at':   self.created_at.isoformat() if self.created_at else None,
            'processed_at': self.processed_at.isoformat() if self.processed_at else None,
        }
