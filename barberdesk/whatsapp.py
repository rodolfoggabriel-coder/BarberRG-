import requests
from flask import current_app
from models import LembreteLog, db
from utils import formatar_telefone


def enviar_whatsapp(telefone, mensagem):
    """Envia mensagem via Z-API ou Evolution API"""
    api_url = current_app.config.get('WHATSAPP_API_URL')
    api_token = current_app.config.get('WHATSAPP_API_TOKEN')
    instance_id = current_app.config.get('WHATSAPP_INSTANCE_ID')

    if not api_url or not api_token:
        return False, 'WhatsApp nao configurado'

    numero = formatar_telefone(telefone)

    try:
        response = requests.post(
            f'{api_url}/instances/{instance_id}/token/{api_token}/send-text',
            json={
                'phone': numero,
                'message': mensagem
            },
            timeout=10
        )
        return response.status_code == 200, response.text
    except Exception as e:
        return False, str(e)


def enviar_confirmacao(agendamento):
    """Envia confirmacao de agendamento"""
    barbearia = agendamento.barbearia
    if not barbearia.wpp_confirmacao:
        return

    mensagem = (
        f"Seu horario esta confirmado! "
        f"{agendamento.barbeiro.nome} te espera "
        f"dia {agendamento.data.strftime('%d/%m')} as {agendamento.hora_inicio} "
        f"para {agendamento.servico.nome}. "
        f"Para cancelar, acesse: /agendar/{barbearia.slug}"
    )

    sucesso, resp = enviar_whatsapp(agendamento.cliente.telefone, mensagem)

    log = LembreteLog(
        agendamento_id=agendamento.id,
        tipo='confirmacao',
        status='enviado' if sucesso else 'falhou',
        tentativas=1
    )
    db.session.add(log)
    db.session.commit()


def enviar_lembrete(agendamento, tipo):
    """Envia lembrete generico"""
    barbearia = agendamento.barbearia

    if tipo == 'lembrete_24h' and not barbearia.wpp_lembrete_24h:
        return
    if tipo == 'lembrete_1h' and not barbearia.wpp_lembrete_1h:
        return

    mensagens = {
        'lembrete_24h': (
            f"Lembrando do seu horario amanha as {agendamento.hora_inicio} "
            f"na {barbearia.nome} com {agendamento.barbeiro.nome}."
        ),
        'lembrete_1h': (
            f"Daqui a 1 hora e sua vez! Te esperamos na {barbearia.nome}."
        ),
        'pos_atendimento': (
            f"Obrigado pela visita! Voce ganhou pontos de fidelidade. "
            f"Agende o proximo: /agendar/{barbearia.slug}"
        ),
    }

    mensagem = mensagens.get(tipo, '')
    if not mensagem:
        return

    sucesso, resp = enviar_whatsapp(agendamento.cliente.telefone, mensagem)

    log = LembreteLog(
        agendamento_id=agendamento.id,
        tipo=tipo,
        status='enviado' if sucesso else 'falhou',
        tentativas=1
    )
    db.session.add(log)
    db.session.commit()


def enviar_avaliacao(agendamento):
    """Envia link de avaliacao pos-atendimento"""
    barbearia = agendamento.barbearia
    if not barbearia.wpp_avaliacao:
        return

    mensagem = (
        f"Como foi seu atendimento com {agendamento.barbeiro.nome}? "
        f"Avalie de 1 a 5: /avaliar/{agendamento.id}"
    )

    sucesso, resp = enviar_whatsapp(agendamento.cliente.telefone, mensagem)

    log = LembreteLog(
        agendamento_id=agendamento.id,
        tipo='avaliacao',
        status='enviado' if sucesso else 'falhou',
        tentativas=1
    )
    db.session.add(log)
    db.session.commit()
