from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify, current_app
from functools import wraps
from datetime import datetime, date, timedelta
from werkzeug.utils import secure_filename
import os
import uuid

from models import (
    db, Barbeiro, Agendamento, Pagamento, Comissao,
    Servico, Cliente, Avaliacao, PortfolioFoto, MetaBarbeiro
)
from utils import calcular_comissao, calcular_hora_fim

barbeiro_bp = Blueprint('barbeiro', __name__, url_prefix='/barbeiro')

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def login_required_barbeiro(f):
    """Decorator que exige login do barbeiro via session."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'barbeiro_id' not in session:
            flash('Faca login para acessar o painel.', 'warning')
            return redirect(url_for('barbeiro.login'))
        barbeiro = Barbeiro.query.get(session['barbeiro_id'])
        if not barbeiro or not barbeiro.ativo:
            session.pop('barbeiro_id', None)
            flash('Conta inativa ou nao encontrada.', 'danger')
            return redirect(url_for('barbeiro.login'))
        return f(*args, **kwargs)
    return decorated_function


def get_barbeiro_logado():
    """Retorna o barbeiro autenticado na sessao."""
    return Barbeiro.query.get(session.get('barbeiro_id'))


# ---------------------------------------------------------------------------
# AUTH
# ---------------------------------------------------------------------------

@barbeiro_bp.route('/login', methods=['GET', 'POST'])
def login():
    if 'barbeiro_id' in session:
        return redirect(url_for('barbeiro.agenda'))

    if request.method == 'POST':
        telefone = request.form.get('telefone', '').strip()
        pin = request.form.get('pin', '').strip()

        if not telefone or not pin:
            flash('Informe o telefone e o PIN.', 'danger')
            return redirect(url_for('barbeiro.login'))

        if len(pin) != 4 or not pin.isdigit():
            flash('O PIN deve ter exatamente 4 digitos.', 'danger')
            return redirect(url_for('barbeiro.login'))

        barbeiro = Barbeiro.query.filter_by(telefone=telefone, ativo=True).first()

        if not barbeiro or not barbeiro.check_pin(pin):
            flash('Telefone ou PIN incorretos.', 'danger')
            return redirect(url_for('barbeiro.login'))

        session['barbeiro_id'] = barbeiro.id
        session['barbearia_id'] = barbeiro.barbearia_id
        flash(f'Bem-vindo, {barbeiro.nome}!', 'success')
        return redirect(url_for('barbeiro.agenda'))

    return render_template('barbeiro/login.html')


@barbeiro_bp.route('/logout')
def logout():
    session.pop('barbeiro_id', None)
    session.pop('barbearia_id', None)
    flash('Voce saiu do painel.', 'info')
    return redirect(url_for('barbeiro.login'))


# ---------------------------------------------------------------------------
# AGENDA DIARIA (TIMELINE)
# ---------------------------------------------------------------------------

@barbeiro_bp.route('/agenda')
@barbeiro_bp.route('/agenda/<string:data_str>')
@login_required_barbeiro
def agenda(data_str=None):
    barbeiro = get_barbeiro_logado()

    if data_str:
        try:
            data_selecionada = datetime.strptime(data_str, '%Y-%m-%d').date()
        except ValueError:
            flash('Data invalida.', 'danger')
            data_selecionada = date.today()
    else:
        data_selecionada = date.today()

    agendamentos = (
        Agendamento.query
        .filter_by(barbeiro_id=barbeiro.id, data=data_selecionada)
        .filter(Agendamento.status.in_(['agendado', 'confirmado', 'concluido']))
        .order_by(Agendamento.hora_inicio)
        .all()
    )

    # Montar timeline: gerar slots de 15 min entre abertura e fechamento
    barbearia = barbeiro.barbearia
    abertura = barbearia.horario_abertura
    fechamento = barbearia.horario_fechamento

    slots = []
    current = datetime.strptime(abertura, '%H:%M')
    end = datetime.strptime(fechamento, '%H:%M')
    while current < end:
        slot_hora = current.strftime('%H:%M')
        ag_no_slot = None
        for ag in agendamentos:
            if ag.hora_inicio <= slot_hora < ag.hora_fim:
                ag_no_slot = ag
                break
        slots.append({'hora': slot_hora, 'agendamento': ag_no_slot})
        current += timedelta(minutes=15)

    data_anterior = data_selecionada - timedelta(days=1)
    data_proxima = data_selecionada + timedelta(days=1)

    return render_template(
        'barbeiro/agenda.html',
        barbeiro=barbeiro,
        agendamentos=agendamentos,
        slots=slots,
        data_selecionada=data_selecionada,
        data_anterior=data_anterior,
        data_proxima=data_proxima,
        hoje=date.today(),
    )


# ---------------------------------------------------------------------------
# CONCLUIR ATENDIMENTO (PAGAMENTO)
# ---------------------------------------------------------------------------

@barbeiro_bp.route('/concluir/<int:agendamento_id>', methods=['GET', 'POST'])
@login_required_barbeiro
def concluir_atendimento(agendamento_id):
    barbeiro = get_barbeiro_logado()
    agendamento = Agendamento.query.get_or_404(agendamento_id)

    if agendamento.barbeiro_id != barbeiro.id:
        flash('Agendamento nao pertence a voce.', 'danger')
        return redirect(url_for('barbeiro.agenda'))

    if agendamento.status == 'concluido':
        flash('Este atendimento ja foi concluido.', 'info')
        return redirect(url_for('barbeiro.agenda'))

    if agendamento.status == 'cancelado':
        flash('Este atendimento foi cancelado.', 'warning')
        return redirect(url_for('barbeiro.agenda'))

    if request.method == 'POST':
        forma_pagamento = request.form.get('forma_pagamento', '').strip()
        desconto_fidelidade = float(request.form.get('desconto_fidelidade', 0))
        desconto_plano = float(request.form.get('desconto_plano', 0))

        # Pagamento dividido (split)
        forma2 = request.form.get('forma_pagamento_2', '').strip() or None
        valor2 = float(request.form.get('valor_2', 0))

        if not forma_pagamento:
            flash('Selecione a forma de pagamento.', 'danger')
            return redirect(url_for('barbeiro.concluir_atendimento', agendamento_id=agendamento_id))

        formas_validas = ['dinheiro', 'pix', 'cartao_debito', 'cartao_credito']
        if forma_pagamento not in formas_validas:
            flash('Forma de pagamento invalida.', 'danger')
            return redirect(url_for('barbeiro.concluir_atendimento', agendamento_id=agendamento_id))

        if forma2 and forma2 not in formas_validas:
            flash('Segunda forma de pagamento invalida.', 'danger')
            return redirect(url_for('barbeiro.concluir_atendimento', agendamento_id=agendamento_id))

        valor_total = agendamento.valor
        valor_pago = valor_total - desconto_fidelidade - desconto_plano

        # Calcular pontos de fidelidade
        config_fid = agendamento.barbearia.get_config_fidelidade()
        pontos = int(valor_pago * config_fid.get('pontos_por_real', 1))

        # Criar pagamento
        pagamento = Pagamento(
            agendamento_id=agendamento.id,
            forma=forma_pagamento,
            valor_pago=valor_pago,
            desconto_fidelidade=desconto_fidelidade,
            desconto_plano=desconto_plano,
            pontos_gerados=pontos,
            forma2=forma2,
            valor2=valor2,
        )
        db.session.add(pagamento)

        # Atualizar status do agendamento
        agendamento.status = 'concluido'

        # Creditar pontos de fidelidade ao cliente
        cliente = agendamento.cliente
        cliente.pontos_fidelidade += pontos

        db.session.flush()  # obter pagamento.id

        # Gerar comissao
        valor_comissao = calcular_comissao(
            valor_pago,
            barbeiro.percentual_comissao,
            sobre_liquido=barbeiro.comissao_sobre_liquido,
            desconto=(desconto_fidelidade + desconto_plano),
        )

        comissao = Comissao(
            barbeiro_id=barbeiro.id,
            pagamento_id=pagamento.id,
            valor_bruto=valor_pago,
            percentual=barbeiro.percentual_comissao,
            valor_comissao=valor_comissao,
        )
        db.session.add(comissao)

        db.session.commit()

        flash(f'Atendimento concluido! Comissao: R$ {valor_comissao:,.2f}', 'success')
        return redirect(url_for('barbeiro.agenda'))

    return render_template(
        'barbeiro/concluir.html',
        barbeiro=barbeiro,
        agendamento=agendamento,
    )


# ---------------------------------------------------------------------------
# MEUS GANHOS
# ---------------------------------------------------------------------------

@barbeiro_bp.route('/ganhos')
@login_required_barbeiro
def ganhos():
    barbeiro = get_barbeiro_logado()
    hoje = date.today()

    # Ganhos de hoje
    comissoes_hoje = (
        Comissao.query
        .filter_by(barbeiro_id=barbeiro.id)
        .filter(db.func.date(Comissao.data_geracao) == hoje)
        .all()
    )
    total_hoje = sum(c.valor_comissao for c in comissoes_hoje)
    bruto_hoje = sum(c.valor_bruto for c in comissoes_hoje)
    atendimentos_hoje = len(comissoes_hoje)

    # Ganhos da semana (segunda a domingo correntes)
    inicio_semana = hoje - timedelta(days=hoje.weekday())
    comissoes_semana = (
        Comissao.query
        .filter_by(barbeiro_id=barbeiro.id)
        .filter(db.func.date(Comissao.data_geracao) >= inicio_semana)
        .filter(db.func.date(Comissao.data_geracao) <= hoje)
        .all()
    )
    total_semana = sum(c.valor_comissao for c in comissoes_semana)
    bruto_semana = sum(c.valor_bruto for c in comissoes_semana)
    atendimentos_semana = len(comissoes_semana)

    # Ganhos do mes
    inicio_mes = hoje.replace(day=1)
    comissoes_mes = (
        Comissao.query
        .filter_by(barbeiro_id=barbeiro.id)
        .filter(db.func.date(Comissao.data_geracao) >= inicio_mes)
        .filter(db.func.date(Comissao.data_geracao) <= hoje)
        .all()
    )
    total_mes = sum(c.valor_comissao for c in comissoes_mes)
    bruto_mes = sum(c.valor_bruto for c in comissoes_mes)
    atendimentos_mes = len(comissoes_mes)

    # Detalhamento do dia (lista de comissoes com servico/cliente)
    detalhes_hoje = []
    for c in comissoes_hoje:
        pagamento = c.pagamento
        agendamento = pagamento.agendamento if pagamento else None
        detalhes_hoje.append({
            'comissao': c,
            'agendamento': agendamento,
            'cliente': agendamento.cliente if agendamento else None,
            'servico': agendamento.servico if agendamento else None,
        })

    return render_template(
        'barbeiro/ganhos.html',
        barbeiro=barbeiro,
        total_hoje=total_hoje,
        bruto_hoje=bruto_hoje,
        atendimentos_hoje=atendimentos_hoje,
        total_semana=total_semana,
        bruto_semana=bruto_semana,
        atendimentos_semana=atendimentos_semana,
        total_mes=total_mes,
        bruto_mes=bruto_mes,
        atendimentos_mes=atendimentos_mes,
        detalhes_hoje=detalhes_hoje,
        percentual_comissao=barbeiro.percentual_comissao,
    )


# ---------------------------------------------------------------------------
# PORTFOLIO
# ---------------------------------------------------------------------------

@barbeiro_bp.route('/portfolio')
@login_required_barbeiro
def portfolio():
    barbeiro = get_barbeiro_logado()
    fotos = (
        PortfolioFoto.query
        .filter_by(barbeiro_id=barbeiro.id)
        .order_by(PortfolioFoto.data_upload.desc())
        .all()
    )
    servicos = Servico.query.filter_by(
        barbearia_id=barbeiro.barbearia_id, ativo=True
    ).all()

    return render_template(
        'barbeiro/portfolio.html',
        barbeiro=barbeiro,
        fotos=fotos,
        servicos=servicos,
    )


@barbeiro_bp.route('/portfolio/upload', methods=['POST'])
@login_required_barbeiro
def portfolio_upload():
    barbeiro = get_barbeiro_logado()

    if 'foto' not in request.files:
        flash('Nenhuma foto selecionada.', 'danger')
        return redirect(url_for('barbeiro.portfolio'))

    foto = request.files['foto']
    if foto.filename == '':
        flash('Nenhuma foto selecionada.', 'danger')
        return redirect(url_for('barbeiro.portfolio'))

    if not allowed_file(foto.filename):
        flash('Formato de imagem nao suportado. Use PNG, JPG ou WEBP.', 'danger')
        return redirect(url_for('barbeiro.portfolio'))

    servico_tag = request.form.get('servico_tag', '').strip()
    legenda = request.form.get('legenda', '').strip()
    destaque = request.form.get('destaque') == '1'

    # Salvar arquivo
    ext = foto.filename.rsplit('.', 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    upload_dir = os.path.join(current_app.static_folder, 'uploads', 'portfolio')
    os.makedirs(upload_dir, exist_ok=True)
    filepath = os.path.join(upload_dir, filename)
    foto.save(filepath)

    url_foto = f"/static/uploads/portfolio/{filename}"

    nova_foto = PortfolioFoto(
        barbeiro_id=barbeiro.id,
        url_foto=url_foto,
        servico_tag=servico_tag or None,
        legenda=legenda or None,
        destaque=destaque,
    )
    db.session.add(nova_foto)
    db.session.commit()

    flash('Foto adicionada ao portfolio!', 'success')
    return redirect(url_for('barbeiro.portfolio'))


@barbeiro_bp.route('/portfolio/excluir/<int:foto_id>', methods=['POST'])
@login_required_barbeiro
def portfolio_excluir(foto_id):
    barbeiro = get_barbeiro_logado()
    foto = PortfolioFoto.query.get_or_404(foto_id)

    if foto.barbeiro_id != barbeiro.id:
        flash('Esta foto nao pertence a voce.', 'danger')
        return redirect(url_for('barbeiro.portfolio'))

    # Remover arquivo fisico se existir
    if foto.url_foto:
        caminho = os.path.join(current_app.root_path, '..', foto.url_foto.lstrip('/'))
        caminho_abs = os.path.abspath(caminho)
        if os.path.exists(caminho_abs):
            os.remove(caminho_abs)

    db.session.delete(foto)
    db.session.commit()

    flash('Foto removida do portfolio.', 'success')
    return redirect(url_for('barbeiro.portfolio'))


@barbeiro_bp.route('/portfolio/destaque/<int:foto_id>', methods=['POST'])
@login_required_barbeiro
def portfolio_destaque(foto_id):
    barbeiro = get_barbeiro_logado()
    foto = PortfolioFoto.query.get_or_404(foto_id)

    if foto.barbeiro_id != barbeiro.id:
        flash('Esta foto nao pertence a voce.', 'danger')
        return redirect(url_for('barbeiro.portfolio'))

    foto.destaque = not foto.destaque
    db.session.commit()

    estado = 'marcada como destaque' if foto.destaque else 'removida dos destaques'
    flash(f'Foto {estado}.', 'success')
    return redirect(url_for('barbeiro.portfolio'))


# ---------------------------------------------------------------------------
# METAS / OBJETIVOS
# ---------------------------------------------------------------------------

@barbeiro_bp.route('/metas')
@login_required_barbeiro
def metas():
    barbeiro = get_barbeiro_logado()
    hoje = date.today()

    meta = MetaBarbeiro.query.filter_by(
        barbeiro_id=barbeiro.id,
        mes=hoje.month,
        ano=hoje.year,
    ).first()

    # Calcular progresso do mes
    inicio_mes = hoje.replace(day=1)
    agendamentos_mes = Agendamento.query.filter(
        Agendamento.barbeiro_id == barbeiro.id,
        Agendamento.data >= inicio_mes,
        Agendamento.data <= hoje,
        Agendamento.status == 'concluido',
    ).count()

    comissoes_mes = (
        Comissao.query
        .filter_by(barbeiro_id=barbeiro.id)
        .filter(db.func.date(Comissao.data_geracao) >= inicio_mes)
        .filter(db.func.date(Comissao.data_geracao) <= hoje)
        .all()
    )
    faturamento_mes = sum(c.valor_bruto for c in comissoes_mes)

    # Media de avaliacao do mes
    avaliacoes_mes = Avaliacao.query.filter(
        Avaliacao.barbeiro_id == barbeiro.id,
        db.func.date(Avaliacao.data) >= inicio_mes,
        db.func.date(Avaliacao.data) <= hoje,
    ).all()
    nota_media = (
        sum(a.nota for a in avaliacoes_mes) / len(avaliacoes_mes)
        if avaliacoes_mes else 0.0
    )

    # Calcular percentuais de progresso
    progresso = {
        'atendimentos': 0,
        'faturamento': 0,
        'nota': 0,
    }
    if meta:
        if meta.meta_atendimentos > 0:
            progresso['atendimentos'] = min(
                round((agendamentos_mes / meta.meta_atendimentos) * 100, 1), 100
            )
        if meta.meta_faturamento > 0:
            progresso['faturamento'] = min(
                round((faturamento_mes / meta.meta_faturamento) * 100, 1), 100
            )
        if meta.meta_nota_minima > 0 and nota_media > 0:
            progresso['nota'] = min(
                round((nota_media / meta.meta_nota_minima) * 100, 1), 100
            )

    return render_template(
        'barbeiro/metas.html',
        barbeiro=barbeiro,
        meta=meta,
        agendamentos_mes=agendamentos_mes,
        faturamento_mes=faturamento_mes,
        nota_media=round(nota_media, 1),
        progresso=progresso,
        mes_atual=hoje.strftime('%B %Y'),
    )


# ---------------------------------------------------------------------------
# PERFIL / CONFIGURACOES
# ---------------------------------------------------------------------------

@barbeiro_bp.route('/perfil', methods=['GET', 'POST'])
@login_required_barbeiro
def perfil():
    barbeiro = get_barbeiro_logado()

    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        telefone = request.form.get('telefone', '').strip()
        pin_atual = request.form.get('pin_atual', '').strip()
        pin_novo = request.form.get('pin_novo', '').strip()
        pin_confirma = request.form.get('pin_confirma', '').strip()

        if nome:
            barbeiro.nome = nome
        if telefone:
            barbeiro.telefone = telefone

        # Upload de foto de perfil
        if 'foto' in request.files:
            foto = request.files['foto']
            if foto.filename and allowed_file(foto.filename):
                ext = foto.filename.rsplit('.', 1)[1].lower()
                filename = f"barbeiro_{barbeiro.id}.{ext}"
                upload_dir = os.path.join(current_app.static_folder, 'uploads', 'barbeiros')
                os.makedirs(upload_dir, exist_ok=True)
                filepath = os.path.join(upload_dir, filename)
                foto.save(filepath)
                barbeiro.foto = f"/static/uploads/barbeiros/{filename}"

        # Alterar PIN
        if pin_novo:
            if not pin_atual:
                flash('Informe o PIN atual para alterar.', 'danger')
                return redirect(url_for('barbeiro.perfil'))
            if not barbeiro.check_pin(pin_atual):
                flash('PIN atual incorreto.', 'danger')
                return redirect(url_for('barbeiro.perfil'))
            if len(pin_novo) != 4 or not pin_novo.isdigit():
                flash('O novo PIN deve ter exatamente 4 digitos.', 'danger')
                return redirect(url_for('barbeiro.perfil'))
            if pin_novo != pin_confirma:
                flash('Confirmacao do novo PIN nao confere.', 'danger')
                return redirect(url_for('barbeiro.perfil'))
            barbeiro.set_pin(pin_novo)
            flash('PIN alterado com sucesso.', 'success')

        db.session.commit()
        flash('Perfil atualizado!', 'success')
        return redirect(url_for('barbeiro.perfil'))

    # Estatisticas para exibir no perfil
    total_avaliacoes = Avaliacao.query.filter_by(barbeiro_id=barbeiro.id).count()
    media_nota = 0.0
    if total_avaliacoes > 0:
        soma = db.session.query(db.func.sum(Avaliacao.nota)).filter_by(
            barbeiro_id=barbeiro.id
        ).scalar() or 0
        media_nota = round(soma / total_avaliacoes, 1)

    total_atendimentos = Agendamento.query.filter_by(
        barbeiro_id=barbeiro.id, status='concluido'
    ).count()

    return render_template(
        'barbeiro/perfil.html',
        barbeiro=barbeiro,
        total_avaliacoes=total_avaliacoes,
        media_nota=media_nota,
        total_atendimentos=total_atendimentos,
    )


# ---------------------------------------------------------------------------
# BLOQUEAR HORARIOS
# ---------------------------------------------------------------------------

@barbeiro_bp.route('/bloquear', methods=['GET', 'POST'])
@login_required_barbeiro
def bloquear_horario():
    barbeiro = get_barbeiro_logado()

    if request.method == 'POST':
        data_str = request.form.get('data', '').strip()
        hora_inicio = request.form.get('hora_inicio', '').strip()
        hora_fim_form = request.form.get('hora_fim', '').strip()
        motivo = request.form.get('motivo', '').strip()

        if not data_str or not hora_inicio or not hora_fim_form:
            flash('Preencha data, hora de inicio e hora de fim.', 'danger')
            return redirect(url_for('barbeiro.bloquear_horario'))

        try:
            data_bloqueio = datetime.strptime(data_str, '%Y-%m-%d').date()
        except ValueError:
            flash('Data invalida.', 'danger')
            return redirect(url_for('barbeiro.bloquear_horario'))

        if data_bloqueio < date.today():
            flash('Nao e possivel bloquear datas passadas.', 'warning')
            return redirect(url_for('barbeiro.bloquear_horario'))

        if hora_inicio >= hora_fim_form:
            flash('Hora de inicio deve ser anterior a hora de fim.', 'danger')
            return redirect(url_for('barbeiro.bloquear_horario'))

        # Verificar conflito com agendamentos existentes
        conflitos = Agendamento.query.filter(
            Agendamento.barbeiro_id == barbeiro.id,
            Agendamento.data == data_bloqueio,
            Agendamento.status.in_(['agendado', 'confirmado']),
            Agendamento.hora_inicio < hora_fim_form,
            Agendamento.hora_fim > hora_inicio,
        ).count()

        if conflitos > 0:
            flash(
                f'Existem {conflitos} agendamento(s) nesse horario. '
                'Cancele-os antes de bloquear.',
                'danger',
            )
            return redirect(url_for('barbeiro.bloquear_horario'))

        # Criar bloqueio como agendamento especial (cliente_id=0 indica bloqueio)
        # Usamos um agendamento "fantasma" com status bloqueado
        bloqueio = Agendamento(
            barbearia_id=barbeiro.barbearia_id,
            barbeiro_id=barbeiro.id,
            cliente_id=0,
            servico_id=0,
            data=data_bloqueio,
            hora_inicio=hora_inicio,
            hora_fim=hora_fim_form,
            status='bloqueado',
            valor=0,
            observacao=motivo or 'Horario bloqueado pelo barbeiro',
            origem='manual',
        )
        db.session.add(bloqueio)
        db.session.commit()

        flash(
            f'Horario bloqueado em {data_bloqueio.strftime("%d/%m/%Y")} '
            f'das {hora_inicio} as {hora_fim_form}.',
            'success',
        )
        return redirect(url_for('barbeiro.bloquear_horario'))

    # Listar bloqueios futuros
    bloqueios = (
        Agendamento.query
        .filter_by(barbeiro_id=barbeiro.id, status='bloqueado')
        .filter(Agendamento.data >= date.today())
        .order_by(Agendamento.data, Agendamento.hora_inicio)
        .all()
    )

    return render_template(
        'barbeiro/bloquear.html',
        barbeiro=barbeiro,
        bloqueios=bloqueios,
    )


@barbeiro_bp.route('/desbloquear/<int:agendamento_id>', methods=['POST'])
@login_required_barbeiro
def desbloquear_horario(agendamento_id):
    barbeiro = get_barbeiro_logado()
    bloqueio = Agendamento.query.get_or_404(agendamento_id)

    if bloqueio.barbeiro_id != barbeiro.id or bloqueio.status != 'bloqueado':
        flash('Bloqueio nao encontrado.', 'danger')
        return redirect(url_for('barbeiro.bloquear_horario'))

    db.session.delete(bloqueio)
    db.session.commit()

    flash('Bloqueio removido.', 'success')
    return redirect(url_for('barbeiro.bloquear_horario'))
