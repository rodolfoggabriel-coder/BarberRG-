from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, abort
from datetime import datetime, date, timedelta
from sqlalchemy import func, extract

from models import (
    db, Barbearia, Barbeiro, Servico, Cliente, Agendamento,
    Avaliacao, PortfolioFoto, MetaBarbeiro
)
from utils import slugify, gerar_horarios_disponiveis, calcular_hora_fim
from whatsapp import enviar_confirmacao

public_bp = Blueprint('public', __name__)


# ---------------------------------------------------------------------------
# Home page
# ---------------------------------------------------------------------------

@public_bp.route('/')
def home():
    return render_template('public/home.html')


# ---------------------------------------------------------------------------
# Booking flow  /agendar/<slug>
# ---------------------------------------------------------------------------

@public_bp.route('/agendar/<slug>', methods=['GET'])
def agendar(slug):
    """Step 1 - choose service (entry point for the booking flow)."""
    barbearia = Barbearia.query.filter_by(slug=slug, ativo=True).first_or_404()
    servicos = Servico.query.filter_by(barbearia_id=barbearia.id, ativo=True).all()
    return render_template(
        'public/agendar_servico.html',
        barbearia=barbearia,
        servicos=servicos,
        etapa=1,
    )


@public_bp.route('/agendar/<slug>/barbeiro', methods=['GET'])
def agendar_barbeiro(slug):
    """Step 2 - choose barber."""
    barbearia = Barbearia.query.filter_by(slug=slug, ativo=True).first_or_404()
    servico_id = request.args.get('servico_id', type=int)
    if not servico_id:
        flash('Selecione um servico.', 'warning')
        return redirect(url_for('public.agendar', slug=slug))

    servico = Servico.query.get_or_404(servico_id)
    barbeiros = Barbeiro.query.filter_by(barbearia_id=barbearia.id, ativo=True).all()

    # Compute average rating per barber
    for b in barbeiros:
        media = (
            db.session.query(func.avg(Avaliacao.nota))
            .filter(Avaliacao.barbeiro_id == b.id)
            .scalar()
        )
        b.media_nota = round(media, 1) if media else None

    return render_template(
        'public/agendar_barbeiro.html',
        barbearia=barbearia,
        servico=servico,
        barbeiros=barbeiros,
        etapa=2,
    )


@public_bp.route('/agendar/<slug>/data', methods=['GET'])
def agendar_data(slug):
    """Step 3 - choose date."""
    barbearia = Barbearia.query.filter_by(slug=slug, ativo=True).first_or_404()
    servico_id = request.args.get('servico_id', type=int)
    barbeiro_id = request.args.get('barbeiro_id', type=int)  # 0 = any

    if not servico_id:
        flash('Selecione um servico.', 'warning')
        return redirect(url_for('public.agendar', slug=slug))

    servico = Servico.query.get_or_404(servico_id)
    barbeiro = None
    if barbeiro_id and barbeiro_id != 0:
        barbeiro = Barbeiro.query.get_or_404(barbeiro_id)

    dias_funcionamento = barbearia.get_dias_funcionamento()

    return render_template(
        'public/agendar_data.html',
        barbearia=barbearia,
        servico=servico,
        barbeiro=barbeiro,
        barbeiro_id=barbeiro_id or 0,
        dias_funcionamento=dias_funcionamento,
        etapa=3,
    )


@public_bp.route('/agendar/<slug>/horario', methods=['GET'])
def agendar_horario(slug):
    """Step 4 - choose time."""
    barbearia = Barbearia.query.filter_by(slug=slug, ativo=True).first_or_404()
    servico_id = request.args.get('servico_id', type=int)
    barbeiro_id = request.args.get('barbeiro_id', type=int)
    data_str = request.args.get('data')

    if not servico_id or not data_str:
        flash('Dados incompletos.', 'warning')
        return redirect(url_for('public.agendar', slug=slug))

    servico = Servico.query.get_or_404(servico_id)
    data_agendamento = datetime.strptime(data_str, '%Y-%m-%d').date()

    # Resolve barbers list (single or all if "any")
    if barbeiro_id and barbeiro_id != 0:
        barbeiros = [Barbeiro.query.get_or_404(barbeiro_id)]
    else:
        barbeiros = Barbeiro.query.filter_by(
            barbearia_id=barbearia.id, ativo=True
        ).all()

    # Build available times per barber
    horarios_por_barbeiro = []
    for b in barbeiros:
        agendamentos_dia = Agendamento.query.filter(
            Agendamento.barbeiro_id == b.id,
            Agendamento.data == data_agendamento,
            Agendamento.status.in_(['agendado', 'confirmado']),
        ).all()
        horarios = gerar_horarios_disponiveis(
            barbearia.horario_abertura,
            barbearia.horario_fechamento,
            servico.duracao_minutos,
            agendamentos_dia,
            data_agendamento,
        )
        if horarios:
            horarios_por_barbeiro.append({'barbeiro': b, 'horarios': horarios})

    barbeiro = barbeiros[0] if barbeiro_id and barbeiro_id != 0 else None

    return render_template(
        'public/agendar_horario.html',
        barbearia=barbearia,
        servico=servico,
        barbeiro=barbeiro,
        barbeiro_id=barbeiro_id or 0,
        data=data_agendamento,
        horarios_por_barbeiro=horarios_por_barbeiro,
        etapa=4,
    )


@public_bp.route('/agendar/<slug>/confirmar', methods=['POST'])
def agendar_confirmar(slug):
    """Process the booking form and create the appointment."""
    barbearia = Barbearia.query.filter_by(slug=slug, ativo=True).first_or_404()

    servico_id = request.form.get('servico_id', type=int)
    barbeiro_id = request.form.get('barbeiro_id', type=int)
    data_str = request.form.get('data')
    hora = request.form.get('hora')
    nome = request.form.get('nome', '').strip()
    telefone = request.form.get('telefone', '').strip()
    observacao = request.form.get('observacao', '').strip()

    # Basic validation
    if not all([servico_id, barbeiro_id, data_str, hora, nome, telefone]):
        flash('Preencha todos os campos obrigatorios.', 'danger')
        return redirect(url_for('public.agendar', slug=slug))

    servico = Servico.query.get_or_404(servico_id)
    barbeiro = Barbeiro.query.get_or_404(barbeiro_id)
    data_agendamento = datetime.strptime(data_str, '%Y-%m-%d').date()
    hora_fim = calcular_hora_fim(hora, servico.duracao_minutos)

    # --- Duplicate check: same client, same day, within 30 minutes ---------
    cliente_existente = Cliente.query.filter_by(
        barbearia_id=barbearia.id, telefone=telefone
    ).first()

    if cliente_existente:
        hora_dt = datetime.strptime(hora, '%H:%M')
        janela_inicio = (hora_dt - timedelta(minutes=30)).strftime('%H:%M')
        janela_fim = (hora_dt + timedelta(minutes=30)).strftime('%H:%M')

        duplicado = Agendamento.query.filter(
            Agendamento.cliente_id == cliente_existente.id,
            Agendamento.data == data_agendamento,
            Agendamento.hora_inicio >= janela_inicio,
            Agendamento.hora_inicio <= janela_fim,
            Agendamento.status.in_(['agendado', 'confirmado']),
        ).first()

        if duplicado:
            flash(
                'Voce ja possui um agendamento proximo a esse horario neste dia.',
                'warning',
            )
            return redirect(url_for('public.agendar', slug=slug))

    # --- Slot conflict check -----------------------------------------------
    conflito = Agendamento.query.filter(
        Agendamento.barbeiro_id == barbeiro.id,
        Agendamento.data == data_agendamento,
        Agendamento.status.in_(['agendado', 'confirmado']),
        Agendamento.hora_inicio < hora_fim,
        Agendamento.hora_fim > hora,
    ).first()

    if conflito:
        flash('Este horario ja foi preenchido. Escolha outro.', 'warning')
        return redirect(url_for('public.agendar_horario', slug=slug,
                                servico_id=servico_id, barbeiro_id=barbeiro_id,
                                data=data_str))

    # --- Auto-create client if not found -----------------------------------
    if not cliente_existente:
        cliente_existente = Cliente(
            barbearia_id=barbearia.id,
            nome=nome,
            telefone=telefone,
        )
        db.session.add(cliente_existente)
        db.session.flush()  # get id before creating agendamento

    # --- Create appointment ------------------------------------------------
    agendamento = Agendamento(
        barbearia_id=barbearia.id,
        barbeiro_id=barbeiro.id,
        cliente_id=cliente_existente.id,
        servico_id=servico.id,
        data=data_agendamento,
        hora_inicio=hora,
        hora_fim=hora_fim,
        status='agendado',
        valor=servico.preco,
        observacao=observacao or None,
        origem='online',
    )
    db.session.add(agendamento)
    db.session.commit()

    # Send WhatsApp confirmation (non-blocking best-effort)
    try:
        enviar_confirmacao(agendamento)
    except Exception:
        pass  # never block the user on messaging failures

    return redirect(url_for('public.agendamento_confirmado', slug=slug,
                            agendamento_id=agendamento.id))


@public_bp.route('/agendar/<slug>/confirmado/<int:agendamento_id>')
def agendamento_confirmado(slug, agendamento_id):
    """Booking confirmation page."""
    barbearia = Barbearia.query.filter_by(slug=slug, ativo=True).first_or_404()
    agendamento = Agendamento.query.get_or_404(agendamento_id)

    if agendamento.barbearia_id != barbearia.id:
        abort(404)

    return render_template(
        'public/agendamento_confirmado.html',
        barbearia=barbearia,
        agendamento=agendamento,
    )


# ---------------------------------------------------------------------------
# Cancel booking
# ---------------------------------------------------------------------------

@public_bp.route('/agendar/<slug>/cancelar/<int:agendamento_id>', methods=['GET', 'POST'])
def cancelar_agendamento(slug, agendamento_id):
    """Cancel a booking (respects cancelamento_horas_minimo)."""
    barbearia = Barbearia.query.filter_by(slug=slug, ativo=True).first_or_404()
    agendamento = Agendamento.query.get_or_404(agendamento_id)

    if agendamento.barbearia_id != barbearia.id:
        abort(404)

    if agendamento.status not in ('agendado', 'confirmado'):
        flash('Este agendamento nao pode mais ser cancelado.', 'warning')
        return redirect(url_for('public.agendar', slug=slug))

    # Build datetime of the appointment
    hora_inicio_dt = datetime.strptime(agendamento.hora_inicio, '%H:%M')
    agendamento_datetime = datetime.combine(agendamento.data, hora_inicio_dt.time())
    horas_restantes = (agendamento_datetime - datetime.now()).total_seconds() / 3600

    if horas_restantes < barbearia.cancelamento_horas_minimo:
        flash(
            f'Cancelamentos devem ser feitos com pelo menos '
            f'{barbearia.cancelamento_horas_minimo}h de antecedencia.',
            'danger',
        )
        return render_template(
            'public/cancelar_agendamento.html',
            barbearia=barbearia,
            agendamento=agendamento,
            pode_cancelar=False,
        )

    if request.method == 'POST':
        agendamento.status = 'cancelado'
        db.session.commit()
        flash('Agendamento cancelado com sucesso.', 'success')
        return redirect(url_for('public.agendar', slug=slug))

    return render_template(
        'public/cancelar_agendamento.html',
        barbearia=barbearia,
        agendamento=agendamento,
        pode_cancelar=True,
    )


# ---------------------------------------------------------------------------
# Loyalty card
# ---------------------------------------------------------------------------

@public_bp.route('/fidelidade/<telefone>')
def fidelidade(telefone):
    """Public loyalty card lookup by phone number."""
    clientes = Cliente.query.filter_by(telefone=telefone).all()

    resultados = []
    for cliente in clientes:
        barbearia = Barbearia.query.get(cliente.barbearia_id)
        config = barbearia.get_config_fidelidade()
        total_agendamentos = Agendamento.query.filter(
            Agendamento.cliente_id == cliente.id,
            Agendamento.status == 'concluido',
        ).count()
        resultados.append({
            'cliente': cliente,
            'barbearia': barbearia,
            'config': config,
            'total_agendamentos': total_agendamentos,
        })

    return render_template(
        'public/fidelidade.html',
        telefone=telefone,
        resultados=resultados,
    )


# ---------------------------------------------------------------------------
# Barber portfolio / public profile
# ---------------------------------------------------------------------------

@public_bp.route('/barbeiro/<slug>')
def barbeiro_perfil(slug):
    """Public barber profile with photo, services, rating, gallery."""
    barbeiro = Barbeiro.query.filter_by(slug=slug, ativo=True).first_or_404()
    barbearia = Barbearia.query.get(barbeiro.barbearia_id)

    # Services offered by the barbershop
    servicos = Servico.query.filter_by(
        barbearia_id=barbearia.id, ativo=True
    ).all()

    # Average rating
    media = (
        db.session.query(func.avg(Avaliacao.nota))
        .filter(Avaliacao.barbeiro_id == barbeiro.id)
        .scalar()
    )
    media_nota = round(media, 1) if media else None

    total_avaliacoes = Avaliacao.query.filter_by(barbeiro_id=barbeiro.id).count()

    # Recent reviews
    avaliacoes = (
        Avaliacao.query
        .filter_by(barbeiro_id=barbeiro.id)
        .order_by(Avaliacao.data.desc())
        .limit(10)
        .all()
    )

    # Portfolio gallery
    fotos = (
        PortfolioFoto.query
        .filter_by(barbeiro_id=barbeiro.id)
        .order_by(PortfolioFoto.destaque.desc(), PortfolioFoto.data_upload.desc())
        .all()
    )

    # Total completed appointments
    total_atendimentos = Agendamento.query.filter(
        Agendamento.barbeiro_id == barbeiro.id,
        Agendamento.status == 'concluido',
    ).count()

    return render_template(
        'public/barbeiro_perfil.html',
        barbeiro=barbeiro,
        barbearia=barbearia,
        servicos=servicos,
        media_nota=media_nota,
        total_avaliacoes=total_avaliacoes,
        avaliacoes=avaliacoes,
        fotos=fotos,
        total_atendimentos=total_atendimentos,
    )


# ---------------------------------------------------------------------------
# Rating / Evaluation
# ---------------------------------------------------------------------------

@public_bp.route('/avaliar/<int:agendamento_id>', methods=['GET', 'POST'])
def avaliar(agendamento_id):
    """Simple 1-5 star rating for a completed appointment."""
    agendamento = Agendamento.query.get_or_404(agendamento_id)
    barbearia = Barbearia.query.get(agendamento.barbearia_id)

    # Check if already rated
    avaliacao_existente = Avaliacao.query.filter_by(
        agendamento_id=agendamento.id
    ).first()

    if avaliacao_existente:
        flash('Voce ja avaliou este atendimento.', 'info')
        return render_template(
            'public/avaliar.html',
            barbearia=barbearia,
            agendamento=agendamento,
            avaliacao=avaliacao_existente,
            ja_avaliado=True,
        )

    if request.method == 'POST':
        nota = request.form.get('nota', type=int)
        comentario = request.form.get('comentario', '').strip()

        if not nota or nota < 1 or nota > 5:
            flash('Selecione uma nota de 1 a 5.', 'warning')
            return redirect(url_for('public.avaliar', agendamento_id=agendamento_id))

        avaliacao = Avaliacao(
            agendamento_id=agendamento.id,
            barbeiro_id=agendamento.barbeiro_id,
            cliente_id=agendamento.cliente_id,
            nota=nota,
            comentario=comentario or None,
        )
        db.session.add(avaliacao)
        db.session.commit()

        flash('Obrigado pela sua avaliacao!', 'success')
        return render_template(
            'public/avaliar.html',
            barbearia=barbearia,
            agendamento=agendamento,
            avaliacao=avaliacao,
            ja_avaliado=True,
        )

    return render_template(
        'public/avaliar.html',
        barbearia=barbearia,
        agendamento=agendamento,
        avaliacao=None,
        ja_avaliado=False,
    )


# ---------------------------------------------------------------------------
# Ranking TV  /ranking/<slug>
# ---------------------------------------------------------------------------

@public_bp.route('/ranking/<slug>')
def ranking_tv(slug):
    """Fullscreen leaderboard for barbershop TV display."""
    barbearia = Barbearia.query.filter_by(slug=slug, ativo=True).first_or_404()

    hoje = date.today()
    mes_atual = hoje.month
    ano_atual = hoje.year

    barbeiros = Barbeiro.query.filter_by(
        barbearia_id=barbearia.id, ativo=True
    ).all()

    ranking = []
    for b in barbeiros:
        # Monthly completed appointments
        atendimentos_mes = Agendamento.query.filter(
            Agendamento.barbeiro_id == b.id,
            Agendamento.status == 'concluido',
            extract('month', Agendamento.data) == mes_atual,
            extract('year', Agendamento.data) == ano_atual,
        ).count()

        # Monthly revenue
        faturamento_mes = (
            db.session.query(func.coalesce(func.sum(Agendamento.valor), 0))
            .filter(
                Agendamento.barbeiro_id == b.id,
                Agendamento.status == 'concluido',
                extract('month', Agendamento.data) == mes_atual,
                extract('year', Agendamento.data) == ano_atual,
            )
            .scalar()
        ) or 0

        # Average rating (all time)
        media = (
            db.session.query(func.avg(Avaliacao.nota))
            .filter(Avaliacao.barbeiro_id == b.id)
            .scalar()
        )
        media_nota = round(media, 1) if media else 0

        # Monthly goal
        meta = MetaBarbeiro.query.filter_by(
            barbeiro_id=b.id, mes=mes_atual, ano=ano_atual
        ).first()

        ranking.append({
            'barbeiro': b,
            'atendimentos': atendimentos_mes,
            'faturamento': faturamento_mes,
            'media_nota': media_nota,
            'meta': meta,
        })

    # Sort by number of appointments descending
    ranking.sort(key=lambda x: x['atendimentos'], reverse=True)

    return render_template(
        'public/ranking_tv.html',
        barbearia=barbearia,
        ranking=ranking,
        mes=mes_atual,
        ano=ano_atual,
    )


# ---------------------------------------------------------------------------
# API endpoints for AJAX during booking
# ---------------------------------------------------------------------------

@public_bp.route('/api/horarios-disponiveis')
def api_horarios_disponiveis():
    """Return available time slots for a given barber, service and date."""
    barbearia_id = request.args.get('barbearia_id', type=int)
    servico_id = request.args.get('servico_id', type=int)
    barbeiro_id = request.args.get('barbeiro_id', type=int)
    data_str = request.args.get('data')

    if not all([barbearia_id, servico_id, data_str]):
        return jsonify({'error': 'Parametros obrigatorios ausentes.'}), 400

    barbearia = Barbearia.query.get_or_404(barbearia_id)
    servico = Servico.query.get_or_404(servico_id)
    data_agendamento = datetime.strptime(data_str, '%Y-%m-%d').date()

    # Determine barbers to check
    if barbeiro_id and barbeiro_id != 0:
        barbeiros = [Barbeiro.query.get_or_404(barbeiro_id)]
    else:
        barbeiros = Barbeiro.query.filter_by(
            barbearia_id=barbearia.id, ativo=True
        ).all()

    resultado = []
    for b in barbeiros:
        agendamentos_dia = Agendamento.query.filter(
            Agendamento.barbeiro_id == b.id,
            Agendamento.data == data_agendamento,
            Agendamento.status.in_(['agendado', 'confirmado']),
        ).all()

        horarios = gerar_horarios_disponiveis(
            barbearia.horario_abertura,
            barbearia.horario_fechamento,
            servico.duracao_minutos,
            agendamentos_dia,
            data_agendamento,
        )

        resultado.append({
            'barbeiro_id': b.id,
            'barbeiro_nome': b.nome,
            'horarios': horarios,
        })

    return jsonify({'data': data_str, 'barbeiros': resultado})


@public_bp.route('/api/barbeiros/<int:barbearia_id>')
def api_barbeiros(barbearia_id):
    """Return active barbers for a barbershop."""
    barbeiros = Barbeiro.query.filter_by(
        barbearia_id=barbearia_id, ativo=True
    ).all()

    lista = []
    for b in barbeiros:
        media = (
            db.session.query(func.avg(Avaliacao.nota))
            .filter(Avaliacao.barbeiro_id == b.id)
            .scalar()
        )
        lista.append({
            'id': b.id,
            'nome': b.nome,
            'foto': b.foto,
            'media_nota': round(media, 1) if media else None,
        })

    return jsonify({'barbeiros': lista})


@public_bp.route('/api/servicos/<int:barbearia_id>')
def api_servicos(barbearia_id):
    """Return active services for a barbershop."""
    servicos = Servico.query.filter_by(
        barbearia_id=barbearia_id, ativo=True
    ).all()

    lista = [{
        'id': s.id,
        'nome': s.nome,
        'preco': s.preco,
        'duracao_minutos': s.duracao_minutos,
    } for s in servicos]

    return jsonify({'servicos': lista})
