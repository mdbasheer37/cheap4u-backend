# extensions.py
#
# Shared Flask extension instances that need to be imported by more than
# one blueprint module. Keeping them here (instead of inside app.py)
# avoids circular imports: app.py does `from ai_chat import ai_chat_bp`
# and ai_chat.py needs the limiter — if the limiter lived in app.py,
# ai_chat.py would have to `from app import limiter`, which in turn
# imports ai_chat.py again → circular import crash.

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Rate limits the AI chat endpoints (and anything else that opts in)
# per client IP address. `init_app(app)` is called once, in app.py.
limiter = Limiter(key_func=get_remote_address, default_limits=[])
