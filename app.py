from flask import Flask, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from config import Config
from models import db
import os

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    
    # Initialize extensions
    CORS(app, resources={r"/api/*": {"origins": "*"}})
    db.init_app(app)
    jwt = JWTManager(app)
    
    # Import blueprints
    from auth import auth_bp
    from payment import payment_bp
    from routes import routes_bp
    from referral import referral_bp
    from admin import admin_bp
    from plans import plans_bp
    
    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(payment_bp, url_prefix='/api/payment')
    app.register_blueprint(vtpass_bp)
    app.register_blueprint(referral_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(plans_bp)
    
    # Health check
    @app.route('/health', methods=['GET'])
    def health_check():
        return jsonify({
            'status': 'healthy',
            'message': 'Backend is running',
            'database': 'connected' if db.session.is_active else 'disconnected',
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
                '/api/plans/data',
                '/api/plans/cable',
                '/api/vtpass/airtime',
                '/api/vtpass/data',
                '/api/vtpass/electricity',
                '/api/vtpass/cable-tv',
                '/api/vtpass/exam-pins',
                '/health'
            ]
        })
    
    # Create tables
    with app.app_context():
        db.create_all()
        print("✅ Database tables created/verified")
        
        # Initialize plans (only if empty)
        from init_plans import init_all
        init_all()
    
    return app

# For Gunicorn
app = create_app()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=Config.DEBUG)
