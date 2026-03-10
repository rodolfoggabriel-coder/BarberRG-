import re
from datetime import datetime, timedelta


def slugify(text):
    """Converte texto para slug URL-friendly"""
    text = text.lower().strip()
    text = re.sub(r'[àáâãäå]', 'a', text)
    text = re.sub(r'[èéêë]', 'e', text)
    text = re.sub(r'[ìíîï]', 'i', text)
    text = re.sub(r'[òóôõö]', 'o', text)
    text = re.sub(r'[ùúûü]', 'u', text)
    text = re.sub(r'[ç]', 'c', text)
    text = re.sub(r'[^a-z0-9]+', '-', text)
    text = text.strip('-')
    return text


def calcular_comissao(valor_bruto, percentual, sobre_liquido=False, desconto=0):
    """Calcula comissao do barbeiro"""
    base = valor_bruto - desconto if sobre_liquido else valor_bruto
    return round(base * (percentual / 100), 2)


def gerar_horarios_disponiveis(abertura, fechamento, duracao_minutos, agendamentos_existentes, data):
    """Gera lista de horarios disponiveis para agendamento"""
    horarios = []
    inicio = datetime.strptime(abertura, '%H:%M')
    fim = datetime.strptime(fechamento, '%H:%M')
    duracao = timedelta(minutes=duracao_minutos)

    ocupados = []
    for ag in agendamentos_existentes:
        ag_inicio = datetime.strptime(ag.hora_inicio, '%H:%M')
        ag_fim = datetime.strptime(ag.hora_fim, '%H:%M')
        ocupados.append((ag_inicio, ag_fim))

    atual = inicio
    while atual + duracao <= fim:
        hora_str = atual.strftime('%H:%M')
        fim_slot = atual + duracao

        disponivel = True
        for oc_inicio, oc_fim in ocupados:
            if atual < oc_fim and fim_slot > oc_inicio:
                disponivel = False
                break

        if disponivel:
            horarios.append(hora_str)

        atual += timedelta(minutes=15)  # slots de 15 min

    return horarios


def calcular_hora_fim(hora_inicio, duracao_minutos):
    """Calcula hora de fim baseado na duracao"""
    inicio = datetime.strptime(hora_inicio, '%H:%M')
    fim = inicio + timedelta(minutes=duracao_minutos)
    return fim.strftime('%H:%M')


def formatar_telefone(telefone):
    """Formata telefone para envio via WhatsApp (somente numeros com DDI)"""
    numeros = re.sub(r'[^0-9]', '', telefone)
    if len(numeros) == 11:  # celular BR sem DDI
        numeros = '55' + numeros
    elif len(numeros) == 10:  # fixo BR sem DDI
        numeros = '55' + numeros
    return numeros


def formatar_moeda(valor):
    """Formata valor para exibicao em reais"""
    return f"R$ {valor:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
