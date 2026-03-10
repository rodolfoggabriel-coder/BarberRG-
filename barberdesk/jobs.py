from datetime import datetime, timedelta, date
from models import db, Agendamento, LembreteLog


def processar_lembretes_24h(app):
    """Envia lembretes 24h antes do agendamento"""
    with app.app_context():
        from whatsapp import enviar_lembrete
        amanha = date.today() + timedelta(days=1)
        agendamentos = Agendamento.query.filter(
            Agendamento.data == amanha,
            Agendamento.status.in_(['agendado', 'confirmado'])
        ).all()

        for ag in agendamentos:
            ja_enviado = LembreteLog.query.filter_by(
                agendamento_id=ag.id,
                tipo='lembrete_24h',
                status='enviado'
            ).first()
            if not ja_enviado:
                enviar_lembrete(ag, 'lembrete_24h')


def processar_lembretes_1h(app):
    """Envia lembretes 1h antes do agendamento"""
    with app.app_context():
        from whatsapp import enviar_lembrete
        agora = datetime.now()
        daqui_1h = agora + timedelta(hours=1)

        agendamentos = Agendamento.query.filter(
            Agendamento.data == date.today(),
            Agendamento.status.in_(['agendado', 'confirmado'])
        ).all()

        for ag in agendamentos:
            hora_ag = datetime.strptime(ag.hora_inicio, '%H:%M').replace(
                year=agora.year, month=agora.month, day=agora.day
            )
            diff = (hora_ag - agora).total_seconds() / 60
            if 55 <= diff <= 65:
                ja_enviado = LembreteLog.query.filter_by(
                    agendamento_id=ag.id,
                    tipo='lembrete_1h',
                    status='enviado'
                ).first()
                if not ja_enviado:
                    enviar_lembrete(ag, 'lembrete_1h')


def processar_pos_atendimento(app):
    """Envia mensagem pos-atendimento 2h depois"""
    with app.app_context():
        from whatsapp import enviar_lembrete, enviar_avaliacao
        agora = datetime.now()
        duas_horas_atras = agora - timedelta(hours=2)

        agendamentos = Agendamento.query.filter(
            Agendamento.data == date.today(),
            Agendamento.status == 'concluido'
        ).all()

        for ag in agendamentos:
            hora_fim = datetime.strptime(ag.hora_fim, '%H:%M').replace(
                year=agora.year, month=agora.month, day=agora.day
            )
            diff = (agora - hora_fim).total_seconds() / 60
            if 115 <= diff <= 125:
                ja_enviado = LembreteLog.query.filter_by(
                    agendamento_id=ag.id,
                    tipo='pos_atendimento',
                    status='enviado'
                ).first()
                if not ja_enviado:
                    enviar_lembrete(ag, 'pos_atendimento')
                    enviar_avaliacao(ag)


def processar_reativacao(app):
    """Envia mensagem para clientes inativos"""
    with app.app_context():
        from models import Cliente, Barbearia
        from whatsapp import enviar_whatsapp
        barbearias = Barbearia.query.filter_by(ativo=True, wpp_reativacao=True).all()

        for barb in barbearias:
            dias = barb.wpp_dias_reativacao
            limite = date.today() - timedelta(days=dias)

            clientes = Cliente.query.filter_by(barbearia_id=barb.id).all()
            for cliente in clientes:
                ultimo = Agendamento.query.filter_by(
                    cliente_id=cliente.id,
                    status='concluido'
                ).order_by(Agendamento.data.desc()).first()

                if ultimo and ultimo.data <= limite:
                    mensagem = (
                        f"Faz tempo que nao te vemos na {barb.nome}! "
                        f"Agende seu horario: /agendar/{barb.slug}"
                    )
                    enviar_whatsapp(cliente.telefone, mensagem)
