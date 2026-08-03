# coupon_routes.py — Coupon / Promo Code System API
#
# Two blueprints:
#   coupon_bp        /api/coupons/...        (any logged-in user)
#   coupon_admin_bp   /api/admin/coupons/...  (admin only)

import logging
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from models import db, User
from admin import admin_required
import coupon as coupon_service
from coupon_models import Coupon

logger = logging.getLogger(__name__)

coupon_bp = Blueprint('coupon', __name__, url_prefix='/api/coupons')
coupon_admin_bp = Blueprint('coupon_admin', __name__, url_prefix='/api/admin/coupons')


def _current_user_id():
    return int(get_jwt_identity())


# ── USER-FACING ──────────────────────────────────────────────────────────────

@coupon_bp.route('/validate', methods=['GET'])
@jwt_required()
def validate():
    """
    Preview a coupon's discount before checkout — does NOT redeem it.
    GET /api/coupons/validate?code=SAVE100&category=airtime&amount=1000
    """
    user_id = _current_user_id()
    user = User.query.get(user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    code = (request.args.get('code') or '').strip()
    category = request.args.get('category')
    try:
        amount = float(request.args.get('amount', 0))
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'Invalid amount'}), 400

    if not code:
        return jsonify({'status': 'error', 'message': 'code is required'}), 400

    coupon, error = coupon_service.validate_coupon(code, user, category=category, base_amount=amount)
    if error:
        return jsonify({'status': 'error', 'message': error}), 400

    discount = coupon_service.compute_discount(coupon, amount)
    return jsonify({
        'status': 'success',
        'data': {
            'code':            coupon.code,
            'discount_type':   coupon.discount_type,
            'discount_value':  coupon.discount_value,
            'discount_amount': discount,
            'final_amount':    round(max(0.0, amount - discount), 2),
        },
    })


@coupon_bp.route('/my-coupons', methods=['GET'])
@jwt_required()
def my_coupons():
    """Coupons personally targeted at this user (e.g. referral rewards)."""
    user_id = _current_user_id()
    rows = (
        Coupon.query
        .filter_by(specific_user_id=user_id, is_active=True)
        .order_by(Coupon.created_at.desc())
        .all()
    )
    return jsonify({'status': 'success', 'data': [c.to_dict() for c in rows]})


# ── ADMIN ─────────────────────────────────────────────────────────────────────

@coupon_admin_bp.route('', methods=['GET'])
@admin_required
def list_coupons():
    search = request.args.get('search', '').strip() or None
    active_only = request.args.get('active_only', 'false').lower() == 'true'
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 30, type=int), 100)
    rows, total, pages = coupon_service.list_coupons(search=search, active_only=active_only,
                                                       page=page, per_page=per_page)
    return jsonify({
        'status': 'success',
        'data': {'coupons': rows, 'total': total, 'pages': pages, 'page': page},
    })


@coupon_admin_bp.route('', methods=['POST'])
@admin_required
def create_coupon():
    data = request.get_json() or {}
    admin_id = int(get_jwt_identity())
    coupon, error = coupon_service.create_coupon(data, admin_id=admin_id)
    if error:
        return jsonify({'status': 'error', 'message': error}), 400
    return jsonify({'status': 'success', 'message': 'Coupon created', 'data': coupon.to_dict()}), 201


@coupon_admin_bp.route('/<int:coupon_id>', methods=['GET'])
@admin_required
def get_coupon(coupon_id):
    coupon = Coupon.query.get(coupon_id)
    if not coupon:
        return jsonify({'status': 'error', 'message': 'Coupon not found'}), 404
    return jsonify({'status': 'success', 'data': coupon.to_dict()})


@coupon_admin_bp.route('/<int:coupon_id>', methods=['PUT'])
@admin_required
def edit_coupon(coupon_id):
    data = request.get_json() or {}
    coupon, error = coupon_service.update_coupon(coupon_id, data)
    if error:
        return jsonify({'status': 'error', 'message': error}), 400
    return jsonify({'status': 'success', 'message': 'Coupon updated', 'data': coupon.to_dict()})


@coupon_admin_bp.route('/<int:coupon_id>', methods=['DELETE'])
@admin_required
def remove_coupon(coupon_id):
    ok, error = coupon_service.delete_coupon(coupon_id)
    if not ok:
        return jsonify({'status': 'error', 'message': error}), 400
    return jsonify({'status': 'success', 'message': 'Coupon deleted'})


@coupon_admin_bp.route('/<int:coupon_id>/redemptions', methods=['GET'])
@admin_required
def coupon_redemptions(coupon_id):
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 30, type=int), 100)
    rows, total, pages = coupon_service.get_redemptions(coupon_id, page, per_page)
    return jsonify({
        'status': 'success',
        'data': {'redemptions': rows, 'total': total, 'pages': pages, 'page': page},
    })


@coupon_admin_bp.route('/stats', methods=['GET'])
@admin_required
def stats():
    return jsonify({'status': 'success', 'data': coupon_service.get_stats()})


@coupon_admin_bp.route('/referral-coupon', methods=['POST'])
@admin_required
def issue_referral_coupon():
    """Manually issue a single-recipient referral-reward coupon to a user."""
    data = request.get_json() or {}
    user_id = data.get('user_id')
    if not user_id or not User.query.get(user_id):
        return jsonify({'status': 'error', 'message': 'Valid user_id is required'}), 400

    admin_id = int(get_jwt_identity())
    coupon = coupon_service.create_referral_coupon(
        user_id,
        discount_value=data.get('discount_value', 200.0),
        discount_type=data.get('discount_type', 'fixed'),
        expiry_days=data.get('expiry_days', 30),
        admin_id=admin_id,
    )
    return jsonify({'status': 'success', 'message': 'Referral coupon issued', 'data': coupon.to_dict()}), 201
