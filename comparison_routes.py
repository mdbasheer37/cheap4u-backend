# comparison_routes.py — Smart Price Comparison API
#
# Two blueprints:
#   comparison_bp        /api/compare/...          (any logged-in user)
#   comparison_admin_bp   /api/admin/compare/...     (admin only)

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required

from models import db
from admin import admin_required
import price_comparison as comparison_service

comparison_bp = Blueprint('comparison', __name__, url_prefix='/api/compare')
comparison_admin_bp = Blueprint('comparison_admin', __name__, url_prefix='/api/admin/compare')


# ── USER-FACING ──────────────────────────────────────────────────────────────

@comparison_bp.route('/data', methods=['GET'])
@jwt_required()
def compare_data():
    plan_type = request.args.get('plan_type')
    data = comparison_service.compare_data_plans(plan_type=plan_type)
    return jsonify({'status': 'success', 'data': data})


@comparison_bp.route('/airtime', methods=['GET'])
@jwt_required()
def compare_airtime():
    data = comparison_service.compare_airtime()
    return jsonify({'status': 'success', 'data': data})


# ── ADMIN ─────────────────────────────────────────────────────────────────────

@comparison_admin_bp.route('/config', methods=['GET'])
@admin_required
def get_config():
    cfg = comparison_service.get_config()
    db.session.commit()
    return jsonify({'status': 'success', 'data': cfg.to_dict()})


@comparison_admin_bp.route('/config', methods=['POST'])
@admin_required
def update_config():
    data = request.get_json() or {}
    cfg = comparison_service.get_config()

    if 'is_enabled' in data:
        cfg.is_enabled = bool(data['is_enabled'])
    for field in ('price_weight', 'speed_weight', 'reliability_weight'):
        if field in data:
            try:
                cfg_val = max(0.0, float(data[field]))
                setattr(cfg, field, cfg_val)
            except (TypeError, ValueError):
                return jsonify({'status': 'error', 'message': f'Invalid value for {field}'}), 400
    for field in ('stats_window_days', 'stats_sample_limit'):
        if field in data:
            try:
                val = max(1, int(data[field]))
                setattr(cfg, field, val)
            except (TypeError, ValueError):
                return jsonify({'status': 'error', 'message': f'Invalid value for {field}'}), 400

    db.session.commit()
    return jsonify({'status': 'success', 'message': 'Comparison settings updated', 'data': cfg.to_dict()})
