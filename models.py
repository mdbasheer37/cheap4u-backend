from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import bcrypt

db = SQLAlchemy()

class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    phone = db.Column(db.String(20), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    wallet_balance = db.Column(db.Float, default=0.0)
    referral_balance = db.Column(db.Float, default=0.0)
    referral_code = db.Column(db.String(20), unique=True)
    referred_by = db.Column(db.String(20), nullable=True)
    is_verified = db.Column(db.Boolean, default=False)
    is_premium = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, default=datetime.utcnow)
    transaction_pin_hash = db.Column(db.String(200), nullable=True) 
 
    
    def set_password(self, password):
        salt = bcrypt.gensalt()
        self.password_hash = bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')
    
    def check_password(self, password):
        return bcrypt.checkpw(password.encode('utf-8'), self.password_hash.encode('utf-8'))
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'phone': self.phone,
            'wallet_balance': self.wallet_balance,
            'referral_balance': self.referral_balance,
            'referral_code': self.referral_code,
            'is_verified': self.is_verified,
            'is_premium': self.is_premium,
            'joined_date': self.created_at.strftime('%Y-%m-%d'),
            'last_login': self.last_login.strftime('%Y-%m-%d %H:%M:%S')
        }

class OTP(db.Model):
    __tablename__ = 'otps'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    email = db.Column(db.String(100))
    phone = db.Column(db.String(20))
    code = db.Column(db.String(6), nullable=False)
    purpose = db.Column(db.String(50), default='registration')  # registration, login, password_reset
    expires_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_used = db.Column(db.Boolean, default=False)

class Transaction(db.Model):
    __tablename__ = 'transactions'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    reference = db.Column(db.String(100), unique=True)
    type = db.Column(db.String(50))  # airtime, data, electricity, cable_tv, exam_pin, wallet_funding, withdrawal
    service_type = db.Column(db.String(50))
    amount = db.Column(db.Float, nullable=False)
    profit = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(20), default='pending')  # pending, success, failed
    details = db.Column(db.JSON, default={})
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'reference': self.reference,
            'type': self.type,
            'service_type': self.service_type,
            'amount': self.amount,
            'profit': self.profit,
            'status': self.status,
            'details': self.details,
            'date': self.created_at.strftime('%B %d, %Y %I:%M:%S %p')
        }

class Referral(db.Model):
    __tablename__ = 'referrals'
    
    id = db.Column(db.Integer, primary_key=True)
    referrer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    referred_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    bonus_paid = db.Column(db.Boolean, default=False)
    bonus_amount = db.Column(db.Float, default=50.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    first_transaction_completed = db.Column(db.Boolean, default=False)

class WithdrawalRequest(db.Model):
    __tablename__ = 'withdrawal_requests'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    bank_name = db.Column(db.String(100))
    account_number = db.Column(db.String(20))
    account_name = db.Column(db.String(100))
    status = db.Column(db.String(20), default='pending')  # pending, processing, completed, failed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    processed_at = db.Column(db.DateTime)

class Profit(db.Model):
    __tablename__ = 'profits'
    
    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    category = db.Column(db.String(50))  # airtime, data, electricity, cable_tv, exam_pin
    amount = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class DataPlan(db.Model):
    __tablename__ = 'data_plans'
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, nullable=False)          # The ID from CheapDataHub table
    provider = db.Column(db.String(50), nullable=False)      # mtn, glo, airtel, 9mobile
    size = db.Column(db.String(50))                          # e.g., "1GB"
    duration = db.Column(db.String(50))                      # e.g., "30 Days"
    selling_price = db.Column(db.Float, nullable=False)      # Price to customer (₦)
    cost_price = db.Column(db.Float, nullable=False)         # What CheapDataHub charges you
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class CablePlan(db.Model):
    __tablename__ = 'cable_plans'
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, nullable=False)          # ID from table (3,4,5...)
    provider = db.Column(db.String(50), nullable=False)      # DSTV, GOTV, STARTIMES
    plan_name = db.Column(db.String(100), nullable=False)    # e.g., "DStv Compact"
    selling_price = db.Column(db.Float, nullable=False)      # Price to customer
    cost_price = db.Column(db.Float, nullable=False)         # Your cost (default = selling_price * 0.95)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class ElectricityProvider(db.Model):
    __tablename__ = 'electricity_providers'
    id = db.Column(db.Integer, primary_key=True)
    provider_id = db.Column(db.Integer, nullable=False)      # 1 for AEDC, etc.
    name = db.Column(db.String(100), nullable=False)         # "Abuja Electric AEDC"
    discount_percent = db.Column(db.Float, default=0.0)      # Discount you get from CheapDataHub (if any)
    created_at = db.Column(db.DateTime, default=datetime.utcnow) 
