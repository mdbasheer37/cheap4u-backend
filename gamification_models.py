# gamification_models.py — Gamification data models (XP, Levels, Badges,
# Missions, Leaderboard)
#
# Uses the SAME `db` SQLAlchemy instance as models.py so these tables are
# created automatically by the existing `db.create_all()` call in app.py.
#
# Deliberately separate from the existing Monthly Champion Challenge
# system (challenge_models.py) and the Spin & Win bonus-points wallet
# (spin_models.UserPoints) rather than merging into either: XP is a
# never-spent progress metric that drives Level, "points" from Spin & Win
# remain a separate spendable-ish reward currency, and the Monthly
# Challenge's spend leaderboard keeps its own identity as one specific
# competition. A level-up here awards bonus points into that same
# UserPoints wallet though, so the systems reward into one place a user
# actually sees and can act on.

from datetime import datetime
from models import db

MISSION_PERIODS = ('daily', 'weekly')
MISSION_TARGET_TYPES = ('transaction_count', 'spend_amount', 'category_count')
BADGE_CRITERIA_TYPES = ('total_transactions', 'total_spent', 'total_xp', 'category_transactions')


class GamificationConfig(db.Model):
    """Singleton settings row (id is always 1)."""
    __tablename__ = 'gamification_config'

    id                        = db.Column(db.Integer, primary_key=True)
    is_enabled                = db.Column(db.Boolean, default=True)

    xp_per_100_naira_spent    = db.Column(db.Float, default=1.0)    # XP earned per ₦100 of a purchase
    xp_daily_login            = db.Column(db.Integer, default=5)    # XP for the first login of a calendar day
    level_up_bonus_points     = db.Column(db.Integer, default=20)   # bonus points (Spin & Win wallet) on level-up

    updated_at                 = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'is_enabled':              self.is_enabled,
            'xp_per_100_naira_spent':  self.xp_per_100_naira_spent,
            'xp_daily_login':          self.xp_daily_login,
            'level_up_bonus_points':   self.level_up_bonus_points,
        }


class XPLevel(db.Model):
    """Admin-defined level ladder. Reuses the Bronze/Silver/Gold/Platinum/
    Diamond naming already used by the Referral Level System for a
    consistent feel across the app, but is fully admin-editable."""
    __tablename__ = 'xp_levels'

    id                = db.Column(db.Integer, primary_key=True)
    level_number      = db.Column(db.Integer, unique=True, nullable=False)
    title             = db.Column(db.String(50), nullable=False)
    xp_required       = db.Column(db.Integer, nullable=False)    # cumulative XP to reach this level
    icon              = db.Column(db.String(40), default='star')
    perk_description  = db.Column(db.String(150), nullable=True)

    def to_dict(self):
        return {
            'level_number':      self.level_number,
            'title':             self.title,
            'xp_required':       self.xp_required,
            'icon':              self.icon,
            'perk_description':  self.perk_description,
        }


class UserXP(db.Model):
    """One row per user — running XP total and cached current level."""
    __tablename__ = 'user_xp'

    id             = db.Column(db.Integer, primary_key=True)
    user_id        = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True, index=True)
    total_xp       = db.Column(db.Integer, default=0)
    current_level  = db.Column(db.Integer, default=1)
    updated_at     = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User')

    def to_dict(self):
        return {'total_xp': self.total_xp or 0, 'current_level': self.current_level or 1}


class XPEntry(db.Model):
    """Ledger of every XP award — purchase, daily login, mission
    completion, badge earned, or admin adjustment."""
    __tablename__ = 'xp_entries'

    id            = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    amount        = db.Column(db.Integer, nullable=False)
    # 'purchase' | 'daily_login' | 'mission' | 'badge' | 'admin_adjust'
    source        = db.Column(db.String(20), nullable=False)
    description   = db.Column(db.String(150), nullable=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def to_dict(self):
        return {
            'amount':      self.amount,
            'source':      self.source,
            'description': self.description,
            'created_at':  self.created_at.isoformat() if self.created_at else None,
        }


class Badge(db.Model):
    """Admin-defined achievement. Awarded automatically the first time a
    user's stats satisfy criteria_type/criteria_value (optionally scoped
    to one purchase category via `category`)."""
    __tablename__ = 'badges'

    id                 = db.Column(db.Integer, primary_key=True)
    code               = db.Column(db.String(40), unique=True, nullable=False)
    name               = db.Column(db.String(80), nullable=False)
    description        = db.Column(db.String(200), nullable=True)
    icon               = db.Column(db.String(40), default='medal')

    criteria_type      = db.Column(db.String(30), nullable=False)    # see BADGE_CRITERIA_TYPES
    criteria_value     = db.Column(db.Float, nullable=False)
    category           = db.Column(db.String(30), nullable=True)     # only for 'category_transactions'

    is_active          = db.Column(db.Boolean, default=True)
    created_at          = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id':              self.id,
            'code':            self.code,
            'name':            self.name,
            'description':     self.description,
            'icon':            self.icon,
            'criteria_type':   self.criteria_type,
            'criteria_value':  self.criteria_value,
            'category':        self.category,
            'is_active':       self.is_active,
        }


class UserBadge(db.Model):
    """A badge a user has actually earned. One row per (user, badge)."""
    __tablename__ = 'user_badges'

    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    badge_id    = db.Column(db.Integer, db.ForeignKey('badges.id'), nullable=False)
    awarded_at  = db.Column(db.DateTime, default=datetime.utcnow)

    badge = db.relationship('Badge')

    __table_args__ = (db.UniqueConstraint('user_id', 'badge_id', name='uq_user_badge'),)

    def to_dict(self):
        d = self.badge.to_dict() if self.badge else {}
        d['awarded_at'] = self.awarded_at.isoformat() if self.awarded_at else None
        return d


class Mission(db.Model):
    """Admin-defined daily or weekly task template (e.g. 'Buy data 2 times
    today' or 'Spend ₦5,000 this week'). Instantiated per-user-per-period
    in UserMissionProgress the first time it's touched."""
    __tablename__ = 'missions'

    id             = db.Column(db.Integer, primary_key=True)
    title          = db.Column(db.String(100), nullable=False)
    description    = db.Column(db.String(200), nullable=True)
    period         = db.Column(db.String(10), nullable=False)     # 'daily' | 'weekly'
    target_type    = db.Column(db.String(20), nullable=False)     # see MISSION_TARGET_TYPES
    target_value   = db.Column(db.Float, nullable=False)
    category       = db.Column(db.String(30), nullable=True)      # optional purchase-type filter

    xp_reward       = db.Column(db.Integer, default=10)
    points_reward   = db.Column(db.Integer, default=0)            # credited to the Spin & Win points wallet

    is_active        = db.Column(db.Boolean, default=True)
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id':             self.id,
            'title':          self.title,
            'description':    self.description,
            'period':         self.period,
            'target_type':    self.target_type,
            'target_value':   self.target_value,
            'category':       self.category,
            'xp_reward':      self.xp_reward,
            'points_reward':  self.points_reward,
            'is_active':      self.is_active,
        }


class UserMissionProgress(db.Model):
    """One row per user, per mission, per period instance. `period_key` is
    'YYYY-MM-DD' for daily missions and 'YYYY-Www' (ISO week) for weekly
    ones, so a new row (fresh progress) is created automatically at the
    start of each period without a separate reset job."""
    __tablename__ = 'user_mission_progress'

    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    mission_id      = db.Column(db.Integer, db.ForeignKey('missions.id'), nullable=False)
    period_key      = db.Column(db.String(10), nullable=False)

    progress_value   = db.Column(db.Float, default=0.0)
    is_completed     = db.Column(db.Boolean, default=False)
    completed_at     = db.Column(db.DateTime, nullable=True)
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)

    mission = db.relationship('Mission')

    __table_args__ = (
        db.UniqueConstraint('user_id', 'mission_id', 'period_key', name='uq_user_mission_period'),
    )

    def to_dict(self):
        m = self.mission
        return {
            'mission_id':      self.mission_id,
            'title':           m.title if m else None,
            'description':     m.description if m else None,
            'period':          m.period if m else None,
            'target_value':    m.target_value if m else None,
            'progress_value':  self.progress_value,
            'is_completed':    self.is_completed,
            'xp_reward':       m.xp_reward if m else 0,
            'points_reward':   m.points_reward if m else 0,
        }
