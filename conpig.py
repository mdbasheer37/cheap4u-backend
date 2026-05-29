# conpig.py  (keep this exact filename — your Render server uses conpig not config)
import os
from datetime import timedelta
from dotenv import load_dotenv

# override=False means Render dashboard env vars always win over .env file
load_dotenv(override=False)


class Config:
    SECRET_KEY                  = os.getenv('SECRET_KEY', 'cheap4u-secret-key-change-me')
    JWT_SECRET_KEY              = os.getenv('JWT_SECRET_KEY', 'cheap4u-jwt-key-change-me')
    JWT_ACCESS_TOKEN_EXPIRES    = timedelta(days=7)

    # Database — Render injects DATABASE_URL automatically
    _db_url = os.getenv('DATABASE_URL', '')
    if _db_url.startswith('postgres://'):
        _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_DATABASE_URI     = _db_url if _db_url else 'sqlite:///database.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Paystack
    PAYSTACK_SECRET_KEY         = os.getenv('PAYSTACK_SECRET_KEY', '')
    PAYSTACK_PUBLIC_KEY         = os.getenv('PAYSTACK_PUBLIC_KEY', '')

    # CheapDataHub
    CHEAPDATAHUB_API_KEY        = os.getenv('CHEAPDATAHUB_API_KEY', '')
    CHEAPDATAHUB_BASE_URL       = os.getenv('CHEAPDATAHUB_BASE_URL', 'https://www.cheapdatahub.ng/api/v1/resellers/')

    # VtuNaija
    VTUNAIJA_API_KEY            = os.getenv('VTUNAIJA_API_KEY', '')
    VTUNAIJA_BASE_URL           = os.getenv('VTUNAIJA_BASE_URL', 'https://vtunaija.com.ng/api')

    # Termii
    TERMII_API_KEY              = os.getenv('TERMII_API_KEY', '')
    TERMII_SENDER_ID            = os.getenv('TERMII_SENDER_ID', 'Cheap4uApp')

    # App
    DEBUG                       = os.getenv('DEBUG', 'False').lower() == 'true'
    BACKEND_URL                 = os.getenv('BACKEND_URL', 'https://cheap4u-backend.onrender.com')
    ALLOWED_ORIGINS             = os.getenv('ALLOWED_ORIGINS', '*')

    # Profit margins
    PROFIT_MARGIN_AIRTIME       = float(os.getenv('PROFIT_MARGIN_AIRTIME', '5'))
    PROFIT_MARGIN_ELECTRICITY   = float(os.getenv('PROFIT_MARGIN_ELECTRICITY', '5'))
    PROFIT_MARGIN_EXAM_PIN      = float(os.getenv('PROFIT_MARGIN_EXAM_PIN', '10'))
 

    ADMIN_EMAILS = ['admin@cheap4u.com', 'muhammadibrahim3766@gmail.com']

