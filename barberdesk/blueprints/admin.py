from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from functools import wraps
from datetime import datetime, date, timedelta
from sqlalchemy import func, extract
import json

from models import (
    db, Barbearia, Barbeiro, Servico, Cliente, Agendamento,
    Pagamento, Comissao, Produto, MovimentacaoEstoque,
    PlanoAssinatura, AssinaturaCliente, Despesa, MetaBarbeiro, LembreteLog
)
from utils import slugify, calcular_comissao, gerar_horarios_disponiveis, calcular_hora_fim, formatar_moeda

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


# ---------------------------------------------------------------------------
# Decorator: login obrigatorio
# ---------------------------------------------------------------------------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'barbearia_id' not in session:
            flash('Faca login para acessar o painel.', 'warning')
            return redirect(url_for('admin.login'))
        return f(*args, **kwargs)
    return decorated_function


def get_barbearia():
    """Retorna a barbearia logada a partir da sessao."""
    return Barbearia.query.get(session['barbearia_id'])


# ===========================================================================
# 1. LOGIN / LOGOUT
# ===========================================================================
@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        senha = request.form.get('senha', '')

        barbearia = Barbearia.query.filter_by(email=email).first()

        if barbearia and barbearia.check_senha(senha):
            if not barbearia.ativo:
                flash('Sua conta esta desativada. Entre em contato com o suporte.', 'danger')
                return redirect(url_for('admin.login'))

            session['barbearia_id'] = barbearia.id
            session['barbearia_nome'] = barbearia.nome
            flash(f'Bem-vindo, {barbearia.nome}!', 'success')

            if not barbearia.setup_completo:
                return redirect(url_for('admin.setup', step=1))

            return redirect(url_for('admin.dashboard'))
        else:
            flash('Email ou senha incorretos.', 'danger')

    return render_template('admin/login.html')


@admin_bp.route('/logout')
def logout():
    session.pop('barbearia_id', None)
    session.pop('barbearia_nome', None)
    flash('Voce saiu do painel.', 'info')
    return redirect(url_for('admin.login'))


# ===========================================================================
# 2. SETUP WIZARD (4 etapas)
# ===========================================================================
@admin_bp.route('/setup/<int:step>', methods=['GET', 'POST'])
@login_required
def setup(step):
    barbearia = get_barbearia()

    # --- STEP 1: Dados da barbearia ---
    if step == 1:
        if request.method == 'POST':
            barbearia.nome = request.form.get('nome', barbearia.nome)
            barbearia.slug = slugify(request.form.get('nome', barbearia.nome))
            barbearia.telefone = request.form.get('telefone', '')
            barbearia.endereco = request.form.get('endereco', '')
            db.session.commit()
            flash('Dados da barbearia salvos!', 'success')
            return redirect(url_for('admin.setup', step=2))

        return render_template('admin/setup_step1.html', barbearia=barbearia, step=1)

    # --- STEP 2: Horarios de funcionamento ---
    elif step == 2:
        if request.method == 'POST':
            barbearia.horario_abertura = request.form.get('horario_abertura', '08:00')
            barbearia.horario_fechamento = request.form.get('horario_fechamento', '20:00')
            dias = request.form.getlist('dias_funcionamento')
            barbearia.set_dias_funcionamento([int(d) for d in dias])
            db.session.commit()
            flash('Horarios salvos!', 'success')
            return redirect(url_for('admin.setup', step=3))

        return render_template('admin/setup_step2.html', barbearia=barbearia, step=2)

    # --- STEP 3: Cadastrar barbeiros ---
    elif step == 3:
        if request.method == 'POST':
            nome = request.form.get('nome', '').strip()
            telefone = request.form.get('telefone', '').strip()
            pin = request.form.get('pin', '1234')
            percentual = float(request.form.get('percentual_comissao', 50))

            if nome and telefone:
                barbeiro = Barbeiro(
                    barbearia_id=barbearia.id,
                    nome=nome,
                    slug=slugify(nome),
                    telefone=telefone,
                    percentual_comissao=percentual,
                    pin_hash=''
                )
                barbeiro.set_pin(pin)
                db.session.add(barbeiro)
                db.session.commit()
                flash(f'Barbeiro {nome} cadastrado!', 'success')
            else:
                flash('Preencha nome e telefone do barbeiro.', 'warning')

            return redirect(url_for('admin.setup', step=3))

        barbeiros = Barbeiro.query.filter_by(barbearia_id=barbearia.id, ativo=True).all()
        return render_template('admin/setup_step3.html', barbearia=barbearia, barbeiros=barbeiros, step=3)

    # --- STEP 4: Cadastrar servicos ---
    elif step == 4:
        if request.method == 'POST':
            action = request.form.get('action', 'add_servico')

            if action == 'add_servico':
                nome = request.form.get('nome', '').strip()
                preco = request.form.get('preco', '0')
                duracao = request.form.get('duracao_minutos', '30')

                if nome:
                    servico = Servico(
                        barbearia_id=barbearia.id,
                        nome=nome,
                        preco=float(preco),
                        duracao_minutos=int(duracao)
                    )
                    db.session.add(servico)
                    db.session.commit()
                    flash(f'Servico {nome} cadastrado!', 'success')
                else:
                    flash('Preencha o nome do servico.', 'warning')

                return redirect(url_for('admin.setup', step=4))

            elif action == 'finalizar':
                barbearia.setup_completo = True
                db.session.commit()
                flash('Setup concluido! Bem-vindo ao BarberDesk.', 'success')
                return redirect(url_for('admin.dashboard'))

        servicos = Servico.query.filter_by(barbearia_id=barbearia.id, ativo=True).all()
        return render_template('admin/setup_step4.html', barbearia=barbearia, servicos=servicos, step=4)

    else:
        return redirect(url_for('admin.setup', step=1))


# ===========================================================================
# 3. DASHBOARD
# ===========================================================================
@admin_bp.route('/dashboard')
@login_required
def dashboard():
    barbearia = get_barbearia()
    hoje = date.today()

    # Agendamentos do dia
    agendamentos_hoje = Agendamento.query.filter_by(
        barbearia_id=barbearia.id, data=hoje
    ).all()

    total_agendamentos = len(agendamentos_hoje)
    concluidos = sum(1 for a in agendamentos_hoje if a.status == 'concluido')
    cancelados = sum(1 for a in agendamentos_hoje if a.status == 'cancelado')
    pendentes = sum(1 for a in agendamentos_hoje if a.status in ('agendado', 'confirmado'))

    # Faturamento do dia
    pagamentos_hoje = Pagamento.query.join(Agendamento).filter(
        Agendamento.barbearia_id == barbearia.id,
        Agendamento.data == hoje,
        Agendamento.status == 'concluido'
    ).all()
    faturamento_dia = sum(p.valor_pago for p in pagamentos_hoje)

    # Comissoes pendentes
    comissoes_pendentes = Comissao.query.join(Barbeiro).filter(
        Barbeiro.barbearia_id == barbearia.id,
        Comissao.status == 'pendente'
    ).count()

    # Produtos com estoque baixo
    alertas_estoque = Produto.query.filter(
        Produto.barbearia_id == barbearia.id,
        Produto.ativo == True,
        Produto.estoque_atual <= Produto.estoque_minimo
    ).count()

    # Barbeiros ativos
    barbeiros = Barbeiro.query.filter_by(barbearia_id=barbearia.id, ativo=True).all()

    # Proximos agendamentos (agendados/confirmados)
    proximos = Agendamento.query.filter(
        Agendamento.barbearia_id == barbearia.id,
        Agendamento.data == hoje,
        Agendamento.status.in_(['agendado', 'confirmado'])
    ).order_by(Agendamento.hora_inicio).limit(10).all()

    return render_template(
        'admin/dashboard.html',
        barbearia=barbearia,
        total_agendamentos=total_agendamentos,
        concluidos=concluidos,
        cancelados=cancelados,
        pendentes=pendentes,
        faturamento_dia=formatar_moeda(faturamento_dia),
        faturamento_dia_raw=faturamento_dia,
        comissoes_pendentes=comissoes_pendentes,
        alertas_estoque=alertas_estoque,
        barbeiros=barbeiros,
        proximos=proximos,
        hoje=hoje
    )


# ===========================================================================
# 4. AGENDA
# ===========================================================================
@admin_bp.route('/agenda')
@login_required
def agenda():
    barbearia = get_barbearia()
    data_str = request.args.get('data', date.today().isoformat())
    try:
        data_selecionada = date.fromisoformat(data_str)
    except ValueError:
        data_selecionada = date.today()

    barbeiro_id = request.args.get('barbeiro_id', type=int)

    query = Agendamento.query.filter_by(barbearia_id=barbearia.id, data=data_selecionada)
    if barbeiro_id:
        query = query.filter_by(barbeiro_id=barbeiro_id)

    agendamentos = query.order_by(Agendamento.hora_inicio).all()
    barbeiros = Barbeiro.query.filter_by(barbearia_id=barbearia.id, ativo=True).all()

    return render_template(
        'admin/agenda.html',
        barbearia=barbearia,
        agendamentos=agendamentos,
        barbeiros=barbeiros,
        data_selecionada=data_selecionada,
        barbeiro_id_filtro=barbeiro_id
    )


@admin_bp.route('/agenda/novo', methods=['GET', 'POST'])
@login_required
def agenda_novo():
    barbearia = get_barbearia()

    if request.method == 'POST':
        barbeiro_id = int(request.form.get('barbeiro_id'))
        servico_id = int(request.form.get('servico_id'))
        data_str = request.form.get('data')
        hora_inicio = request.form.get('hora_inicio')
        cliente_nome = request.form.get('cliente_nome', '').strip()
        cliente_telefone = request.form.get('cliente_telefone', '').strip()
        observacao = request.form.get('observacao', '').strip()

        data_ag = date.fromisoformat(data_str)
        servico = Servico.query.get(servico_id)
        hora_fim = calcular_hora_fim(hora_inicio, servico.duracao_minutos)

        # Buscar ou criar cliente
        cliente = Cliente.query.filter_by(
            barbearia_id=barbearia.id,
            telefone=cliente_telefone
        ).first()

        if not cliente:
            cliente = Cliente(
                barbearia_id=barbearia.id,
                nome=cliente_nome,
                telefone=cliente_telefone
            )
            db.session.add(cliente)
            db.session.flush()

        agendamento = Agendamento(
            barbearia_id=barbearia.id,
            barbeiro_id=barbeiro_id,
            cliente_id=cliente.id,
            servico_id=servico_id,
            data=data_ag,
            hora_inicio=hora_inicio,
            hora_fim=hora_fim,
            status='agendado',
            valor=servico.preco,
            observacao=observacao,
            origem='manual'
        )
        db.session.add(agendamento)
        db.session.commit()
        flash('Agendamento criado com sucesso!', 'success')
        return redirect(url_for('admin.agenda', data=data_str))

    barbeiros = Barbeiro.query.filter_by(barbearia_id=barbearia.id, ativo=True).all()
    servicos = Servico.query.filter_by(barbearia_id=barbearia.id, ativo=True).all()
    return render_template(
        'admin/agenda_novo.html',
        barbearia=barbearia,
        barbeiros=barbeiros,
        servicos=servicos
    )


@admin_bp.route('/agenda/<int:agendamento_id>/status', methods=['POST'])
@login_required
def agenda_atualizar_status(agendamento_id):
    barbearia = get_barbearia()
    agendamento = Agendamento.query.filter_by(
        id=agendamento_id, barbearia_id=barbearia.id
    ).first_or_404()

    novo_status = request.form.get('status')
    if novo_status not in ('agendado', 'confirmado', 'concluido', 'cancelado'):
        flash('Status invalido.', 'danger')
        return redirect(url_for('admin.agenda', data=agendamento.data.isoformat()))

    agendamento.status = novo_status

    # Ao concluir, registrar pagamento se informado
    if novo_status == 'concluido':
        forma = request.form.get('forma_pagamento', 'dinheiro')
        valor_pago = float(request.form.get('valor_pago', agendamento.valor))
        desconto_fidelidade = float(request.form.get('desconto_fidelidade', 0))
        desconto_plano = float(request.form.get('desconto_plano', 0))

        pagamento = Pagamento(
            agendamento_id=agendamento.id,
            forma=forma,
            valor_pago=valor_pago,
            desconto_fidelidade=desconto_fidelidade,
            desconto_plano=desconto_plano
        )

        # Split payment
        forma2 = request.form.get('forma2')
        valor2 = request.form.get('valor2', 0)
        if forma2 and float(valor2) > 0:
            pagamento.forma2 = forma2
            pagamento.valor2 = float(valor2)

        # Pontos fidelidade
        config_fid = barbearia.get_config_fidelidade()
        pontos = int(valor_pago * config_fid.get('pontos_por_real', 1))
        pagamento.pontos_gerados = pontos

        db.session.add(pagamento)
        db.session.flush()

        # Atualizar pontos do cliente
        cliente = agendamento.cliente
        cliente.pontos_fidelidade += pontos

        # Gerar comissao
        barbeiro = agendamento.barbeiro
        valor_comissao = calcular_comissao(
            valor_pago,
            barbeiro.percentual_comissao,
            sobre_liquido=barbeiro.comissao_sobre_liquido,
            desconto=desconto_fidelidade + desconto_plano
        )
        comissao = Comissao(
            barbeiro_id=barbeiro.id,
            pagamento_id=pagamento.id,
            valor_bruto=valor_pago,
            percentual=barbeiro.percentual_comissao,
            valor_comissao=valor_comissao,
            status='pendente'
        )
        db.session.add(comissao)

    db.session.commit()
    flash(f'Status atualizado para {novo_status}.', 'success')
    return redirect(url_for('admin.agenda', data=agendamento.data.isoformat()))


@admin_bp.route('/agenda/horarios-disponiveis')
@login_required
def horarios_disponiveis():
    """Endpoint AJAX para retornar horarios disponiveis."""
    barbearia = get_barbearia()
    barbeiro_id = request.args.get('barbeiro_id', type=int)
    servico_id = request.args.get('servico_id', type=int)
    data_str = request.args.get('data', '')

    if not all([barbeiro_id, servico_id, data_str]):
        return jsonify([])

    try:
        data_ag = date.fromisoformat(data_str)
    except ValueError:
        return jsonify([])

    servico = Servico.query.get(servico_id)
    if not servico:
        return jsonify([])

    agendamentos_existentes = Agendamento.query.filter(
        Agendamento.barbearia_id == barbearia.id,
        Agendamento.barbeiro_id == barbeiro_id,
        Agendamento.data == data_ag,
        Agendamento.status.in_(['agendado', 'confirmado'])
    ).all()

    horarios = gerar_horarios_disponiveis(
        barbearia.horario_abertura,
        barbearia.horario_fechamento,
        servico.duracao_minutos,
        agendamentos_existentes,
        data_ag
    )
    return jsonify(horarios)


# ===========================================================================
# 5. CLIENTES
# ===========================================================================
@admin_bp.route('/clientes')
@login_required
def clientes():
    barbearia = get_barbearia()
    busca = request.args.get('busca', '').strip()
    page = request.args.get('page', 1, type=int)

    query = Cliente.query.filter_by(barbearia_id=barbearia.id)
    if busca:
        query = query.filter(
            (Cliente.nome.ilike(f'%{busca}%')) | (Cliente.telefone.ilike(f'%{busca}%'))
        )

    clientes_pag = query.order_by(Cliente.nome).paginate(page=page, per_page=20, error_out=False)

    return render_template(
        'admin/clientes.html',
        barbearia=barbearia,
        clientes=clientes_pag,
        busca=busca
    )


@admin_bp.route('/clientes/<int:cliente_id>')
@login_required
def cliente_detalhe(cliente_id):
    barbearia = get_barbearia()
    cliente = Cliente.query.filter_by(
        id=cliente_id, barbearia_id=barbearia.id
    ).first_or_404()

    historico = Agendamento.query.filter_by(
        cliente_id=cliente.id
    ).order_by(Agendamento.data.desc(), Agendamento.hora_inicio.desc()).all()

    total_gasto = sum(
        a.pagamento.valor_pago for a in historico
        if a.status == 'concluido' and a.pagamento
    )

    return render_template(
        'admin/cliente_detalhe.html',
        barbearia=barbearia,
        cliente=cliente,
        historico=historico,
        total_gasto=formatar_moeda(total_gasto)
    )


# ===========================================================================
# 6. BARBEIROS
# ===========================================================================
@admin_bp.route('/barbeiros')
@login_required
def barbeiros():
    barbearia = get_barbearia()
    lista = Barbeiro.query.filter_by(barbearia_id=barbearia.id).order_by(Barbeiro.nome).all()
    return render_template('admin/barbeiros.html', barbearia=barbearia, barbeiros=lista)


@admin_bp.route('/barbeiros/novo', methods=['GET', 'POST'])
@login_required
def barbeiro_novo():
    barbearia = get_barbearia()

    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        telefone = request.form.get('telefone', '').strip()
        pin = request.form.get('pin', '1234')
        percentual = float(request.form.get('percentual_comissao', 50))
        comissao_liquido = request.form.get('comissao_sobre_liquido') == 'on'

        if not nome or not telefone:
            flash('Nome e telefone sao obrigatorios.', 'warning')
            return redirect(url_for('admin.barbeiro_novo'))

        barbeiro = Barbeiro(
            barbearia_id=barbearia.id,
            nome=nome,
            slug=slugify(nome),
            telefone=telefone,
            percentual_comissao=percentual,
            comissao_sobre_liquido=comissao_liquido,
            pin_hash=''
        )
        barbeiro.set_pin(pin)
        db.session.add(barbeiro)
        db.session.commit()
        flash(f'Barbeiro {nome} cadastrado com sucesso!', 'success')
        return redirect(url_for('admin.barbeiros'))

    return render_template('admin/barbeiro_form.html', barbearia=barbearia, barbeiro=None)


@admin_bp.route('/barbeiros/<int:barbeiro_id>/editar', methods=['GET', 'POST'])
@login_required
def barbeiro_editar(barbeiro_id):
    barbearia = get_barbearia()
    barbeiro = Barbeiro.query.filter_by(
        id=barbeiro_id, barbearia_id=barbearia.id
    ).first_or_404()

    if request.method == 'POST':
        barbeiro.nome = request.form.get('nome', barbeiro.nome).strip()
        barbeiro.slug = slugify(barbeiro.nome)
        barbeiro.telefone = request.form.get('telefone', barbeiro.telefone).strip()
        barbeiro.percentual_comissao = float(request.form.get('percentual_comissao', barbeiro.percentual_comissao))
        barbeiro.comissao_sobre_liquido = request.form.get('comissao_sobre_liquido') == 'on'
        barbeiro.ativo = request.form.get('ativo') == 'on'

        novo_pin = request.form.get('pin', '').strip()
        if novo_pin:
            barbeiro.set_pin(novo_pin)

        db.session.commit()
        flash(f'Barbeiro {barbeiro.nome} atualizado!', 'success')
        return redirect(url_for('admin.barbeiros'))

    return render_template('admin/barbeiro_form.html', barbearia=barbearia, barbeiro=barbeiro)


@admin_bp.route('/barbeiros/<int:barbeiro_id>/excluir', methods=['POST'])
@login_required
def barbeiro_excluir(barbeiro_id):
    barbearia = get_barbearia()
    barbeiro = Barbeiro.query.filter_by(
        id=barbeiro_id, barbearia_id=barbearia.id
    ).first_or_404()

    # Soft delete: desativa em vez de excluir
    barbeiro.ativo = False
    db.session.commit()
    flash(f'Barbeiro {barbeiro.nome} desativado.', 'info')
    return redirect(url_for('admin.barbeiros'))


# ===========================================================================
# 7. SERVICOS
# ===========================================================================
@admin_bp.route('/servicos')
@login_required
def servicos():
    barbearia = get_barbearia()
    lista = Servico.query.filter_by(barbearia_id=barbearia.id).order_by(Servico.nome).all()
    return render_template('admin/servicos.html', barbearia=barbearia, servicos=lista)


@admin_bp.route('/servicos/novo', methods=['GET', 'POST'])
@login_required
def servico_novo():
    barbearia = get_barbearia()

    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        preco = request.form.get('preco', '0')
        duracao = request.form.get('duracao_minutos', '30')
        creditos = request.form.get('creditos_plano', '1')

        if not nome:
            flash('Nome do servico e obrigatorio.', 'warning')
            return redirect(url_for('admin.servico_novo'))

        servico = Servico(
            barbearia_id=barbearia.id,
            nome=nome,
            preco=float(preco),
            duracao_minutos=int(duracao),
            creditos_plano=int(creditos)
        )
        db.session.add(servico)
        db.session.commit()
        flash(f'Servico {nome} criado!', 'success')
        return redirect(url_for('admin.servicos'))

    return render_template('admin/servico_form.html', barbearia=barbearia, servico=None)


@admin_bp.route('/servicos/<int:servico_id>/editar', methods=['GET', 'POST'])
@login_required
def servico_editar(servico_id):
    barbearia = get_barbearia()
    servico = Servico.query.filter_by(
        id=servico_id, barbearia_id=barbearia.id
    ).first_or_404()

    if request.method == 'POST':
        servico.nome = request.form.get('nome', servico.nome).strip()
        servico.preco = float(request.form.get('preco', servico.preco))
        servico.duracao_minutos = int(request.form.get('duracao_minutos', servico.duracao_minutos))
        servico.creditos_plano = int(request.form.get('creditos_plano', servico.creditos_plano))
        servico.ativo = request.form.get('ativo') == 'on'
        db.session.commit()
        flash(f'Servico {servico.nome} atualizado!', 'success')
        return redirect(url_for('admin.servicos'))

    return render_template('admin/servico_form.html', barbearia=barbearia, servico=servico)


@admin_bp.route('/servicos/<int:servico_id>/excluir', methods=['POST'])
@login_required
def servico_excluir(servico_id):
    barbearia = get_barbearia()
    servico = Servico.query.filter_by(
        id=servico_id, barbearia_id=barbearia.id
    ).first_or_404()

    servico.ativo = False
    db.session.commit()
    flash(f'Servico {servico.nome} desativado.', 'info')
    return redirect(url_for('admin.servicos'))


# ===========================================================================
# 8. CAIXA / FECHAMENTO DIARIO
# ===========================================================================
@admin_bp.route('/caixa')
@login_required
def caixa():
    barbearia = get_barbearia()
    data_str = request.args.get('data', date.today().isoformat())
    try:
        data_selecionada = date.fromisoformat(data_str)
    except ValueError:
        data_selecionada = date.today()

    pagamentos = Pagamento.query.join(Agendamento).filter(
        Agendamento.barbearia_id == barbearia.id,
        Agendamento.data == data_selecionada,
        Agendamento.status == 'concluido'
    ).all()

    # Faturamento por forma de pagamento
    faturamento_por_forma = {}
    total_geral = 0
    total_descontos = 0

    for p in pagamentos:
        # Forma principal
        forma = p.forma or 'dinheiro'
        faturamento_por_forma.setdefault(forma, 0)
        faturamento_por_forma[forma] += p.valor_pago

        # Forma secundaria (split)
        if p.forma2 and p.valor2 and p.valor2 > 0:
            faturamento_por_forma.setdefault(p.forma2, 0)
            faturamento_por_forma[p.forma2] += p.valor2

        total_geral += p.valor_pago + (p.valor2 or 0)
        total_descontos += (p.desconto_fidelidade or 0) + (p.desconto_plano or 0)

    # Comissoes do dia
    comissoes_dia = Comissao.query.join(Pagamento).join(Agendamento).filter(
        Agendamento.barbearia_id == barbearia.id,
        Agendamento.data == data_selecionada
    ).all()
    total_comissoes = sum(c.valor_comissao for c in comissoes_dia)

    # Resumo por barbeiro
    resumo_barbeiros = {}
    for c in comissoes_dia:
        barbeiro_nome = c.barbeiro.nome
        if barbeiro_nome not in resumo_barbeiros:
            resumo_barbeiros[barbeiro_nome] = {
                'atendimentos': 0,
                'faturamento': 0,
                'comissao': 0
            }
        resumo_barbeiros[barbeiro_nome]['atendimentos'] += 1
        resumo_barbeiros[barbeiro_nome]['faturamento'] += c.valor_bruto
        resumo_barbeiros[barbeiro_nome]['comissao'] += c.valor_comissao

    formas_formatadas = {
        k: formatar_moeda(v) for k, v in faturamento_por_forma.items()
    }

    return render_template(
        'admin/caixa.html',
        barbearia=barbearia,
        data_selecionada=data_selecionada,
        pagamentos=pagamentos,
        faturamento_por_forma=formas_formatadas,
        faturamento_por_forma_raw=faturamento_por_forma,
        total_geral=formatar_moeda(total_geral),
        total_geral_raw=total_geral,
        total_descontos=formatar_moeda(total_descontos),
        total_comissoes=formatar_moeda(total_comissoes),
        total_comissoes_raw=total_comissoes,
        resumo_barbeiros=resumo_barbeiros,
        lucro_liquido=formatar_moeda(total_geral - total_comissoes)
    )


# ===========================================================================
# 9. COMISSOES
# ===========================================================================
@admin_bp.route('/comissoes')
@login_required
def comissoes():
    barbearia = get_barbearia()
    status_filtro = request.args.get('status', 'pendente')
    barbeiro_id = request.args.get('barbeiro_id', type=int)

    query = Comissao.query.join(Barbeiro).filter(
        Barbeiro.barbearia_id == barbearia.id
    )

    if status_filtro:
        query = query.filter(Comissao.status == status_filtro)
    if barbeiro_id:
        query = query.filter(Comissao.barbeiro_id == barbeiro_id)

    lista = query.order_by(Comissao.data_geracao.desc()).all()

    # Totais por barbeiro
    totais_barbeiro = {}
    for c in lista:
        nome = c.barbeiro.nome
        totais_barbeiro.setdefault(nome, 0)
        totais_barbeiro[nome] += c.valor_comissao

    barbeiros = Barbeiro.query.filter_by(barbearia_id=barbearia.id, ativo=True).all()

    return render_template(
        'admin/comissoes.html',
        barbearia=barbearia,
        comissoes=lista,
        barbeiros=barbeiros,
        status_filtro=status_filtro,
        barbeiro_id_filtro=barbeiro_id,
        totais_barbeiro=totais_barbeiro
    )


@admin_bp.route('/comissoes/pagar', methods=['POST'])
@login_required
def comissoes_pagar():
    barbearia = get_barbearia()
    comissao_ids = request.form.getlist('comissao_ids')

    if not comissao_ids:
        flash('Selecione ao menos uma comissao para pagar.', 'warning')
        return redirect(url_for('admin.comissoes'))

    agora = datetime.utcnow()
    total_pago = 0

    for cid in comissao_ids:
        comissao = Comissao.query.join(Barbeiro).filter(
            Comissao.id == int(cid),
            Barbeiro.barbearia_id == barbearia.id,
            Comissao.status == 'pendente'
        ).first()

        if comissao:
            comissao.status = 'pago'
            comissao.data_pagamento = agora
            total_pago += comissao.valor_comissao

    db.session.commit()
    flash(f'Comissoes pagas: {formatar_moeda(total_pago)}', 'success')
    return redirect(url_for('admin.comissoes'))


@admin_bp.route('/comissoes/pagar-barbeiro/<int:barbeiro_id>', methods=['POST'])
@login_required
def comissoes_pagar_barbeiro(barbeiro_id):
    barbearia = get_barbearia()
    barbeiro = Barbeiro.query.filter_by(
        id=barbeiro_id, barbearia_id=barbearia.id
    ).first_or_404()

    pendentes = Comissao.query.filter_by(
        barbeiro_id=barbeiro.id,
        status='pendente'
    ).all()

    agora = datetime.utcnow()
    total = 0
    for c in pendentes:
        c.status = 'pago'
        c.data_pagamento = agora
        total += c.valor_comissao

    db.session.commit()
    flash(f'Todas as comissoes de {barbeiro.nome} pagas: {formatar_moeda(total)}', 'success')
    return redirect(url_for('admin.comissoes'))


# ===========================================================================
# 10. FIDELIDADE
# ===========================================================================
@admin_bp.route('/fidelidade', methods=['GET', 'POST'])
@login_required
def fidelidade():
    barbearia = get_barbearia()

    if request.method == 'POST':
        config = {
            'pontos_por_real': int(request.form.get('pontos_por_real', 1)),
            'pontos_para_desconto': int(request.form.get('pontos_para_desconto', 100)),
            'valor_desconto': float(request.form.get('valor_desconto', 10)),
            'validade_dias': int(request.form.get('validade_dias', 90))
        }
        barbearia.set_config_fidelidade(config)
        db.session.commit()
        flash('Configuracoes de fidelidade atualizadas!', 'success')
        return redirect(url_for('admin.fidelidade'))

    config = barbearia.get_config_fidelidade()

    # Top clientes por pontos
    top_clientes = Cliente.query.filter_by(
        barbearia_id=barbearia.id
    ).order_by(Cliente.pontos_fidelidade.desc()).limit(20).all()

    return render_template(
        'admin/fidelidade.html',
        barbearia=barbearia,
        config=config,
        top_clientes=top_clientes
    )


# ===========================================================================
# 11. PRODUTOS / ESTOQUE
# ===========================================================================
@admin_bp.route('/produtos')
@login_required
def produtos():
    barbearia = get_barbearia()
    lista = Produto.query.filter_by(barbearia_id=barbearia.id).order_by(Produto.nome).all()

    alertas = [p for p in lista if p.ativo and p.estoque_atual <= p.estoque_minimo]

    return render_template(
        'admin/produtos.html',
        barbearia=barbearia,
        produtos=lista,
        alertas=alertas
    )


@admin_bp.route('/produtos/novo', methods=['GET', 'POST'])
@login_required
def produto_novo():
    barbearia = get_barbearia()

    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        unidade = request.form.get('unidade', 'un')
        estoque_atual = float(request.form.get('estoque_atual', 0))
        estoque_minimo = float(request.form.get('estoque_minimo', 5))
        preco_custo = float(request.form.get('preco_custo', 0))
        preco_venda = float(request.form.get('preco_venda', 0))

        if not nome:
            flash('Nome do produto e obrigatorio.', 'warning')
            return redirect(url_for('admin.produto_novo'))

        produto = Produto(
            barbearia_id=barbearia.id,
            nome=nome,
            unidade=unidade,
            estoque_atual=estoque_atual,
            estoque_minimo=estoque_minimo,
            preco_custo=preco_custo,
            preco_venda=preco_venda
        )
        db.session.add(produto)
        db.session.flush()

        # Registrar entrada inicial se houver estoque
        if estoque_atual > 0:
            mov = MovimentacaoEstoque(
                produto_id=produto.id,
                tipo='entrada',
                quantidade=estoque_atual,
                motivo='Estoque inicial'
            )
            db.session.add(mov)

        db.session.commit()
        flash(f'Produto {nome} cadastrado!', 'success')
        return redirect(url_for('admin.produtos'))

    return render_template('admin/produto_form.html', barbearia=barbearia, produto=None)


@admin_bp.route('/produtos/<int:produto_id>/editar', methods=['GET', 'POST'])
@login_required
def produto_editar(produto_id):
    barbearia = get_barbearia()
    produto = Produto.query.filter_by(
        id=produto_id, barbearia_id=barbearia.id
    ).first_or_404()

    if request.method == 'POST':
        produto.nome = request.form.get('nome', produto.nome).strip()
        produto.unidade = request.form.get('unidade', produto.unidade)
        produto.estoque_minimo = float(request.form.get('estoque_minimo', produto.estoque_minimo))
        produto.preco_custo = float(request.form.get('preco_custo', produto.preco_custo))
        produto.preco_venda = float(request.form.get('preco_venda', produto.preco_venda))
        produto.ativo = request.form.get('ativo') == 'on'
        db.session.commit()
        flash(f'Produto {produto.nome} atualizado!', 'success')
        return redirect(url_for('admin.produtos'))

    movimentacoes = MovimentacaoEstoque.query.filter_by(
        produto_id=produto.id
    ).order_by(MovimentacaoEstoque.data.desc()).limit(50).all()

    return render_template(
        'admin/produto_form.html',
        barbearia=barbearia,
        produto=produto,
        movimentacoes=movimentacoes
    )


@admin_bp.route('/produtos/<int:produto_id>/movimentar', methods=['POST'])
@login_required
def produto_movimentar(produto_id):
    barbearia = get_barbearia()
    produto = Produto.query.filter_by(
        id=produto_id, barbearia_id=barbearia.id
    ).first_or_404()

    tipo = request.form.get('tipo', 'entrada')
    quantidade = float(request.form.get('quantidade', 0))
    motivo = request.form.get('motivo', '').strip()

    if quantidade <= 0:
        flash('Quantidade deve ser maior que zero.', 'warning')
        return redirect(url_for('admin.produto_editar', produto_id=produto.id))

    if tipo in ('saida', 'venda') and quantidade > produto.estoque_atual:
        flash('Quantidade maior que o estoque disponivel.', 'danger')
        return redirect(url_for('admin.produto_editar', produto_id=produto.id))

    mov = MovimentacaoEstoque(
        produto_id=produto.id,
        tipo=tipo,
        quantidade=quantidade,
        motivo=motivo
    )
    db.session.add(mov)

    if tipo == 'entrada':
        produto.estoque_atual += quantidade
    elif tipo in ('saida', 'venda'):
        produto.estoque_atual -= quantidade
    elif tipo == 'ajuste':
        produto.estoque_atual = quantidade

    db.session.commit()
    flash(f'Movimentacao registrada: {tipo} de {quantidade} {produto.unidade}.', 'success')
    return redirect(url_for('admin.produto_editar', produto_id=produto.id))


@admin_bp.route('/produtos/<int:produto_id>/excluir', methods=['POST'])
@login_required
def produto_excluir(produto_id):
    barbearia = get_barbearia()
    produto = Produto.query.filter_by(
        id=produto_id, barbearia_id=barbearia.id
    ).first_or_404()

    produto.ativo = False
    db.session.commit()
    flash(f'Produto {produto.nome} desativado.', 'info')
    return redirect(url_for('admin.produtos'))


# ===========================================================================
# 12. WHATSAPP SETTINGS
# ===========================================================================
@admin_bp.route('/whatsapp', methods=['GET', 'POST'])
@login_required
def whatsapp_settings():
    barbearia = get_barbearia()

    if request.method == 'POST':
        barbearia.wpp_confirmacao = request.form.get('wpp_confirmacao') == 'on'
        barbearia.wpp_lembrete_24h = request.form.get('wpp_lembrete_24h') == 'on'
        barbearia.wpp_lembrete_1h = request.form.get('wpp_lembrete_1h') == 'on'
        barbearia.wpp_pos_atendimento = request.form.get('wpp_pos_atendimento') == 'on'
        barbearia.wpp_aniversario = request.form.get('wpp_aniversario') == 'on'
        barbearia.wpp_reativacao = request.form.get('wpp_reativacao') == 'on'
        barbearia.wpp_avaliacao = request.form.get('wpp_avaliacao') == 'on'
        barbearia.wpp_dias_reativacao = int(request.form.get('wpp_dias_reativacao', 45))

        db.session.commit()
        flash('Configuracoes de WhatsApp salvas!', 'success')
        return redirect(url_for('admin.whatsapp_settings'))

    # Log de lembretes recentes
    lembretes_recentes = LembreteLog.query.join(Agendamento).filter(
        Agendamento.barbearia_id == barbearia.id
    ).order_by(LembreteLog.enviado_em.desc()).limit(50).all()

    return render_template(
        'admin/whatsapp.html',
        barbearia=barbearia,
        lembretes=lembretes_recentes
    )


# ===========================================================================
# 13. PLANOS DE ASSINATURA
# ===========================================================================
@admin_bp.route('/planos')
@login_required
def planos():
    barbearia = get_barbearia()
    lista = PlanoAssinatura.query.filter_by(barbearia_id=barbearia.id).order_by(PlanoAssinatura.nome).all()

    # Contar assinantes por plano
    contagem = {}
    for plano in lista:
        contagem[plano.id] = AssinaturaCliente.query.filter_by(
            plano_id=plano.id, status='ativo'
        ).count()

    return render_template(
        'admin/planos.html',
        barbearia=barbearia,
        planos=lista,
        contagem_assinantes=contagem
    )


@admin_bp.route('/planos/novo', methods=['GET', 'POST'])
@login_required
def plano_novo():
    barbearia = get_barbearia()

    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        preco = float(request.form.get('preco_mensal', 0))
        descricao = request.form.get('descricao', '').strip()
        creditos_expiram = request.form.get('creditos_expiram') == 'on'

        # Montar creditos mensais a partir dos campos do formulario
        servicos = Servico.query.filter_by(barbearia_id=barbearia.id, ativo=True).all()
        creditos = {}
        for s in servicos:
            qtd = request.form.get(f'creditos_{s.id}', '0')
            if int(qtd) > 0:
                creditos[s.nome] = int(qtd)

        if not nome:
            flash('Nome do plano e obrigatorio.', 'warning')
            return redirect(url_for('admin.plano_novo'))

        plano = PlanoAssinatura(
            barbearia_id=barbearia.id,
            nome=nome,
            preco_mensal=preco,
            descricao=descricao,
            creditos_mensais=json.dumps(creditos),
            creditos_expiram=creditos_expiram
        )
        db.session.add(plano)
        db.session.commit()
        flash(f'Plano {nome} criado!', 'success')
        return redirect(url_for('admin.planos'))

    servicos = Servico.query.filter_by(barbearia_id=barbearia.id, ativo=True).all()
    return render_template('admin/plano_form.html', barbearia=barbearia, plano=None, servicos=servicos)


@admin_bp.route('/planos/<int:plano_id>/editar', methods=['GET', 'POST'])
@login_required
def plano_editar(plano_id):
    barbearia = get_barbearia()
    plano = PlanoAssinatura.query.filter_by(
        id=plano_id, barbearia_id=barbearia.id
    ).first_or_404()

    if request.method == 'POST':
        plano.nome = request.form.get('nome', plano.nome).strip()
        plano.preco_mensal = float(request.form.get('preco_mensal', plano.preco_mensal))
        plano.descricao = request.form.get('descricao', '').strip()
        plano.creditos_expiram = request.form.get('creditos_expiram') == 'on'
        plano.ativo = request.form.get('ativo') == 'on'

        servicos = Servico.query.filter_by(barbearia_id=barbearia.id, ativo=True).all()
        creditos = {}
        for s in servicos:
            qtd = request.form.get(f'creditos_{s.id}', '0')
            if int(qtd) > 0:
                creditos[s.nome] = int(qtd)
        plano.creditos_mensais = json.dumps(creditos)

        db.session.commit()
        flash(f'Plano {plano.nome} atualizado!', 'success')
        return redirect(url_for('admin.planos'))

    servicos = Servico.query.filter_by(barbearia_id=barbearia.id, ativo=True).all()
    assinantes = AssinaturaCliente.query.filter_by(plano_id=plano.id).all()

    return render_template(
        'admin/plano_form.html',
        barbearia=barbearia,
        plano=plano,
        servicos=servicos,
        assinantes=assinantes
    )


@admin_bp.route('/planos/<int:plano_id>/excluir', methods=['POST'])
@login_required
def plano_excluir(plano_id):
    barbearia = get_barbearia()
    plano = PlanoAssinatura.query.filter_by(
        id=plano_id, barbearia_id=barbearia.id
    ).first_or_404()

    # Verificar assinantes ativos
    ativos = AssinaturaCliente.query.filter_by(plano_id=plano.id, status='ativo').count()
    if ativos > 0:
        flash(f'Nao e possivel desativar: {ativos} assinantes ativos.', 'danger')
        return redirect(url_for('admin.planos'))

    plano.ativo = False
    db.session.commit()
    flash(f'Plano {plano.nome} desativado.', 'info')
    return redirect(url_for('admin.planos'))


# ===========================================================================
# 14. FINANCEIRO (Despesas + DRE Simplificado)
# ===========================================================================
@admin_bp.route('/financeiro')
@login_required
def financeiro():
    barbearia = get_barbearia()
    mes = request.args.get('mes', date.today().month, type=int)
    ano = request.args.get('ano', date.today().year, type=int)

    # Despesas do mes
    despesas = Despesa.query.filter(
        Despesa.barbearia_id == barbearia.id,
        extract('month', Despesa.data_vencimento) == mes,
        extract('year', Despesa.data_vencimento) == ano
    ).order_by(Despesa.data_vencimento).all()

    total_despesas = sum(d.valor for d in despesas)
    despesas_pagas = sum(d.valor for d in despesas if d.pago)
    despesas_pendentes = sum(d.valor for d in despesas if not d.pago)

    # Despesas por categoria
    por_categoria = {}
    for d in despesas:
        cat = d.categoria or 'outros'
        por_categoria.setdefault(cat, 0)
        por_categoria[cat] += d.valor

    # Receita do mes (faturamento)
    receita_mes = db.session.query(func.sum(Pagamento.valor_pago)).join(
        Agendamento
    ).filter(
        Agendamento.barbearia_id == barbearia.id,
        extract('month', Agendamento.data) == mes,
        extract('year', Agendamento.data) == ano,
        Agendamento.status == 'concluido'
    ).scalar() or 0

    # Comissoes do mes
    comissoes_mes = db.session.query(func.sum(Comissao.valor_comissao)).join(
        Pagamento
    ).join(Agendamento).filter(
        Agendamento.barbearia_id == barbearia.id,
        extract('month', Agendamento.data) == mes,
        extract('year', Agendamento.data) == ano
    ).scalar() or 0

    # DRE simplificado
    lucro_bruto = receita_mes - comissoes_mes
    lucro_liquido = lucro_bruto - total_despesas

    return render_template(
        'admin/financeiro.html',
        barbearia=barbearia,
        mes=mes,
        ano=ano,
        despesas=despesas,
        total_despesas=formatar_moeda(total_despesas),
        despesas_pagas=formatar_moeda(despesas_pagas),
        despesas_pendentes=formatar_moeda(despesas_pendentes),
        por_categoria=por_categoria,
        receita_mes=formatar_moeda(receita_mes),
        receita_mes_raw=receita_mes,
        comissoes_mes=formatar_moeda(comissoes_mes),
        comissoes_mes_raw=comissoes_mes,
        lucro_bruto=formatar_moeda(lucro_bruto),
        lucro_liquido=formatar_moeda(lucro_liquido),
        lucro_liquido_raw=lucro_liquido
    )


@admin_bp.route('/financeiro/despesas/nova', methods=['GET', 'POST'])
@login_required
def despesa_nova():
    barbearia = get_barbearia()

    if request.method == 'POST':
        descricao = request.form.get('descricao', '').strip()
        categoria = request.form.get('categoria', 'outros')
        valor = float(request.form.get('valor', 0))
        tipo = request.form.get('tipo', 'variavel')
        data_venc = request.form.get('data_vencimento', '')
        pago = request.form.get('pago') == 'on'

        if not descricao:
            flash('Descricao e obrigatoria.', 'warning')
            return redirect(url_for('admin.despesa_nova'))

        despesa = Despesa(
            barbearia_id=barbearia.id,
            descricao=descricao,
            categoria=categoria,
            valor=valor,
            tipo=tipo,
            pago=pago
        )

        if data_venc:
            despesa.data_vencimento = date.fromisoformat(data_venc)

        db.session.add(despesa)
        db.session.commit()
        flash(f'Despesa "{descricao}" registrada!', 'success')
        return redirect(url_for('admin.financeiro'))

    return render_template('admin/despesa_form.html', barbearia=barbearia, despesa=None)


@admin_bp.route('/financeiro/despesas/<int:despesa_id>/editar', methods=['GET', 'POST'])
@login_required
def despesa_editar(despesa_id):
    barbearia = get_barbearia()
    despesa = Despesa.query.filter_by(
        id=despesa_id, barbearia_id=barbearia.id
    ).first_or_404()

    if request.method == 'POST':
        despesa.descricao = request.form.get('descricao', despesa.descricao).strip()
        despesa.categoria = request.form.get('categoria', despesa.categoria)
        despesa.valor = float(request.form.get('valor', despesa.valor))
        despesa.tipo = request.form.get('tipo', despesa.tipo)
        despesa.pago = request.form.get('pago') == 'on'

        data_venc = request.form.get('data_vencimento', '')
        if data_venc:
            despesa.data_vencimento = date.fromisoformat(data_venc)

        db.session.commit()
        flash(f'Despesa "{despesa.descricao}" atualizada!', 'success')
        return redirect(url_for('admin.financeiro'))

    return render_template('admin/despesa_form.html', barbearia=barbearia, despesa=despesa)


@admin_bp.route('/financeiro/despesas/<int:despesa_id>/pagar', methods=['POST'])
@login_required
def despesa_pagar(despesa_id):
    barbearia = get_barbearia()
    despesa = Despesa.query.filter_by(
        id=despesa_id, barbearia_id=barbearia.id
    ).first_or_404()

    despesa.pago = True
    db.session.commit()
    flash(f'Despesa "{despesa.descricao}" marcada como paga.', 'success')
    return redirect(url_for('admin.financeiro'))


@admin_bp.route('/financeiro/despesas/<int:despesa_id>/excluir', methods=['POST'])
@login_required
def despesa_excluir(despesa_id):
    barbearia = get_barbearia()
    despesa = Despesa.query.filter_by(
        id=despesa_id, barbearia_id=barbearia.id
    ).first_or_404()

    db.session.delete(despesa)
    db.session.commit()
    flash('Despesa removida.', 'info')
    return redirect(url_for('admin.financeiro'))


# ===========================================================================
# 15. CONFIGURACOES
# ===========================================================================
@admin_bp.route('/configuracoes', methods=['GET', 'POST'])
@login_required
def configuracoes():
    barbearia = get_barbearia()

    if request.method == 'POST':
        action = request.form.get('action', 'geral')

        if action == 'geral':
            barbearia.nome = request.form.get('nome', barbearia.nome).strip()
            barbearia.slug = slugify(barbearia.nome)
            barbearia.telefone = request.form.get('telefone', barbearia.telefone)
            barbearia.endereco = request.form.get('endereco', barbearia.endereco)
            barbearia.email = request.form.get('email', barbearia.email).strip().lower()
            barbearia.cancelamento_horas_minimo = int(request.form.get('cancelamento_horas_minimo', 2))

        elif action == 'horarios':
            barbearia.horario_abertura = request.form.get('horario_abertura', barbearia.horario_abertura)
            barbearia.horario_fechamento = request.form.get('horario_fechamento', barbearia.horario_fechamento)
            dias = request.form.getlist('dias_funcionamento')
            barbearia.set_dias_funcionamento([int(d) for d in dias])

        elif action == 'senha':
            senha_atual = request.form.get('senha_atual', '')
            nova_senha = request.form.get('nova_senha', '')
            confirmar_senha = request.form.get('confirmar_senha', '')

            if not barbearia.check_senha(senha_atual):
                flash('Senha atual incorreta.', 'danger')
                return redirect(url_for('admin.configuracoes'))

            if nova_senha != confirmar_senha:
                flash('A nova senha e a confirmacao nao coincidem.', 'danger')
                return redirect(url_for('admin.configuracoes'))

            if len(nova_senha) < 6:
                flash('A nova senha deve ter pelo menos 6 caracteres.', 'warning')
                return redirect(url_for('admin.configuracoes'))

            barbearia.set_senha(nova_senha)

        db.session.commit()
        flash('Configuracoes salvas!', 'success')
        return redirect(url_for('admin.configuracoes'))

    return render_template('admin/configuracoes.html', barbearia=barbearia)


# ===========================================================================
# 16. RELATORIOS
# ===========================================================================
@admin_bp.route('/relatorios')
@login_required
def relatorios():
    barbearia = get_barbearia()
    return render_template('admin/relatorios.html', barbearia=barbearia)


@admin_bp.route('/relatorios/faturamento')
@login_required
def relatorio_faturamento():
    barbearia = get_barbearia()
    data_inicio_str = request.args.get('data_inicio', (date.today() - timedelta(days=30)).isoformat())
    data_fim_str = request.args.get('data_fim', date.today().isoformat())

    try:
        data_inicio = date.fromisoformat(data_inicio_str)
        data_fim = date.fromisoformat(data_fim_str)
    except ValueError:
        data_inicio = date.today() - timedelta(days=30)
        data_fim = date.today()

    # Faturamento diario
    pagamentos = Pagamento.query.join(Agendamento).filter(
        Agendamento.barbearia_id == barbearia.id,
        Agendamento.data >= data_inicio,
        Agendamento.data <= data_fim,
        Agendamento.status == 'concluido'
    ).all()

    faturamento_diario = {}
    faturamento_por_forma = {}
    faturamento_por_barbeiro = {}
    faturamento_por_servico = {}
    total = 0

    for p in pagamentos:
        ag = p.agendamento
        dia = ag.data.isoformat()

        # Por dia
        faturamento_diario.setdefault(dia, 0)
        faturamento_diario[dia] += p.valor_pago

        # Por forma de pagamento
        faturamento_por_forma.setdefault(p.forma, 0)
        faturamento_por_forma[p.forma] += p.valor_pago
        if p.forma2 and p.valor2:
            faturamento_por_forma.setdefault(p.forma2, 0)
            faturamento_por_forma[p.forma2] += p.valor2

        # Por barbeiro
        barbeiro_nome = ag.barbeiro.nome
        faturamento_por_barbeiro.setdefault(barbeiro_nome, 0)
        faturamento_por_barbeiro[barbeiro_nome] += p.valor_pago

        # Por servico
        servico_nome = ag.servico.nome
        faturamento_por_servico.setdefault(servico_nome, {'qtd': 0, 'valor': 0})
        faturamento_por_servico[servico_nome]['qtd'] += 1
        faturamento_por_servico[servico_nome]['valor'] += p.valor_pago

        total += p.valor_pago + (p.valor2 or 0)

    return render_template(
        'admin/relatorio_faturamento.html',
        barbearia=barbearia,
        data_inicio=data_inicio,
        data_fim=data_fim,
        faturamento_diario=faturamento_diario,
        faturamento_por_forma=faturamento_por_forma,
        faturamento_por_barbeiro=faturamento_por_barbeiro,
        faturamento_por_servico=faturamento_por_servico,
        total=formatar_moeda(total),
        total_raw=total
    )


@admin_bp.route('/relatorios/barbeiros')
@login_required
def relatorio_barbeiros():
    barbearia = get_barbearia()
    mes = request.args.get('mes', date.today().month, type=int)
    ano = request.args.get('ano', date.today().year, type=int)

    barbeiros = Barbeiro.query.filter_by(barbearia_id=barbearia.id, ativo=True).all()
    dados = []

    for barbeiro in barbeiros:
        agendamentos = Agendamento.query.filter(
            Agendamento.barbeiro_id == barbeiro.id,
            extract('month', Agendamento.data) == mes,
            extract('year', Agendamento.data) == ano
        ).all()

        total_atendimentos = sum(1 for a in agendamentos if a.status == 'concluido')
        cancelados = sum(1 for a in agendamentos if a.status == 'cancelado')

        faturamento = sum(
            a.pagamento.valor_pago for a in agendamentos
            if a.status == 'concluido' and a.pagamento
        )

        comissao_total = sum(
            a.pagamento.comissao.valor_comissao for a in agendamentos
            if a.status == 'concluido' and a.pagamento and a.pagamento.comissao
        )

        # Nota media
        notas = [a.avaliacao.nota for a in agendamentos if a.avaliacao]
        nota_media = sum(notas) / len(notas) if notas else 0

        # Meta
        meta = MetaBarbeiro.query.filter_by(
            barbeiro_id=barbeiro.id, mes=mes, ano=ano
        ).first()

        dados.append({
            'barbeiro': barbeiro,
            'atendimentos': total_atendimentos,
            'cancelados': cancelados,
            'faturamento': formatar_moeda(faturamento),
            'faturamento_raw': faturamento,
            'comissao': formatar_moeda(comissao_total),
            'nota_media': round(nota_media, 1),
            'meta': meta
        })

    return render_template(
        'admin/relatorio_barbeiros.html',
        barbearia=barbearia,
        dados=dados,
        mes=mes,
        ano=ano
    )


@admin_bp.route('/relatorios/clientes')
@login_required
def relatorio_clientes():
    barbearia = get_barbearia()
    mes = request.args.get('mes', date.today().month, type=int)
    ano = request.args.get('ano', date.today().year, type=int)

    # Novos clientes no periodo
    novos = Cliente.query.filter(
        Cliente.barbearia_id == barbearia.id,
        extract('month', Cliente.data_cadastro) == mes,
        extract('year', Cliente.data_cadastro) == ano
    ).count()

    # Total de clientes
    total_clientes = Cliente.query.filter_by(barbearia_id=barbearia.id).count()

    # Clientes ativos (com agendamento no mes)
    clientes_ativos = db.session.query(func.count(func.distinct(Agendamento.cliente_id))).filter(
        Agendamento.barbearia_id == barbearia.id,
        extract('month', Agendamento.data) == mes,
        extract('year', Agendamento.data) == ano,
        Agendamento.status.in_(['agendado', 'confirmado', 'concluido'])
    ).scalar() or 0

    # Top clientes por gasto
    top_clientes_query = db.session.query(
        Cliente.id,
        Cliente.nome,
        Cliente.telefone,
        func.count(Agendamento.id).label('total_visitas'),
        func.sum(Pagamento.valor_pago).label('total_gasto')
    ).join(Agendamento, Agendamento.cliente_id == Cliente.id).join(
        Pagamento, Pagamento.agendamento_id == Agendamento.id
    ).filter(
        Cliente.barbearia_id == barbearia.id,
        Agendamento.status == 'concluido',
        extract('month', Agendamento.data) == mes,
        extract('year', Agendamento.data) == ano
    ).group_by(Cliente.id, Cliente.nome, Cliente.telefone).order_by(
        func.sum(Pagamento.valor_pago).desc()
    ).limit(20).all()

    top_clientes = [
        {
            'id': c.id,
            'nome': c.nome,
            'telefone': c.telefone,
            'visitas': c.total_visitas,
            'gasto': formatar_moeda(c.total_gasto or 0)
        }
        for c in top_clientes_query
    ]

    return render_template(
        'admin/relatorio_clientes.html',
        barbearia=barbearia,
        mes=mes,
        ano=ano,
        novos=novos,
        total_clientes=total_clientes,
        clientes_ativos=clientes_ativos,
        top_clientes=top_clientes
    )


@admin_bp.route('/relatorios/exportar-pdf')
@login_required
def exportar_pdf():
    """Gera relatorio em PDF para download."""
    barbearia = get_barbearia()
    tipo = request.args.get('tipo', 'faturamento')
    data_inicio_str = request.args.get('data_inicio', (date.today() - timedelta(days=30)).isoformat())
    data_fim_str = request.args.get('data_fim', date.today().isoformat())

    try:
        data_inicio = date.fromisoformat(data_inicio_str)
        data_fim = date.fromisoformat(data_fim_str)
    except ValueError:
        data_inicio = date.today() - timedelta(days=30)
        data_fim = date.today()

    # Coletar dados para o PDF
    pagamentos = Pagamento.query.join(Agendamento).filter(
        Agendamento.barbearia_id == barbearia.id,
        Agendamento.data >= data_inicio,
        Agendamento.data <= data_fim,
        Agendamento.status == 'concluido'
    ).all()

    total_faturamento = sum(p.valor_pago + (p.valor2 or 0) for p in pagamentos)
    total_atendimentos = len(pagamentos)

    # Comissoes do periodo
    comissoes = Comissao.query.join(Pagamento).join(Agendamento).filter(
        Agendamento.barbearia_id == barbearia.id,
        Agendamento.data >= data_inicio,
        Agendamento.data <= data_fim
    ).all()
    total_comissoes = sum(c.valor_comissao for c in comissoes)

    # Despesas do periodo
    despesas = Despesa.query.filter(
        Despesa.barbearia_id == barbearia.id,
        Despesa.data_vencimento >= data_inicio,
        Despesa.data_vencimento <= data_fim
    ).all()
    total_despesas = sum(d.valor for d in despesas)

    lucro_liquido = total_faturamento - total_comissoes - total_despesas

    # Faturamento por forma
    por_forma = {}
    for p in pagamentos:
        por_forma.setdefault(p.forma, 0)
        por_forma[p.forma] += p.valor_pago
        if p.forma2 and p.valor2:
            por_forma.setdefault(p.forma2, 0)
            por_forma[p.forma2] += p.valor2

    # Faturamento por barbeiro
    por_barbeiro = {}
    for p in pagamentos:
        nome = p.agendamento.barbeiro.nome
        por_barbeiro.setdefault(nome, {'atendimentos': 0, 'valor': 0})
        por_barbeiro[nome]['atendimentos'] += 1
        por_barbeiro[nome]['valor'] += p.valor_pago

    return render_template(
        'admin/relatorio_pdf.html',
        barbearia=barbearia,
        tipo=tipo,
        data_inicio=data_inicio,
        data_fim=data_fim,
        total_faturamento=formatar_moeda(total_faturamento),
        total_atendimentos=total_atendimentos,
        total_comissoes=formatar_moeda(total_comissoes),
        total_despesas=formatar_moeda(total_despesas),
        lucro_liquido=formatar_moeda(lucro_liquido),
        por_forma={k: formatar_moeda(v) for k, v in por_forma.items()},
        por_barbeiro=por_barbeiro,
        despesas=despesas
    )


# ===========================================================================
# METAS DE BARBEIROS
# ===========================================================================
@admin_bp.route('/barbeiros/<int:barbeiro_id>/metas', methods=['GET', 'POST'])
@login_required
def barbeiro_metas(barbeiro_id):
    barbearia = get_barbearia()
    barbeiro = Barbeiro.query.filter_by(
        id=barbeiro_id, barbearia_id=barbearia.id
    ).first_or_404()

    mes = request.args.get('mes', date.today().month, type=int)
    ano = request.args.get('ano', date.today().year, type=int)

    if request.method == 'POST':
        mes_form = int(request.form.get('mes', mes))
        ano_form = int(request.form.get('ano', ano))

        meta = MetaBarbeiro.query.filter_by(
            barbeiro_id=barbeiro.id, mes=mes_form, ano=ano_form
        ).first()

        if not meta:
            meta = MetaBarbeiro(
                barbeiro_id=barbeiro.id,
                mes=mes_form,
                ano=ano_form
            )
            db.session.add(meta)

        meta.meta_atendimentos = int(request.form.get('meta_atendimentos', 0))
        meta.meta_faturamento = float(request.form.get('meta_faturamento', 0))
        meta.meta_nota_minima = float(request.form.get('meta_nota_minima', 4.0))
        meta.bonus_tipo = request.form.get('bonus_tipo', 'fixo')
        meta.bonus_valor = float(request.form.get('bonus_valor', 0))

        db.session.commit()
        flash(f'Meta de {barbeiro.nome} para {mes_form}/{ano_form} salva!', 'success')
        return redirect(url_for('admin.barbeiro_metas', barbeiro_id=barbeiro.id, mes=mes_form, ano=ano_form))

    meta = MetaBarbeiro.query.filter_by(
        barbeiro_id=barbeiro.id, mes=mes, ano=ano
    ).first()

    # Progresso atual
    atendimentos_realizados = Agendamento.query.filter(
        Agendamento.barbeiro_id == barbeiro.id,
        extract('month', Agendamento.data) == mes,
        extract('year', Agendamento.data) == ano,
        Agendamento.status == 'concluido'
    ).count()

    faturamento_atual = db.session.query(func.sum(Pagamento.valor_pago)).join(
        Agendamento
    ).filter(
        Agendamento.barbeiro_id == barbeiro.id,
        extract('month', Agendamento.data) == mes,
        extract('year', Agendamento.data) == ano,
        Agendamento.status == 'concluido'
    ).scalar() or 0

    return render_template(
        'admin/barbeiro_metas.html',
        barbearia=barbearia,
        barbeiro=barbeiro,
        meta=meta,
        mes=mes,
        ano=ano,
        atendimentos_realizados=atendimentos_realizados,
        faturamento_atual=formatar_moeda(faturamento_atual),
        faturamento_atual_raw=faturamento_atual
    )
