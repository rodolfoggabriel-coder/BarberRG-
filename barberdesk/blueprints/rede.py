"""
Blueprint para painel da rede/franquia (plano 'rede').
Permite ao franqueador gerenciar todas as unidades, servicos padrao,
royalties e relatorios consolidados.
"""

from functools import wraps
from datetime import datetime, date, timedelta
from calendar import monthrange

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, session, jsonify, abort
)
from sqlalchemy import func, case

from models import (
    db, Grupo, Barbearia, Barbeiro, Agendamento, Pagamento, Comissao, Servico
)
from utils import formatar_moeda

rede_bp = Blueprint('rede', __name__, url_prefix='/rede')


# ---------------------------------------------------------------------------
# Decorator de autenticacao do franqueador
# ---------------------------------------------------------------------------

def franqueador_required(f):
    """Exige que o franqueador esteja logado (grupo_id na sessao)."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        grupo_id = session.get('grupo_id')
        if not grupo_id:
            flash('Faca login como franqueador para acessar.', 'warning')
            return redirect(url_for('rede.login'))
        grupo = Grupo.query.get(grupo_id)
        if not grupo or not grupo.ativo:
            session.pop('grupo_id', None)
            flash('Conta de rede nao encontrada ou inativa.', 'danger')
            return redirect(url_for('rede.login'))
        return f(grupo, *args, **kwargs)
    return decorated_function


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _periodo_mes(ano, mes):
    """Retorna (data_inicio, data_fim) do mes informado."""
    primeiro = date(ano, mes, 1)
    ultimo = date(ano, mes, monthrange(ano, mes)[1])
    return primeiro, ultimo


def _receita_unidade(barbearia_id, dt_inicio, dt_fim):
    """Soma de pagamentos concluidos de uma unidade no periodo."""
    total = (
        db.session.query(func.coalesce(func.sum(Pagamento.valor_pago), 0))
        .join(Agendamento, Agendamento.id == Pagamento.agendamento_id)
        .filter(
            Agendamento.barbearia_id == barbearia_id,
            Agendamento.status == 'concluido',
            Agendamento.data >= dt_inicio,
            Agendamento.data <= dt_fim,
        )
        .scalar()
    )
    return float(total)


def _agendamentos_unidade(barbearia_id, dt_inicio, dt_fim):
    """Contadores de agendamentos de uma unidade no periodo."""
    totais = (
        db.session.query(
            func.count(Agendamento.id).label('total'),
            func.sum(case((Agendamento.status == 'concluido', 1), else_=0)).label('concluidos'),
            func.sum(case((Agendamento.status == 'cancelado', 1), else_=0)).label('cancelados'),
        )
        .filter(
            Agendamento.barbearia_id == barbearia_id,
            Agendamento.data >= dt_inicio,
            Agendamento.data <= dt_fim,
        )
        .first()
    )
    return {
        'total': totais.total or 0,
        'concluidos': int(totais.concluidos or 0),
        'cancelados': int(totais.cancelados or 0),
    }


def _ocupacao_unidade(barbearia_id, dt_inicio, dt_fim):
    """Taxa de ocupacao aproximada (concluidos / total) em percentual."""
    nums = _agendamentos_unidade(barbearia_id, dt_inicio, dt_fim)
    if nums['total'] == 0:
        return 0.0
    return round((nums['concluidos'] / nums['total']) * 100, 1)


def _ticket_medio_unidade(barbearia_id, dt_inicio, dt_fim):
    """Ticket medio de pagamentos concluidos."""
    resultado = (
        db.session.query(
            func.coalesce(func.avg(Pagamento.valor_pago), 0)
        )
        .join(Agendamento, Agendamento.id == Pagamento.agendamento_id)
        .filter(
            Agendamento.barbearia_id == barbearia_id,
            Agendamento.status == 'concluido',
            Agendamento.data >= dt_inicio,
            Agendamento.data <= dt_fim,
        )
        .scalar()
    )
    return float(resultado)


def _alertas_unidade(barbearia):
    """Retorna lista de alertas relevantes para a unidade."""
    alertas = []
    barbeiros_ativos = Barbeiro.query.filter_by(
        barbearia_id=barbearia.id, ativo=True
    ).count()
    if barbeiros_ativos == 0:
        alertas.append('Nenhum barbeiro ativo cadastrado')

    servicos_ativos = Servico.query.filter_by(
        barbearia_id=barbearia.id, ativo=True
    ).count()
    if servicos_ativos == 0:
        alertas.append('Nenhum servico ativo cadastrado')

    if not barbearia.setup_completo:
        alertas.append('Setup inicial incompleto')

    return alertas


# ---------------------------------------------------------------------------
# Rotas de autenticacao
# ---------------------------------------------------------------------------

@rede_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Login do franqueador com email + senha."""
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        senha = request.form.get('senha', '')

        grupo = Grupo.query.filter_by(email_franqueador=email).first()
        if grupo and grupo.check_senha(senha):
            if not grupo.ativo:
                flash('Conta de rede desativada. Entre em contato com o suporte.', 'danger')
                return redirect(url_for('rede.login'))
            session['grupo_id'] = grupo.id
            flash(f'Bem-vindo, {grupo.nome}!', 'success')
            return redirect(url_for('rede.dashboard'))
        flash('Email ou senha invalidos.', 'danger')
    return render_template('rede/login.html')


@rede_bp.route('/logout')
def logout():
    """Encerra sessao do franqueador."""
    session.pop('grupo_id', None)
    flash('Voce saiu do painel da rede.', 'info')
    return redirect(url_for('rede.login'))


# ---------------------------------------------------------------------------
# Dashboard consolidado
# ---------------------------------------------------------------------------

@rede_bp.route('/dashboard')
@franqueador_required
def dashboard(grupo):
    """Painel com cards de todas as unidades: receita, agendamentos, ocupacao, alertas."""
    hoje = date.today()
    ano = int(request.args.get('ano', hoje.year))
    mes = int(request.args.get('mes', hoje.month))
    dt_inicio, dt_fim = _periodo_mes(ano, mes)

    unidades = Barbearia.query.filter_by(grupo_id=grupo.id, ativo=True).all()

    cards = []
    receita_total = 0.0
    for u in unidades:
        receita = _receita_unidade(u.id, dt_inicio, dt_fim)
        receita_total += receita
        nums = _agendamentos_unidade(u.id, dt_inicio, dt_fim)
        ocupacao = _ocupacao_unidade(u.id, dt_inicio, dt_fim)
        alertas = _alertas_unidade(u)
        cards.append({
            'barbearia': u,
            'receita': receita,
            'receita_fmt': formatar_moeda(receita),
            'agendamentos': nums,
            'ocupacao': ocupacao,
            'alertas': alertas,
        })

    return render_template(
        'rede/dashboard.html',
        grupo=grupo,
        cards=cards,
        receita_total=receita_total,
        receita_total_fmt=formatar_moeda(receita_total),
        ano=ano,
        mes=mes,
        total_unidades=len(unidades),
    )


# ---------------------------------------------------------------------------
# Acessar painel de uma unidade especifica
# ---------------------------------------------------------------------------

@rede_bp.route('/unidade/<int:barbearia_id>/entrar')
@franqueador_required
def entrar_unidade(grupo, barbearia_id):
    """Permite ao franqueador entrar no painel de uma unidade como se fosse o dono."""
    barbearia = Barbearia.query.get_or_404(barbearia_id)
    if barbearia.grupo_id != grupo.id:
        abort(403)

    # Salva referencia de que veio do painel da rede
    session['rede_acesso_unidade'] = True
    session['barbearia_id'] = barbearia.id
    flash(f'Voce esta acessando a unidade {barbearia.nome} como franqueador.', 'info')
    return redirect(url_for('rede.ver_unidade', barbearia_id=barbearia.id))


@rede_bp.route('/unidade/<int:barbearia_id>')
@franqueador_required
def ver_unidade(grupo, barbearia_id):
    """Exibe detalhes de uma unidade especifica para o franqueador."""
    barbearia = Barbearia.query.get_or_404(barbearia_id)
    if barbearia.grupo_id != grupo.id:
        abort(403)

    hoje = date.today()
    ano = int(request.args.get('ano', hoje.year))
    mes = int(request.args.get('mes', hoje.month))
    dt_inicio, dt_fim = _periodo_mes(ano, mes)

    receita = _receita_unidade(barbearia.id, dt_inicio, dt_fim)
    nums = _agendamentos_unidade(barbearia.id, dt_inicio, dt_fim)
    ocupacao = _ocupacao_unidade(barbearia.id, dt_inicio, dt_fim)
    ticket_medio = _ticket_medio_unidade(barbearia.id, dt_inicio, dt_fim)
    alertas = _alertas_unidade(barbearia)

    barbeiros = Barbeiro.query.filter_by(barbearia_id=barbearia.id, ativo=True).all()
    servicos = Servico.query.filter_by(barbearia_id=barbearia.id, ativo=True).all()

    return render_template(
        'rede/unidade_detalhe.html',
        grupo=grupo,
        barbearia=barbearia,
        receita=receita,
        receita_fmt=formatar_moeda(receita),
        agendamentos=nums,
        ocupacao=ocupacao,
        ticket_medio=ticket_medio,
        ticket_medio_fmt=formatar_moeda(ticket_medio),
        alertas=alertas,
        barbeiros=barbeiros,
        servicos=servicos,
        ano=ano,
        mes=mes,
    )


# ---------------------------------------------------------------------------
# Relatorios (ranking de unidades)
# ---------------------------------------------------------------------------

@rede_bp.route('/relatorios')
@franqueador_required
def relatorios(grupo):
    """Ranking de unidades por receita, ticket medio e ocupacao."""
    hoje = date.today()
    ano = int(request.args.get('ano', hoje.year))
    mes = int(request.args.get('mes', hoje.month))
    dt_inicio, dt_fim = _periodo_mes(ano, mes)

    unidades = Barbearia.query.filter_by(grupo_id=grupo.id, ativo=True).all()

    ranking = []
    for u in unidades:
        receita = _receita_unidade(u.id, dt_inicio, dt_fim)
        ticket = _ticket_medio_unidade(u.id, dt_inicio, dt_fim)
        ocupacao = _ocupacao_unidade(u.id, dt_inicio, dt_fim)
        nums = _agendamentos_unidade(u.id, dt_inicio, dt_fim)
        ranking.append({
            'barbearia': u,
            'receita': receita,
            'receita_fmt': formatar_moeda(receita),
            'ticket_medio': ticket,
            'ticket_medio_fmt': formatar_moeda(ticket),
            'ocupacao': ocupacao,
            'agendamentos': nums,
        })

    criterio = request.args.get('ordenar', 'receita')
    if criterio == 'ticket_medio':
        ranking.sort(key=lambda x: x['ticket_medio'], reverse=True)
    elif criterio == 'ocupacao':
        ranking.sort(key=lambda x: x['ocupacao'], reverse=True)
    else:
        ranking.sort(key=lambda x: x['receita'], reverse=True)

    receita_rede = sum(r['receita'] for r in ranking)

    return render_template(
        'rede/relatorios.html',
        grupo=grupo,
        ranking=ranking,
        receita_rede=receita_rede,
        receita_rede_fmt=formatar_moeda(receita_rede),
        ano=ano,
        mes=mes,
        criterio=criterio,
    )


# ---------------------------------------------------------------------------
# Catalogo padrao da rede (servicos herdados por todas as unidades)
# ---------------------------------------------------------------------------

@rede_bp.route('/catalogo', methods=['GET'])
@franqueador_required
def catalogo(grupo):
    """Lista servicos padrao da rede agrupados por unidade e catalogo geral."""
    unidades = Barbearia.query.filter_by(grupo_id=grupo.id, ativo=True).all()

    # Servicos que existem em TODAS as unidades (considerados "padrao da rede")
    # Coleta todos servicos das unidades para exibicao
    servicos_por_unidade = {}
    for u in unidades:
        servicos_por_unidade[u.id] = Servico.query.filter_by(
            barbearia_id=u.id, ativo=True
        ).order_by(Servico.nome).all()

    return render_template(
        'rede/catalogo.html',
        grupo=grupo,
        unidades=unidades,
        servicos_por_unidade=servicos_por_unidade,
    )


@rede_bp.route('/catalogo/propagar', methods=['POST'])
@franqueador_required
def catalogo_propagar(grupo):
    """Cria um servico padrao em todas as unidades da rede."""
    nome = request.form.get('nome', '').strip()
    preco = request.form.get('preco', type=float)
    duracao = request.form.get('duracao_minutos', type=int, default=30)

    if not nome or preco is None or preco < 0:
        flash('Preencha todos os campos corretamente.', 'danger')
        return redirect(url_for('rede.catalogo'))

    unidades = Barbearia.query.filter_by(grupo_id=grupo.id, ativo=True).all()
    criados = 0
    for u in unidades:
        existente = Servico.query.filter_by(
            barbearia_id=u.id, nome=nome
        ).first()
        if not existente:
            servico = Servico(
                barbearia_id=u.id,
                nome=nome,
                preco=preco,
                duracao_minutos=duracao,
                ativo=True,
            )
            db.session.add(servico)
            criados += 1
    db.session.commit()

    flash(f'Servico "{nome}" propagado para {criados} unidade(s).', 'success')
    return redirect(url_for('rede.catalogo'))


@rede_bp.route('/catalogo/remover', methods=['POST'])
@franqueador_required
def catalogo_remover(grupo):
    """Remove (desativa) um servico pelo nome em todas as unidades da rede."""
    nome = request.form.get('nome', '').strip()
    if not nome:
        flash('Nome do servico e obrigatorio.', 'danger')
        return redirect(url_for('rede.catalogo'))

    unidades = Barbearia.query.filter_by(grupo_id=grupo.id, ativo=True).all()
    removidos = 0
    for u in unidades:
        servicos = Servico.query.filter_by(
            barbearia_id=u.id, nome=nome, ativo=True
        ).all()
        for s in servicos:
            s.ativo = False
            removidos += 1
    db.session.commit()

    flash(f'Servico "{nome}" desativado em {removidos} unidade(s).', 'success')
    return redirect(url_for('rede.catalogo'))


# ---------------------------------------------------------------------------
# Controle de preco minimo
# ---------------------------------------------------------------------------

@rede_bp.route('/preco-minimo', methods=['GET', 'POST'])
@franqueador_required
def preco_minimo(grupo):
    """Define preco minimo para servicos. Exibe servicos abaixo do minimo definido."""
    unidades = Barbearia.query.filter_by(grupo_id=grupo.id, ativo=True).all()

    if request.method == 'POST':
        nome_servico = request.form.get('nome_servico', '').strip()
        valor_minimo = request.form.get('valor_minimo', type=float)

        if not nome_servico or valor_minimo is None or valor_minimo < 0:
            flash('Preencha todos os campos.', 'danger')
            return redirect(url_for('rede.preco_minimo'))

        ajustados = 0
        for u in unidades:
            servicos = Servico.query.filter_by(
                barbearia_id=u.id, nome=nome_servico, ativo=True
            ).all()
            for s in servicos:
                if s.preco < valor_minimo:
                    s.preco = valor_minimo
                    ajustados += 1
        db.session.commit()

        flash(
            f'Preco minimo de {formatar_moeda(valor_minimo)} aplicado a '
            f'"{nome_servico}". {ajustados} servico(s) ajustado(s).',
            'success',
        )
        return redirect(url_for('rede.preco_minimo'))

    # GET: listar todos servicos de todas unidades com precos
    servicos_rede = []
    for u in unidades:
        for s in Servico.query.filter_by(barbearia_id=u.id, ativo=True).all():
            servicos_rede.append({
                'barbearia': u.nome,
                'barbearia_id': u.id,
                'servico_id': s.id,
                'nome': s.nome,
                'preco': s.preco,
                'preco_fmt': formatar_moeda(s.preco),
                'duracao': s.duracao_minutos,
            })

    # Agrupa nomes unicos para select
    nomes_servicos = sorted(set(s['nome'] for s in servicos_rede))

    return render_template(
        'rede/preco_minimo.html',
        grupo=grupo,
        servicos_rede=servicos_rede,
        nomes_servicos=nomes_servicos,
    )


# ---------------------------------------------------------------------------
# Royalties
# ---------------------------------------------------------------------------

@rede_bp.route('/royalties', methods=['GET', 'POST'])
@franqueador_required
def royalties(grupo):
    """Configuracao de percentual de royalty e relatorio mensal de calculo."""
    if request.method == 'POST':
        novo_percentual = request.form.get('percentual_royalty', type=float)
        if novo_percentual is not None and 0 <= novo_percentual <= 100:
            grupo.percentual_royalty = novo_percentual
            db.session.commit()
            flash(
                f'Percentual de royalty atualizado para {novo_percentual}%.',
                'success',
            )
        else:
            flash('Percentual invalido. Informe um valor entre 0 e 100.', 'danger')
        return redirect(url_for('rede.royalties'))

    hoje = date.today()
    ano = int(request.args.get('ano', hoje.year))
    mes = int(request.args.get('mes', hoje.month))
    dt_inicio, dt_fim = _periodo_mes(ano, mes)

    unidades = Barbearia.query.filter_by(grupo_id=grupo.id, ativo=True).all()

    relatorio = []
    total_royalty = 0.0
    total_receita = 0.0
    for u in unidades:
        receita = _receita_unidade(u.id, dt_inicio, dt_fim)
        royalty = round(receita * (grupo.percentual_royalty / 100), 2)
        total_royalty += royalty
        total_receita += receita
        relatorio.append({
            'barbearia': u,
            'receita': receita,
            'receita_fmt': formatar_moeda(receita),
            'royalty': royalty,
            'royalty_fmt': formatar_moeda(royalty),
        })

    relatorio.sort(key=lambda x: x['receita'], reverse=True)

    return render_template(
        'rede/royalties.html',
        grupo=grupo,
        relatorio=relatorio,
        total_royalty=total_royalty,
        total_royalty_fmt=formatar_moeda(total_royalty),
        total_receita=total_receita,
        total_receita_fmt=formatar_moeda(total_receita),
        percentual=grupo.percentual_royalty,
        ano=ano,
        mes=mes,
    )


# ---------------------------------------------------------------------------
# Gestao de unidades (adicionar / remover)
# ---------------------------------------------------------------------------

@rede_bp.route('/unidades')
@franqueador_required
def listar_unidades(grupo):
    """Lista todas as unidades do grupo (ativas e inativas)."""
    unidades = Barbearia.query.filter_by(grupo_id=grupo.id).order_by(
        Barbearia.ativo.desc(), Barbearia.nome
    ).all()
    return render_template(
        'rede/unidades.html',
        grupo=grupo,
        unidades=unidades,
    )


@rede_bp.route('/unidades/adicionar', methods=['POST'])
@franqueador_required
def adicionar_unidade(grupo):
    """Vincula uma barbearia existente ao grupo pelo slug, ou cria nova."""
    slug = request.form.get('slug', '').strip().lower()
    if not slug:
        flash('Informe o slug da barbearia.', 'danger')
        return redirect(url_for('rede.listar_unidades'))

    barbearia = Barbearia.query.filter_by(slug=slug).first()
    if not barbearia:
        flash(f'Barbearia com slug "{slug}" nao encontrada.', 'danger')
        return redirect(url_for('rede.listar_unidades'))

    if barbearia.grupo_id and barbearia.grupo_id != grupo.id:
        flash('Esta barbearia ja pertence a outra rede.', 'danger')
        return redirect(url_for('rede.listar_unidades'))

    barbearia.grupo_id = grupo.id
    barbearia.plano = 'rede'
    barbearia.ativo = True
    db.session.commit()

    flash(f'Unidade "{barbearia.nome}" adicionada a rede.', 'success')
    return redirect(url_for('rede.listar_unidades'))


@rede_bp.route('/unidades/<int:barbearia_id>/remover', methods=['POST'])
@franqueador_required
def remover_unidade(grupo, barbearia_id):
    """Remove (desvincula) uma unidade do grupo."""
    barbearia = Barbearia.query.get_or_404(barbearia_id)
    if barbearia.grupo_id != grupo.id:
        abort(403)

    barbearia.grupo_id = None
    barbearia.plano = 'starter'
    db.session.commit()

    flash(f'Unidade "{barbearia.nome}" removida da rede.', 'success')
    return redirect(url_for('rede.listar_unidades'))


@rede_bp.route('/unidades/<int:barbearia_id>/desativar', methods=['POST'])
@franqueador_required
def desativar_unidade(grupo, barbearia_id):
    """Desativa uma unidade sem desvincular do grupo."""
    barbearia = Barbearia.query.get_or_404(barbearia_id)
    if barbearia.grupo_id != grupo.id:
        abort(403)

    barbearia.ativo = not barbearia.ativo
    db.session.commit()

    estado = 'ativada' if barbearia.ativo else 'desativada'
    flash(f'Unidade "{barbearia.nome}" {estado}.', 'success')
    return redirect(url_for('rede.listar_unidades'))
