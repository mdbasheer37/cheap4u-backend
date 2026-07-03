# conpig.py
import os
from datetime import timedelta
from dotenv import load_dotenv

# override=False: Render dashboard env vars always win over .env file
load_dotenv(override=False)


def _fix_db_url(url):
    """Fix postgres:// → postgresql:// for SQLAlchemy."""
    if url and url.startswith('postgres://'):
        return url.replace('postgres://', 'postgresql://', 1)
    return url


class Config:
    SECRET_KEY               = os.getenv('SECRET_KEY', 'cheap4u-secret-key')
    JWT_SECRET_KEY           = os.getenv('JWT_SECRET_KEY', 'cheap4u-jwt-key')
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(days=7)

    # Database — use os.environ directly (bypasses dotenv caching)
    # Tries DATABASE_URL first, then falls back to SQLite
    _raw_url = os.environ.get('DATABASE_URL') or os.getenv('DATABASE_URL', '')
    SQLALCHEMY_DATABASE_URI = _fix_db_url(_raw_url) or 'sqlite:///database.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Connection pool settings to handle Render's cold starts.
    # 'connect_timeout' is a psycopg2 (Postgres)-only kwarg — it breaks
    # sqlite3.connect() if DATABASE_URL is ever missing/invalid and we
    # silently fall back to SQLite. Only apply it for Postgres.
    if SQLALCHEMY_DATABASE_URI.startswith('postgresql'):
        SQLALCHEMY_ENGINE_OPTIONS = {
            'pool_pre_ping': True,
            'pool_recycle':  300,
            'connect_args':  {'connect_timeout': 10},
        }
    else:
        SQLALCHEMY_ENGINE_OPTIONS = {
            'pool_pre_ping': True,
            'pool_recycle':  300,
        }

    PAYSTACK_SECRET_KEY  = os.getenv('PAYSTACK_SECRET_KEY', '')
    PAYSTACK_PUBLIC_KEY  = os.getenv('PAYSTACK_PUBLIC_KEY', '')

    CHEAPDATAHUB_API_KEY  = os.getenv('CHEAPDATAHUB_API_KEY', '')
    CHEAPDATAHUB_BASE_URL = os.getenv('CHEAPDATAHUB_BASE_URL',
                            'https://www.cheapdatahub.ng/api/v1/resellers/')

    VTUNAIJA_API_KEY  = os.getenv('VTUNAIJA_API_KEY', '')
    VTUNAIJA_BASE_URL = os.getenv('VTUNAIJA_BASE_URL',
                        'https://vtunaija.com.ng/api')

    TERMII_API_KEY   = os.getenv('TERMII_API_KEY', '')
    TERMII_SENDER_ID = os.getenv('TERMII_SENDER_ID', 'Cheap4uApp')

    DEBUG       = os.getenv('DEBUG', 'False').lower() == 'true'
    BACKEND_URL = os.getenv('BACKEND_URL',
                  'https://cheap4u-backend.onrender.com')

    PROFIT_MARGIN_AIRTIME     = float(os.getenv('PROFIT_MARGIN_AIRTIME', '5'))
    PROFIT_MARGIN_ELECTRICITY = float(os.getenv('PROFIT_MARGIN_ELECTRICITY', '5'))
    PROFIT_MARGIN_EXAM_PIN    = float(os.getenv('PROFIT_MARGIN_EXAM_PIN', '10'))
