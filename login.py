import streamlit as st
import os
import mercadopago
from datetime import date
from supabase import create_client, Client
from dotenv import load_dotenv

# Carrega variáveis locais se houver
load_dotenv()

# Configuração do Supabase
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# Configurações do Mercado Pago
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN")
MP_PRICE = float(os.environ.get("MP_PRICE", "1.00")) # Valor alterado para 1.00 para facilitar testes de produção
APP_URL = os.environ.get("APP_URL", "http://localhost:8501").strip()
if not APP_URL:
    APP_URL = "http://localhost:8501"
elif not APP_URL.startswith("http"):
    APP_URL = "https://" + APP_URL

WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://derma-ai-prd-webhook.onrender.com/webhook")

# Inicializando Supabase se as chaves existirem
supabase: Client | None = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        st.error(f"Erro ao conectar ao Supabase: {e}")

# estado de login
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if "user_email" not in st.session_state:
    st.session_state.user_email = None

if "auth_token" not in st.session_state:
    st.session_state.auth_token = None

# Função para checar a assinatura do usuário após autenticação
def checar_assinatura(email):
    if not supabase:
        st.error("Supabase não configurado.")
        return False
    
    try:
        response = supabase.table("user_subscriptions").select("*").eq("email", email).execute()
        
        if not response.data:
            # Caso não exista registro, podemos assumir desativado
            return {"status": "desativado", "data_fim": None}
        
        return response.data[0]
    except Exception as e:
        st.error(f"Erro ao buscar assinatura: {e}")
        return False

# Função para gerar link de pagamento dinâmico com Mercado Pago
def gerar_link_pagamento(email):
    if not MP_ACCESS_TOKEN:
        st.error("MP_ACCESS_TOKEN não configurado no seu ambiente. Não é possível gerar o pagamento.")
        return None
        
    try:
        sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
        
        preference_data = {
            "items": [
                {
                    "id": "assinatura_derma_ai",
                    "title": "Assinatura Derma Ai - 1 ano",
                    "quantity": 1,
                    "currency_id": "BRL",
                    "unit_price": MP_PRICE
                }
            ],
            "payer": {
                "email": email
            },
            "back_urls": {
                "success": f"{APP_URL}?success=true",
                "failure": f"{APP_URL}?canceled=true",
                "pending": f"{APP_URL}?pending=true"
            },
            "auto_return": "approved",
            "notification_url": WEBHOOK_URL,
            "external_reference": email,
            "statement_descriptor": "DERMA AI"
        }
        
        preference_response = sdk.preference().create(preference_data)
        
        # Verifica se a requisição falhou (status diferente de 200/201)
        if preference_response.get("status") not in (200, 201):
            erro_detalhado = preference_response.get("response", {})
            st.error(f"O Mercado Pago recusou a criação do link. Motivo: {erro_detalhado} | URL enviada: {APP_URL}?success=true")
            return None
            
        preference = preference_response.get("response", {})
        
        if "init_point" not in preference:
            st.error(f"Resposta inesperada do Mercado Pago: {preference}")
            return None
            
        # URL de init_point é o link para o Checkout Pro
        return preference["init_point"]
    except Exception as e:
        st.error(f"Erro ao comunicar com o Mercado Pago: {str(e)}")
        return None

# 🔒 LOGIN / CADASTRO
if not st.session_state.logged_in:
    
    st.title("Derm AI Copilot")
    st.subheader("Faça login para continuar")

    if not supabase:
        st.warning("Aguardando configurações do Supabase nas variáveis de ambiente.")
        st.stop()

    # Recuperação de senha: Se houver 'code' na URL, o usuário clicou no link do e-mail
    if "code" in st.query_params:
        try:
            # Troca o código por uma sessão
            supabase.auth.exchange_code_for_session(st.query_params["code"])
            # Remove o code da URL para não processar de novo
            st.query_params.clear()
            st.session_state.show_reset_password = True
            st.rerun()
        except Exception as e:
            st.error(f"O link de recuperação é inválido ou expirou. {e}")
            st.query_params.clear()

    # Fluxo de redefinição de senha
    if st.session_state.get("show_reset_password", False):
        st.subheader("Redefinir Senha")
        st.write("Digite sua nova senha abaixo.")
        nova_senha = st.text_input("Nova Senha", type="password", key="new_pwd")
        confirmar_senha = st.text_input("Confirmar Nova Senha", type="password", key="conf_new_pwd")
        
        if st.button("Atualizar Senha"):
            if nova_senha and nova_senha == confirmar_senha:
                try:
                    # Atualiza a senha do usuário autenticado pela sessão do link
                    supabase.auth.update_user({"password": nova_senha})
                    st.success("Senha atualizada com sucesso! Faça login com a nova senha.")
                    st.session_state.show_reset_password = False
                    supabase.auth.sign_out()
                except Exception as e:
                    st.error(f"Erro ao atualizar a senha: {e}")
            elif nova_senha != confirmar_senha:
                st.warning("As senhas não coincidem.")
            else:
                st.warning("Preencha a nova senha.")
        
        if st.button("Voltar ao Login"):
            st.session_state.show_reset_password = False
            supabase.auth.sign_out()
            st.rerun()

        st.stop() # Impede de mostrar as abas de login

    tab1, tab2, tab3 = st.tabs(["Login", "Cadastrar", "Esqueci a Senha"])

    with tab1:
        st.write("Digite suas credenciais")
        email_login = st.text_input("Email", key="email_l")
        senha_login = st.text_input("Senha", type="password", key="senha_l")

        if st.button("Entrar"):
            if email_login and senha_login:
                try:
                    # Autenticação via Supabase
                    auth_response = supabase.auth.sign_in_with_password({
                        "email": email_login,
                        "password": senha_login
                    })
                    
                    user_email = auth_response.user.email
                    st.session_state.user_email = user_email
                    st.session_state.auth_token = auth_response.session.access_token

                    # Verifica a assinatura
                    assinatura = checar_assinatura(user_email)
                    
                    if assinatura:
                        status = assinatura.get("status", "desativado")
                        data_fim_str = assinatura.get("data_fim")
                        
                        hoje = date.today()
                        
                        if status == "desativado":
                            st.error(f"Ative seu cadastro hoje")
                            with st.spinner("Gerando ambiente de pagamento seguro..."):
                                link = gerar_link_pagamento(user_email)
                                if link:
                                    st.markdown(f'<meta http-equiv="refresh" content="2;url={link}">', unsafe_allow_html=True)
                                    st.link_button("💳 **Clique aqui para ativar seu plano**", link)
                        elif status == "ativo":
                            if data_fim_str:
                                data_fim = date.fromisoformat(data_fim_str)
                                if hoje > data_fim:
                                    st.warning("Garanta mais tempo e reative seu cadastro")
                                    with st.spinner("Gerando ambiente de pagamento seguro..."):
                                        link = gerar_link_pagamento(user_email)
                                        if link:
                                            st.markdown(f'<meta http-equiv="refresh" content="2;url={link}">', unsafe_allow_html=True)
                                            st.link_button("💳 **Clique aqui para reativar seu cadastro**", link)
                                else:
                                    # Acesso Permitido
                                    st.success("Login efetuado com sucesso!")
                                    st.session_state.logged_in = True
                                    st.rerun()
                            else:
                                st.warning("Data de validade da assinatura não encontrada. Entre em contato com o suporte.")
                    
                except Exception as e:
                    # Pega a mensagem de erro da exception
                    st.error("Falha no login. Verifique seu email e senha.")
            else:
                st.warning("Preencha email e senha.")

    with tab2:
        st.write("Crie sua conta para utilizar o assistente")
        email_cad = st.text_input("Email", key="email_c")
        senha_cad = st.text_input("Senha", type="password", key="senha_c")
        
        if st.button("Cadastrar e Assinar"):
            if email_cad and senha_cad:
                try:
                    # 1. Cria usuário no Auth
                    res = supabase.auth.sign_up({
                        "email": email_cad,
                        "password": senha_cad
                    })
                    
                    # 2. Insere na tabela user_subscriptions
                    # Pode acontecer de o usuário já ter se cadastrado no Auth em outra tentativa, 
                    # então verificamos se já existe. Para simplificar, forçamos o insert.
                    try:
                        supabase.table("user_subscriptions").insert({
                            "email": email_cad,
                            "status": "desativado"
                        }).execute()
                    except Exception as e:
                        # Ignora se já existir
                        pass
                    
                    st.success("Conta criada com sucesso! Redirecionando para ativação do plano...")
                    link = gerar_link_pagamento(email_cad)
                    if link:
                        st.markdown(f'<meta http-equiv="refresh" content="3;url={link}">', unsafe_allow_html=True)
                        st.markdown(f"*(Se não for redirecionado em instantes, [clique aqui]({link}))*")
                
                except Exception as e:
                    st.error(f"Não foi possível criar a conta: {e}")
            else:
                st.warning("Preencha email e senha.")

    with tab3:
        st.write("Insira seu e-mail para receber um link de recuperação.")
        email_rec = st.text_input("Email", key="email_rec")
        
        if st.button("Enviar link de recuperação"):
            if email_rec:
                try:
                    supabase.auth.reset_password_email(email_rec)
                    st.success("Se o e-mail estiver cadastrado, você receberá um link de recuperação em breve.")
                except Exception as e:
                    st.error("Erro ao solicitar a recuperação. Tente novamente.")
            else:
                st.warning("Preencha o e-mail.")

    st.stop()  # 🔴 impede execução do app antes do login

# ✅ APP PRINCIPAL (SEM BUG DE IMPORT)
with open("main_app.py", "r", encoding="utf-8") as f:
    code = f.read()
    exec(code, globals())