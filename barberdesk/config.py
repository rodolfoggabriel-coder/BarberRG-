import os

basedir = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'barberdesk-dev-secret-key-change-in-prod')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', f'sqlite:///{os.path.join(basedir, "barberdesk.db")}')

    # Render uses postgres:// but SQLAlchemy requires postgresql://
    if SQLALCHEMY_DATABASE_URI and SQLALCHEMY_DATABASE_URI.startswith('postgres://'):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # WhatsApp (Z-API / Evolution API)
    WHATSAPP_API_URL = os.environ.get('WHATSAPP_API_URL', '')
    WHATSAPP_API_TOKEN = os.environ.get('WHATSAPP_API_TOKEN', '')
    WHATSAPP_INSTANCE_ID = os.environ.get('WHATSAPP_INSTANCE_ID', '')

    # Asaas (Pagamentos)
    ASAAS_API_KEY = os.environ.get('ASAAS_API_KEY', '')
    ASAAS_API_URL = os.environ.get('ASAAS_API_URL', 'https://sandbox.asaas.com/api/v3')

    # Upload
    UPLOAD_FOLDER = os.path.join(basedir, 'static', 'uploads')
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 5MB

    # Scheduler
    SCHEDULER_API_ENABLED = True
