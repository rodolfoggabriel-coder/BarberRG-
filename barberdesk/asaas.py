import requests
from flask import current_app


class AsaasClient:
    """Cliente para API do Asaas - Pagamentos e Recorrencia"""

    def __init__(self):
        self.api_url = current_app.config.get('ASAAS_API_URL', 'https://sandbox.asaas.com/api/v3')
        self.api_key = current_app.config.get('ASAAS_API_KEY', '')

    @property
    def headers(self):
        return {
            'access_token': self.api_key,
            'Content-Type': 'application/json'
        }

    def criar_cliente(self, nome, telefone, email=None):
        """Cria cliente no Asaas"""
        data = {
            'name': nome,
            'mobilePhone': telefone,
        }
        if email:
            data['email'] = email

        try:
            resp = requests.post(
                f'{self.api_url}/customers',
                json=data,
                headers=self.headers,
                timeout=10
            )
            if resp.status_code == 200:
                return resp.json().get('id')
        except Exception:
            pass
        return None

    def criar_assinatura(self, customer_id, valor, descricao, dia_cobranca):
        """Cria assinatura recorrente"""
        data = {
            'customer': customer_id,
            'billingType': 'UNDEFINED',  # cliente escolhe na hora
            'value': valor,
            'cycle': 'MONTHLY',
            'description': descricao,
            'nextDueDate': None,  # Asaas calcula
        }

        try:
            resp = requests.post(
                f'{self.api_url}/subscriptions',
                json=data,
                headers=self.headers,
                timeout=10
            )
            if resp.status_code == 200:
                return resp.json().get('id')
        except Exception:
            pass
        return None

    def cancelar_assinatura(self, subscription_id):
        """Cancela assinatura"""
        try:
            resp = requests.delete(
                f'{self.api_url}/subscriptions/{subscription_id}',
                headers=self.headers,
                timeout=10
            )
            return resp.status_code == 200
        except Exception:
            return False

    def gerar_cobranca_pix(self, customer_id, valor, descricao):
        """Gera cobranca avulsa via Pix"""
        data = {
            'customer': customer_id,
            'billingType': 'PIX',
            'value': valor,
            'description': descricao,
            'dueDate': None,
        }

        try:
            resp = requests.post(
                f'{self.api_url}/payments',
                json=data,
                headers=self.headers,
                timeout=10
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return None
