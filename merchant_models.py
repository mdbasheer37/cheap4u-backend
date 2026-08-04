# merchant_models.py — Merchant Dashboard data models
#
# Uses the SAME `db` SQLAlchemy instance as models.py so these tables are
# created automatically by the existing `db.create_all()` call in app.py.
# Deliberately does NOT touch the User model or User.role — merchant status
# lives entirely in MerchantProfile so approving/suspending a merchant can
# never interfere with the existing admin_required / auth logic.

import secrets
from datetime import datetime
from models import db


class MerchantProfile(db.Model):
    """One row per user who has applied to become a merchant. KYC here is
    structured business information (name, registration number, address) —
    not document image uploads, which would need separate file-storage
    infrastructure this project doesn't have yet."""
    __tablename__ = 'merchant_profiles'

    id                      = db.Column(db.Integer, primary_key=True)
    user_id                 = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True, index=True)

    business_name           = db.Column(db.String(150), nullable=False)
    # 'individual' | 'registered_business'
    business_type           = db.Column(db.String(30), default='individual')
    registration_number     = db.Column(db.String(50), nullable=True)   # CAC/RC number, if registered
    business_address        = db.Column(db.String(255), nullable=True)
    business_phone          = db.Column(db.String(20), nullable=True)

    # 'pending' | 'approved' | 'rejected' | 'suspended'
    status                  = db.Column(db.String(20), default='pending', index=True)
    rejection_reason        = db.Column(db.String(255), nullable=True)
    reviewed_by_admin_id    = db.Column(db.Integer, nullable=True)
    reviewed_at             = db.Column(db.DateTime, nullable=True)

    api_key                 = db.Column(db.String(64), unique=True, nullable=True, index=True)
    api_key_created_at      = db.Column(db.DateTime, nullable=True)
    is_api_enabled          = db.Column(db.Boolean, default=False)

    created_at               = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at               = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User')

    def generate_api_key(self):
        self.api_key = 'c4u_live_' + secrets.token_hex(24)
        self.api_key_created_at = datetime.utcnow()
        return self.api_key

    def to_dict(self, include_api_key=False):
        data = {
            'id':                    self.id,
            'user_id':               self.user_id,
            'business_name':         self.business_name,
            'business_type':         self.business_type,
            'registration_number':   self.registration_number,
            'business_address':      self.business_address,
            'business_phone':        self.business_phone,
            'status':                self.status,
            'rejection_reason':      self.rejection_reason,
            'reviewed_at':           self.reviewed_at.isoformat() if self.reviewed_at else None,
            'is_api_enabled':        self.is_api_enabled,
            'has_api_key':           bool(self.api_key),
            'created_at':            self.created_at.isoformat() if self.created_at else None,
        }
        if include_api_key:
            data['api_key'] = self.api_key
        return data


class MerchantBulkJob(db.Model):
    """One batch upload — e.g. 200 airtime top-ups submitted in one CSV.
    Each row is processed through the exact same buy_* functions as a
    normal single purchase, so wallet deduction, profit tracking, cashback
    and coupons all behave identically — this table just tracks the batch."""
    __tablename__ = 'merchant_bulk_jobs'

    id                    = db.Column(db.Integer, primary_key=True)
    merchant_user_id      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)

    # 'airtime' | 'data' | 'electricity' | 'cable_tv' | 'exam_pin'
    job_type              = db.Column(db.String(20), nullable=False)

    total_items           = db.Column(db.Integer, default=0)
    success_count         = db.Column(db.Integer, default=0)
    failed_count          = db.Column(db.Integer, default=0)
    total_amount_charged  = db.Column(db.Float, default=0.0)
    total_profit          = db.Column(db.Float, default=0.0)

    # 'processing' | 'completed' | 'completed_with_errors' | 'failed'
    status                = db.Column(db.String(30), default='processing')

    created_at             = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    completed_at           = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            'id':                    self.id,
            'job_type':              self.job_type,
            'total_items':           self.total_items,
            'success_count':         self.success_count,
            'failed_count':          self.failed_count,
            'total_amount_charged':  round(self.total_amount_charged or 0, 2),
            'total_profit':          round(self.total_profit or 0, 2),
            'status':                self.status,
            'created_at':            self.created_at.isoformat() if self.created_at else None,
            'completed_at':          self.completed_at.isoformat() if self.completed_at else None,
        }


class MerchantBulkItem(db.Model):
    """One row within a bulk job — its input data and outcome."""
    __tablename__ = 'merchant_bulk_items'

    id              = db.Column(db.Integer, primary_key=True)
    job_id          = db.Column(db.Integer, db.ForeignKey('merchant_bulk_jobs.id'), nullable=False, index=True)
    row_number      = db.Column(db.Integer, nullable=False)

    input_data      = db.Column(db.JSON, default=dict)
    status          = db.Column(db.String(10), default='pending')   # 'success' | 'failed'
    transaction_id  = db.Column(db.Integer, db.ForeignKey('transactions.id'), nullable=True)
    error_message   = db.Column(db.String(255), nullable=True)

    created_at      = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'row_number':      self.row_number,
            'input':           self.input_data,
            'status':          self.status,
            'transaction_id':  self.transaction_id,
            'error_message':   self.error_message,
        }
