# gamification_routes.py — Gamification API
#
# Two blueprints:
#   gamification_bp        /api/gamification/...        (any logged-in user)
#   gamification_admin_bp   /api/admin/gamification/...  (admin only)

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from models import db, User
from admin import admin_required
import gamification as gam_service
from gamification_models import Mission, Badge, MISSION_PERIODS, MISSION_TARGET_TYPES, BADGE_CRITERIA_TYPES

gamification_bp = Blueprint('gamification', __name__, url_prefix='/api/gamification')
gamification_admin_bp = Blueprint('gamification_admin', __name__, url_prefix='/api/admin/gamification')


def _current_user_id():
    return int(get_jwt_identity())


# ── USER-FACING ──────────────────────────────────────────────────────────────

@gamification_bp.route('/summary', methods=['GET'])
@jwt_required()
def summary():
    return jsonify({'status': 'success', 'data': gam_service.get_user_summary(_current_user_id())})


@gamification_bp.route('/levels', methods=['GET'])
@jwt_required()
def levels():
    rows = gam_service.get_levels()
    db.session.commit()
    return jsonify({'status': 'success', 'data': [l.to_dict() for l in rows]})


@gamification_bp.route('/missions', methods=['GET'])
@jwt_required()
def missions():
    data = gam_service.get_user_missions(_current_user_id())
    return jsonify({'status': 'success', 'data': data})


@gamification_bp.route('/badges', methods=['GET'])
@jwt_required()
def my_badges():
    data = gam_service.get_user_badges(_current_user_id())
    return jsonify({'status': 'success', 'data': data})


@gamification_bp.route('/badges/all', methods=['GET'])
@jwt_required()
def all_badges():
    """Every active badge (including ones not yet earned) so the app can
    show a 'locked' state for badges still to unlock."""
    rows = Badge.query.filter_by(is_active=True).all()
    from gamification_models import UserBadge
    earned_ids = {ub.badge_id for ub in UserBadge.query.filter_by(user_id=_current_user_id()).all()}
    data = []
    for b in rows:
        d = b.to_dict()
        d['earned'] = b.id in earned_ids
        data.append(d)
    return jsonify({'status': 'success', 'data': data})


@gamification_bp.route('/leaderboard', methods=['GET'])
@jwt_required()
def leaderboard():
    limit = min(request.args.get('limit', 20, type=int), 100)
    data = gam_service.get_leaderboard(limit=limit)
    return jsonify({'status': 'success', 'data': data})


# ── ADMIN ─────────────────────────────────────────────────────────────────────

@gamification_admin_bp.route('/config', methods=['GET'])
@admin_required
def get_config():
    cfg = gam_service.get_config()
    db.session.commit()
    return jsonify({'status': 'success', 'data': cfg.to_dict()})


@gamification_admin_bp.route('/config', methods=['POST'])
@admin_required
def update_config():
    data = request.get_json() or {}
    cfg = gam_service.get_config()
    if 'is_enabled' in data:
        cfg.is_enabled = bool(data['is_enabled'])
    for field in ('xp_per_100_naira_spent',):
        if field in data:
            try:
                cfg.xp_per_100_naira_spent = max(0.0, float(data[field]))
            except (TypeError, ValueError):
                return jsonify({'status': 'error', 'message': f'Invalid value for {field}'}), 400
    for field in ('xp_daily_login', 'level_up_bonus_points'):
        if field in data:
            try:
                setattr(cfg, field, max(0, int(data[field])))
            except (TypeError, ValueError):
                return jsonify({'status': 'error', 'message': f'Invalid value for {field}'}), 400
    db.session.commit()
    return jsonify({'status': 'success', 'message': 'Gamification settings updated', 'data': cfg.to_dict()})


@gamification_admin_bp.route('/levels', methods=['GET'])
@admin_required
def admin_levels():
    rows = gam_service.get_levels()
    db.session.commit()
    return jsonify({'status': 'success', 'data': [l.to_dict() for l in rows]})


@gamification_admin_bp.route('/levels/<int:level_number>', methods=['PUT'])
@admin_required
def update_level(level_number):
    from gamification_models import XPLevel
    lvl = XPLevel.query.filter_by(level_number=level_number).first()
    if not lvl:
        return jsonify({'status': 'error', 'message': 'Level not found'}), 404
    data = request.get_json() or {}
    if 'title' in data:
        lvl.title = data['title']
    if 'xp_required' in data:
        try:
            lvl.xp_required = int(data['xp_required'])
        except (TypeError, ValueError):
            return jsonify({'status': 'error', 'message': 'Invalid xp_required'}), 400
    if 'icon' in data:
        lvl.icon = data['icon']
    if 'perk_description' in data:
        lvl.perk_description = data['perk_description']
    db.session.commit()
    return jsonify({'status': 'success', 'message': 'Level updated', 'data': lvl.to_dict()})


@gamification_admin_bp.route('/badges', methods=['GET'])
@admin_required
def list_badges():
    rows = Badge.query.order_by(Badge.id.asc()).all()
    return jsonify({'status': 'success', 'data': [b.to_dict() for b in rows]})


@gamification_admin_bp.route('/badges', methods=['POST'])
@admin_required
def create_badge():
    data = request.get_json() or {}
    code = (data.get('code') or '').strip()
    name = (data.get('name') or '').strip()
    criteria_type = data.get('criteria_type')
    if not code or not name:
        return jsonify({'status': 'error', 'message': 'code and name are required'}), 400
    if criteria_type not in BADGE_CRITERIA_TYPES:
        return jsonify({'status': 'error', 'message': f'criteria_type must be one of {BADGE_CRITERIA_TYPES}'}), 400
    try:
        criteria_value = float(data.get('criteria_value'))
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'criteria_value must be numeric'}), 400
    if Badge.query.filter_by(code=code).first():
        return jsonify({'status': 'error', 'message': 'A badge with this code already exists'}), 400

    badge = Badge(
        code=code, name=name, description=data.get('description'),
        icon=data.get('icon', 'medal'), criteria_type=criteria_type,
        criteria_value=criteria_value, category=data.get('category'),
        is_active=bool(data.get('is_active', True)),
    )
    db.session.add(badge)
    db.session.commit()
    return jsonify({'status': 'success', 'message': 'Badge created', 'data': badge.to_dict()}), 201


@gamification_admin_bp.route('/badges/<int:badge_id>', methods=['PUT'])
@admin_required
def update_badge(badge_id):
    badge = Badge.query.get(badge_id)
    if not badge:
        return jsonify({'status': 'error', 'message': 'Badge not found'}), 404
    data = request.get_json() or {}
    if 'name' in data:
        badge.name = data['name']
    if 'description' in data:
        badge.description = data['description']
    if 'icon' in data:
        badge.icon = data['icon']
    if 'criteria_type' in data:
        if data['criteria_type'] not in BADGE_CRITERIA_TYPES:
            return jsonify({'status': 'error', 'message': f'criteria_type must be one of {BADGE_CRITERIA_TYPES}'}), 400
        badge.criteria_type = data['criteria_type']
    if 'criteria_value' in data:
        try:
            badge.criteria_value = float(data['criteria_value'])
        except (TypeError, ValueError):
            return jsonify({'status': 'error', 'message': 'Invalid criteria_value'}), 400
    if 'category' in data:
        badge.category = data['category']
    if 'is_active' in data:
        badge.is_active = bool(data['is_active'])
    db.session.commit()
    return jsonify({'status': 'success', 'message': 'Badge updated', 'data': badge.to_dict()})


@gamification_admin_bp.route('/badges/<int:badge_id>', methods=['DELETE'])
@admin_required
def delete_badge(badge_id):
    badge = Badge.query.get(badge_id)
    if not badge:
        return jsonify({'status': 'error', 'message': 'Badge not found'}), 404
    db.session.delete(badge)
    db.session.commit()
    return jsonify({'status': 'success', 'message': 'Badge deleted'})


@gamification_admin_bp.route('/missions', methods=['GET'])
@admin_required
def list_missions():
    rows = Mission.query.order_by(Mission.id.asc()).all()
    return jsonify({'status': 'success', 'data': [m.to_dict() for m in rows]})


@gamification_admin_bp.route('/missions', methods=['POST'])
@admin_required
def create_mission():
    data = request.get_json() or {}
    title = (data.get('title') or '').strip()
    period = data.get('period')
    target_type = data.get('target_type')
    if not title:
        return jsonify({'status': 'error', 'message': 'title is required'}), 400
    if period not in MISSION_PERIODS:
        return jsonify({'status': 'error', 'message': f'period must be one of {MISSION_PERIODS}'}), 400
    if target_type not in MISSION_TARGET_TYPES:
        return jsonify({'status': 'error', 'message': f'target_type must be one of {MISSION_TARGET_TYPES}'}), 400
    try:
        target_value = float(data.get('target_value'))
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'target_value must be numeric'}), 400

    mission = Mission(
        title=title, description=data.get('description'), period=period,
        target_type=target_type, target_value=target_value, category=data.get('category'),
        xp_reward=int(data.get('xp_reward', 10) or 0), points_reward=int(data.get('points_reward', 0) or 0),
        is_active=bool(data.get('is_active', True)),
    )
    db.session.add(mission)
    db.session.commit()
    return jsonify({'status': 'success', 'message': 'Mission created', 'data': mission.to_dict()}), 201


@gamification_admin_bp.route('/missions/<int:mission_id>', methods=['PUT'])
@admin_required
def update_mission(mission_id):
    mission = Mission.query.get(mission_id)
    if not mission:
        return jsonify({'status': 'error', 'message': 'Mission not found'}), 404
    data = request.get_json() or {}
    if 'title' in data:
        mission.title = data['title']
    if 'description' in data:
        mission.description = data['description']
    if 'period' in data:
        if data['period'] not in MISSION_PERIODS:
            return jsonify({'status': 'error', 'message': f'period must be one of {MISSION_PERIODS}'}), 400
        mission.period = data['period']
    if 'target_type' in data:
        if data['target_type'] not in MISSION_TARGET_TYPES:
            return jsonify({'status': 'error', 'message': f'target_type must be one of {MISSION_TARGET_TYPES}'}), 400
        mission.target_type = data['target_type']
    if 'target_value' in data:
        try:
            mission.target_value = float(data['target_value'])
        except (TypeError, ValueError):
            return jsonify({'status': 'error', 'message': 'Invalid target_value'}), 400
    if 'category' in data:
        mission.category = data['category']
    if 'xp_reward' in data:
        mission.xp_reward = int(data['xp_reward'] or 0)
    if 'points_reward' in data:
        mission.points_reward = int(data['points_reward'] or 0)
    if 'is_active' in data:
        mission.is_active = bool(data['is_active'])
    db.session.commit()
    return jsonify({'status': 'success', 'message': 'Mission updated', 'data': mission.to_dict()})


@gamification_admin_bp.route('/missions/<int:mission_id>', methods=['DELETE'])
@admin_required
def delete_mission(mission_id):
    mission = Mission.query.get(mission_id)
    if not mission:
        return jsonify({'status': 'error', 'message': 'Mission not found'}), 404
    db.session.delete(mission)
    db.session.commit()
    return jsonify({'status': 'success', 'message': 'Mission deleted'})


@gamification_admin_bp.route('/adjust/<int:user_id>', methods=['POST'])
@admin_required
def adjust_xp(user_id):
    if not User.query.get(user_id):
        return jsonify({'status': 'error', 'message': 'User not found'}), 404
    data = request.get_json() or {}
    if 'amount' not in data:
        return jsonify({'status': 'error', 'message': 'amount is required'}), 400
    try:
        amount = int(data['amount'])
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'Invalid amount'}), 400
    result = gam_service.admin_adjust_xp(user_id, amount, note=data.get('note'))
    status_code = 200 if result.get('status') == 'success' else 400
    return jsonify(result), status_code


@gamification_admin_bp.route('/stats', methods=['GET'])
@admin_required
def stats():
    return jsonify({'status': 'success', 'data': gam_service.get_platform_stats()})
