# coupon.py — Coupon / Promo Code System: core business logic
#
# Self-contained, following the same shape as cashback.py / spin.py.
# validate_and_price() is the hook every purchase function calls; it never
# touches the database beyond a read, so an invalid/declined coupon never
# leaves a stray transaction behind.

import re
import logging
from datetime import datetime

from models import db, User, Transaction
from coupon_models import Coupon, CouponRedemption

logger = logging.getLogger(__name__)

VALID_CATEGORIES = ('airtime', 'data', 'electricity', 'cable_tv', 'exam_pin')


# ── lookup / validation ─────────────────────────────────────────────────────

def get_coupon_by_code(code):
    if not code:
        return None
    return Coupon.query.filter(db.func.upper(Coupon.code) == code.strip().upper()).first()


def _has_prior_successful_purchase(user_id):
    return db.session.query(Transaction.id).filter(
        Transaction.user_id == user_id, Transaction.status == 'success',
    ).first() is not None


def validate_coupon(code, user, category=None, base_amount=0.0):
    """Returns (coupon, error). `coupon` is None if `error` is set."""
    coupon = get_coupon_by_code(code)
    if not coupon:
        return None, 'Invalid coupon code'
    if not coupon.is_active:
        return None, 'This coupon is no longer active'

    now = datetime.utcnow()
    if coupon.starts_at and now < coupon.starts_at:
        return None, 'This coupon is not active yet'
    if coupon.expires_at and now > coupon.expires_at:
        return None, 'This coupon has expired'

    if coupon.applicable_categories:
        allowed = [c.strip() for c in coupon.applicable_categories.split(',') if c.strip()]
        if allowed and category not in allowed:
            return None, f'This coupon cannot be used for {category or "this"} purchases'

    if base_amount and coupon.min_transaction_amount and base_amount < coupon.min_transaction_amount:
        return None, f'Minimum purchase of ₦{coupon.min_transaction_amount:,.2f} required for this coupon'

    if coupon.usage_limit_total is not None and coupon.total_used >= coupon.usage_limit_total:
        return None, 'This coupon has reached its usage limit'

    if coupon.specific_user_id is not None and coupon.specific_user_id != user.id:
        return None, 'This coupon is not valid for your account'

    if coupon.new_users_only and _has_prior_successful_purchase(user.id):
        return None, 'This coupon is only valid for new users'

    if coupon.usage_limit_per_user:
        used_by_user = CouponRedemption.query.filter_by(coupon_id=coupon.id, user_id=user.id).count()
        if used_by_user >= coupon.usage_limit_per_user:
            return None, 'You have already used this coupon the maximum number of times'

    return coupon, None


def compute_discount(coupon, base_amount):
    if coupon.discount_type == 'percentage':
        discount = base_amount * (coupon.discount_value or 0) / 100.0
        if coupon.max_discount_amount:
            discount = min(discount, coupon.max_discount_amount)
    else:
        discount = coupon.discount_value or 0

    discount = max(0.0, min(discount, base_amount))  # never discount more than the price itself
    return round(discount, 2)


def validate_and_price(user, code, category, base_amount):
    """
    The hook purchase functions call. Returns (discount, coupon, error).
    Pass code=None/'' to skip cleanly — returns (0.0, None, None).
    """
    if not code:
        return 0.0, None, None
    coupon, error = validate_coupon(code, user, category=category, base_amount=base_amount)
    if error:
        return None, None, error
    discount = compute_discount(coupon, base_amount)
    return discount, coupon, None


def redeem_coupon(coupon, user, transaction, discount_amount, category=None):
    """Logs the redemption and bumps the usage counter. Does NOT commit —
    rides along with the caller's existing transaction commit."""
    coupon.total_used = (coupon.total_used or 0) + 1
    db.session.add(CouponRedemption(
        coupon_id=coupon.id, user_id=user.id,
        transaction_id=transaction.id if transaction else None,
        discount_amount=discount_amount, category=category,
    ))


# ── admin CRUD ───────────────────────────────────────────────────────────────

def _normalize_code(code):
    code = re.sub(r'[^A-Za-z0-9_-]', '', (code or '').strip().upper())
    return code


def create_coupon(data, admin_id=None):
    code = _normalize_code(data.get('code'))
    if not code:
        return None, 'A valid coupon code is required (letters, numbers, - and _ only)'
    if Coupon.query.filter(db.func.upper(Coupon.code) == code).first():
        return None, 'A coupon with this code already exists'

    discount_type = data.get('discount_type', 'fixed')
    if discount_type not in ('fixed', 'percentage'):
        return None, "discount_type must be 'fixed' or 'percentage'"
    try:
        discount_value = float(data.get('discount_value', 0))
    except (TypeError, ValueError):
        return None, 'Invalid discount_value'
    if discount_value <= 0:
        return None, 'discount_value must be greater than zero'
    if discount_type == 'percentage' and discount_value > 100:
        return None, 'Percentage discount cannot exceed 100'

    categories = data.get('applicable_categories')
    if categories:
        if isinstance(categories, list):
            categories = [c for c in categories if c in VALID_CATEGORIES]
        else:
            categories = [c.strip() for c in str(categories).split(',') if c.strip() in VALID_CATEGORIES]
        categories_str = ','.join(categories) if categories else None
    else:
        categories_str = None

    def _parse_dt(val):
        if not val:
            return None
        try:
            return datetime.fromisoformat(str(val).replace('Z', ''))
        except ValueError:
            return None

    coupon = Coupon(
        code=code,
        discount_type=discount_type,
        discount_value=discount_value,
        max_discount_amount=_safe_float(data.get('max_discount_amount')),
        min_transaction_amount=_safe_float(data.get('min_transaction_amount')) or 0.0,
        applicable_categories=categories_str,
        usage_limit_total=_safe_int(data.get('usage_limit_total')),
        usage_limit_per_user=_safe_int(data.get('usage_limit_per_user'), default=1),
        new_users_only=bool(data.get('new_users_only', False)),
        specific_user_id=_safe_int(data.get('specific_user_id')),
        is_referral_coupon=bool(data.get('is_referral_coupon', False)),
        is_active=bool(data.get('is_active', True)),
        starts_at=_parse_dt(data.get('starts_at')),
        expires_at=_parse_dt(data.get('expires_at')),
        note=data.get('note'),
        created_by_admin_id=admin_id,
    )
    db.session.add(coupon)
    db.session.commit()
    return coupon, None


def _safe_float(val):
    if val in (None, '', '0'):
        return None if val in (None, '') else 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _safe_int(val, default=None):
    if val in (None, ''):
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def update_coupon(coupon_id, data):
    coupon = Coupon.query.get(coupon_id)
    if not coupon:
        return None, 'Coupon not found'

    if 'code' in data:
        new_code = _normalize_code(data['code'])
        if not new_code:
            return None, 'Invalid coupon code'
        existing = Coupon.query.filter(db.func.upper(Coupon.code) == new_code).first()
        if existing and existing.id != coupon.id:
            return None, 'A coupon with this code already exists'
        coupon.code = new_code

    if 'discount_type' in data:
        if data['discount_type'] not in ('fixed', 'percentage'):
            return None, "discount_type must be 'fixed' or 'percentage'"
        coupon.discount_type = data['discount_type']
    if 'discount_value' in data:
        val = _safe_float(data['discount_value'])
        if val is None or val <= 0:
            return None, 'discount_value must be greater than zero'
        coupon.discount_value = val
    if 'max_discount_amount' in data:
        coupon.max_discount_amount = _safe_float(data['max_discount_amount'])
    if 'min_transaction_amount' in data:
        coupon.min_transaction_amount = _safe_float(data['min_transaction_amount']) or 0.0
    if 'applicable_categories' in data:
        categories = data['applicable_categories']
        if categories:
            if isinstance(categories, list):
                categories = [c for c in categories if c in VALID_CATEGORIES]
            else:
                categories = [c.strip() for c in str(categories).split(',') if c.strip() in VALID_CATEGORIES]
            coupon.applicable_categories = ','.join(categories) if categories else None
        else:
            coupon.applicable_categories = None
    if 'usage_limit_total' in data:
        coupon.usage_limit_total = _safe_int(data['usage_limit_total'])
    if 'usage_limit_per_user' in data:
        coupon.usage_limit_per_user = _safe_int(data['usage_limit_per_user'])
    if 'new_users_only' in data:
        coupon.new_users_only = bool(data['new_users_only'])
    if 'specific_user_id' in data:
        coupon.specific_user_id = _safe_int(data['specific_user_id'])
    if 'is_referral_coupon' in data:
        coupon.is_referral_coupon = bool(data['is_referral_coupon'])
    if 'is_active' in data:
        coupon.is_active = bool(data['is_active'])
    if 'note' in data:
        coupon.note = data['note']

    for field in ('starts_at', 'expires_at'):
        if field in data:
            val = data[field]
            if not val:
                setattr(coupon, field, None)
            else:
                try:
                    setattr(coupon, field, datetime.fromisoformat(str(val).replace('Z', '')))
                except ValueError:
                    return None, f'Invalid {field}'

    db.session.commit()
    return coupon, None


def delete_coupon(coupon_id):
    coupon = Coupon.query.get(coupon_id)
    if not coupon:
        return False, 'Coupon not found'
    db.session.delete(coupon)  # redemption history rows are kept (nullable FK-safe join via id)
    db.session.commit()
    return True, None


def list_coupons(search=None, active_only=False, page=1, per_page=30):
    per_page = min(per_page, 100)
    q = Coupon.query
    if active_only:
        q = q.filter_by(is_active=True)
    if search:
        q = q.filter(Coupon.code.ilike(f'%{search}%'))
    q = q.order_by(Coupon.created_at.desc())
    total = q.count()
    rows = q.limit(per_page).offset((page - 1) * per_page).all()
    pages = (total + per_page - 1) // per_page if per_page else 1
    return [c.to_dict() for c in rows], total, pages


def get_redemptions(coupon_id, page=1, per_page=30):
    per_page = min(per_page, 100)
    q = CouponRedemption.query.filter_by(coupon_id=coupon_id).order_by(CouponRedemption.created_at.desc())
    total = q.count()
    rows = q.limit(per_page).offset((page - 1) * per_page).all()
    pages = (total + per_page - 1) // per_page if per_page else 1
    return [r.to_dict() for r in rows], total, pages


def get_stats():
    total_coupons = Coupon.query.count()
    active_coupons = Coupon.query.filter_by(is_active=True).count()
    total_redemptions = CouponRedemption.query.count()
    total_discount_given = (
        db.session.query(db.func.coalesce(db.func.sum(CouponRedemption.discount_amount), 0.0)).scalar() or 0.0
    )

    top_coupons = (
        db.session.query(Coupon.code, Coupon.total_used,
                          db.func.coalesce(db.func.sum(CouponRedemption.discount_amount), 0.0))
        .outerjoin(CouponRedemption, CouponRedemption.coupon_id == Coupon.id)
        .group_by(Coupon.id, Coupon.code, Coupon.total_used)
        .order_by(Coupon.total_used.desc())
        .limit(10)
        .all()
    )

    return {
        'total_coupons':         total_coupons,
        'active_coupons':        active_coupons,
        'total_redemptions':     total_redemptions,
        'total_discount_given':  round(total_discount_given, 2),
        'top_coupons': [
            {'code': code, 'times_used': used, 'total_discount': round(discount, 2)}
            for code, used, discount in top_coupons
        ],
    }


# ── referral-coupon convenience (integration point, referral.py left untouched) ──

def create_referral_coupon(user_id, discount_value=200.0, discount_type='fixed', expiry_days=30, admin_id=None):
    """Issue a single-recipient coupon to one user — e.g. a reward for a
    successful referral. Callable from anywhere (admin action today; a
    future hook in referral.py's completion logic tomorrow) without
    touching the referral system itself."""
    from datetime import timedelta
    code = 'REF' + _normalize_code(User.query.get(user_id).phone[-6:] if User.query.get(user_id) else '') \
        or f'REF{user_id}{int(datetime.utcnow().timestamp()) % 100000}'
    # Ensure uniqueness even if two coupons get generated in the same second
    base_code, suffix = code, 1
    while Coupon.query.filter(db.func.upper(Coupon.code) == code).first():
        suffix += 1
        code = f'{base_code}{suffix}'

    coupon = Coupon(
        code=code, discount_type=discount_type, discount_value=discount_value,
        usage_limit_total=1, usage_limit_per_user=1,
        specific_user_id=user_id, is_referral_coupon=True, is_active=True,
        expires_at=datetime.utcnow() + timedelta(days=expiry_days),
        note='Referral reward coupon', created_by_admin_id=admin_id,
    )
    db.session.add(coupon)
    db.session.commit()
    return coupon
