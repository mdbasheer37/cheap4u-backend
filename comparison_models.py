# comparison_models.py — Smart Price Comparison config
#
# The comparison engine itself is stateless — it reads live DataPlan,
# Transaction and Coupon data on every request rather than caching
# results, so it's never stale. This file only holds the admin-tunable
# scoring weights and lookback window.

from datetime import datetime
from models import db


class ComparisonConfig(db.Model):
    """Singleton settings row (id is always 1)."""
    __tablename__ = 'comparison_config'

    id                 = db.Column(db.Integer, primary_key=True)
    is_enabled          = db.Column(db.Boolean, default=True)

    # How "best value" is scored: weighted blend of price, speed (recent
    # avg processing time) and reliability (recent success rate). Kept as
    # three independent weights (not forced to sum to 1) and normalized at
    # scoring time, so admin can tweak one without recalculating the rest.
    price_weight        = db.Column(db.Float, default=0.4)
    speed_weight         = db.Column(db.Float, default=0.3)
    reliability_weight   = db.Column(db.Float, default=0.3)

    stats_window_days    = db.Column(db.Integer, default=30)    # how far back to look for speed/reliability stats
    stats_sample_limit    = db.Column(db.Integer, default=300)   # cap per provider, keeps queries fast

    updated_at            = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'is_enabled':          self.is_enabled,
            'price_weight':        self.price_weight,
            'speed_weight':        self.speed_weight,
            'reliability_weight':  self.reliability_weight,
            'stats_window_days':   self.stats_window_days,
            'stats_sample_limit':  self.stats_sample_limit,
        }
