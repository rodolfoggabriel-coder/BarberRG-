import os
from flask import Flask, send_from_directory, redirect, url_for, flash, session
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

    # Seed route - create test data
    @app.route('/seed')
    def seed_data():
        from models import Barbearia, Barbeiro, Servico, Cliente

        # Check if already seeded
        if Barbearia.query.filter_by(email='admin@barberdesk.com').first():
            flash('Dados de teste ja existem! Login: admin@barberdesk.com / Senha: 0000', 'info')
            return redirect(url_for('admin.login'))

        # Create test barbearia
        barb = Barbearia(
            nome='Barbearia Teste',
            slug='barbearia-teste',
            email='admin@barberdesk.com',
            telefone='(11) 99999-0000',
            endereco='Rua Teste, 123 - Centro',
            horario_abertura='08:00',
            horario_fechamento='20:00',
            plano='pro',
            setup_completo=True,
            senha_hash=''
        )
        barb.set_senha('0000')
        db.session.add(barb)
        db.session.flush()

        # Create test barbers (PIN: 0000)
        barbeiros_data = [
            ('Carlos Silva', '(11) 91111-0000'),
            ('Rafael Santos', '(11) 92222-0000'),
            ('Lucas Oliveira', '(11) 93333-0000'),
        ]
        for nome, tel in barbeiros_data:
            b = Barbeiro(
                barbearia_id=barb.id,
                nome=nome,
                slug=nome.lower().replace(' ', '-'),
                telefone=tel,
                percentual_comissao=50.0,
                pin_hash=''
            )
            b.set_pin('0000')
            db.session.add(b)

        # Create test services
        servicos_data = [
            ('Corte Masculino', 45.00, 30),
            ('Barba', 30.00, 20),
            ('Corte + Barba', 65.00, 50),
            ('Pigmentacao', 80.00, 40),
            ('Hidratacao', 35.00, 25),
        ]
        for nome, preco, dur in servicos_data:
            s = Servico(
                barbearia_id=barb.id,
                nome=nome,
                preco=preco,
                duracao_minutos=dur
            )
            db.session.add(s)

        # Create test clients
        clientes_data = [
            ('Joao Pedro', '(11) 98001-0000'),
            ('Marcos Lima', '(11) 98002-0000'),
            ('Felipe Costa', '(11) 98003-0000'),
            ('Bruno Alves', '(11) 98004-0000'),
            ('Gabriel Souza', '(11) 98005-0000'),
        ]
        for nome, tel in clientes_data:
            c = Cliente(
                barbearia_id=barb.id,
                nome=nome,
                telefone=tel
            )
            db.session.add(c)

        db.session.commit()
        session['barbearia_id'] = barb.id
        session['barbearia_nome'] = barb.nome
        flash('Dados de teste criados! Bem-vindo ao BarberDesk.', 'success')
        return redirect(url_for('admin.dashboard'))

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
