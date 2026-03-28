import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Flask
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
    
    # Database
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'sqlite:///database.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # JWT
    JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'jwt-secret-key-change-in-production')
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(days=7)
    
    # Paystack
    PAYSTACK_SECRET_KEY = os.getenv('PAYSTACK_SECRET_KEY', 'sk_test_...')
    PAYSTACK_PUBLIC_KEY = os.getenv('PAYSTACK_PUBLIC_KEY', 'pk_test_...')
    
    # VTPass
    VTPASS_API_KEY = os.getenv('VTPASS_API_KEY', '')
    VTPASS_BASE_URL = os.getenv('VTPASS_BASE_URL', 'https://vtpass.com/api')
    
    # Termii (SMS)
    TERMII_API_KEY = os.getenv('TERMII_API_KEY', '')
    TERMII_SENDER_ID = os.getenv('TERMII_SENDER_ID', 'Cheap4uApp')
    
    # Admin emails
    ADMIN_EMAILS = ['admin@cheap4u.com', 'muhammadibrahim376@gmail.com']
    
    # App settings
    DEBUG = os.getenv('DEBUG', 'True').lower() == 'true'
    BACKEND_URL = os.getenv('BACKEND_URL', 'http://localhost:10000') 
