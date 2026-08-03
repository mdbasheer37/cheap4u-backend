# spin_routes.py — Spin & Win API
#
# Two blueprints:
#   spin_bp        /api/spin/...        (any logged-in user)
#   spin_admin_bp   /api/admin/spin/...  (admin only)

import logging
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from models import db, User
from admin import admin_required
from extensions import limiter
import spin as spin_service
from spin_models import SpinCouponAward

logger = logging.getLogger(__name__)

spin_bp = Blueprint('spin', __name__, url_prefix='/api/spin')
spin_admin_bp = Blueprint('spin_admin', __name__, url_prefix='/api/admin/spin')


def _current_user_id():
    return int(get_jwt_identity())


def _client_ip():
    fwd = request.headers.get('X-Forwarded-For', '')
    return fwd.split(',')[0].strip() if fwd else request.remote_addr


# ── USER-FACING ──────────────────────────────────────────────────────────────

@spin_bp.route('/status', methods=['GET'])
@jwt_required()
def status():
    user_id = _current_user_id()
    if not User.query.get(user_id):
        return jsonify({'status': 'error', 'message': 'User not found'}), 404
    data = spin_service.get_spin_status(user_id)
    db.session.commit()
    return jsonify({'status': 'success', 'data': data})


@spin_bp.route('/segments', methods=['GET'])
@jwt_required()
def segments():
    """The current wheel layout (only active segments) so the client can
    draw the wheel before the user even spins."""
    spin_service._seed_default_segments_if_empty()
    db.session.commit()
    segs = spin_service.list_segments(active_only=True)
    return jsonify({'status': 'success', 'data': [s.to_dict() for s in segs]})


@spin_bp.route('/spin', methods=['POST'])
@jwt_required()
@limiter.limit('15 per minute')   # rate-limit guard on top of the daily-cap anti-cheat
def spin_now():
    user_id = _current_user_id()
    result = spin_service.perform_spin(user_id, ip_address=_client_ip())
    status_code = 200 if result.get('status') == 'success' else 400
    return jsonify(result), status_code


@spin_bp.route('/history', methods=['GET'])
@jwt_required()
def history():
    user_id = _current_user_id()
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 30, type=int), 100)
    rows, total, pages = spin_service.get_history(user_id, page, per_page)
    return jsonify({
        'status': 'success',
        'data': {'entries': rows, 'total': total, 'pages': pages, 'page': page},
    })


@spin_bp.route('/coupons', methods=['GET'])
@jwt_required()
def my_coupons():
    """Coupons this user has personally won from the wheel."""
    user_id = _current_user_id()
    rows = (
        SpinCouponAward.query
        .filter_by(user_id=user_id)
        .order_by(SpinCouponAward.created_at.desc())
        .all()
    )
    return jsonify({'status': 'success', 'data': [r.to_dict() for r in rows]})


# ── ADMIN ─────────────────────────────────────────────────────────────────────

@spin_admin_bp.route('/config', methods=['GET'])
@admin_required
def get_config():
    cfg = spin_service.get_config()
    db.session.commit()
    return jsonify({'status': 'success', 'data': cfg.to_dict()})


@spin_admin_bp.route('/config', methods=['POST'])
@admin_required
def update_config():
    data = request.get_json() or {}
    cfg = spin_service.get_config()

    if 'is_enabled' in data:
        cfg.is_enabled = bool(data['is_enabled'])
    for field in ('free_spins_per_day', 'max_spins_per_day'):
        if field in data:
            try:
                val = int(data[field])
                if val < 0:
                    raise ValueError
                setattr(cfg, field, val)
            except (TypeError, ValueError):
                return jsonify({'status': 'error', 'message': f'Invalid value for {field}'}), 400
    if 'extra_spin_cost' in data:
        try:
            cfg.extra_spin_cost = max(0.0, float(data['extra_spin_cost']))
        except (TypeError, ValueError):
            return jsonify({'status': 'error', 'message': 'Invalid value for extra_spin_cost'}), 400

    db.session.commit()
    return jsonify({'status': 'success', 'message': 'Spin settings updated', 'data': cfg.to_dict()})


@spin_admin_bp.route('/segments', methods=['GET'])
@admin_required
def list_all_segments():
    spin_service._seed_default_segments_if_empty()
    db.session.commit()
    segs = spin_service.list_segments(active_only=False)
    return jsonify({'status': 'success', 'data': [s.to_dict() for s in segs]})


@spin_admin_bp.route('/segments', methods=['POST'])
@admin_required
def add_segment():
    data = request.get_json() or {}
    seg, error = spin_service.create_segment(data)
    if error:
        return jsonify({'status': 'error', 'message': error}), 400
    return jsonify({'status': 'success', 'message': 'Prize segment created', 'data': seg.to_dict()}), 201


@spin_admin_bp.route('/segments/<int:segment_id>', methods=['PUT'])
@admin_required
def edit_segment(segment_id):
    data = request.get_json() or {}
    seg, error = spin_service.update_segment(segment_id, data)
    if error:
        return jsonify({'status': 'error', 'message': error}), 400
    return jsonify({'status': 'success', 'message': 'Prize segment updated', 'data': seg.to_dict()})


@spin_admin_bp.route('/segments/<int:segment_id>', methods=['DELETE'])
@admin_required
def remove_segment(segment_id):
    ok, error = spin_service.delete_segment(segment_id)
    if not ok:
        return jsonify({'status': 'error', 'message': error}), 400
    return jsonify({'status': 'success', 'message': 'Prize segment deleted'})


@spin_admin_bp.route('/stats', methods=['GET'])
@admin_required
def stats():
    return jsonify({'status': 'success', 'data': spin_service.get_platform_stats()})


@spin_admin_bp.route('/users/<int:user_id>/history', methods=['GET'])
@admin_required
def user_history(user_id):
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 30, type=int), 100)
    rows, total, pages = spin_service.get_history(user_id, page, per_page)
    return jsonify({
        'status': 'success',
        'data': {'entries': rows, 'total': total, 'pages': pages, 'page': page},
    })
