# app.py
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
    # Restrict CORS to your frontend domain in production
    CORS(app, resources={r"/api/*": {"origins": os.getenv("ALLOWED_ORIGINS", "*")}})
    db.init_app(app)
    JWTManager(app)

    # Import blueprints
    from auth import auth_bp
    from payment import payment_bp
    from routes import vtpass_bp
    from referral import referral_bp
    from admin import admin_bp       # admin_bp already has url_prefix='/api/admin' set
    from plans import plans_bp
    from debug_routes import debug_bp  # TEMPORARY — remove after SMS is working

    # Fixed: do NOT pass url_prefix here for admin_bp — it sets its own prefix.
    # All other blueprints also use their own prefixes so no url_prefix needed.
    app.register_blueprint(auth_bp)
    app.register_blueprint(payment_bp)
    app.register_blueprint(vtpass_bp)
    app.register_blueprint(referral_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(plans_bp)
    app.register_blueprint(debug_bp)  # TEMPORARY — remove after SMS is working

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

    with app.app_context():
        db.create_all()
        print("✅ Database tables created/verified")
        from init_plans import init_all
        init_all()

    return app


# For Gunicorn
app = create_app()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=Config.DEBUG)
