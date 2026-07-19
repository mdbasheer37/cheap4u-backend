# challenge_models.py — Monthly Champion Challenge data models
#
# Uses the SAME `db` SQLAlchemy instance as models.py so these tables are
# created automatically by the existing `db.create_all()` call in app.py.
# No changes to models.py are required — just import this module once
# (done in challenge.py) so SQLAlchemy registers the tables before
# create_all() runs.

from datetime import datetime
from models import db


class ChallengeConfig(db.Model):
    """
    Singleton settings row (id is always 1) controlling the whole
    Monthly Champion Challenge feature. Editable from the admin panel.
    """
    __tablename__ = 'challenge_config'

    id                     = db.Column(db.Integer, primary_key=True)
    is_enabled             = db.Column(db.Boolean, default=True)

    # 🥇 1st place reward = this % of the winner's total monthly purchases
    first_place_percent    = db.Column(db.Float, default=50.0)
    # 🥈 / 🥉 fixed wallet bonuses
    second_place_bonus     = db.Column(db.Float, default=10000.0)
    third_place_bonus      = db.Column(db.Float, default=5000.0)

    # Optional floor — a user must have spent at least this much in the
    # month to be eligible for a reward (0 = no minimum).
    min_qualifying_amount  = db.Column(db.Float, default=0.0)

    # Bookkeeping for the monthly cron job — the last month (YYYY-MM)
    # that was fully processed (winners archived + credited).
    last_processed_month   = db.Column(db.String(7), nullable=True)

    updated_at             = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'is_enabled':            self.is_enabled,
            'first_place_percent':   self.first_place_percent,
            'second_place_bonus':    self.second_place_bonus,
            'third_place_bonus':     self.third_place_bonus,
            'min_qualifying_amount': self.min_qualifying_amount,
            'last_processed_month':  self.last_processed_month,
        }


class ChallengeEntry(db.Model):
    """
    One row per user per month — the running leaderboard total.
    Ranking is computed on read: ORDER BY total_amount DESC, updated_at ASC
    (ties go to whoever reached that total first).
    """
    __tablename__ = 'challenge_entries'

    id                  = db.Column(db.Integer, primary_key=True)
    user_id             = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    month               = db.Column(db.String(7), nullable=False)   # 'YYYY-MM'
    total_amount        = db.Column(db.Float, default=0.0)
    purchase_count      = db.Column(db.Integer, default=0)

    # Highest (numerically lowest) rank we've already sent a push
    # notification for, so a user isn't re-notified for the same milestone.
    last_notified_rank  = db.Column(db.Integer, default=999999)

    created_at          = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at          = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User')

    __table_args__ = (
        db.UniqueConstraint('user_id', 'month', name='uq_challenge_entry_user_month'),
    )


class ChallengeWinner(db.Model):
    """
    Permanent archive of every monthly Top-3 winner and the reward paid.
    Rows are created once, at month-end processing, and never mutated
    except to flip `credited` when the wallet payout completes.
    """
    __tablename__ = 'challenge_winners'

    id             = db.Column(db.Integer, primary_key=True)
    month          = db.Column(db.String(7), nullable=False)
    rank           = db.Column(db.Integer, nullable=False)          # 1, 2, 3
    user_id        = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    user_name      = db.Column(db.String(100))                      # snapshot at win-time
    total_amount   = db.Column(db.Float, default=0.0)
    reward_amount  = db.Column(db.Float, default=0.0)
    reward_type    = db.Column(db.String(20))                       # 'cashback' | 'bonus'
    credited       = db.Column(db.Boolean, default=False)
    credited_at    = db.Column(db.DateTime, nullable=True)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User')

    def to_dict(self):
        return {
            'id':            self.id,
            'month':         self.month,
            'rank':          self.rank,
            'user_id':       self.user_id,
            'user_name':     self.user_name,
            'total_amount':  round(self.total_amount or 0, 2),
            'reward_amount': round(self.reward_amount or 0, 2),
            'reward_type':   self.reward_type,
            'credited':      self.credited,
            'credited_at':   self.credited_at.isoformat() if self.credited_at else None,
        }


class ChallengeNotification(db.Model):
    """
    Lightweight in-app / push-ready notification log for challenge events:
    entering Top 10, entering Top 3, becoming #1, challenge ending,
    reward credited. Frontend polls /api/challenge/notifications and can
    forward unseen ones to the OS notification tray.
    """
    __tablename__ = 'challenge_notifications'

    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    type       = db.Column(db.String(30))     # top10 | top3 | first_place | challenge_ended | reward_credited
    title      = db.Column(db.String(150))
    message    = db.Column(db.String(300))
    is_read    = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id':         self.id,
            'type':       self.type,
            'title':      self.title,
            'message':    self.message,
            'is_read':    self.is_read,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
