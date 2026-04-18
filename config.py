# config.py
import os
from datetime import timedelta
from dotenv import load_dotenv

# Only load .env file in local development.
# On Render, environment variables are set directly in the dashboard
# and must NOT be overridden by a blank .env file.
# load_dotenv() with override=False means: only set variables that
# are NOT already set in the environment — so Render's values win.
load_dotenv(override=False)


class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')

    # Database — handle Render's PostgreSQL URL format
    database_url = os.getenv('DATABASE_URL')
    if database_url and database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_DATABASE_URI = database_url or 'sqlite:///database.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # JWT
    JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'jwt-secret-key-change-in-production')
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(days=7)

    # Paystack
    PAYSTACK_SECRET_KEY = os.getenv('PAYSTACK_SECRET_KEY', '')
    PAYSTACK_PUBLIC_KEY = os.getenv('PAYSTACK_PUBLIC_KEY', '')

    # CheapDataHub
    CHEAPDATAHUB_API_KEY = os.getenv('CHEAPDATAHUB_API_KEY', '')
    CHEAPDATAHUB_BASE_URL = os.getenv('CHEAPDATAHUB_BASE_URL', 'https://www.cheapdatahub.ng/api/v1/resellers/')

    # VtuNaija
    VTUNAIJA_API_KEY = os.getenv('VTUNAIJA_API_KEY', '')
    VTUNAIJA_BASE_URL = os.getenv('VTUNAIJA_BASE_URL', 'https://vtunaija.com.ng/api')

    # Termii SMS
    TERMII_API_KEY = os.getenv('TERMII_API_KEY', '')
    TERMII_SENDER_ID = os.getenv('TERMII_SENDER_ID', 'Cheap4uApp')

    # Admin emails
    ADMIN_EMAILS = ['admin@cheap4u.com', 'muhammadibrahim376@gmail.com']

    # App settings
    DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'
    BACKEND_URL = os.getenv('BACKEND_URL', 'http://localhost:10000')

    # Profit margins
    PROFIT_MARGIN_AIRTIME = float(os.getenv('PROFIT_MARGIN_AIRTIME', '5'))
    PROFIT_MARGIN_ELECTRICITY = float(os.getenv('PROFIT_MARGIN_ELECTRICITY', '5'))
    PROFIT_MARGIN_EXAM_PIN = float(os.getenv('PROFIT_MARGIN_EXAM_PIN', '10'))
