# merchant.py — Merchant Dashboard: core business logic
#
# Self-contained, following the same shape as cashback.py / spin.py /
# coupon.py. Bulk purchases are NOT a separate money rail — each row calls
# the exact same buy_airtime/buy_data/buy_electricity/buy_cable_tv/
# buy_exam_pin functions used everywhere else in the app, so wallet
# deduction, Transaction/Profit records, cashback and coupons all behave
# identically to a normal single purchase. This file only orchestrates the
# batch and tracks outcomes.

import logging
from datetime import datetime

from models import db, User, Transaction, Profit
from merchant_models import MerchantProfile, MerchantBulkJob, MerchantBulkItem

logger = logging.getLogger(__name__)

MAX_BULK_ITEMS = 500
JOB_TYPES = ('airtime', 'data', 'electricity', 'cable_tv', 'exam_pin')


# ── application / profile ───────────────────────────────────────────────────

def get_profile(user_id):
    return MerchantProfile.query.filter_by(user_id=user_id).first()


def apply_for_merchant(user_id, data):
    business_name = (data.get('business_name') or '').strip()
    if not business_name:
        return None, 'business_name is required'

    profile = get_profile(user_id)
    if profile and profile.status == 'approved':
        return None, 'You are already an approved merchant'
    if profile and profile.status == 'pending':
        return None, 'Your merchant application is already under review'

    if not profile:
        profile = MerchantProfile(user_id=user_id)
        db.session.add(profile)

    profile.business_name = business_name
    profile.business_type = data.get('business_type', 'individual')
    profile.registration_number = data.get('registration_number')
    profile.business_address = data.get('business_address')
    profile.business_phone = data.get('business_phone')
    profile.status = 'pending'
    profile.rejection_reason = None
    profile.reviewed_by_admin_id = None
    profile.reviewed_at = None

    db.session.commit()
    return profile, None


def require_approved_merchant(user_id):
    """Returns (profile, error). error is None if the user is an active,
    approved merchant eligible to use bulk features."""
    profile = get_profile(user_id)
    if not profile:
        return None, 'You have not applied for a merchant account yet'
    if profile.status == 'pending':
        return None, 'Your merchant application is still under review'
    if profile.status == 'rejected':
        return None, f'Your merchant application was rejected: {profile.rejection_reason or "no reason given"}'
    if profile.status == 'suspended':
        return None, 'Your merchant account has been suspended'
    return profile, None


# ── admin: review applications ──────────────────────────────────────────────

def list_applications(status=None, page=1, per_page=30):
    per_page = min(per_page, 100)
    q = db.session.query(MerchantProfile, User).join(User, User.id == MerchantProfile.user_id)
    if status:
        q = q.filter(MerchantProfile.status == status)
    q = q.order_by(MerchantProfile.created_at.desc())
    total = q.count()
    rows = q.limit(per_page).offset((page - 1) * per_page).all()
    pages = (total + per_page - 1) // per_page if per_page else 1
    result = []
    for profile, user in rows:
        d = profile.to_dict()
        d['user_name'] = user.name
        d['user_email'] = user.email
        result.append(d)
    return result, total, pages


def approve_merchant(profile_id, admin_id):
    profile = MerchantProfile.query.get(profile_id)
    if not profile:
        return None, 'Application not found'
    profile.status = 'approved'
    profile.reviewed_by_admin_id = admin_id
    profile.reviewed_at = datetime.utcnow()
    profile.is_api_enabled = True
    if not profile.api_key:
        profile.generate_api_key()
    db.session.commit()
    return profile, None


def reject_merchant(profile_id, admin_id, reason=None):
    profile = MerchantProfile.query.get(profile_id)
    if not profile:
        return None, 'Application not found'
    profile.status = 'rejected'
    profile.rejection_reason = reason or 'Application did not meet requirements'
    profile.reviewed_by_admin_id = admin_id
    profile.reviewed_at = datetime.utcnow()
    profile.is_api_enabled = False
    db.session.commit()
    return profile, None


def suspend_merchant(profile_id, admin_id, reason=None):
    profile = MerchantProfile.query.get(profile_id)
    if not profile:
        return None, 'Merchant not found'
    profile.status = 'suspended'
    profile.rejection_reason = reason
    profile.reviewed_by_admin_id = admin_id
    profile.reviewed_at = datetime.utcnow()
    profile.is_api_enabled = False
    db.session.commit()
    return profile, None


def reactivate_merchant(profile_id, admin_id):
    profile = MerchantProfile.query.get(profile_id)
    if not profile:
        return None, 'Merchant not found'
    profile.status = 'approved'
    profile.rejection_reason = None
    profile.reviewed_by_admin_id = admin_id
    profile.reviewed_at = datetime.utcnow()
    profile.is_api_enabled = True
    db.session.commit()
    return profile, None


# ── API key ──────────────────────────────────────────────────────────────────

def regenerate_api_key(user_id):
    profile, error = require_approved_merchant(user_id)
    if error:
        return None, error
    key = profile.generate_api_key()
    db.session.commit()
    return key, None


def authenticate_api_key(api_key):
    """Used by the external merchant-API blueprint. Returns (user, profile)
    or (None, None) if the key is missing/invalid/disabled."""
    if not api_key:
        return None, None
    profile = MerchantProfile.query.filter_by(api_key=api_key, is_api_enabled=True, status='approved').first()
    if not profile:
        return None, None
    user = User.query.get(profile.user_id)
    if not user or not user.is_active:
        return None, None
    return user, profile


# ── bulk purchases ───────────────────────────────────────────────────────────

def _dispatch_single(job_type, item, user):
    """Calls the real purchase function for one row. Imported lazily to
    avoid any import-order issues between merchant.py and cheapdatahub.py/
    vtunaija.py at module load time."""
    from cheapdatahub import buy_airtime, buy_data, buy_electricity, buy_cable_tv
    from vtunaija import buy_exam_pin

    if job_type == 'airtime':
        return buy_airtime(item.get('network'), item.get('phone'), float(item.get('amount', 0)), user.email)
    if job_type == 'data':
        return buy_data(item.get('plan_id'), item.get('phone'), user.email)
    if job_type == 'electricity':
        return buy_electricity(item.get('disco'), item.get('meter_number'), item.get('meter_type'),
                                float(item.get('amount', 0)), item.get('phone'), user.email)
    if job_type == 'cable_tv':
        return buy_cable_tv(item.get('plan_id'), item.get('smartcard'), user.email, phone=item.get('phone', ''))
    if job_type == 'exam_pin':
        return buy_exam_pin(item.get('exam_name'), item.get('quantity', 1), user.email,
                             selling_price=item.get('selling_price'))
    return {'status': 'error', 'message': f'Unknown job_type: {job_type}'}


def process_bulk_job(user_id, job_type, items):
    if job_type not in JOB_TYPES:
        return None, f'job_type must be one of {JOB_TYPES}'
    if not items:
        return None, 'No items provided'
    if len(items) > MAX_BULK_ITEMS:
        return None, f'A single batch cannot exceed {MAX_BULK_ITEMS} items (got {len(items)})'

    profile, error = require_approved_merchant(user_id)
    if error:
        return None, error

    user = User.query.get(user_id)
    if not user:
        return None, 'User not found'

    job = MerchantBulkJob(merchant_user_id=user_id, job_type=job_type, total_items=len(items), status='processing')
    db.session.add(job)
    db.session.commit()

    success_count = 0
    failed_count = 0
    total_charged = 0.0
    total_profit = 0.0

    for i, item in enumerate(items, start=1):
        try:
            result = _dispatch_single(job_type, item, user)
        except Exception as e:
            logger.exception(f'[Merchant Bulk] row {i} raised an unexpected error')
            result = {'status': 'error', 'message': f'Unexpected error: {e}'}

        if result.get('status') == 'success':
            success_count += 1
            data = result.get('data', {})
            charged = data.get('amount_charged', data.get('selling_price', 0)) or 0
            profit = data.get('profit_amount', 0) or 0
            total_charged += charged
            total_profit += profit

            # Best-effort link to the Transaction row via its reference
            txn = None
            ref = data.get('reference')
            if ref:
                txn = Transaction.query.filter_by(reference=ref).first()

            db.session.add(MerchantBulkItem(
                job_id=job.id, row_number=i, input_data=item, status='success',
                transaction_id=txn.id if txn else None,
            ))
        else:
            failed_count += 1
            db.session.add(MerchantBulkItem(
                job_id=job.id, row_number=i, input_data=item, status='failed',
                error_message=result.get('message', 'Unknown error'),
            ))

    job.success_count = success_count
    job.failed_count = failed_count
    job.total_amount_charged = round(total_charged, 2)
    job.total_profit = round(total_profit, 2)
    job.status = 'completed' if failed_count == 0 else ('failed' if success_count == 0 else 'completed_with_errors')
    job.completed_at = datetime.utcnow()
    db.session.commit()

    return job, None


def get_bulk_job(job_id, user_id):
    job = MerchantBulkJob.query.filter_by(id=job_id, merchant_user_id=user_id).first()
    if not job:
        return None, None, 'Job not found'
    items = MerchantBulkItem.query.filter_by(job_id=job_id).order_by(MerchantBulkItem.row_number.asc()).all()
    return job, items, None


def list_bulk_jobs(user_id, page=1, per_page=20):
    per_page = min(per_page, 100)
    q = MerchantBulkJob.query.filter_by(merchant_user_id=user_id).order_by(MerchantBulkJob.created_at.desc())
    total = q.count()
    rows = q.limit(per_page).offset((page - 1) * per_page).all()
    pages = (total + per_page - 1) // per_page if per_page else 1
    return [j.to_dict() for j in rows], total, pages


# ── analytics / reports (merchant's own data) ───────────────────────────────

def get_profit_analytics(user_id, date_from=None, date_to=None):
    q = Profit.query.filter(Profit.user_id == user_id)
    if date_from:
        q = q.filter(Profit.created_at >= date_from)
    if date_to:
        q = q.filter(Profit.created_at <= date_to)

    total_profit = q.with_entities(db.func.coalesce(db.func.sum(Profit.amount), 0.0)).scalar() or 0.0

    by_category = (
        q.with_entities(Profit.category, db.func.coalesce(db.func.sum(Profit.amount), 0.0), db.func.count(Profit.id))
        .group_by(Profit.category)
        .all()
    )

    return {
        'total_profit': round(total_profit, 2),
        'by_category': [
            {'category': cat, 'profit': round(amt, 2), 'count': cnt}
            for cat, amt, cnt in by_category
        ],
    }


def get_transaction_report(user_id, date_from=None, date_to=None, category=None, page=1, per_page=50):
    per_page = min(per_page, 200)
    q = Transaction.query.filter(Transaction.user_id == user_id)
    if date_from:
        q = q.filter(Transaction.created_at >= date_from)
    if date_to:
        q = q.filter(Transaction.created_at <= date_to)
    if category:
        q = q.filter(Transaction.type == category)
    q = q.order_by(Transaction.created_at.desc())
    total = q.count()
    rows = q.limit(per_page).offset((page - 1) * per_page).all()
    pages = (total + per_page - 1) // per_page if per_page else 1
    return [t.to_dict() for t in rows], total, pages


# ── admin stats ──────────────────────────────────────────────────────────────

def get_platform_stats():
    total_merchants = MerchantProfile.query.filter_by(status='approved').count()
    pending = MerchantProfile.query.filter_by(status='pending').count()
    suspended = MerchantProfile.query.filter_by(status='suspended').count()

    total_jobs = MerchantBulkJob.query.count()
    total_bulk_volume = (
        db.session.query(db.func.coalesce(db.func.sum(MerchantBulkJob.total_amount_charged), 0.0)).scalar() or 0.0
    )
    total_bulk_profit = (
        db.session.query(db.func.coalesce(db.func.sum(MerchantBulkJob.total_profit), 0.0)).scalar() or 0.0
    )

    top_merchants = (
        db.session.query(MerchantProfile.business_name, MerchantProfile.user_id,
                          db.func.coalesce(db.func.sum(MerchantBulkJob.total_amount_charged), 0.0).label('volume'))
        .join(MerchantBulkJob, MerchantBulkJob.merchant_user_id == MerchantProfile.user_id)
        .group_by(MerchantProfile.business_name, MerchantProfile.user_id)
        .order_by(db.desc('volume'))
        .limit(10)
        .all()
    )

    return {
        'total_approved_merchants':  total_merchants,
        'pending_applications':      pending,
        'suspended_merchants':       suspended,
        'total_bulk_jobs':           total_jobs,
        'total_bulk_volume':         round(total_bulk_volume, 2),
        'total_bulk_profit':         round(total_bulk_profit, 2),
        'top_merchants': [
            {'business_name': name, 'user_id': uid, 'volume': round(vol, 2)}
            for name, uid, vol in top_merchants
        ],
    }
