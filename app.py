import streamlit as st
from datetime import date
import tempfile
import re
import sqlite3
import os
import uuid
import hashlib
from supabase import create_client, Client
from streamlit_mic_recorder import mic_recorder
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from src.transcriber import transcrever_audio
from src.clinical_ai import analisar_consulta
from src.image_analysis import analisar_imagem
from src.lesion_detection import detectar_lesao
from src.pathology_interpreter import interpretar_laudo
from src.melanoma_abcd import analisar_abcd
from src.biopsy_request import gerar_pedido_biopsia
from src.patient_report import gerar_laudo_paciente


# ----------------------------
# KEEP-ALIVE JS (Previne timeouts no Render)
# ----------------------------

import streamlit.components.v1 as components

components.html(
    """
    <script>
        // Pings health check endpoint to keep connection active
        setInterval(() => {
            fetch(window.location.origin + '/_stcore/health')
                .then(r => console.log('Keep-alive ping successful'))
                .catch(e => console.error('Keep-alive ping failed', e));
        }, 30000);
    </script>
    """,
    height=0,
    width=0
)


# ----------------------------
# BANCO DE DADOS (SUPABASE & SQLITE FALLBACK)
# ----------------------------

def inicializar_supabase():
    # Usar cliente global de login.py se disponível
    if 'supabase' in globals() and globals()['supabase'] is not None:
        return globals()['supabase']
    
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if url and key:
        try:
            return create_client(url, key)
        except Exception as e:
            print(f"Erro ao inicializar Supabase Client: {e}")
    return None

supabase_client = inicializar_supabase()

def conectar_db():
    return sqlite3.connect("derm_ai.db", check_same_thread=False)

def criar_tabelas():
    conn = conectar_db()
    cursor = conn.cursor()
    
    # Criar a tabela consultas se não existir com a estrutura completa
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS consultas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uuid TEXT UNIQUE,
        patient_name TEXT,
        patient_sex TEXT,
        patient_age INTEGER,
        data_consulta TEXT,
        transcricao TEXT,
        analise TEXT,
        user_email TEXT
    )
    """)
    
    # Garantir que colunas adicionadas em migrações existam no SQLite local
    colunas_migracao = [
        ("uuid", "TEXT UNIQUE"),
        ("patient_sex", "TEXT"),
        ("patient_age", "INTEGER"),
        ("user_email", "TEXT")
    ]
    for col, col_type in colunas_migracao:
        try:
            cursor.execute(f"ALTER TABLE consultas ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError:
            pass  # Coluna já existe
            
    conn.commit()
    conn.close()

criar_tabelas()


# ----------------------------
# SALVAR E LISTAR CONSULTAS
# ----------------------------

def salvar_consulta(nome, sexo, idade, transcricao, analise):
    # Recupera ou gera o UUID ativo para esta consulta
    if "active_consultation_uuid" not in st.session_state or not st.session_state.active_consultation_uuid:
        st.session_state.active_consultation_uuid = str(uuid.uuid4())
        
    c_uuid = st.session_state.active_consultation_uuid
    user_email = st.session_state.get("user_email", "anonimo")
    hoje = date.today().strftime("%Y-%m-%d")
    
    # 1. Salvar no Supabase (Upsert via API)
    salvou_supabase = False
    if supabase_client:
        try:
            data = {
                "uuid": c_uuid,
                "user_email": user_email,
                "patient_name": nome,
                "patient_sex": sexo,
                "patient_age": int(idade) if idade is not None else None,
                "data_consulta": hoje,
                "transcricao": transcricao,
                "analise": analise
            }
            supabase_client.table("consultas").upsert(data, on_conflict="uuid").execute()
            salvou_supabase = True
        except Exception as e:
            print(f"Erro ao salvar no Supabase (upsert): {e}")
            
    # 2. Salvar no SQLite local (como cópia local / fallback)
    try:
        conn = conectar_db()
        cursor = conn.cursor()
        
        # Verificar se o UUID já existe no SQLite
        cursor.execute("SELECT id FROM consultas WHERE uuid = ?", (c_uuid,))
        existe = cursor.fetchone()
        
        if existe:
            # Atualiza registro existente
            cursor.execute("""
            UPDATE consultas 
            SET patient_name = ?, patient_sex = ?, patient_age = ?, data_consulta = ?, transcricao = ?, analise = ?, user_email = ?
            WHERE uuid = ?
            """, (nome, sexo, idade, hoje, transcricao, analise, user_email, c_uuid))
        else:
            # Insere novo registro
            cursor.execute("""
            INSERT INTO consultas (uuid, patient_name, patient_sex, patient_age, data_consulta, transcricao, analise, user_email)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (c_uuid, nome, sexo, idade, hoje, transcricao, analise, user_email))
            
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Erro ao salvar no SQLite local: {e}")
        
    return salvou_supabase


def listar_consultas_usuario():
    user_email = st.session_state.get("user_email")
    if not user_email:
        return []
        
    # 1. Tentar ler do Supabase
    if supabase_client:
        try:
            response = supabase_client.table("consultas") \
                .select("*") \
                .eq("user_email", user_email) \
                .order("created_at", descending=True) \
                .limit(15) \
                .execute()
            return response.data
        except Exception as e:
            print(f"Erro ao listar consultas do Supabase: {e}")
            
    # 2. Se falhar ou não configurado, ler do SQLite local
    try:
        conn = conectar_db()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
        SELECT uuid, patient_name, patient_sex, patient_age, data_consulta, transcricao, analise 
        FROM consultas 
        WHERE user_email = ? 
        ORDER BY id DESC 
        LIMIT 15
        """, (user_email,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception as e:
        print(f"Erro ao carregar consultas do SQLite local: {e}")
        return []


# ----------------------------
# APP
# ----------------------------

st.set_page_config(page_title="Derm AI Copilot", layout="wide")

st.title("Derm AI Copilot")
st.write("Assistente dermatológico com IA")


# ----------------------------
# SESSION STATE
# ----------------------------

if "patient_started" not in st.session_state:
    st.session_state.patient_started = False

if "patient_name" not in st.session_state:
    st.session_state.patient_name = ""

if "patient_sex" not in st.session_state:
    st.session_state.patient_sex = ""

if "patient_age" not in st.session_state:
    st.session_state.patient_age = ""

if "transcricao_total" not in st.session_state:
    st.session_state.transcricao_total = ""

if "analise_total" not in st.session_state:
    st.session_state.analise_total = ""

if "camera_ativa" not in st.session_state:
    st.session_state.camera_ativa = False

if "active_consultation_uuid" not in st.session_state:
    st.session_state.active_consultation_uuid = None

if "last_processed_audio_file" not in st.session_state:
    st.session_state.last_processed_audio_file = None


# ----------------------------
# CALCULAR IDADE
# ----------------------------

def calcular_idade(data_nascimento):

    hoje = date.today()

    idade = hoje.year - data_nascimento.year - (
        (hoje.month, hoje.day) < (data_nascimento.month, data_nascimento.day)
    )

    return idade


# ----------------------------
# PDF
# ----------------------------

def gerar_pdf():

    arquivo = "consulta_medica.pdf"

    c = canvas.Canvas(arquivo, pagesize=A4)

    largura, altura = A4
    y = altura - 50

    c.setFont("Helvetica-Bold", 18)
    c.drawString(50, y, "RELATÓRIO MÉDICO")

    y -= 40
    c.setFont("Helvetica", 12)

    c.drawString(50, y, f"Paciente: {st.session_state.patient_name}")
    y -= 20

    c.drawString(50, y, f"Sexo: {st.session_state.patient_sex}")
    y -= 20

    c.drawString(50, y, f"Idade: {st.session_state.patient_age}")
    y -= 20

    for linha in st.session_state.analise_total.split("\n"):

        c.drawString(50, y, linha[:95])
        y -= 15

        if y < 120:
            c.showPage()
            y = altura - 50

    c.save()

    return arquivo


# ----------------------------
# NOVO PACIENTE E CONSULTAS RECENTES
# ----------------------------

if not st.session_state.patient_started:
    col_novo, col_recentes = st.columns(2)
    
    with col_novo:
        st.header("Novo paciente")
        nome = st.text_input("Paciente")
        sexo = st.selectbox(
            "Sexo",
            ["Masculino", "Feminino", "Outro"]
        )
        
        if "patient_dob" not in st.session_state:
            st.session_state.patient_dob = None
            
        dob = st.date_input(
            "Data de nascimento",
            value=st.session_state.patient_dob,
            min_value=date(1900,1,1),
            max_value=date.today()
        )
        st.session_state.patient_dob = dob
        
        idade = None
        if dob:
            idade = calcular_idade(dob)
            st.write(f"Idade: {idade} anos")
            
        if st.button("Iniciar consulta"):
            if not nome:
                st.warning("Por favor, insira o nome do paciente.")
            else:
                st.session_state.patient_name = nome
                st.session_state.patient_sex = sexo
                st.session_state.patient_age = idade
                st.session_state.patient_started = True
                st.session_state.active_consultation_uuid = str(uuid.uuid4())
                st.session_state.transcricao_total = ""
                st.session_state.analise_total = ""
                st.session_state.last_processed_audio_file = None
                st.rerun()
                
    with col_recentes:
        st.header("Consultas Recentes")
        email = st.session_state.get("user_email")
        if email:
            recentes = listar_consultas_usuario()
            if recentes:
                for c in recentes:
                    with st.container(border=True):
                        st.markdown(f"**👤 {c['patient_name']}**")
                        st.markdown(f"📅 Data: {c['data_consulta']} | Idade: {c.get('patient_age', 'N/A')} | Sexo: {c.get('patient_sex', 'N/A')}")
                        if st.button("Carregar Consulta", key=f"load_{c['uuid']}"):
                            st.session_state.patient_name = c["patient_name"]
                            st.session_state.patient_sex = c.get("patient_sex", "Masculino")
                            st.session_state.patient_age = c.get("patient_age", "")
                            st.session_state.transcricao_total = c.get("transcricao", "")
                            st.session_state.analise_total = c.get("analise", "")
                            st.session_state.active_consultation_uuid = c["uuid"]
                            st.session_state.patient_started = True
                            st.session_state.last_processed_audio_file = None
                            # Resetar estados adicionais
                            if "imagem_resultado" in st.session_state:
                                del st.session_state.imagem_resultado
                            if "imagem_path" in st.session_state:
                                del st.session_state.imagem_path
                            if "abcd_resultado" in st.session_state:
                                del st.session_state.abcd_resultado
                            if "chat_history" in st.session_state:
                                del st.session_state.chat_history
                            st.rerun()
            else:
                st.info("Nenhuma consulta anterior encontrada.")
        else:
            st.warning("E-mail de usuário não encontrado no session state.")
            
    st.stop()


# ----------------------------
# SIDEBAR
# ----------------------------

st.sidebar.header("Paciente")

st.sidebar.write(f"Paciente: {st.session_state.patient_name}")
st.sidebar.write(f"Sexo: {st.session_state.patient_sex}")
st.sidebar.write(f"Idade: {st.session_state.patient_age}")

if st.sidebar.button("Novo paciente"):
    # Limpa estado completo para nova consulta
    st.session_state.patient_started = False
    st.session_state.patient_name = ""
    st.session_state.patient_sex = ""
    st.session_state.patient_age = ""
    st.session_state.transcricao_total = ""
    st.session_state.analise_total = ""
    st.session_state.active_consultation_uuid = None
    st.session_state.last_processed_audio_file = None
    if "imagem_resultado" in st.session_state:
        del st.session_state.imagem_resultado
    if "imagem_path" in st.session_state:
        del st.session_state.imagem_path
    if "abcd_resultado" in st.session_state:
        del st.session_state.abcd_resultado
    if "chat_history" in st.session_state:
        del st.session_state.chat_history
        
    st.rerun()


# ----------------------------
# CONSULTA
# ----------------------------

from src.audio_chunker import transcrever_audio_grande  # 👈 IMPORTANTE

st.header("Consulta")

tab_rec, tab_up = st.tabs(["🎙️ Gravar Consulta ao Vivo", "📁 Enviar Arquivo de Áudio"])

audio = None
audio_upload = None

with tab_rec:
    audio = mic_recorder(
        start_prompt="🎙️ Iniciar Gravação",
        stop_prompt="⏹️ Parar Gravação e Processar",
        just_once=True
    )

with tab_up:
    audio_upload = st.file_uploader(
        "Selecione um arquivo de áudio (WAV, MP3, M4A, etc.)",
        type=["wav", "mp3", "m4a", "ogg", "mp4"]
    )

# Identificação e captura de áudio com guardas
audio_bytes = None
audio_id = None
file_suffix = ".wav"

if audio:
    audio_bytes = audio["bytes"]
    # Identificador único baseado no conteúdo
    audio_id = f"recorded_{hashlib.md5(audio_bytes).hexdigest()}"
    file_suffix = ".wav"
elif audio_upload:
    audio_bytes = audio_upload.getvalue()
    # Identificador baseado no nome e tamanho
    audio_id = f"uploaded_{audio_upload.name}_{len(audio_bytes)}"
    file_suffix = os.path.splitext(audio_upload.name)[1].lower() or ".wav"

if audio_bytes and audio_id != st.session_state.get("last_processed_audio_file"):
    # Salva o ID processado para evitar loops
    st.session_state.last_processed_audio_file = audio_id
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=file_suffix) as temp_audio:
        temp_audio.write(audio_bytes)
        audio_path = temp_audio.name
        
    try:
        progress_bar = st.progress(0)
        st.info("Processando e transcrevendo áudio...")
        
        texto = transcrever_audio_grande(
            audio_path,
            transcrever_audio,
            progress_bar=progress_bar
        )
        
        progress_bar.empty()
        
        if texto:
            st.session_state.transcricao_total += "\n\n" + texto
            st.info("Gerando prontuário médico...")
            
            analise = analisar_consulta(st.session_state.transcricao_total)
            st.session_state.analise_total = analise.replace("*", "")
            
            # Salvar de forma híbrida
            salvar_consulta(
                st.session_state.patient_name,
                st.session_state.patient_sex,
                st.session_state.patient_age,
                st.session_state.transcricao_total,
                st.session_state.analise_total
            )
            st.success("Consulta gravada e prontuário salvo!")
            st.rerun()
        else:
            st.error("Não foi possível obter texto da gravação.")
    except Exception as e:
        st.error(f"Erro ao processar o áudio da consulta: {e}")
    finally:
        # Garantir exclusão do arquivo temporário
        try:
            if os.path.exists(audio_path):
                os.remove(audio_path)
        except Exception as e:
            print(f"Erro ao deletar arquivo temporário de áudio: {e}")
# ----------------------------
# PRONTUÁRIO
# ----------------------------

if st.session_state.analise_total:

    st.subheader("Prontuário médico")

    st.markdown(st.session_state.analise_total)

    st.code(st.session_state.analise_total)

# ----------------------------
# SOLICITAR BIÓPSIA
# ----------------------------

if st.session_state.transcricao_total:

    if st.checkbox("Solicitar biópsia"):

        pedido = gerar_pedido_biopsia(st.session_state.transcricao_total)

        st.subheader("Pedido anatomopatológico")

        st.write(pedido)


# ----------------------------
# GERAR LAUDO PARA PACIENTE
# ----------------------------

if st.session_state.transcricao_total:

    if st.checkbox("Gerar laudo para paciente"):

        laudo = gerar_laudo_paciente(
            st.session_state.transcricao_total,
            st.session_state.analise_total
        )

        st.subheader("Laudo médico para paciente")

        st.write(laudo)

        st.code(laudo)


# ----------------------------
# INTERPRETAR LAUDO
# ----------------------------

st.divider()

st.header("Interpretar anatomopatológico")

laudo = st.text_area("Cole o laudo anatomopatológico")

if laudo:

    if st.button("Interpretar laudo"):

        interpretacao = interpretar_laudo(laudo)

        st.write(interpretacao)


# ----------------------------
# IMAGEM DERMATOLÓGICA
# ----------------------------

st.divider()

st.header("Imagem dermatológica")

imagem = st.file_uploader(
    "Upload da imagem",
    type=["jpg", "jpeg", "png"]
)

if st.button("Usar câmera"):

    st.session_state.camera_ativa = True

if st.session_state.camera_ativa:

    imagem_camera = st.camera_input("Fotografar lesão")

    if imagem_camera:

        imagem = imagem_camera

if imagem:

    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp_img:

        temp_img.write(imagem.read())

        img_path = temp_img.name

    resultado = analisar_imagem(img_path)
    st.session_state.imagem_resultado = resultado
    st.session_state.imagem_path = img_path

    st.subheader("Análise da imagem")

    st.write(resultado)

    img_detect, _ = detectar_lesao(img_path)

    st.image(img_detect)

if st.checkbox("Screening melanoma ABCD"):

    from src.melanoma.melanoma_clinico import gerar_relatorio_clinico_abcd

    abcd = analisar_abcd(img_path)

    relatorio = gerar_relatorio_clinico_abcd(abcd)

    # 👇 SALVA PARA O CHAT USAR
    st.session_state.abcd_resultado = relatorio

    st.subheader("Screening melanoma (ABCD)")

    st.write("Score:", relatorio["score"])
    st.write("Classificação:", relatorio["risco"])

    st.write("Análise detalhada:")
    for item in relatorio["explicacoes"]:
        st.write("-", item)

    st.write("Conduta sugerida:")
    st.write(relatorio["conduta"])


# 👇 FORA DO IF (IMPORTANTE)
from src.chat_assistant import render_chat

render_chat()