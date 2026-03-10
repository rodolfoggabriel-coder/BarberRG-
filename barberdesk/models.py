from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date
import json

db = SQLAlchemy()


class Grupo(db.Model):
    """Rede de franquias"""
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False)
    email_franqueador = db.Column(db.String(120), nullable=False)
    senha_hash = db.Column(db.String(256), nullable=False)
    percentual_royalty = db.Column(db.Float, default=5.0)
    plano = db.Column(db.String(20), default='rede')  # starter/pro/rede
    ativo = db.Column(db.Boolean, default=True)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)

    barbearias = db.relationship('Barbearia', backref='grupo', lazy=True)

    def set_senha(self, senha):
        self.senha_hash = generate_password_hash(senha)

    def check_senha(self, senha):
        return check_password_hash(self.senha_hash, senha)


class Barbearia(db.Model):
    """Unidade central do sistema"""
    id = db.Column(db.Integer, primary_key=True)
    grupo_id = db.Column(db.Integer, db.ForeignKey('grupo.id'), nullable=True)
    nome = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False)
    telefone = db.Column(db.String(20))
    endereco = db.Column(db.String(200))
    logo = db.Column(db.String(200))
    email = db.Column(db.String(120), nullable=False)
    senha_hash = db.Column(db.String(256), nullable=False)
    horario_abertura = db.Column(db.String(5), default='08:00')
    horario_fechamento = db.Column(db.String(5), default='20:00')
    dias_funcionamento = db.Column(db.Text, default='[1,2,3,4,5,6]')  # JSON: 0=dom, 6=sab
    config_fidelidade = db.Column(db.Text, default='{"pontos_por_real": 1, "pontos_para_desconto": 100, "valor_desconto": 10, "validade_dias": 90}')
    cancelamento_horas_minimo = db.Column(db.Integer, default=2)
    plano = db.Column(db.String(20), default='starter')  # starter/pro/rede
    white_label = db.Column(db.Boolean, default=False)
    ativo = db.Column(db.Boolean, default=True)
    setup_completo = db.Column(db.Boolean, default=False)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)

    # WhatsApp config
    wpp_confirmacao = db.Column(db.Boolean, default=True)
    wpp_lembrete_24h = db.Column(db.Boolean, default=True)
    wpp_lembrete_1h = db.Column(db.Boolean, default=True)
    wpp_pos_atendimento = db.Column(db.Boolean, default=True)
    wpp_aniversario = db.Column(db.Boolean, default=True)
    wpp_reativacao = db.Column(db.Boolean, default=True)
    wpp_avaliacao = db.Column(db.Boolean, default=True)
    wpp_dias_reativacao = db.Column(db.Integer, default=45)

    barbeiros = db.relationship('Barbeiro', backref='barbearia', lazy=True)
    servicos = db.relationship('Servico', backref='barbearia', lazy=True)
    clientes = db.relationship('Cliente', backref='barbearia', lazy=True)
    agendamentos = db.relationship('Agendamento', backref='barbearia', lazy=True)
    produtos = db.relationship('Produto', backref='barbearia', lazy=True)
    planos_assinatura = db.relationship('PlanoAssinatura', backref='barbearia', lazy=True)
    despesas = db.relationship('Despesa', backref='barbearia', lazy=True)

    def set_senha(self, senha):
        self.senha_hash = generate_password_hash(senha)

    def check_senha(self, senha):
        return check_password_hash(self.senha_hash, senha)

    def get_dias_funcionamento(self):
        return json.loads(self.dias_funcionamento)

    def set_dias_funcionamento(self, dias):
        self.dias_funcionamento = json.dumps(dias)

    def get_config_fidelidade(self):
        return json.loads(self.config_fidelidade)

    def set_config_fidelidade(self, config):
        self.config_fidelidade = json.dumps(config)


class Barbeiro(db.Model):
    """Barbeiro - escalavel sem limite"""
    id = db.Column(db.Integer, primary_key=True)
    barbearia_id = db.Column(db.Integer, db.ForeignKey('barbearia.id'), nullable=False)
    nome = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(100))
    foto = db.Column(db.String(200))
    telefone = db.Column(db.String(20), nullable=False)
    pin_hash = db.Column(db.String(256), nullable=False)
    percentual_comissao = db.Column(db.Float, default=50.0)
    comissao_sobre_liquido = db.Column(db.Boolean, default=False)
    ativo = db.Column(db.Boolean, default=True)
    data_cadastro = db.Column(db.DateTime, default=datetime.utcnow)

    agendamentos = db.relationship('Agendamento', backref='barbeiro', lazy=True)
    comissoes = db.relationship('Comissao', backref='barbeiro', lazy=True)
    avaliacoes = db.relationship('Avaliacao', backref='barbeiro', lazy=True)
    portfolio_fotos = db.relationship('PortfolioFoto', backref='barbeiro', lazy=True)
    metas = db.relationship('MetaBarbeiro', backref='barbeiro', lazy=True)

    def set_pin(self, pin):
        self.pin_hash = generate_password_hash(str(pin))

    def check_pin(self, pin):
        return check_password_hash(self.pin_hash, str(pin))


class Servico(db.Model):
    """Catalogo de servicos por barbearia"""
    id = db.Column(db.Integer, primary_key=True)
    barbearia_id = db.Column(db.Integer, db.ForeignKey('barbearia.id'), nullable=False)
    nome = db.Column(db.String(100), nullable=False)
    preco = db.Column(db.Float, nullable=False)
    duracao_minutos = db.Column(db.Integer, nullable=False, default=30)
    creditos_plano = db.Column(db.Integer, default=1)
    ativo = db.Column(db.Boolean, default=True)

    agendamentos = db.relationship('Agendamento', backref='servico', lazy=True)


class Cliente(db.Model):
    """Cliente - identificado pelo telefone"""
    id = db.Column(db.Integer, primary_key=True)
    barbearia_id = db.Column(db.Integer, db.ForeignKey('barbearia.id'), nullable=False)
    nome = db.Column(db.String(100), nullable=False)
    telefone = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(120))
    data_nascimento = db.Column(db.Date)
    pontos_fidelidade = db.Column(db.Integer, default=0)
    barbeiro_preferido_id = db.Column(db.Integer, db.ForeignKey('barbeiro.id'), nullable=True)
    observacoes = db.Column(db.Text)
    data_cadastro = db.Column(db.DateTime, default=datetime.utcnow)

    barbeiro_preferido = db.relationship('Barbeiro', foreign_keys=[barbeiro_preferido_id])
    agendamentos = db.relationship('Agendamento', backref='cliente', lazy=True)
    assinaturas = db.relationship('AssinaturaCliente', backref='cliente', lazy=True)
    avaliacoes = db.relationship('Avaliacao', backref='cliente', lazy=True)


class Agendamento(db.Model):
    """Nucleo do sistema"""
    id = db.Column(db.Integer, primary_key=True)
    barbearia_id = db.Column(db.Integer, db.ForeignKey('barbearia.id'), nullable=False)
    barbeiro_id = db.Column(db.Integer, db.ForeignKey('barbeiro.id'), nullable=False)
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=False)
    servico_id = db.Column(db.Integer, db.ForeignKey('servico.id'), nullable=False)
    data = db.Column(db.Date, nullable=False)
    hora_inicio = db.Column(db.String(5), nullable=False)  # HH:MM
    hora_fim = db.Column(db.String(5), nullable=False)
    status = db.Column(db.String(20), default='agendado')  # agendado/confirmado/concluido/cancelado
    valor = db.Column(db.Float, nullable=False)
    observacao = db.Column(db.Text)
    origem = db.Column(db.String(20), default='online')  # online/manual/recorrente

    pagamento = db.relationship('Pagamento', backref='agendamento', uselist=False, lazy=True)
    avaliacao = db.relationship('Avaliacao', backref='agendamento', uselist=False, lazy=True)
    lembretes = db.relationship('LembreteLog', backref='agendamento', lazy=True)


class Pagamento(db.Model):
    """Gerado ao concluir agendamento"""
    id = db.Column(db.Integer, primary_key=True)
    agendamento_id = db.Column(db.Integer, db.ForeignKey('agendamento.id'), nullable=False)
    forma = db.Column(db.String(20), nullable=False)  # dinheiro/pix/cartao_debito/cartao_credito
    valor_pago = db.Column(db.Float, nullable=False)
    desconto_fidelidade = db.Column(db.Float, default=0)
    desconto_plano = db.Column(db.Float, default=0)
    pontos_gerados = db.Column(db.Integer, default=0)
    data = db.Column(db.DateTime, default=datetime.utcnow)
    forma2 = db.Column(db.String(20), nullable=True)  # split payment
    valor2 = db.Column(db.Float, default=0)

    comissao = db.relationship('Comissao', backref='pagamento', uselist=False, lazy=True)


class Comissao(db.Model):
    """Gerada automaticamente ao confirmar pagamento"""
    id = db.Column(db.Integer, primary_key=True)
    barbeiro_id = db.Column(db.Integer, db.ForeignKey('barbeiro.id'), nullable=False)
    pagamento_id = db.Column(db.Integer, db.ForeignKey('pagamento.id'), nullable=False)
    valor_bruto = db.Column(db.Float, nullable=False)
    percentual = db.Column(db.Float, nullable=False)
    valor_comissao = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='pendente')  # pendente/pago
    data_geracao = db.Column(db.DateTime, default=datetime.utcnow)
    data_pagamento = db.Column(db.DateTime, nullable=True)


class Produto(db.Model):
    """Estoque de insumos e produtos"""
    id = db.Column(db.Integer, primary_key=True)
    barbearia_id = db.Column(db.Integer, db.ForeignKey('barbearia.id'), nullable=False)
    nome = db.Column(db.String(100), nullable=False)
    unidade = db.Column(db.String(10), default='un')  # ml/un/kg
    estoque_atual = db.Column(db.Float, default=0)
    estoque_minimo = db.Column(db.Float, default=5)
    preco_custo = db.Column(db.Float, default=0)
    preco_venda = db.Column(db.Float, default=0)
    ativo = db.Column(db.Boolean, default=True)

    movimentacoes = db.relationship('MovimentacaoEstoque', backref='produto', lazy=True)


class MovimentacaoEstoque(db.Model):
    """Auditoria de movimentacoes de estoque"""
    id = db.Column(db.Integer, primary_key=True)
    produto_id = db.Column(db.Integer, db.ForeignKey('produto.id'), nullable=False)
    tipo = db.Column(db.String(20), nullable=False)  # entrada/saida/ajuste/venda
    quantidade = db.Column(db.Float, nullable=False)
    motivo = db.Column(db.String(200))
    data = db.Column(db.DateTime, default=datetime.utcnow)
    barbeiro_id = db.Column(db.Integer, db.ForeignKey('barbeiro.id'), nullable=True)


class PlanoAssinatura(db.Model):
    """Planos mensais criados pelo dono"""
    id = db.Column(db.Integer, primary_key=True)
    barbearia_id = db.Column(db.Integer, db.ForeignKey('barbearia.id'), nullable=False)
    nome = db.Column(db.String(100), nullable=False)
    preco_mensal = db.Column(db.Float, nullable=False)
    descricao = db.Column(db.Text)
    creditos_mensais = db.Column(db.Text, default='{}')  # JSON: {"corte": 2, "barba": 1}
    creditos_expiram = db.Column(db.Boolean, default=True)
    ativo = db.Column(db.Boolean, default=True)

    assinaturas = db.relationship('AssinaturaCliente', backref='plano', lazy=True)

    def get_creditos(self):
        return json.loads(self.creditos_mensais)


class AssinaturaCliente(db.Model):
    """Contrato do cliente com o plano"""
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=False)
    plano_id = db.Column(db.Integer, db.ForeignKey('plano_assinatura.id'), nullable=False)
    status = db.Column(db.String(20), default='ativo')  # ativo/cancelado/inadimplente
    dia_cobranca = db.Column(db.Integer, default=1)
    creditos_restantes = db.Column(db.Text, default='{}')  # JSON
    data_inicio = db.Column(db.Date, default=date.today)
    data_renovacao = db.Column(db.Date)
    asaas_subscription_id = db.Column(db.String(100))

    def get_creditos_restantes(self):
        return json.loads(self.creditos_restantes)

    def set_creditos_restantes(self, creditos):
        self.creditos_restantes = json.dumps(creditos)


class Avaliacao(db.Model):
    """Avaliacao pos-atendimento"""
    id = db.Column(db.Integer, primary_key=True)
    agendamento_id = db.Column(db.Integer, db.ForeignKey('agendamento.id'), nullable=False)
    barbeiro_id = db.Column(db.Integer, db.ForeignKey('barbeiro.id'), nullable=False)
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=False)
    nota = db.Column(db.Integer, nullable=False)  # 1-5
    comentario = db.Column(db.Text)
    data = db.Column(db.DateTime, default=datetime.utcnow)
    enviado_por_whatsapp = db.Column(db.Boolean, default=False)


class PortfolioFoto(db.Model):
    """Galeria de trabalhos do barbeiro"""
    id = db.Column(db.Integer, primary_key=True)
    barbeiro_id = db.Column(db.Integer, db.ForeignKey('barbeiro.id'), nullable=False)
    url_foto = db.Column(db.String(200), nullable=False)
    servico_tag = db.Column(db.String(50))
    legenda = db.Column(db.String(200))
    destaque = db.Column(db.Boolean, default=False)
    data_upload = db.Column(db.DateTime, default=datetime.utcnow)


class MetaBarbeiro(db.Model):
    """Metas mensais por barbeiro"""
    id = db.Column(db.Integer, primary_key=True)
    barbeiro_id = db.Column(db.Integer, db.ForeignKey('barbeiro.id'), nullable=False)
    mes = db.Column(db.Integer, nullable=False)
    ano = db.Column(db.Integer, nullable=False)
    meta_atendimentos = db.Column(db.Integer, default=0)
    meta_faturamento = db.Column(db.Float, default=0)
    meta_nota_minima = db.Column(db.Float, default=4.0)
    bonus_tipo = db.Column(db.String(20), default='fixo')  # fixo/percentual
    bonus_valor = db.Column(db.Float, default=0)


class Despesa(db.Model):
    """Controle financeiro real"""
    id = db.Column(db.Integer, primary_key=True)
    barbearia_id = db.Column(db.Integer, db.ForeignKey('barbearia.id'), nullable=False)
    descricao = db.Column(db.String(200), nullable=False)
    categoria = db.Column(db.String(50))  # aluguel/energia/produtos/manutencao/outros
    valor = db.Column(db.Float, nullable=False)
    tipo = db.Column(db.String(20), default='fixa')  # fixa/variavel
    data_vencimento = db.Column(db.Date)
    pago = db.Column(db.Boolean, default=False)


class LembreteLog(db.Model):
    """Auditoria de disparos de WhatsApp"""
    id = db.Column(db.Integer, primary_key=True)
    agendamento_id = db.Column(db.Integer, db.ForeignKey('agendamento.id'), nullable=False)
    tipo = db.Column(db.String(30), nullable=False)  # confirmacao/lembrete_24h/lembrete_1h/pos_atendimento/aniversario/reativacao
    enviado_em = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default='enviado')  # enviado/falhou/ignorado
    tentativas = db.Column(db.Integer, default=1)
