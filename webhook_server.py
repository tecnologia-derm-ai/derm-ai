import os
import mercadopago
from fastapi import FastAPI, Request, HTTPException
from supabase import create_client, Client
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

# Configuração do Supabase
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

supabase: Client | None = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Configuração do Mercado Pago
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN")
sdk = mercadopago.SDK(MP_ACCESS_TOKEN) if MP_ACCESS_TOKEN else None

app = FastAPI(title="Mercado Pago Webhook Server")

@app.post("/webhook")
async def mercadopago_webhook(request: Request):
    if not sdk:
        print("Erro: MP_ACCESS_TOKEN não configurado no servidor.")
        return {"status": "error", "detail": "MP_ACCESS_TOKEN não configurado"}

    try:
        # Tenta pegar o payload como JSON
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        
        # Pega também os query parameters
        query_params = dict(request.query_params)
        
        # Descobre o tipo do evento e o ID
        topic = payload.get("type") or payload.get("topic") or query_params.get("topic") or query_params.get("type")
        
        payment_id = None
        if payload.get("data") and payload["data"].get("id"):
            payment_id = payload["data"]["id"]
        elif query_params.get("data.id"):
            payment_id = query_params.get("data.id")
        elif query_params.get("id"):
            payment_id = query_params.get("id")

        if topic == "payment" and payment_id:
            # 1. Busca os detalhes do pagamento na API oficial do Mercado Pago (para garantir segurança contra fraudes)
            payment_info = sdk.payment().get(payment_id)
            
            if payment_info["status"] == 200:
                payment_data = payment_info["response"]
                
                status_pagamento = payment_data.get("status")
                # O external_reference é onde guardamos o e-mail do cliente ao gerar o link
                email_cliente = payment_data.get("external_reference") 
                
                if status_pagamento == "approved" and email_cliente:
                    if supabase:
                        atualizar_assinatura(email_cliente)
                        print(f"Assinatura atualizada/criada para o email: {email_cliente}")
                    else:
                        print("Supabase não configurado.")
                else:
                    print(f"Notificação recebida, mas pagamento não está aprovado ou sem email. Status: {status_pagamento}")
            else:
                print(f"Falha ao consultar pagamento no Mercado Pago. Status Http: {payment_info['status']}")

    except Exception as e:
        print(f"Erro ao processar webhook: {e}")
        # Retorna 200 de qualquer forma para o MP parar de reenviar a notificação
        pass
        
    return {"status": "success"}

def atualizar_assinatura(email: str):
    """
    Se estiver desativado -> muda para ativo, data_inicio hoje, data_fim = hoje + 365 dias
    Se estiver ativo -> só adiciona 365 dias na data_fim
    """
    try:
        response = supabase.table("user_subscriptions").select("*").eq("email", email).execute()
        
        hoje = date.today()
        um_ano = timedelta(days=365)
        
        if response.data and len(response.data) > 0:
            reg = response.data[0]
            status_atual = reg.get("status")
            data_fim_str = reg.get("data_fim")
            
            nova_data_fim = hoje + um_ano
            novo_status = "ativo"
            nova_data_inicio = hoje
            
            if status_atual == "ativo" and data_fim_str:
                data_fim_atual = date.fromisoformat(data_fim_str)
                # Se ainda tem tempo sobrando, soma a partir de hoje ou do fim? 
                # Conforme regra, muda apenas o período estendendo
                nova_data_fim = data_fim_atual + um_ano
                # data inicio continua a mesma, então não atualizar
                supabase.table("user_subscriptions").update({
                    "data_fim": nova_data_fim.isoformat()
                }).eq("email", email).execute()
                
            else:
                # Estava desativado ou sem data
                supabase.table("user_subscriptions").update({
                    "status": novo_status,
                    "data_inicio": nova_data_inicio.isoformat(),
                    "data_fim": nova_data_fim.isoformat()
                }).eq("email", email).execute()
        else:
            # Caso não exista registro, criamos um novo já ativo
            supabase.table("user_subscriptions").insert({
                "email": email,
                "status": "ativo",
                "data_inicio": hoje.isoformat(),
                "data_fim": (hoje + um_ano).isoformat()
            }).execute()
            
    except Exception as e:
        print(f"Erro ao atualizar o Supabase para {email}: {e}")

# Para rodar manualmente: uvicorn webhook_server:app --host 0.0.0.0 --port 8000
