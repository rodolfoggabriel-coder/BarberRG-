import os
from flask import Flask, send_from_directory
from config import Config
from models import db

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Init database
    db.init_app(app)

    # Ensure upload folder exists
    os.makedirs(app.config.get('UPLOAD_FOLDER', 'static/uploads'), exist_ok=True)

    # Register blueprints
    from blueprints.admin import admin_bp
    from blueprints.barbeiro import barbeiro_bp
    from blueprints.public import public_bp
    from blueprints.rede import rede_bp

    app.register_blueprint(admin_bp)
    app.register_blueprint(barbeiro_bp)
    app.register_blueprint(public_bp)
    app.register_blueprint(rede_bp)

    # Service Worker route (must be at root)
    @app.route('/sw.js')
    def service_worker():
        return send_from_directory('static', 'sw.js', mimetype='application/javascript')

    @app.route('/manifest.json')
    def manifest():
        return send_from_directory('static', 'manifest.json', mimetype='application/json')

    # Context processor - inject white_label flag
    @app.context_processor
    def inject_globals():
        from flask import session
        from models import Barbearia
        white_label = False
        barbearia_id = session.get('barbearia_id')
        if barbearia_id:
            barb = Barbearia.query.get(barbearia_id)
            if barb:
                white_label = barb.white_label
        return dict(white_label=white_label)

    # Create tables
    with app.app_context():
        db.create_all()

    # Setup scheduled jobs
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from jobs import (
            processar_lembretes_24h,
            processar_lembretes_1h,
            processar_pos_atendimento,
            processar_reativacao
        )

        scheduler = BackgroundScheduler()
        scheduler.add_job(
            processar_lembretes_24h, 'interval', minutes=5, args=[app],
            id='lembretes_24h', replace_existing=True
        )
        scheduler.add_job(
            processar_lembretes_1h, 'interval', minutes=5, args=[app],
            id='lembretes_1h', replace_existing=True
        )
        scheduler.add_job(
            processar_pos_atendimento, 'interval', minutes=5, args=[app],
            id='pos_atendimento', replace_existing=True
        )
        scheduler.add_job(
            processar_reativacao, 'cron', hour=10, args=[app],
            id='reativacao', replace_existing=True
        )
        scheduler.start()
    except Exception as e:
        print(f'Scheduler nao iniciado: {e}')

    return app


app = create_app()

if __name__ == '__main__':
    app.run(debug=True, port=5000)
