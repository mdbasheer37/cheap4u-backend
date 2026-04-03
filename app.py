import os
from flask import Flask, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from config import Config
from models import db
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)

def create_app():
    app = Flask(__name__)
    
    # Use environment variables with fallbacks
    app.config.from_object(Config)
    
    # Override database URL for production
    if os.getenv('DATABASE_URL'):
        app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL').replace('postgres://', 'postgresql://')
    
    # Initialize extensions
    CORS(app, resources={r"/api/*": {"origins": "*"}})
    db.init_app(app)
    jwt = JWTManager(app)
    
    # Register blueprints
    from auth import auth_bp
    from payment import payment_bp
    from vtpass import vtpass_bp
    from referral import referral_bp
    from admin import admin_bp
    from plans import plans_bp
    
    app.register_blueprint(plans_bp)
    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(payment_bp, url_prefix='/api/payment')
    app.register_blueprint(vtpass_bp)
    app.register_blueprint(referral_bp)
    app.register_blueprint(admin_bp)
    
    # Health check
    @app.route('/health', methods=['GET'])
    def health_check():
        return jsonify({
            'status': 'healthy',
            'message': 'Backend is running',
            'version': '1.0.0'
        })
    
    @app.route('/', methods=['GET'])
    def index():
        return jsonify({
            'message': 'Cheap4U API is running',
            'endpoints': [
                '/api/auth/register',
                '/api/auth/login',
                '/api/auth/verify-otp',
                '/api/payment/initialize',
                '/api/vtpass/*',
                '/health'
            ]
        })
    
    # Create tables
    with app.app_context():
        db.create_all()
        # In app.py, after db.create_all()
        from init_plans import init_all
init_all() 
        print("✅ Database tables ready")
    
    return app

# For Gunicorn
app = create_app()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=Config.DEBUG) 
