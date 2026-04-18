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

    CORS(app, resources={r"/api/*": {"origins": os.getenv("ALLOWED_ORIGINS", "*")}})
    db.init_app(app)
    JWTManager(app)

    from auth import auth_bp
    from payment import payment_bp
    from referral import referral_bp
    from admin import admin_bp
    from plans import plans_bp

    # Import vtpass blueprint — try every possible filename
    vtpass_bp = None
    for mod_name in ('vtpass', 'routes'):
        try:
            import importlib
            mod = importlib.import_module(mod_name)
            # Get whichever blueprint name exists in that module
            for attr in ('vtpass_bp', 'bp', 'main'):
                if hasattr(mod, attr):
                    vtpass_bp = getattr(mod, attr)
                    print(f"✅ Loaded vtpass blueprint from {mod_name}.{attr}")
                    break
            if vtpass_bp:
                break
        except ImportError:
            continue

    app.register_blueprint(auth_bp)
    app.register_blueprint(payment_bp)
    if vtpass_bp:
        app.register_blueprint(vtpass_bp)
    else:
        print("⚠️  vtpass blueprint not found — vtpass routes will be unavailable")
    app.register_blueprint(referral_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(plans_bp)

    # TEMPORARY debug blueprint — remove after SMS is confirmed working
    try:
        from debug_routes import debug_bp
        app.register_blueprint(debug_bp)
        print("✅ Debug routes loaded")
    except ImportError:
        pass

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
        return jsonify({'message': 'Cheap4U API is running'})

    with app.app_context():
        db.create_all()
        print("✅ Database tables created/verified")
        from init_plans import init_all
        init_all()

    return app


app = create_app()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=Config.DEBUG)
