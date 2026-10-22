# spin.py — Spin & Win: core business logic
#
# Self-contained, following the same shape as challenge.py / cashback.py.
# Reward payouts reuse the existing wallet_balance field and the Cashback
# System's own ledger (via cashback.admin_adjust) so a "Cashback" prize
# shows up correctly in the user's Cashback wallet/history, not as a
# separate, disconnected balance.

import random
import string
import logging
from datetime import datetime, date, timedelta

from models import db, User
from spin_models import (
    SpinConfig, SpinSegment, SpinDailyCounter, SpinEntry,
    UserPoints, SpinCouponAward,
)
from models import Profit

logger = logging.getLogger(__name__)

# reward_type values a segment/admin may use
REWARD_TYPES = ('airtime', 'data', 'cashback', 'wallet_balance', 'coupon', 'bonus_points')

# 'airtime' and 'data' rewards are credited as spendable wallet balance —
# Cheap4U has no safe way to auto-dispatch a real top-up without a phone
# number/network chosen by the user, so the naira value is credited to
# their wallet, clearly labeled, and they spend it on the airtime/data of
# their choice. This keeps the payout instant, atomic and reversible-free
# instead of firing an unattended external purchase.
WALLET_CREDIT_TYPES = ('airtime', 'data', 'wallet_balance')


# ── config / segments ───────────────────────────────────────────────────────

def get_config():
    cfg = SpinConfig.query.get(1)
    if not cfg:
        cfg = SpinConfig(id=1)
        db.session.add(cfg)
        db.session.flush()
    return cfg


def list_segments(active_only=False):
    q = SpinSegment.query
    if active_only:
        q = q.filter_by(is_active=True)
    return q.order_by(SpinSegment.display_order.asc(), SpinSegment.id.asc()).all()


def _seed_default_segments_if_empty():
    """First-run convenience: if no admin has configured any segments yet,
    seed a sensible default wheel so the feature works out of the box
    instead of showing an empty wheel. Admin can edit/delete freely after."""
    if SpinSegment.query.first():
        return
    defaults = [
        ('₦50 Cashback',     'cashback',      50.0,  15, '#16A34A'),
        ('₦100 Wallet',      'wallet_balance', 100.0, 8,  '#2563EB'),
        ('₦20 Airtime',      'airtime',       20.0,  20, '#F59E0B'),
        ('50 Bonus Points',  'bonus_points',  50,    15, '#7C3AED'),
        ('₦200 Data',        'data',          200.0, 6,  '#DB2777'),
        ('₦100 Coupon',      'coupon',        100.0, 5,  '#0EA5E9'),
        ('Try Again',        'bonus_points',  0,     25, '#94A3B8'),
        ('₦500 Cashback',    'cashback',      500.0, 2,  '#DC2626'),
    ]
    for i, (label, rtype, value, weight, color) in enumerate(defaults):
        db.session.add(SpinSegment(
            label=label, reward_type=rtype, reward_value=value,
            weight=weight, is_active=True, color=color, display_order=i,
        ))
    db.session.flush()


def create_segment(data):
    label = (data.get('label') or '').strip()
    reward_type = data.get('reward_type')
    if not label:
        return None, 'label is required'
    if reward_type not in REWARD_TYPES:
        return None, f'reward_type must be one of {REWARD_TYPES}'
    try:
        reward_value = float(data.get('reward_value', 0))
        weight = int(data.get('weight', 10))
    except (TypeError, ValueError):
        return None, 'reward_value/weight must be numeric'
    if weight < 0:
        return None, 'weight cannot be negative'

    seg = SpinSegment(
        label=label, reward_type=reward_type, reward_value=reward_value,
        weight=weight, is_active=bool(data.get('is_active', True)),
        color=data.get('color', '#2196F3'),
        display_order=int(data.get('display_order', 0)),
    )
    db.session.add(seg)
    db.session.commit()
    return seg, None


def update_segment(segment_id, data):
    seg = SpinSegment.query.get(segment_id)
    if not seg:
        return None, 'Segment not found'

    if 'label' in data:
        seg.label = (data['label'] or '').strip() or seg.label
    if 'reward_type' in data:
        if data['reward_type'] not in REWARD_TYPES:
            return None, f'reward_type must be one of {REWARD_TYPES}'
        seg.reward_type = data['reward_type']
    if 'reward_value' in data:
        try:
            seg.reward_value = float(data['reward_value'])
        except (TypeError, ValueError):
            return None, 'Invalid reward_value'
    if 'weight' in data:
        try:
            w = int(data['weight'])
            if w < 0:
                return None, 'weight cannot be negative'
            seg.weight = w
        except (TypeError, ValueError):
            return None, 'Invalid weight'
    if 'is_active' in data:
        seg.is_active = bool(data['is_active'])
    if 'color' in data:
        seg.color = data['color']
    if 'display_order' in data:
        try:
            seg.display_order = int(data['display_order'])
        except (TypeError, ValueError):
            pass

    db.session.commit()
    return seg, None


def delete_segment(segment_id):
    seg = SpinSegment.query.get(segment_id)
    if not seg:
        return False, 'Segment not found'
    # Historical SpinEntry rows keep their own label/reward snapshot, so
    # deleting the segment definition doesn't corrupt past history.
    db.session.delete(seg)
    db.session.commit()
    return True, None


# ── daily status / anti-cheat ───────────────────────────────────────────────

def _get_or_create_counter(user_id, today):
    counter = (
        SpinDailyCounter.query
        .filter_by(user_id=user_id, spin_date=today)
        .with_for_update(read=False)
        .first()
    )
    if not counter:
        counter = SpinDailyCounter(user_id=user_id, spin_date=today, free_used=0, paid_used=0)
        db.session.add(counter)
        db.session.flush()
    return counter


def get_spin_status(user_id):
    cfg = get_config()
    today = date.today()
    counter = SpinDailyCounter.query.filter_by(user_id=user_id, spin_date=today).first()
    free_used = counter.free_used if counter else 0
    paid_used = counter.paid_used if counter else 0
    total_used = free_used + paid_used

    free_remaining = max(0, cfg.free_spins_per_day - free_used)
    spins_left_today = max(0, cfg.max_spins_per_day - total_used)

    can_spin_free = cfg.is_enabled and free_remaining > 0 and spins_left_today > 0
    can_spin_paid = (
        cfg.is_enabled and not can_spin_free and spins_left_today > 0
        and cfg.extra_spin_cost > 0
    )

    return {
        'spin_enabled':      cfg.is_enabled,
        'free_spins_per_day': cfg.free_spins_per_day,
        'free_spins_remaining': free_remaining,
        'max_spins_per_day': cfg.max_spins_per_day,
        'spins_used_today':  total_used,
        'spins_left_today':  spins_left_today,
        'extra_spin_cost':   cfg.extra_spin_cost,
        'can_spin_free':     can_spin_free,
        'can_spin_paid':     can_spin_paid,
        'can_spin':          can_spin_free or can_spin_paid,
    }


# ── reward payout ───────────────────────────────────────────────────────────

def _generate_coupon_code():
    while True:
        code = 'SPIN' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if not SpinCouponAward.query.filter_by(code=code).first():
            return code


def _get_or_create_points(user_id):
    points = UserPoints.query.filter_by(user_id=user_id).first()
    if not points:
        points = UserPoints(user_id=user_id, balance=0)
        db.session.add(points)
        db.session.flush()
    return points


def _apply_reward(user, segment):
    """Credits the won reward to the correct place. Returns extra info
    (e.g. a coupon code) to include in the spin result, if any."""
    reward_type = segment.reward_type
    value = segment.reward_value or 0
    extra = {}

    if value <= 0:
        return extra  # "Try Again" / zero-value segments — nothing to credit

    if reward_type in WALLET_CREDIT_TYPES:
        user.wallet_balance = round(user.wallet_balance + value, 2)

    elif reward_type == 'cashback':
        import cashback as cashback_service
        cashback_service.admin_adjust(user.id, value, note='Spin & Win reward')

    elif reward_type == 'bonus_points':
        points = _get_or_create_points(user.id)
        points.balance = (points.balance or 0) + int(value)

    elif reward_type == 'coupon':
        code = _generate_coupon_code()
        expires_at = datetime.utcnow() + timedelta(days=30)
        db.session.add(SpinCouponAward(
            user_id=user.id, code=code, discount_amount=value, expires_at=expires_at,
        ))
        extra['coupon_code'] = code
        extra['coupon_expires_at'] = expires_at.isoformat()

    return extra


def _pick_segment(segments):
    weights = [max(0, s.weight or 0) for s in segments]
    if sum(weights) <= 0:
        return random.choice(segments)
    return random.choices(segments, weights=weights, k=1)[0]


def perform_spin(user_id, ip_address=None):
    """
    The whole spin, atomically: validate eligibility, charge for a paid
    spin if applicable, pick a weighted-random segment, pay out the
    reward, log the counter + history row, and commit.
    """
    cfg = get_config()
    if not cfg.is_enabled:
        return {'status': 'error', 'message': 'Spin & Win is currently unavailable'}

    _seed_default_segments_if_empty()
    segments = list_segments(active_only=True)
    if not segments:
        return {'status': 'error', 'message': 'No prizes configured right now'}

    user = User.query.get(user_id)
    if not user:
        return {'status': 'error', 'message': 'User not found'}
    if not user.is_active:
        return {'status': 'error', 'message': 'Account is blocked'}

    today = date.today()
    counter = _get_or_create_counter(user_id, today)  # row-locked — anti double-spin
    total_used = counter.free_used + counter.paid_used

    if total_used >= cfg.max_spins_per_day:
        return {'status': 'error', 'message': 'You have reached today\'s spin limit. Come back tomorrow!'}

    is_free = counter.free_used < cfg.free_spins_per_day
    cost = 0.0

    if not is_free:
        if cfg.extra_spin_cost <= 0:
            return {'status': 'error', 'message': 'No free spins left today'}
        cost = cfg.extra_spin_cost
        if user.wallet_balance < cost:
            return {'status': 'error',
                    'message': f'Insufficient balance for an extra spin (₦{cost:,.2f}). '
                               f'Available: ₦{user.wallet_balance:,.2f}'}
        user.wallet_balance = round(user.wallet_balance - cost, 2)
        # The ₦{cost} fee is pure revenue (no cost-of-goods behind an extra
        # spin) — log it the same way every other purchase's profit is
        # logged, so it shows up in admin revenue/profit reporting instead
        # of just silently leaving the user's wallet with no paper trail.
        db.session.add(Profit(user_id=user_id, category='spin_fee', amount=cost))

    segment = _pick_segment(segments)
    extra = _apply_reward(user, segment)

    if is_free:
        counter.free_used += 1
    else:
        counter.paid_used += 1

    entry = SpinEntry(
        user_id=user_id, segment_id=segment.id, label=segment.label,
        reward_type=segment.reward_type, reward_value=segment.reward_value,
        is_free_spin=is_free, cost_paid=cost, ip_address=ip_address,
    )
    db.session.add(entry)
    db.session.commit()

    result = {
        'status': 'success',
        'data': {
            'segment_id':      segment.id,
            'label':           segment.label,
            'reward_type':     segment.reward_type,
            'reward_value':    round(segment.reward_value or 0, 2),
            'is_free_spin':    is_free,
            'cost_paid':       cost,
            'wallet_balance':  round(user.wallet_balance, 2),
            'wheel_segments':  [s.to_dict() for s in segments],   # for client to locate the winning index
        },
    }
    result['data'].update(extra)
    return result


def get_history(user_id, page=1, per_page=30):
    per_page = min(per_page, 100)
    q = SpinEntry.query.filter_by(user_id=user_id).order_by(SpinEntry.created_at.desc())
    total = q.count()
    rows = q.limit(per_page).offset((page - 1) * per_page).all()
    pages = (total + per_page - 1) // per_page if per_page else 1
    return [r.to_dict() for r in rows], total, pages


# ── admin stats ──────────────────────────────────────────────────────────────

def get_platform_stats():
    total_spins = SpinEntry.query.count()
    free_spins = SpinEntry.query.filter_by(is_free_spin=True).count()
    paid_spins = total_spins - free_spins

    revenue = (
        db.session.query(db.func.coalesce(db.func.sum(SpinEntry.cost_paid), 0.0)).scalar() or 0.0
    )

    payout_by_type = (
        db.session.query(SpinEntry.reward_type,
                          db.func.coalesce(db.func.sum(SpinEntry.reward_value), 0.0),
                          db.func.count(SpinEntry.id))
        .group_by(SpinEntry.reward_type)
        .all()
    )

    top_winners = (
        db.session.query(SpinEntry.user_id, User.name, User.email,
                          db.func.sum(SpinEntry.reward_value).label('total_won'),
                          db.func.count(SpinEntry.id).label('spins'))
        .join(User, User.id == SpinEntry.user_id)
        .group_by(SpinEntry.user_id, User.name, User.email)
        .order_by(db.desc('total_won'))
        .limit(10)
        .all()
    )

    return {
        'total_spins':        total_spins,
        'free_spins':         free_spins,
        'paid_spins':         paid_spins,
        'revenue_from_spins': round(revenue, 2),
        'payout_by_type': [
            {'reward_type': t, 'total_value': round(v, 2), 'count': c}
            for t, v, c in payout_by_type
        ],
        'top_winners': [
            {'user_id': uid, 'name': name, 'email': email,
             'total_won': round(total_won, 2), 'spins': spins}
            for uid, name, email, total_won, spins in top_winners
        ],
    }


# ── admin reporting (Spin & Win dashboard) ───────────────────────────────────
#
# What the admin sees:
#   * summary  - spins, players, free/paid, fees collected, cash given away ...
#   * prizes   - which prizes were won how often
#   * players  - who spun, how many times, how much they won (searchable)
#   * entries  - the live feed of every single spin
#
# The heavy lifting is done by small PURE functions (build_report /
# aggregate_players) that work on plain dicts, so they can be tested without a
# database; the DB-facing functions below only fetch rows and hand them over.

# Real naira that lands in a user's wallet / cashback wallet.
MONEY_TYPES = ('airtime', 'data', 'wallet_balance', 'cashback')
_WAT = timedelta(hours=1)          # Nigeria time = UTC + 1 (no daylight saving)
_PERIOD_DAYS = {'today': 0, '7d': 6, '30d': 29}


def _period_since(period):
    """UTC datetime at which the period starts (None = all time).
    Days start at midnight Nigeria time, so 'today' matches the admin's day."""
    days = _PERIOD_DAYS.get(period)
    if days is None:
        return None
    now_wat = datetime.utcnow() + _WAT
    midnight_wat = now_wat.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight_wat - timedelta(days=days) - _WAT


def _normalise_period(period):
    return period if period in ('today', '7d', '30d', 'all') else 'today'


def _row_to_dict(r):
    return {
        'id': r[0], 'user_id': r[1], 'label': r[2] or '', 'reward_type': r[3] or '',
        'reward_value': float(r[4] or 0), 'is_free': bool(r[5]),
        'cost': float(r[6] or 0), 'created_at': r[7],
    }


def _fetch_rows(since, user_id=None):
    q = db.session.query(
        SpinEntry.id, SpinEntry.user_id, SpinEntry.label, SpinEntry.reward_type,
        SpinEntry.reward_value, SpinEntry.is_free_spin, SpinEntry.cost_paid,
        SpinEntry.created_at,
    )
    if since is not None:
        q = q.filter(SpinEntry.created_at >= since)
    if user_id:
        q = q.filter(SpinEntry.user_id == user_id)
    return [_row_to_dict(r) for r in q.all()]


def _bucket(rtype, value):
    """Which total a reward counts towards: 'money', 'coupon', 'points' or 'none'."""
    if value <= 0:
        return 'none'
    if rtype in MONEY_TYPES:
        return 'money'
    if rtype == 'coupon':
        return 'coupon'
    if rtype == 'bonus_points':
        return 'points'
    return 'none'


def build_report(rows, coupons_used_value=0.0, coupons_used_count=0, period='today', now=None):
    """Summary, per-prize table and daily series from plain row dicts."""
    now = now or datetime.utcnow()
    total = len(rows)
    players = {r['user_id'] for r in rows}
    free = sum(1 for r in rows if r['is_free'])
    fees = sum(r['cost'] for r in rows)
    money = coupon = points = 0.0
    coupons_issued = 0
    zero_spins = 0
    for r in rows:
        b = _bucket(r['reward_type'], r['reward_value'])
        if b == 'money':
            money += r['reward_value']
        elif b == 'coupon':
            coupon += r['reward_value']
            coupons_issued += 1
        elif b == 'points':
            points += r['reward_value']
        else:
            zero_spins += 1

    summary = {
        'total_spins':        total,
        'unique_players':     len(players),
        'free_spins':         free,
        'paid_spins':         total - free,
        'fees_collected':     round(fees, 2),
        'cash_won':           round(money, 2),
        'coupons_issued':     coupons_issued,
        'coupon_value_issued': round(coupon, 2),
        'coupons_used_count': int(coupons_used_count),
        'coupons_used_value': round(coupons_used_value, 2),
        'points_awarded':     int(points),
        'no_prize_spins':     zero_spins,
        'avg_spins_per_player': round(total / len(players), 2) if players else 0,
        # what the feature really cost / earned: fees in, cash + used coupons out
        'net_result':         round(fees - money - coupons_used_value, 2),
    }

    # per prize label
    prizes = {}
    for r in rows:
        key = (r['label'], r['reward_type'])
        p = prizes.setdefault(key, {'label': r['label'], 'reward_type': r['reward_type'],
                                    'spins': 0, 'total_value': 0.0})
        p['spins'] += 1
        p['total_value'] += r['reward_value']
    by_prize = sorted(prizes.values(), key=lambda p: (-p['spins'], p['label']))[:15]
    for p in by_prize:
        p['total_value'] = round(p['total_value'], 2)

    # per day (Nigeria time), last 14 days at most, never before the period start
    today_wat = (now + _WAT).date()
    first_day = today_wat - timedelta(days=13)
    days_in_period = _PERIOD_DAYS.get(period)
    if days_in_period is not None:
        first_day = max(first_day, today_wat - timedelta(days=days_in_period))
    daily = {}
    d = first_day
    while d <= today_wat:
        daily[d] = {'date': d.isoformat(), 'spins': 0, 'players': set(), 'cash_won': 0.0, 'fees': 0.0}
        d += timedelta(days=1)
    for r in rows:
        if not r['created_at']:
            continue
        day = (r['created_at'] + _WAT).date()
        if day in daily:
            slot = daily[day]
            slot['spins'] += 1
            slot['players'].add(r['user_id'])
            slot['fees'] += r['cost']
            if _bucket(r['reward_type'], r['reward_value']) == 'money':
                slot['cash_won'] += r['reward_value']
    daily_list = [{'date': v['date'], 'spins': v['spins'], 'players': len(v['players']),
                   'cash_won': round(v['cash_won'], 2), 'fees': round(v['fees'], 2)}
                  for _, v in sorted(daily.items())]

    return {'summary': summary, 'by_prize': by_prize, 'daily': daily_list}


def aggregate_players(rows):
    """One entry per user: spins, free/paid, cash won, coupons, points, fees, last spin."""
    agg = {}
    for r in rows:
        a = agg.setdefault(r['user_id'], {
            'user_id': r['user_id'], 'spins': 0, 'free_spins': 0, 'paid_spins': 0,
            'cash_won': 0.0, 'coupon_value': 0.0, 'points': 0, 'fees_paid': 0.0,
            'last_spin': None, 'biggest_win': 0.0,
        })
        a['spins'] += 1
        if r['is_free']:
            a['free_spins'] += 1
        else:
            a['paid_spins'] += 1
        a['fees_paid'] += r['cost']
        b = _bucket(r['reward_type'], r['reward_value'])
        if b == 'money':
            a['cash_won'] += r['reward_value']
            a['biggest_win'] = max(a['biggest_win'], r['reward_value'])
        elif b == 'coupon':
            a['coupon_value'] += r['reward_value']
        elif b == 'points':
            a['points'] += int(r['reward_value'])
        if r['created_at'] and (a['last_spin'] is None or r['created_at'] > a['last_spin']):
            a['last_spin'] = r['created_at']
    for a in agg.values():
        a['cash_won'] = round(a['cash_won'], 2)
        a['coupon_value'] = round(a['coupon_value'], 2)
        a['fees_paid'] = round(a['fees_paid'], 2)
        a['biggest_win'] = round(a['biggest_win'], 2)
    return list(agg.values())


def _sort_players(players, sort):
    if sort == 'spins':
        return sorted(players, key=lambda p: (-p['spins'], -p['cash_won']))
    if sort == 'recent':
        return sorted(players, key=lambda p: p['last_spin'] or datetime.min, reverse=True)
    return sorted(players, key=lambda p: (-p['cash_won'], -p['spins']))      # 'won' (default)


def _coupons_used(since):
    q = SpinCouponAward.query.filter(SpinCouponAward.is_used.is_(True))
    if since is not None:
        q = q.filter(SpinCouponAward.used_at >= since)
    used = q.all()
    return sum(c.discount_amount or 0 for c in used), len(used)


def _user_brief(user):
    return {'name': user.name, 'email': user.email, 'phone': user.phone} if user else \
        {'name': 'Deleted user', 'email': '', 'phone': ''}


def get_admin_report(period='today'):
    period = _normalise_period(period)
    since = _period_since(period)
    rows = _fetch_rows(since)
    used_value, used_count = _coupons_used(since)
    report = build_report(rows, used_value, used_count, period)
    report['period'] = period
    report['since'] = since.isoformat() if since else None

    # five biggest cash wins in the period
    wins = sorted((r for r in rows if _bucket(r['reward_type'], r['reward_value']) == 'money'),
                  key=lambda r: -r['reward_value'])[:5]
    users = {u.id: u for u in User.query.filter(User.id.in_(list({r['user_id'] for r in wins}))).all()} if wins else {}
    report['biggest_wins'] = [
        {'label': r['label'], 'reward_value': round(r['reward_value'], 2),
         'created_at': r['created_at'].isoformat() if r['created_at'] else None,
         **_user_brief(users.get(r['user_id']))}
        for r in wins
    ]
    return report


def get_admin_players(period='today', sort='won', q='', page=1, per_page=20):
    period = _normalise_period(period)
    since = _period_since(period)
    per_page = max(1, min(per_page, 50))
    page = max(1, page)
    players = aggregate_players(_fetch_rows(since))

    q = (q or '').strip()
    if q:
        from sqlalchemy import or_
        like = f'%{q}%'
        matches = User.query.filter(
            or_(User.name.ilike(like), User.email.ilike(like), User.phone.ilike(like))
        ).limit(500).all()
        ids = {u.id for u in matches}
        players = [p for p in players if p['user_id'] in ids]

    players = _sort_players(players, sort)
    total = len(players)
    pages = (total + per_page - 1) // per_page
    chunk = players[(page - 1) * per_page: page * per_page]
    users = {u.id: u for u in User.query.filter(User.id.in_([p['user_id'] for p in chunk])).all()} if chunk else {}
    out = []
    for p in chunk:
        item = dict(p)
        item['last_spin'] = p['last_spin'].isoformat() if p['last_spin'] else None
        item.update(_user_brief(users.get(p['user_id'])))
        out.append(item)
    return {'players': out, 'total': total, 'pages': pages, 'page': page,
            'period': period, 'sort': sort}


def get_admin_entries(period='today', user_id=None, page=1, per_page=30):
    period = _normalise_period(period)
    since = _period_since(period)
    per_page = max(1, min(per_page, 100))
    page = max(1, page)
    q = db.session.query(SpinEntry, User).join(User, User.id == SpinEntry.user_id)
    if since is not None:
        q = q.filter(SpinEntry.created_at >= since)
    if user_id:
        q = q.filter(SpinEntry.user_id == user_id)
    total = q.count()
    rows = q.order_by(SpinEntry.created_at.desc()).limit(per_page).offset((page - 1) * per_page).all()
    entries = []
    for e, u in rows:
        d = e.to_dict()
        d.update({'user_id': u.id, 'name': u.name, 'email': u.email, 'phone': u.phone,
                  'ip_address': e.ip_address})
        entries.append(d)
    pages = (total + per_page - 1) // per_page
    return {'entries': entries, 'total': total, 'pages': pages, 'page': page, 'period': period}
