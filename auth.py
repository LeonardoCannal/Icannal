"""
auth.py
Cadastro, login, controle de usuários e Painel Médico do i.cannal.

- Usa a sessão nativa do Flask pra controlar quem está logado.
- Senhas são guardadas com hash (nunca em texto puro), usando o
  werkzeug.security que já vem junto com o Flask.
- O e-mail leonardo@grupocannal.com vira administrador automaticamente
  ao se cadastrar (veja EMAILS_ADMIN mais abaixo). Todo mundo que se
  cadastra com outro e-mail é usuário comum. Para adicionar outro admin
  fixo, basta incluir o e-mail no conjunto EMAILS_ADMIN.

BANCO DE DADOS — SQLite local OU PostgreSQL externo:
- Se a variável de ambiente DATABASE_URL estiver definida (é o que o
  Render preenche sozinho quando você conecta um banco PostgreSQL ao
  serviço, ou a connection string do Supabase), o i.cannal usa esse banco
  externo — os cadastros ficam permanentes mesmo quando o serviço
  reinicia.
- Se DATABASE_URL não estiver definida (rodando local ou no .exe), o
  i.cannal usa um arquivo `cannal.db` (SQLite) do lado do programa.
- Você não precisa mudar nada no código pra trocar entre os dois — é só
  a variável de ambiente estar definida ou não.

PAINEL MÉDICO:
- Cada usuário tem sua própria lista de médicos "prospectados" e
  "visitados" — é o controle pessoal de quem ele já buscou e quem já
  visitou. A "chave" de cada médico é o CRM (quando existe) ou o nome +
  cidade (quando não tem CRM identificado), pra saber se um médico que
  aparece de novo numa busca já está no painel de alguém.
"""

import os
import re
import sqlite3
from contextlib import contextmanager
from functools import wraps
from datetime import datetime, timedelta
from flask import session, redirect, url_for, render_template
from werkzeug.security import generate_password_hash, check_password_hash

DATABASE_URL = os.environ.get("DATABASE_URL")
USANDO_POSTGRES = bool(DATABASE_URL)

if USANDO_POSTGRES:
    import psycopg2
    import psycopg2.extras
    ErroIntegridade = psycopg2.IntegrityError
else:
    ErroIntegridade = sqlite3.IntegrityError

_SQLITE_PATH = None


def inicializar_auth(app, sqlite_path):
    """
    Chama isso uma vez, logo depois de criar o app Flask.
    `sqlite_path` só é usado quando NÃO tem DATABASE_URL definida.
    """
    global _SQLITE_PATH
    _SQLITE_PATH = sqlite_path

    if USANDO_POSTGRES:
        sql_usuarios = """
            CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                cpf TEXT UNIQUE NOT NULL,
                senha_hash TEXT NOT NULL,
                is_admin BOOLEAN NOT NULL DEFAULT FALSE,
                criado_em TEXT NOT NULL,
                ultimo_login TEXT
            )
        """
        sql_painel = """
            CREATE TABLE IF NOT EXISTS painel_medicos (
                id SERIAL PRIMARY KEY,
                usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
                chave_medico TEXT NOT NULL,
                nome TEXT NOT NULL,
                crm TEXT,
                especialidade TEXT,
                cidade TEXT,
                uf TEXT,
                endereco TEXT,
                telefone TEXT,
                status TEXT NOT NULL DEFAULT 'prospectado',
                adicionado_em TEXT NOT NULL,
                UNIQUE(usuario_id, chave_medico)
            )
        """
        sql_buscas_log = """
            CREATE TABLE IF NOT EXISTS buscas_log (
                id SERIAL PRIMARY KEY,
                usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
                criado_em TEXT NOT NULL
            )
        """
        sql_solicitacoes_senha = """
            CREATE TABLE IF NOT EXISTS solicitacoes_senha (
                id SERIAL PRIMARY KEY,
                cpf TEXT NOT NULL,
                nome TEXT,
                email TEXT,
                atendido BOOLEAN NOT NULL DEFAULT FALSE,
                criado_em TEXT NOT NULL
            )
        """
        sql_visitas = """
            CREATE TABLE IF NOT EXISTS visitas_log (
                id SERIAL PRIMARY KEY,
                usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
                painel_medico_id INTEGER NOT NULL REFERENCES painel_medicos(id),
                visitado_em TEXT NOT NULL
            )
        """
        sql_agenda = """
            CREATE TABLE IF NOT EXISTS agenda_compromissos (
                id SERIAL PRIMARY KEY,
                usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
                painel_medico_id INTEGER NOT NULL REFERENCES painel_medicos(id),
                data TEXT NOT NULL,
                horario TEXT NOT NULL,
                criado_em TEXT NOT NULL,
                UNIQUE(usuario_id, data, horario)
            )
        """
    else:
        sql_usuarios = """
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                cpf TEXT UNIQUE NOT NULL,
                senha_hash TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                criado_em TEXT NOT NULL,
                ultimo_login TEXT
            )
        """
        sql_painel = """
            CREATE TABLE IF NOT EXISTS painel_medicos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
                chave_medico TEXT NOT NULL,
                nome TEXT NOT NULL,
                crm TEXT,
                especialidade TEXT,
                cidade TEXT,
                uf TEXT,
                endereco TEXT,
                telefone TEXT,
                status TEXT NOT NULL DEFAULT 'prospectado',
                adicionado_em TEXT NOT NULL,
                UNIQUE(usuario_id, chave_medico)
            )
        """
        sql_buscas_log = """
            CREATE TABLE IF NOT EXISTS buscas_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
                criado_em TEXT NOT NULL
            )
        """
        sql_solicitacoes_senha = """
            CREATE TABLE IF NOT EXISTS solicitacoes_senha (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cpf TEXT NOT NULL,
                nome TEXT,
                email TEXT,
                atendido INTEGER NOT NULL DEFAULT 0,
                criado_em TEXT NOT NULL
            )
        """
        sql_visitas = """
            CREATE TABLE IF NOT EXISTS visitas_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
                painel_medico_id INTEGER NOT NULL REFERENCES painel_medicos(id),
                visitado_em TEXT NOT NULL
            )
        """
        sql_agenda = """
            CREATE TABLE IF NOT EXISTS agenda_compromissos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
                painel_medico_id INTEGER NOT NULL REFERENCES painel_medicos(id),
                data TEXT NOT NULL,
                horario TEXT NOT NULL,
                criado_em TEXT NOT NULL,
                UNIQUE(usuario_id, data, horario)
            )
        """

    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(sql_usuarios)
        cursor.execute(sql_painel)
        cursor.execute(sql_buscas_log)
        cursor.execute(sql_solicitacoes_senha)
        cursor.execute(sql_agenda)
        cursor.execute(sql_visitas)

    # Bancos criados antes dessa versão não têm essa coluna — adiciona sem
    # quebrar se ela já existir (mesmo problema que já pegou o "cpf" antes).
    _adicionar_coluna_se_faltar("painel_medicos", "visitado_em", "TEXT")
    _adicionar_coluna_se_faltar("painel_medicos", "observacoes", "TEXT")

    # Médicos já marcados como visitados antes dessas colunas existirem ficam
    # sem data: usa a data em que entraram no painel como aproximação. Depois,
    # copia a última visita de cada médico pro histórico (só o que ainda não
    # está lá), pra os gráficos mensais não perderem visitas antigas.
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE painel_medicos SET visitado_em = adicionado_em "
            "WHERE status = 'visitado' AND visitado_em IS NULL"
        )
        cursor.execute(
            "INSERT INTO visitas_log (usuario_id, painel_medico_id, visitado_em) "
            "SELECT p.usuario_id, p.id, p.visitado_em FROM painel_medicos p "
            "WHERE p.visitado_em IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM visitas_log v WHERE v.painel_medico_id = p.id)"
        )

    @app.context_processor
    def injetar_usuario_logado():
        return {"current_user": usuario_logado()}


def _get_conn_bruta():
    if USANDO_POSTGRES:
        kwargs = {}
        if "sslmode" not in DATABASE_URL:
            kwargs["sslmode"] = "require"  # exigido pelo Postgres gerenciado do Render/Supabase
        return psycopg2.connect(
            DATABASE_URL,
            cursor_factory=psycopg2.extras.RealDictCursor,
            **kwargs,
        )
    conn = sqlite3.connect(_SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _adicionar_coluna_se_faltar(tabela: str, coluna: str, tipo_sql: str):
    """
    Adiciona uma coluna nova numa tabela que pode já existir de uma versão
    anterior do banco (sem essa coluna). Roda numa conexão própria e
    ignora silenciosamente o erro de "coluna já existe" — assim, se a
    coluna já foi criada, não quebra nada.
    """
    conn = _get_conn_bruta()
    try:
        cursor = conn.cursor()
        cursor.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo_sql}")
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


@contextmanager
def _conexao():
    """Abre uma conexão, garante commit/rollback certo e sempre fecha no final."""
    conn = _get_conn_bruta()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _q(sql: str) -> str:
    """Troca os placeholders '?' por '%s' quando o banco é PostgreSQL."""
    return sql.replace("?", "%s") if USANDO_POSTGRES else sql


# ---------------------------------------------------------------------------
# CPF
# ---------------------------------------------------------------------------

def limpar_cpf(cpf: str) -> str:
    return re.sub(r"\D", "", cpf or "")


def cpf_valido(cpf: str) -> bool:
    """Valida o CPF pelo algoritmo oficial dos dígitos verificadores."""
    cpf = limpar_cpf(cpf)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False

    soma = sum(int(cpf[i]) * (10 - i) for i in range(9))
    resto = (soma * 10) % 11
    dv1 = 0 if resto == 10 else resto
    if dv1 != int(cpf[9]):
        return False

    soma = sum(int(cpf[i]) * (11 - i) for i in range(10))
    resto = (soma * 10) % 11
    dv2 = 0 if resto == 10 else resto
    return dv2 == int(cpf[10])


def formatar_cpf(cpf: str) -> str:
    cpf = limpar_cpf(cpf)
    if len(cpf) != 11:
        return cpf
    return f"{cpf[0:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:11]}"


# ---------------------------------------------------------------------------
# Usuários
# ---------------------------------------------------------------------------

def contar_usuarios() -> int:
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) AS total FROM usuarios")
        return cursor.fetchone()["total"]


def buscar_usuario_por_email(email: str):
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("SELECT * FROM usuarios WHERE email = ?"), (email,))
        return cursor.fetchone()


def buscar_usuario_por_cpf(cpf: str):
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("SELECT * FROM usuarios WHERE cpf = ?"), (limpar_cpf(cpf),))
        return cursor.fetchone()


def buscar_usuario_por_id(user_id: int):
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("SELECT * FROM usuarios WHERE id = ?"), (user_id,))
        return cursor.fetchone()


# Só esse(s) e-mail(is) recebem admin automaticamente ao se cadastrar.
# Pra adicionar outro admin fixo depois, é só colocar o e-mail aqui.
EMAILS_ADMIN = {"leonardo@grupocannal.com"}


def criar_usuario(nome: str, email: str, cpf: str, senha: str) -> int:
    is_admin = email.strip().lower() in EMAILS_ADMIN
    cpf_limpo = limpar_cpf(cpf)
    senha_hash = generate_password_hash(senha)
    agora = datetime.utcnow().isoformat()

    with _conexao() as conn:
        cursor = conn.cursor()
        if USANDO_POSTGRES:
            cursor.execute(
                """INSERT INTO usuarios (nome, email, cpf, senha_hash, is_admin, criado_em)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                (nome, email, cpf_limpo, senha_hash, is_admin, agora),
            )
            return cursor.fetchone()["id"]
        else:
            cursor.execute(
                """INSERT INTO usuarios (nome, email, cpf, senha_hash, is_admin, criado_em)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (nome, email, cpf_limpo, senha_hash, int(is_admin), agora),
            )
            return cursor.lastrowid


def checar_senha(usuario_row, senha: str) -> bool:
    return check_password_hash(usuario_row["senha_hash"], senha)


def atualizar_ultimo_login(user_id: int):
    agora = datetime.utcnow().isoformat()
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("UPDATE usuarios SET ultimo_login = ? WHERE id = ?"), (agora, user_id))


def listar_usuarios():
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM usuarios ORDER BY criado_em DESC")
        return cursor.fetchall()


def usuario_logado():
    """Retorna a linha do usuário logado (via sessão) ou None."""
    user_id = session.get("user_id")
    if not user_id:
        return None
    return buscar_usuario_por_id(user_id)


def fazer_login(user_id: int):
    session["user_id"] = user_id
    session.permanent = True


def fazer_logout():
    session.pop("user_id", None)


def login_required(f):
    @wraps(f)
    def decorador(*args, **kwargs):
        if not usuario_logado():
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorador


def admin_required(f):
    @wraps(f)
    def decorador(*args, **kwargs):
        usuario = usuario_logado()
        if not usuario:
            return redirect(url_for("login"))
        if not usuario["is_admin"]:
            return render_template("erro_acesso.html"), 403
        return f(*args, **kwargs)
    return decorador


# ---------------------------------------------------------------------------
# Painel Médico
# ---------------------------------------------------------------------------

def computar_chave_medico(medico: dict) -> str:
    """
    Identifica um médico de forma estável entre buscas diferentes: usa o
    CRM quando dá pra confiar nele, ou nome+cidade como alternativa.
    """
    crm = (medico.get("crm") or "").strip()
    if crm and crm.lower() != "não encontrado":
        return "crm:" + re.sub(r"\s+", "", crm).lower()

    nome_norm = re.sub(r"\s+", " ", (medico.get("nome") or "").strip()).lower()
    cidade_norm = re.sub(r"\s+", " ", (medico.get("cidade") or "").strip()).lower()
    return f"nomecidade:{nome_norm}|{cidade_norm}"


def adicionar_ao_painel(usuario_id: int, medico: dict):
    """
    Adiciona um médico ao painel do usuário, como 'prospectado'.
    Cada médico só pode estar no painel de UM usuário por vez: se ele já
    está no painel de outra pessoa, a inclusão é bloqueada com uma
    mensagem específica. Retorna (ok, mensagem).
    """
    chave = computar_chave_medico(medico)
    agora = datetime.utcnow().isoformat()

    with _conexao() as conn:
        cursor = conn.cursor()

        # Checa se esse médico já está em ALGUM painel (de qualquer usuário).
        cursor.execute(
            _q("SELECT usuario_id FROM painel_medicos WHERE chave_medico = ? LIMIT 1"),
            (chave,),
        )
        existente = cursor.fetchone()
        if existente:
            if existente["usuario_id"] == usuario_id:
                return False, "Esse médico já está no seu painel."
            return False, "Médico cadastrado em outro painel médico."

        try:
            cursor.execute(
                _q("""INSERT INTO painel_medicos
                      (usuario_id, chave_medico, nome, crm, especialidade, cidade, uf, endereco, telefone, status, adicionado_em)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'prospectado', ?)"""),
                (
                    usuario_id, chave,
                    medico.get("nome", ""), medico.get("crm", ""),
                    medico.get("especialidade", ""), medico.get("cidade", ""),
                    medico.get("uf", ""), medico.get("endereco", ""),
                    medico.get("telefone", ""), agora,
                ),
            )
        except ErroIntegridade:
            return False, "Esse médico já está no seu painel."
    return True, "Adicionado ao painel médico."


def mover_no_painel(usuario_id: int, entrada_id: int, novo_status: str) -> bool:
    """Move o médico entre prospectado/visitado (o 'agendado' só nasce pelo
    botão Agendar). Cada visita fica registrada no histórico (visitas_log),
    então revisitar o mesmo médico não apaga a visita anterior das estatísticas."""
    if novo_status not in ("prospectado", "agendado", "visitado"):
        return False
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(
            _q("SELECT status FROM painel_medicos WHERE id = ? AND usuario_id = ?"),
            (entrada_id, usuario_id),
        )
        linha = cursor.fetchone()
        if not linha:
            return False
        status_atual = linha["status"]

        if novo_status == "visitado":
            if status_atual == "visitado":
                return True  # já estava visitado: não conta duas vezes
            agora = datetime.utcnow().isoformat()
            cursor.execute(
                _q("UPDATE painel_medicos SET status = ?, visitado_em = ? WHERE id = ? AND usuario_id = ?"),
                (novo_status, agora, entrada_id, usuario_id),
            )
            cursor.execute(
                _q("INSERT INTO visitas_log (usuario_id, painel_medico_id, visitado_em) VALUES (?, ?, ?)"),
                (usuario_id, entrada_id, agora),
            )
            return True

        # Desfazer uma visita (voltar pra Prospectados): tira a visita mais
        # recente do histórico e volta a "última visita" pra anterior (se houver).
        if status_atual == "visitado":
            cursor.execute(
                _q("SELECT id FROM visitas_log WHERE usuario_id = ? AND painel_medico_id = ? ORDER BY visitado_em DESC, id DESC LIMIT 1"),
                (usuario_id, entrada_id),
            )
            ultima = cursor.fetchone()
            if ultima:
                cursor.execute(_q("DELETE FROM visitas_log WHERE id = ?"), (ultima["id"],))
            cursor.execute(
                _q("SELECT MAX(visitado_em) AS anterior FROM visitas_log WHERE usuario_id = ? AND painel_medico_id = ?"),
                (usuario_id, entrada_id),
            )
            anterior = cursor.fetchone()["anterior"]
        else:
            anterior = None

        cursor.execute(
            _q("UPDATE painel_medicos SET status = ?, visitado_em = ? WHERE id = ? AND usuario_id = ?"),
            (novo_status, anterior, entrada_id, usuario_id),
        )
        return True


def bucket_painel(painel_rows):
    """Agrupa os registros do painel em 4 colunas: prospectados, agendados,
    visitados e revisitar (visitados há 60 dias ou mais)."""
    agora = datetime.utcnow()
    limite_revisitar = agora - timedelta(days=60)
    prospectados, agendados, visitados, revisitar = [], [], [], []

    for m in painel_rows:
        status = m["status"]
        if status == "prospectado":
            prospectados.append(m)
        elif status == "agendado":
            agendados.append(m)
        elif status == "visitado":
            antigo = False
            visitado_em = m["visitado_em"]
            if visitado_em:
                try:
                    antigo = datetime.fromisoformat(visitado_em) < limite_revisitar
                except ValueError:
                    pass
            (revisitar if antigo else visitados).append(m)

    return prospectados, agendados, visitados, revisitar


def listar_painel_usuario(usuario_id: int):
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(
            _q("SELECT * FROM painel_medicos WHERE usuario_id = ? ORDER BY adicionado_em DESC"),
            (usuario_id,),
        )
        return cursor.fetchall()


# ---------------------------------------------------------------------------
# Fuso horário (Brasil, UTC-3 — o país não usa horário de verão desde 2019)
# ---------------------------------------------------------------------------

FUSO_BRASIL = timedelta(hours=3)


def agora_brasil(agora_utc=None):
    """Data/hora de agora no horário de Brasília."""
    return (agora_utc or datetime.utcnow()) - FUSO_BRASIL


def hoje_brasil(agora_utc=None) -> str:
    """Data de hoje no Brasil, no formato AAAA-MM-DD."""
    return agora_brasil(agora_utc).strftime("%Y-%m-%d")


def inicio_hoje_brasil_utc(agora_utc=None) -> str:
    """Meia-noite de hoje no Brasil, convertida pra UTC (ISO). Serve pra
    comparar com os carimbos de data/hora que o banco guarda em UTC."""
    meia_noite = agora_brasil(agora_utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return (meia_noite + FUSO_BRASIL).isoformat()


def data_valida(data) -> bool:
    """True só se for uma data real, no formato exato AAAA-MM-DD."""
    if not isinstance(data, str):
        return False
    try:
        return datetime.strptime(data, "%Y-%m-%d").strftime("%Y-%m-%d") == data
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Agenda de visitas
# ---------------------------------------------------------------------------

HORARIOS_AGENDA = [
    f"{h:02d}:{m:02d}" for h in range(8, 18) for m in (0, 30)
]  # 08:00 até 17:30, de 30 em 30 min

LIMITE_AGENDA_DIAS = 730  # não deixa agendar além de ~2 anos


def _horario_ja_passou(data: str, horario: str, agora_utc=None) -> bool:
    agora = agora_brasil(agora_utc)
    hoje = agora.strftime("%Y-%m-%d")
    if data < hoje:
        return True
    if data == hoje:
        return horario <= agora.strftime("%H:%M")
    return False


def _ultimo_compromisso_id(cursor, usuario_id: int, painel_medico_id: int):
    cursor.execute(
        _q("""SELECT id FROM agenda_compromissos
              WHERE usuario_id = ? AND painel_medico_id = ?
              ORDER BY data DESC, horario DESC LIMIT 1"""),
        (usuario_id, painel_medico_id),
    )
    row = cursor.fetchone()
    return row["id"] if row else None


def listar_horarios_dia(usuario_id: int, data: str, agora_utc=None):
    """Devolve os 20 horários do dia (08:00–17:30), cada um marcado como livre,
    ocupado (com os dados do médico daquele compromisso) ou já passado."""
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(
            _q("""
                SELECT a.horario, a.painel_medico_id, p.nome, p.especialidade, p.cidade, p.uf
                FROM agenda_compromissos a
                JOIN painel_medicos p ON p.id = a.painel_medico_id
                WHERE a.usuario_id = ? AND a.data = ?
            """),
            (usuario_id, data),
        )
        ocupados = {row["horario"]: dict(row) for row in cursor.fetchall()}

    return [
        {
            "horario": h,
            "ocupado": h in ocupados,
            "passado": _horario_ja_passou(data, h, agora_utc),
            "medico": ocupados.get(h),
        }
        for h in HORARIOS_AGENDA
    ]


def agendar_medico(usuario_id: int, entrada_id: int, data: str, horario: str, agora_utc=None):
    """Marca um horário da agenda do usuário pra visitar um médico específico
    do painel dele. Se o médico já estava agendado, é um reagendamento (o
    horário antigo é liberado). Retorna (ok, mensagem)."""
    if not data_valida(data):
        return False, "Data inválida."
    if horario not in HORARIOS_AGENDA:
        return False, "Horário inválido."
    if _horario_ja_passou(data, horario, agora_utc):
        return False, "Esse horário já passou. Escolha uma data ou horário futuro."
    limite = (agora_brasil(agora_utc) + timedelta(days=LIMITE_AGENDA_DIAS)).strftime("%Y-%m-%d")
    if data > limite:
        return False, "Escolha uma data dentro dos próximos 2 anos."

    agora = datetime.utcnow().isoformat()

    with _conexao() as conn:
        cursor = conn.cursor()

        cursor.execute(
            _q("SELECT id, status FROM painel_medicos WHERE id = ? AND usuario_id = ?"),
            (entrada_id, usuario_id),
        )
        entrada = cursor.fetchone()
        if not entrada:
            return False, "Médico não encontrado no seu painel."

        cursor.execute(
            _q("SELECT id FROM agenda_compromissos WHERE usuario_id = ? AND data = ? AND horario = ?"),
            (usuario_id, data, horario),
        )
        if cursor.fetchone():
            return False, "Esse horário já está ocupado na sua agenda."

        antigo_id = None
        if entrada["status"] == "agendado":
            antigo_id = _ultimo_compromisso_id(cursor, usuario_id, entrada_id)

        try:
            cursor.execute(
                _q("""INSERT INTO agenda_compromissos (usuario_id, painel_medico_id, data, horario, criado_em)
                      VALUES (?, ?, ?, ?, ?)"""),
                (usuario_id, entrada_id, data, horario, agora),
            )
        except ErroIntegridade:
            return False, "Esse horário já está ocupado na sua agenda."

        if antigo_id is not None:
            cursor.execute(_q("DELETE FROM agenda_compromissos WHERE id = ?"), (antigo_id,))

        cursor.execute(
            _q("UPDATE painel_medicos SET status = 'agendado' WHERE id = ? AND usuario_id = ?"),
            (entrada_id, usuario_id),
        )

    return True, "Visita agendada com sucesso."


def cancelar_agendamento(usuario_id: int, entrada_id: int) -> bool:
    """Libera o horário do agendamento atual. Só age em médicos que estão como
    'agendado'; os compromissos antigos (histórico) são preservados."""
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(
            _q("SELECT status, visitado_em FROM painel_medicos WHERE id = ? AND usuario_id = ?"),
            (entrada_id, usuario_id),
        )
        linha = cursor.fetchone()
        if not linha or linha["status"] != "agendado":
            return False
        # Se o médico já tinha sido visitado antes (reagendamento de revisita),
        # cancelar devolve ele pra Visitados/Revisitar; senão, pra Prospectados.
        status_volta = "visitado" if linha["visitado_em"] else "prospectado"

        atual_id = _ultimo_compromisso_id(cursor, usuario_id, entrada_id)
        if atual_id is not None:
            cursor.execute(_q("DELETE FROM agenda_compromissos WHERE id = ?"), (atual_id,))

        cursor.execute(
            _q("UPDATE painel_medicos SET status = ? WHERE id = ? AND usuario_id = ?"),
            (status_volta, entrada_id, usuario_id),
        )
        return True


def listar_compromissos_mes(usuario_id: int, ano: int, mes: int):
    """Pros pontinhos no calendário: quantos compromissos existem em cada dia
    do mês (pra saber quais dias marcar como 'tem agenda')."""
    prefixo = f"{ano:04d}-{mes:02d}-"
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(
            _q("""
                SELECT data, COUNT(*) AS total
                FROM agenda_compromissos
                WHERE usuario_id = ? AND data LIKE ?
                GROUP BY data
            """),
            (usuario_id, prefixo + "%"),
        )
        return {row["data"]: row["total"] for row in cursor.fetchall()}


def buscar_compromisso_do_medico(usuario_id: int, painel_medico_id: int):
    """O compromisso mais recente (data/horário) desse médico, usado pra
    mostrar o selo de data no card da coluna Agendados."""
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(
            _q("""SELECT data, horario FROM agenda_compromissos
                  WHERE usuario_id = ? AND painel_medico_id = ?
                  ORDER BY data DESC, horario DESC LIMIT 1"""),
            (usuario_id, painel_medico_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Observações do médico
# ---------------------------------------------------------------------------

LIMITE_OBSERVACOES = 2000


def salvar_observacoes(usuario_id: int, entrada_id: int, texto: str) -> bool:
    texto = (texto or "")[:LIMITE_OBSERVACOES]
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(
            _q("UPDATE painel_medicos SET observacoes = ? WHERE id = ? AND usuario_id = ?"),
            (texto, entrada_id, usuario_id),
        )
        return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Alerta de inatividade (sem visitar médicos do painel há muito tempo)
# ---------------------------------------------------------------------------

def dias_sem_visitar(usuario_id: int, agora_utc=None):
    """Há quantos dias o usuário não visita ninguém do painel. Conta a partir
    da última visita; se nunca visitou, conta a partir do dia em que colocou o
    primeiro médico no painel. None se o painel dele está vazio."""
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(
            _q("SELECT MAX(visitado_em) AS ultima FROM visitas_log WHERE usuario_id = ?"),
            (usuario_id,),
        )
        ultima = (cursor.fetchone() or {"ultima": None})["ultima"]
        cursor.execute(
            _q("SELECT MIN(adicionado_em) AS primeira FROM painel_medicos WHERE usuario_id = ?"),
            (usuario_id,),
        )
        primeira = (cursor.fetchone() or {"primeira": None})["primeira"]

    referencia = ultima or primeira
    if not referencia:
        return None

    try:
        dt = datetime.fromisoformat(referencia)
    except ValueError:
        return None

    return ((agora_utc or datetime.utcnow()) - dt).days


def chaves_no_painel_usuario(usuario_id: int) -> set:
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(
            _q("SELECT chave_medico FROM painel_medicos WHERE usuario_id = ?"),
            (usuario_id,),
        )
        return {row["chave_medico"] for row in cursor.fetchall()}


def listar_usuarios_com_contagem_painel():
    """Pra tela de admin: cada usuário com quantos médicos tem no painel dele."""
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT u.id, u.nome, u.email, u.cpf, u.criado_em, u.ultimo_login, u.is_admin,
                   COUNT(p.id) AS total_painel
            FROM usuarios u
            LEFT JOIN painel_medicos p ON p.usuario_id = u.id
            GROUP BY u.id, u.nome, u.email, u.cpf, u.criado_em, u.ultimo_login, u.is_admin
            ORDER BY u.criado_em DESC
        """)
        return cursor.fetchall()


# ---------------------------------------------------------------------------
# "Esqueci minha senha" — vira notificação pro admin, sem envio automático
# ---------------------------------------------------------------------------

_ATENDIDO = True if USANDO_POSTGRES else 1
_NAO_ATENDIDO = False if USANDO_POSTGRES else 0


def registrar_solicitacao_senha(cpf: str):
    """Registra o pedido de redefinição de senha pra aparecer como
    notificação no painel admin. Tenta casar com um usuário existente
    pelo CPF pra já trazer nome/e-mail junto."""
    cpf_limpo = limpar_cpf(cpf)
    usuario = buscar_usuario_por_cpf(cpf_limpo) if cpf_limpo else None
    agora = datetime.utcnow().isoformat()

    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(
            _q("""INSERT INTO solicitacoes_senha (cpf, nome, email, atendido, criado_em)
                  VALUES (?, ?, ?, ?, ?)"""),
            (
                cpf_limpo,
                usuario["nome"] if usuario else None,
                usuario["email"] if usuario else None,
                _NAO_ATENDIDO,
                agora,
            ),
        )


def listar_solicitacoes_senha_pendentes():
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(
            _q("SELECT * FROM solicitacoes_senha WHERE atendido = ? ORDER BY criado_em DESC"),
            (_NAO_ATENDIDO,),
        )
        return cursor.fetchall()


def contar_solicitacoes_senha_pendentes() -> int:
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(
            _q("SELECT COUNT(*) AS total FROM solicitacoes_senha WHERE atendido = ?"),
            (_NAO_ATENDIDO,),
        )
        return cursor.fetchone()["total"]


def marcar_solicitacao_atendida(solicitacao_id: int) -> bool:
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(
            _q("UPDATE solicitacoes_senha SET atendido = ? WHERE id = ?"),
            (_ATENDIDO, solicitacao_id),
        )
        return cursor.rowcount > 0


def buscar_solicitacao_senha_por_id(solicitacao_id: int):
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("SELECT * FROM solicitacoes_senha WHERE id = ?"), (solicitacao_id,))
        return cursor.fetchone()


def definir_senha(usuario_id: int, nova_senha: str) -> bool:
    """Define uma senha NOVA pro usuário (usado pelo admin ao atender um
    pedido de redefinição). Nunca lê ou expõe a senha antiga — só troca."""
    senha_hash = generate_password_hash(nova_senha)
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("UPDATE usuarios SET senha_hash = ? WHERE id = ?"), (senha_hash, usuario_id))
        return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Log de buscas (pra estatística do dashboard)
# ---------------------------------------------------------------------------

def registrar_busca(usuario_id: int):
    agora = datetime.utcnow().isoformat()
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(
            _q("INSERT INTO buscas_log (usuario_id, criado_em) VALUES (?, ?)"),
            (usuario_id, agora),
        )


# ---------------------------------------------------------------------------
# Dashboard do admin
# ---------------------------------------------------------------------------

_MESES_ABREV = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]


def _inicio_do_mes(dt):
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _mes_anterior(dt):
    primeiro = _inicio_do_mes(dt)
    return _inicio_do_mes(primeiro - timedelta(days=1))


def estatisticas_dashboard():
    """Junta todos os números do Dashboard do admin numa única consulta ao
    banco (várias queries agregadas, mas uma conexão só)."""
    agora = datetime.utcnow()
    hoje_ts = inicio_hoje_brasil_utc(agora)   # meia-noite de hoje (Brasil), em UTC
    hoje_data = hoje_brasil(agora)            # AAAA-MM-DD de hoje no Brasil
    d7_str = (agora - timedelta(days=7)).isoformat()
    d30_str = (agora - timedelta(days=30)).isoformat()

    inicio_mes_atual = _inicio_do_mes(agora)
    inicio_mes_anterior = _mes_anterior(agora)
    inicio_mes_atual_str = inicio_mes_atual.isoformat()
    inicio_mes_anterior_str = inicio_mes_anterior.isoformat()

    # limites dos últimos 6 meses (do mais antigo pro atual)
    inicios_meses = [inicio_mes_atual]
    cursor_mes = inicio_mes_atual
    for _ in range(5):
        cursor_mes = _mes_anterior(cursor_mes)
        inicios_meses.append(cursor_mes)
    inicios_meses.reverse()
    fins_meses = inicios_meses[1:] + [agora]

    with _conexao() as conn:
        cursor = conn.cursor()

        def contar(sql, params=()):
            cursor.execute(_q(sql), params)
            return cursor.fetchone()["total"]

        prospectados = contar("SELECT COUNT(*) AS total FROM painel_medicos WHERE status = 'prospectado'")
        agendados = contar("SELECT COUNT(*) AS total FROM painel_medicos WHERE status = 'agendado'")
        visitados = contar("SELECT COUNT(*) AS total FROM painel_medicos WHERE status = 'visitado'")
        usuarios_total = contar("SELECT COUNT(*) AS total FROM usuarios")

        limite_revisitar_str = (agora - timedelta(days=60)).isoformat()
        revisitar_total = contar(
            "SELECT COUNT(*) AS total FROM painel_medicos WHERE status = 'visitado' AND visitado_em < ?",
            (limite_revisitar_str,),
        )

        medicos_base = contar("SELECT COUNT(DISTINCT chave_medico) AS total FROM painel_medicos")
        medicos_semana = contar(
            "SELECT COUNT(DISTINCT chave_medico) AS total FROM painel_medicos WHERE adicionado_em >= ?", (d7_str,)
        )
        medicos_mes = contar(
            "SELECT COUNT(DISTINCT chave_medico) AS total FROM painel_medicos WHERE adicionado_em >= ?", (d30_str,)
        )

        visitados_mes_atual = contar(
            "SELECT COUNT(*) AS total FROM visitas_log WHERE visitado_em >= ?",
            (inicio_mes_atual_str,),
        )
        visitados_mes_anterior = contar(
            "SELECT COUNT(*) AS total FROM visitas_log WHERE visitado_em >= ? AND visitado_em < ?",
            (inicio_mes_anterior_str, inicio_mes_atual_str),
        )
        if visitados_mes_anterior > 0:
            variacao_visitados_pct = round((visitados_mes_atual - visitados_mes_anterior) / visitados_mes_anterior * 100)
        else:
            variacao_visitados_pct = 100 if visitados_mes_atual > 0 else 0

        buscas_hoje = contar("SELECT COUNT(*) AS total FROM buscas_log WHERE criado_em >= ?", (hoje_ts,))
        buscas_7d = contar("SELECT COUNT(*) AS total FROM buscas_log WHERE criado_em >= ?", (d7_str,))
        buscas_30d = contar("SELECT COUNT(*) AS total FROM buscas_log WHERE criado_em >= ?", (d30_str,))

        ativos_hoje = contar("SELECT COUNT(*) AS total FROM usuarios WHERE ultimo_login >= ?", (hoje_ts,))
        ativos_7d = contar("SELECT COUNT(*) AS total FROM usuarios WHERE ultimo_login >= ?", (d7_str,))
        ativos_30d = contar("SELECT COUNT(*) AS total FROM usuarios WHERE ultimo_login >= ?", (d30_str,))

        cursor.execute(_q("""
            SELECT especialidade, COUNT(*) AS total
            FROM painel_medicos
            WHERE especialidade IS NOT NULL AND especialidade != ''
            GROUP BY especialidade
            ORDER BY total DESC
            LIMIT 8
        """))
        top_especialidades = [dict(r) for r in cursor.fetchall()]

        cursor.execute(_q("""
            SELECT uf, COUNT(*) AS total
            FROM painel_medicos
            WHERE uf IS NOT NULL AND uf != ''
            GROUP BY uf
            ORDER BY total DESC
        """))
        por_estado = [dict(r) for r in cursor.fetchall()]

        cursor.execute(_q("""
            SELECT cidade, uf, COUNT(*) AS total
            FROM painel_medicos
            WHERE cidade IS NOT NULL AND cidade != ''
            GROUP BY cidade, uf
            ORDER BY total DESC
            LIMIT 10
        """))
        por_cidade = [dict(r) for r in cursor.fetchall()]

        cursor.execute(_q("""
            SELECT a.data, a.horario, p.nome AS medico_nome, p.especialidade, p.cidade, p.uf, u.nome AS usuario_nome
            FROM agenda_compromissos a
            JOIN painel_medicos p ON p.id = a.painel_medico_id
            JOIN usuarios u ON u.id = a.usuario_id
            WHERE a.data >= ? AND p.status = 'agendado'
            ORDER BY a.data, a.horario
            LIMIT 15
        """), (hoje_data,))
        proximos_agendamentos = [dict(r) for r in cursor.fetchall()]

        serie_meses = []
        for inicio, fim in zip(inicios_meses, fins_meses):
            inicio_str = inicio.isoformat()
            fim_str = fim.isoformat()
            novos = contar(
                "SELECT COUNT(DISTINCT chave_medico) AS total FROM painel_medicos WHERE adicionado_em >= ? AND adicionado_em < ?",
                (inicio_str, fim_str),
            )
            visitados_no_mes = contar(
                "SELECT COUNT(*) AS total FROM visitas_log WHERE visitado_em >= ? AND visitado_em < ?",
                (inicio_str, fim_str),
            )
            serie_meses.append({
                "label": f"{_MESES_ABREV[inicio.month - 1]}/{str(inicio.year)[2:]}",
                "novos": novos,
                "visitados": visitados_no_mes,
            })

    return {
        "prospectados": prospectados,
        "agendados": agendados,
        "visitados": visitados,
        "revisitar_total": revisitar_total,
        "usuarios_total": usuarios_total,
        "medicos_base": medicos_base,
        "medicos_semana": medicos_semana,
        "medicos_mes": medicos_mes,
        "visitados_mes_atual": visitados_mes_atual,
        "visitados_mes_anterior": visitados_mes_anterior,
        "variacao_visitados_pct": variacao_visitados_pct,
        "buscas_hoje": buscas_hoje,
        "buscas_7d": buscas_7d,
        "buscas_30d": buscas_30d,
        "ativos_hoje": ativos_hoje,
        "ativos_7d": ativos_7d,
        "ativos_30d": ativos_30d,
        "top_especialidades": top_especialidades,
        "por_estado": por_estado,
        "por_cidade": por_cidade,
        "serie_meses": serie_meses,
        "proximos_agendamentos": proximos_agendamentos,
    }


def estatisticas_usuario(usuario_id: int):
    """Mesma ideia do estatisticas_dashboard(), mas só com os números de UM
    usuário — pro admin abrir o dashboard individual clicando no nome dele."""
    agora = datetime.utcnow()
    hoje_ts = inicio_hoje_brasil_utc(agora)
    hoje_data = hoje_brasil(agora)
    d7_str = (agora - timedelta(days=7)).isoformat()
    d30_str = (agora - timedelta(days=30)).isoformat()
    d60_str = (agora - timedelta(days=60)).isoformat()

    inicio_mes_atual = _inicio_do_mes(agora)
    inicio_mes_anterior = _mes_anterior(agora)
    inicio_mes_atual_str = inicio_mes_atual.isoformat()
    inicio_mes_anterior_str = inicio_mes_anterior.isoformat()

    with _conexao() as conn:
        cursor = conn.cursor()

        def contar(sql, params=()):
            cursor.execute(_q(sql), params)
            return cursor.fetchone()["total"]

        def linha_periodo(desde_str):
            prospectados = contar(
                """SELECT COUNT(DISTINCT chave_medico) AS total FROM painel_medicos
                   WHERE usuario_id = ? AND status = 'prospectado' AND adicionado_em >= ?""",
                (usuario_id, desde_str),
            )
            visitados = contar(
                "SELECT COUNT(*) AS total FROM visitas_log WHERE usuario_id = ? AND visitado_em >= ?",
                (usuario_id, desde_str),
            )
            return {"prospectados": prospectados, "visitados": visitados}

        tabela_periodos = [
            {"label": "Hoje", **linha_periodo(hoje_ts)},
            {"label": "7 dias", **linha_periodo(d7_str)},
            {"label": "30 dias", **linha_periodo(d30_str)},
            {"label": "60 dias", **linha_periodo(d60_str)},
        ]

        total_prospectados = contar(
            "SELECT COUNT(*) AS total FROM painel_medicos WHERE usuario_id = ? AND status = 'prospectado'", (usuario_id,)
        )
        total_agendados = contar(
            "SELECT COUNT(*) AS total FROM painel_medicos WHERE usuario_id = ? AND status = 'agendado'", (usuario_id,)
        )
        total_visitados = contar(
            "SELECT COUNT(*) AS total FROM painel_medicos WHERE usuario_id = ? AND status = 'visitado'", (usuario_id,)
        )
        total_revisitar = contar(
            "SELECT COUNT(*) AS total FROM painel_medicos WHERE usuario_id = ? AND status = 'visitado' AND visitado_em < ?",
            (usuario_id, d60_str),
        )

        visitados_mes_atual = contar(
            "SELECT COUNT(*) AS total FROM visitas_log WHERE usuario_id = ? AND visitado_em >= ?",
            (usuario_id, inicio_mes_atual_str),
        )
        visitados_mes_anterior = contar(
            "SELECT COUNT(*) AS total FROM visitas_log WHERE usuario_id = ? AND visitado_em >= ? AND visitado_em < ?",
            (usuario_id, inicio_mes_anterior_str, inicio_mes_atual_str),
        )
        if visitados_mes_anterior > 0:
            variacao_visitados_pct = round((visitados_mes_atual - visitados_mes_anterior) / visitados_mes_anterior * 100)
        else:
            variacao_visitados_pct = 100 if visitados_mes_atual > 0 else 0

        buscas_hoje = contar("SELECT COUNT(*) AS total FROM buscas_log WHERE usuario_id = ? AND criado_em >= ?", (usuario_id, hoje_ts))
        buscas_7d = contar("SELECT COUNT(*) AS total FROM buscas_log WHERE usuario_id = ? AND criado_em >= ?", (usuario_id, d7_str))
        buscas_30d = contar("SELECT COUNT(*) AS total FROM buscas_log WHERE usuario_id = ? AND criado_em >= ?", (usuario_id, d30_str))

        cursor.execute(_q("""
            SELECT especialidade, COUNT(*) AS total
            FROM painel_medicos
            WHERE usuario_id = ? AND especialidade IS NOT NULL AND especialidade != ''
            GROUP BY especialidade
            ORDER BY total DESC
            LIMIT 8
        """), (usuario_id,))
        top_especialidades = [dict(r) for r in cursor.fetchall()]

        cursor.execute(_q("""
            SELECT a.data, a.horario, p.nome AS medico_nome, p.especialidade, p.cidade, p.uf
            FROM agenda_compromissos a
            JOIN painel_medicos p ON p.id = a.painel_medico_id
            WHERE a.usuario_id = ? AND a.data >= ? AND p.status = 'agendado'
            ORDER BY a.data, a.horario
            LIMIT 10
        """), (usuario_id, hoje_data))
        proximos_agendamentos = [dict(r) for r in cursor.fetchall()]

    return {
        "total_prospectados": total_prospectados,
        "total_agendados": total_agendados,
        "total_visitados": total_visitados,
        "total_revisitar": total_revisitar,
        "tabela_periodos": tabela_periodos,
        "visitados_mes_atual": visitados_mes_atual,
        "visitados_mes_anterior": visitados_mes_anterior,
        "variacao_visitados_pct": variacao_visitados_pct,
        "buscas_hoje": buscas_hoje,
        "buscas_7d": buscas_7d,
        "buscas_30d": buscas_30d,
        "top_especialidades": top_especialidades,
        "proximos_agendamentos": proximos_agendamentos,
    }
