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

import hashlib
import os
import re
import secrets
import sqlite3
import threading
import time
import unicodedata
from contextlib import contextmanager
from functools import wraps
from datetime import datetime, timedelta
from flask import session, redirect, url_for, render_template, g, has_request_context
from werkzeug.security import generate_password_hash, check_password_hash

DATABASE_URL = os.environ.get("DATABASE_URL")
USANDO_POSTGRES = bool(DATABASE_URL)

if USANDO_POSTGRES:
    import psycopg2
    import psycopg2.extras
    import psycopg2.pool
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
                ultimo_login TEXT,
                telefone TEXT,
                aprovado BOOLEAN NOT NULL DEFAULT TRUE
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
                telefone_pessoal TEXT,
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
        sql_base_propria = """
            CREATE TABLE IF NOT EXISTS medicos_base_propria (
                id SERIAL PRIMARY KEY,
                chave_medico TEXT NOT NULL UNIQUE,
                nome TEXT NOT NULL,
                crm TEXT,
                especialidade TEXT NOT NULL,
                cidade TEXT,
                cidade_norm TEXT,
                uf TEXT,
                endereco TEXT,
                telefone TEXT,
                importado_em TEXT NOT NULL,
                importado_por INTEGER REFERENCES usuarios(id)
            )
        """
        sql_removidos = """
            CREATE TABLE IF NOT EXISTS medicos_removidos_inatividade (
                id SERIAL PRIMARY KEY,
                usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
                nome TEXT NOT NULL,
                crm TEXT,
                especialidade TEXT,
                cidade TEXT,
                uf TEXT,
                motivo TEXT NOT NULL,
                removido_em TEXT NOT NULL,
                lida BOOLEAN NOT NULL DEFAULT FALSE
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
                ultimo_login TEXT,
                telefone TEXT,
                aprovado INTEGER NOT NULL DEFAULT 1
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
                telefone_pessoal TEXT,
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
        sql_base_propria = """
            CREATE TABLE IF NOT EXISTS medicos_base_propria (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chave_medico TEXT NOT NULL UNIQUE,
                nome TEXT NOT NULL,
                crm TEXT,
                especialidade TEXT NOT NULL,
                cidade TEXT,
                cidade_norm TEXT,
                uf TEXT,
                endereco TEXT,
                telefone TEXT,
                importado_em TEXT NOT NULL,
                importado_por INTEGER REFERENCES usuarios(id)
            )
        """
        sql_removidos = """
            CREATE TABLE IF NOT EXISTS medicos_removidos_inatividade (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
                nome TEXT NOT NULL,
                crm TEXT,
                especialidade TEXT,
                cidade TEXT,
                uf TEXT,
                motivo TEXT NOT NULL,
                removido_em TEXT NOT NULL,
                lida INTEGER NOT NULL DEFAULT 0
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
        # Histórico PERMANENTE de visitas (pra tela "Histórico de visitas" do
        # admin). Guarda uma cópia dos dados do médico no momento da visita,
        # sem depender do painel: a visita continua no histórico mesmo se o
        # médico sair do painel depois (removido ou por inatividade).
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS historico_visitas (
                id {"SERIAL PRIMARY KEY" if USANDO_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"},
                visita_log_id INTEGER UNIQUE,
                usuario_id INTEGER NOT NULL,
                painel_medico_id INTEGER,
                visitado_em TEXT NOT NULL,
                resultado TEXT,
                medico_nome TEXT NOT NULL,
                medico_crm TEXT,
                medico_especialidade TEXT,
                medico_cidade TEXT,
                medico_uf TEXT
            )
        """)
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS alertas_clientes (
                id {"SERIAL PRIMARY KEY" if USANDO_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"},
                usuario_id INTEGER NOT NULL,
                painel_medico_id INTEGER,
                visita_log_id INTEGER,
                tipo TEXT NOT NULL,
                detalhe TEXT,
                medico_nome TEXT NOT NULL,
                medico_crm TEXT,
                criado_em TEXT NOT NULL,
                lida INTEGER NOT NULL DEFAULT 0
            )
        """)
        # Registro do que os admins fizeram. Guarda os nomes do momento, então
        # continua legível mesmo se a conta (de quem fez ou de quem sofreu a
        # ação) for excluída depois.
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS acoes_admin (
                id {"SERIAL PRIMARY KEY" if USANDO_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"},
                admin_id INTEGER,
                admin_nome TEXT NOT NULL,
                acao TEXT NOT NULL,
                alvo_usuario_id INTEGER,
                alvo_nome TEXT,
                detalhe TEXT,
                criado_em TEXT NOT NULL
            )
        """)
        # Resumo semanal: uma linha por semana (segunda-feira, horário de
        # Brasília), com os números congelados no momento em que foi gerado.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS resumos_semanais (
                semana_inicio TEXT PRIMARY KEY,
                criado_em TEXT NOT NULL,
                dados TEXT NOT NULL
            )
        """)
        # Chaves de API para outros sistemas lerem dados do i.cannal. Só o
        # hash da chave fica no banco: a chave em si aparece uma vez, ao criar.
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS api_chaves (
                id {"SERIAL PRIMARY KEY" if USANDO_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"},
                nome TEXT NOT NULL,
                prefixo TEXT NOT NULL,
                hash TEXT NOT NULL UNIQUE,
                criado_por INTEGER,
                criado_por_nome TEXT,
                criado_em TEXT NOT NULL,
                ultimo_uso_em TEXT,
                usos INTEGER NOT NULL DEFAULT 0,
                revogada_em TEXT
            )
        """)
        # Ajustes gerais do sistema (chave → valor), ex.: horário do relatório diário.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS config_sistema (
                chave TEXT PRIMARY KEY,
                valor TEXT
            )
        """)
        # Relatório diário pelo WhatsApp: um registro por envio. `chave_envio`
        # é única ("usuario:dia" no automático) — assim, com 2 processos no
        # servidor, cada promotor recebe 1 relatório por dia só.
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS relatorio_envios (
                id {"SERIAL PRIMARY KEY" if USANDO_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"},
                chave_envio TEXT NOT NULL UNIQUE,
                usuario_id INTEGER,
                usuario_nome TEXT,
                telefone TEXT,
                dia TEXT NOT NULL,
                tipo TEXT NOT NULL,
                status TEXT NOT NULL,
                detalhe TEXT,
                mensagem TEXT,
                criado_em TEXT NOT NULL,
                atualizado_em TEXT
            )
        """)
        cursor.execute(sql_base_propria)
        cursor.execute(sql_removidos)
        # Traduções de artigos (Google Cloud Translation): cada texto é
        # traduzido uma vez só e reaproveitado por todo mundo.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS traducoes (
                chave TEXT PRIMARY KEY,
                traducao TEXT NOT NULL,
                criado_em TEXT NOT NULL
            )
        """)

    # Índices: deixam rápidas as consultas que o sistema faz o tempo todo
    # (painel de cada usuário, histórico de visitas, dashboards, buscas na
    # base própria). "IF NOT EXISTS": rodar de novo não faz nada.
    for nome, tabela, colunas in [
        ("idx_painel_usuario", "painel_medicos", "usuario_id, status"),
        ("idx_painel_chave", "painel_medicos", "chave_medico"),
        ("idx_visitas_usuario_data", "visitas_log", "usuario_id, visitado_em"),
        ("idx_visitas_data", "visitas_log", "visitado_em"),
        ("idx_visitas_painel", "visitas_log", "painel_medico_id"),
        ("idx_historico_usuario_data", "historico_visitas", "usuario_id, visitado_em"),
        ("idx_historico_data", "historico_visitas", "visitado_em"),
        ("idx_alertas_lida", "alertas_clientes", "lida, criado_em"),
        ("idx_acoes_admin_data", "acoes_admin", "criado_em"),
        ("idx_relatorio_envios_criado", "relatorio_envios", "criado_em"),
        ("idx_agenda_criado", "agenda_compromissos", "criado_em"),
        ("idx_painel_adicionado", "painel_medicos", "adicionado_em"),
        # apagar/desfazer visita e excluir médico procuram por essas colunas
        ("idx_historico_visita_log", "historico_visitas", "visita_log_id"),
        ("idx_historico_painel", "historico_visitas", "painel_medico_id"),
        ("idx_alertas_visita_log", "alertas_clientes", "visita_log_id"),
        ("idx_alertas_painel", "alertas_clientes", "painel_medico_id"),
        ("idx_agenda_usuario_data", "agenda_compromissos", "usuario_id, data"),
        ("idx_agenda_painel", "agenda_compromissos", "painel_medico_id"),
        ("idx_agenda_data", "agenda_compromissos", "data"),
        ("idx_buscas_usuario_data", "buscas_log", "usuario_id, criado_em"),
        ("idx_buscas_data", "buscas_log", "criado_em"),
        ("idx_base_propria_busca", "medicos_base_propria", "especialidade, cidade_norm"),
        ("idx_removidos_usuario", "medicos_removidos_inatividade", "usuario_id, removido_em"),
        ("idx_senha_atendido", "solicitacoes_senha", "atendido"),
    ]:
        _criar_indice_se_faltar(nome, tabela, colunas)

    # Bancos criados antes dessa versão não têm essa coluna — adiciona sem
    # quebrar se ela já existir (mesmo problema que já pegou o "cpf" antes).
    _adicionar_coluna_se_faltar("painel_medicos", "visitado_em", "TEXT")
    _adicionar_coluna_se_faltar("painel_medicos", "observacoes", "TEXT")
    _adicionar_coluna_se_faltar("painel_medicos", "resultado_visita", "TEXT")
    # 2 telefones por médico: "telefone" = da clínica/consultório (o que vem
    # das buscas) e "telefone_pessoal" = celular do próprio médico.
    _adicionar_coluna_se_faltar("painel_medicos", "telefone_pessoal", "TEXT")
    # Médico "cliente" (já comprou desse promotor): marcado pelo admin, fica
    # em vermelho no painel e gera alerta pro admin quando é agendado/visitado.
    _adicionar_coluna_se_faltar("painel_medicos", "ja_comprou", "INTEGER DEFAULT 0")
    _adicionar_coluna_se_faltar("painel_medicos", "marcado_cliente_em", "TEXT")
    _adicionar_coluna_se_faltar("painel_medicos", "ultimo_pedido_em", "TEXT")  # da planilha de pedidos (AAAA-MM-DD)
    _adicionar_coluna_se_faltar("visitas_log", "resultado", "TEXT")
    _adicionar_coluna_se_faltar("usuarios", "telefone", "TEXT")
    _adicionar_coluna_se_faltar("usuarios", "relatorio_whatsapp", "TEXT")
    _adicionar_coluna_se_faltar("usuarios", "relatorio_ativo", "INTEGER DEFAULT 0")
    _adicionar_coluna_se_faltar("usuarios", "desativado", "INTEGER DEFAULT 0")
    _adicionar_coluna_se_faltar("usuarios", "desativado_em", "TEXT")
    # DEFAULT verdadeiro é de propósito: ninguém que já tinha conta fica
    # bloqueado quando essa coluna é criada — só cadastro NOVO nasce
    # precisando de aprovação (veja criar_usuario). O tipo precisa bater
    # com o da tabela criada do zero (BOOLEAN no Postgres, INTEGER no SQLite).
    _adicionar_coluna_se_faltar(
        "usuarios", "aprovado",
        "BOOLEAN NOT NULL DEFAULT TRUE" if USANDO_POSTGRES else "INTEGER NOT NULL DEFAULT 1",
    )

    # Coluna antiga, de antes da troca de nome — não é mais usada.
    _remover_coluna_se_existir("usuarios", "meta_visitas_mes")

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
        cursor.execute(
            "INSERT INTO historico_visitas (visita_log_id, usuario_id, painel_medico_id, visitado_em, resultado, "
            "medico_nome, medico_crm, medico_especialidade, medico_cidade, medico_uf) "
            "SELECT v.id, v.usuario_id, v.painel_medico_id, v.visitado_em, v.resultado, "
            "p.nome, p.crm, p.especialidade, p.cidade, p.uf "
            "FROM visitas_log v JOIN painel_medicos p ON p.id = v.painel_medico_id "
            "WHERE NOT EXISTS (SELECT 1 FROM historico_visitas h WHERE h.visita_log_id = v.id)"
        )

    @app.context_processor
    def injetar_usuario_logado():
        return {"current_user": usuario_logado()}


def _get_conn_bruta():
    if USANDO_POSTGRES:
        return psycopg2.connect(DATABASE_URL, **_kwargs_postgres())
    conn = sqlite3.connect(_SQLITE_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _criar_indice_se_faltar(nome: str, tabela: str, colunas: str):
    """Cria um índice em conexão própria — se der qualquer erro (ex.: falta
    de permissão no banco), segue sem ele: índice só acelera, nunca é
    obrigatório pro sistema funcionar."""
    conn = _get_conn_bruta()
    try:
        cursor = conn.cursor()
        cursor.execute(f"CREATE INDEX IF NOT EXISTS {nome} ON {tabela} ({colunas})")
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


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


def _remover_coluna_se_existir(tabela: str, coluna: str):
    """
    Remove uma coluna que pode ter ficado de uma versão anterior do banco
    (por exemplo, depois de renomear um campo). Só funciona em bancos
    recentes (SQLite 3.35+/Postgres já suportam); se não suportar, ignora
    silenciosamente — a coluna antiga só fica sem uso, sem quebrar nada.
    """
    conn = _get_conn_bruta()
    try:
        cursor = conn.cursor()
        cursor.execute(f"ALTER TABLE {tabela} DROP COLUMN {coluna}")
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Pool de conexões (só Postgres)
# ---------------------------------------------------------------------------
# Abrir uma conexão nova com o Supabase custa um "aperto de mão" criptografado
# a cada vez — e uma página como a de Buscar abre várias. Com o pool, cada
# processo do servidor mantém algumas conexões abertas e reaproveita.
# Pra desligar (voltar ao jeito antigo, uma conexão por consulta), defina
# DB_POOL=0 no arquivo de configuração do servidor.
POOL_ATIVO = USANDO_POSTGRES and os.environ.get("DB_POOL", "1") != "0"
POOL_MAX_CONEXOES = int(os.environ.get("DB_POOL_MAX", "4"))
POOL_TESTAR_APOS_SEG = 30  # conexão parada há mais que isso é testada antes de usar

_pool = None
_pool_pid = None
_pool_trava = threading.Lock()
_ultimo_uso = {}  # id(conn) -> momento em que foi devolvida ao pool


def _kwargs_postgres():
    kwargs = {"cursor_factory": psycopg2.extras.RealDictCursor}
    if "sslmode" not in DATABASE_URL:
        kwargs["sslmode"] = "require"  # exigido pelo Postgres gerenciado do Render/Supabase
    return kwargs


class _PoolCheio(Exception):
    pass


class _PoolDeConexoes:
    """Pool simples e próprio. O ThreadedConnectionPool do psycopg2 FECHA toda
    conexão devolvida quando o mínimo é 0 — na prática não reaproveitava nada
    e cada consulta reabria a conexão com o Supabase (~0,5 s cada vez).
    Aqui a conexão devolvida fica guardada aberta, até o limite."""

    def __init__(self, maximo: int):
        self.maximo = maximo
        self.livres = []
        self.em_uso = 0
        self._trava = threading.Lock()

    def getconn(self):
        with self._trava:
            if self.livres:
                self.em_uso += 1
                return self.livres.pop()
            if self.em_uso >= self.maximo:
                raise _PoolCheio("pool cheio")
            self.em_uso += 1
        try:
            return _get_conn_bruta()  # conecta fora da trava (é a parte demorada)
        except Exception:
            with self._trava:
                self.em_uso -= 1
            raise

    def putconn(self, conn, close=False):
        with self._trava:
            self.em_uso = max(0, self.em_uso - 1)
            if not close and not conn.closed and len(self.livres) < self.maximo:
                self.livres.append(conn)
                return
        try:
            conn.close()
        except Exception:
            pass


def _obter_pool():
    """Um pool por processo: o gunicorn cria processos separados, e uma
    conexão nunca pode ser compartilhada entre processos."""
    global _pool, _pool_pid
    pid = os.getpid()
    if _pool is None or _pool_pid != pid:
        with _pool_trava:
            if _pool is None or _pool_pid != pid:
                _pool = _PoolDeConexoes(POOL_MAX_CONEXOES)
                _pool_pid = pid
                _ultimo_uso.clear()
    return _pool


def _conexao_esta_viva(conn) -> bool:
    if conn.closed:
        return False
    parada_desde = _ultimo_uso.get(id(conn))
    if parada_desde is None:
        return True  # acabou de ser aberta: não precisa testar
    if time.time() - parada_desde < POOL_TESTAR_APOS_SEG:
        return True
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.rollback()
        return True
    except Exception:
        return False


def _pegar_conexao():
    """Devolve (conexão, veio_do_pool)."""
    if not POOL_ATIVO:
        return _get_conn_bruta(), False
    pool = _obter_pool()
    for _ in range(2):
        try:
            conn = pool.getconn()
        except _PoolCheio:
            # Pool cheio (muita coisa ao mesmo tempo): usa uma conexão avulsa
            # em vez de esperar — nunca trava a página.
            return _get_conn_bruta(), False
        if _conexao_esta_viva(conn):
            return conn, True
        try:
            pool.putconn(conn, close=True)  # morreu (o Supabase fecha conexões paradas): descarta
        except Exception:
            pass
    return _get_conn_bruta(), False


def _devolver_conexao(conn, veio_do_pool: bool, quebrada: bool = False):
    if not veio_do_pool:
        try:
            conn.close()
        except Exception:
            pass
        return
    pool = _obter_pool()
    fechar = quebrada or conn.closed
    try:
        pool.putconn(conn, close=fechar)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        return
    if fechar:
        _ultimo_uso.pop(id(conn), None)
    else:
        _ultimo_uso[id(conn)] = time.time()


@contextmanager
def _conexao():
    """Pega uma conexão (do pool, no Postgres), garante commit/rollback certo
    e sempre devolve/fecha no final."""
    conn, veio_do_pool = _pegar_conexao()
    quebrada = False
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            quebrada = True
        raise
    finally:
        _devolver_conexao(conn, veio_do_pool, quebrada)


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


def tornar_admin(usuario_id: int) -> bool:
    """Promove um usuário a admin. Só quem já é admin pode chamar isso
    (a checagem fica na rota, com @admin_required)."""
    valor = True if USANDO_POSTGRES else 1
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("UPDATE usuarios SET is_admin = ? WHERE id = ?"), (valor, usuario_id))
        return cursor.rowcount > 0


def listar_cadastros_pendentes():
    """Usuários que se cadastraram e ainda esperam um admin aprovar —
    mais recentes primeiro."""
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("""
            SELECT id, nome, email, cpf, telefone, criado_em
            FROM usuarios WHERE aprovado = ?
            ORDER BY criado_em DESC
        """), (False if USANDO_POSTGRES else 0,))
        return cursor.fetchall()


def contar_cadastros_pendentes() -> int:
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(
            _q("SELECT COUNT(*) AS total FROM usuarios WHERE aprovado = ?"),
            (False if USANDO_POSTGRES else 0,),
        )
        return cursor.fetchone()["total"]


def aprovar_usuario(usuario_id: int) -> bool:
    valor = True if USANDO_POSTGRES else 1
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("UPDATE usuarios SET aprovado = ? WHERE id = ?"), (valor, usuario_id))
        return cursor.rowcount > 0


def eh_admin_mestre(usuario) -> bool:
    """O(s) e-mail(is) em EMAILS_ADMIN são os admins "mestres" — os únicos
    que podem tirar o admin de outra pessoa. Isso evita que qualquer admin
    remova o acesso de outro admin (inclusive por engano)."""
    if not usuario:
        return False
    return (usuario["email"] or "").strip().lower() in EMAILS_ADMIN


def remover_admin(usuario_id: int) -> bool:
    """Tira o admin de alguém. Quem chama isso já devia ter sido validado
    como admin mestre antes (a checagem fica na rota)."""
    valor = False if USANDO_POSTGRES else 0
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("UPDATE usuarios SET is_admin = ? WHERE id = ?"), (valor, usuario_id))
        return cursor.rowcount > 0


# Só esse(s) e-mail(is) recebem admin automaticamente ao se cadastrar.
# Pra adicionar outro admin fixo depois, é só colocar o e-mail aqui.
EMAILS_ADMIN = {"leonardo@grupocannal.com"}


def criar_usuario(nome: str, email: str, cpf: str, senha: str, telefone: str = None) -> int:
    is_admin = email.strip().lower() in EMAILS_ADMIN
    # Admin (os e-mails fixos de EMAILS_ADMIN) não precisa de aprovação —
    # senão ninguém conseguiria aprovar ninguém na primeira vez. Todo mundo
    # mais nasce precisando que um admin valide o cadastro.
    aprovado = is_admin
    cpf_limpo = limpar_cpf(cpf)
    senha_hash = generate_password_hash(senha)
    agora = datetime.utcnow().isoformat()

    with _conexao() as conn:
        cursor = conn.cursor()
        if USANDO_POSTGRES:
            cursor.execute(
                """INSERT INTO usuarios (nome, email, cpf, senha_hash, is_admin, criado_em, telefone, aprovado)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (nome, email, cpf_limpo, senha_hash, is_admin, agora, telefone, aprovado),
            )
            return cursor.fetchone()["id"]
        else:
            cursor.execute(
                """INSERT INTO usuarios (nome, email, cpf, senha_hash, is_admin, criado_em, telefone, aprovado)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (nome, email, cpf_limpo, senha_hash, int(is_admin), agora, telefone, int(aprovado)),
            )
            return cursor.lastrowid


def banco_respondendo() -> bool:
    """Teste rápido de saúde: o banco responde a uma consulta simples?"""
    try:
        with _conexao() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return True
    except Exception:
        return False


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
    """Retorna a linha do usuário logado (via sessão) ou None. Fica guardado
    durante a requisição: a mesma página consulta isso várias vezes (decorador
    de login, menu, a própria rota) e não precisa ir ao banco em todas."""
    user_id = session.get("user_id")
    if not user_id:
        return None
    if has_request_context():
        guardado = g.get("_usuario_logado")
        if guardado is not None and guardado["id"] == user_id:
            return guardado
    usuario = buscar_usuario_por_id(user_id)
    if usuario is not None and _esta_desativado(usuario):
        usuario = None  # conta desativada: a sessão aberta deixa de valer
    if has_request_context():
        g._usuario_logado = usuario
    return usuario


def _esta_desativado(usuario) -> bool:
    try:
        return bool(usuario["desativado"])
    except (KeyError, IndexError):
        return False


def _esquecer_usuario_da_requisicao():
    if has_request_context():
        g.pop("_usuario_logado", None)


def fazer_login(user_id: int):
    session.clear()  # sessão nova a cada login (evita reaproveitar uma sessão antiga)
    session["user_id"] = user_id
    session.permanent = True
    _esquecer_usuario_da_requisicao()


def fazer_logout():
    session.clear()
    _esquecer_usuario_da_requisicao()


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


def _normalizar_cidade(cidade: str) -> str:
    """Tira acento, deixa minúsculo — pra 'São Paulo' e 'Sao paulo' darem
    o mesmo resultado na hora de comparar/filtrar."""
    sem_acento = unicodedata.normalize("NFKD", cidade or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", sem_acento.strip().lower())


LIMITE_IMPORTACAO_BASE = 20000


def importar_base_propria(admin_usuario_id: int, linhas: list, slugs_validos: set):
    """
    Importa uma planilha de médicos (comprada de terceiros, por exemplo)
    pra base própria do sistema — fica disponível pra qualquer busca, sem
    depender dos sites externos. Se o médico (pelo CRM, ou nome+cidade) já
    estava na base, atualiza os dados em vez de duplicar.

    `linhas`: lista de dicts com nome, crm, especialidade (slug), cidade,
    uf, endereco, telefone. `slugs_validos`: o conjunto de slugs de
    especialidade que o sistema reconhece (pra recusar linha com
    especialidade desconhecida, em vez de aceitar qualquer texto).
    Devolve um resumo: quantos entraram/atualizaram, e os erros por linha.
    """
    if len(linhas) > LIMITE_IMPORTACAO_BASE:
        return {
            "adicionados": 0, "atualizados": 0, "total": len(linhas),
            "erros": [{"linha": 0, "motivo": f"Máximo de {LIMITE_IMPORTACAO_BASE} médicos por arquivo."}],
        }

    agora = datetime.utcnow().isoformat()
    adicionados = 0
    atualizados = 0
    erros = []

    with _conexao() as conn:
        cursor = conn.cursor()
        for i, linha in enumerate(linhas, start=2):  # linha 1 do arquivo é o cabeçalho
            nome = (linha.get("nome") or "").strip()
            especialidade = (linha.get("especialidade") or "").strip().lower()
            cidade = (linha.get("cidade") or "").strip()
            crm = (linha.get("crm") or "").strip()
            uf = (linha.get("uf") or "").strip().upper()
            endereco = (linha.get("endereco") or "").strip()
            telefone = (linha.get("telefone") or "").strip()

            if not nome:
                erros.append({"linha": i, "motivo": "Sem nome — linha ignorada."})
                continue
            if especialidade not in slugs_validos:
                erros.append({"linha": i, "motivo": f"{nome}: especialidade \"{especialidade}\" não reconhecida."})
                continue

            chave = computar_chave_medico({"crm": crm, "nome": nome, "cidade": cidade})

            cursor.execute(_q("SELECT id FROM medicos_base_propria WHERE chave_medico = ?"), (chave,))
            existente = cursor.fetchone()

            if existente:
                cursor.execute(
                    _q("""UPDATE medicos_base_propria
                          SET nome = ?, crm = ?, especialidade = ?, cidade = ?, cidade_norm = ?,
                              uf = ?, endereco = ?, telefone = ?, importado_em = ?, importado_por = ?
                          WHERE id = ?"""),
                    (nome, crm, especialidade, cidade, _normalizar_cidade(cidade), uf, endereco, telefone,
                     agora, admin_usuario_id, existente["id"]),
                )
                atualizados += 1
            else:
                try:
                    cursor.execute(
                        _q("""INSERT INTO medicos_base_propria
                              (chave_medico, nome, crm, especialidade, cidade, cidade_norm, uf, endereco, telefone, importado_em, importado_por)
                              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""),
                        (chave, nome, crm, especialidade, cidade, _normalizar_cidade(cidade), uf, endereco, telefone,
                         agora, admin_usuario_id),
                    )
                    adicionados += 1
                except ErroIntegridade:
                    erros.append({"linha": i, "motivo": f"{nome}: já existe na base (conflito ao salvar)."})

    return {"adicionados": adicionados, "atualizados": atualizados, "total": len(linhas), "erros": erros}


def buscar_base_propria(especialidade_slugs: list, cidade: str, uf: str, nomes_especialidades: dict):
    """
    Busca médicos na base própria (importada) que batem com a especialidade
    e a cidade escolhidas na tela de busca. Devolve no mesmo formato que os
    resultados dos sites externos, pra entrar junto na mesma lista.
    `nomes_especialidades`: dict slug -> nome de exibição (ex.: "cardiologista" -> "Cardiologista").
    """
    if not especialidade_slugs:
        return []

    cidade_norm = _normalizar_cidade(cidade)
    marcadores = ",".join(["?"] * len(especialidade_slugs))

    with _conexao() as conn:
        cursor = conn.cursor()
        if uf:
            cursor.execute(
                _q(f"""SELECT * FROM medicos_base_propria
                       WHERE especialidade IN ({marcadores}) AND cidade_norm = ? AND UPPER(uf) = ?"""),
                (*especialidade_slugs, cidade_norm, uf.upper()),
            )
        else:
            cursor.execute(
                _q(f"""SELECT * FROM medicos_base_propria
                       WHERE especialidade IN ({marcadores}) AND cidade_norm = ?"""),
                (*especialidade_slugs, cidade_norm),
            )
        linhas = cursor.fetchall()

    resultado = []
    for linha in linhas:
        resultado.append({
            "nome": linha["nome"],
            "crm": linha["crm"] or "Não encontrado",
            "cidade": linha["cidade"] or "Não encontrado",
            "uf": linha["uf"] or "Não encontrado",
            "endereco": linha["endereco"] or "Não encontrado",
            "telefone": linha["telefone"] or "Não disponível",
            "especialidade": nomes_especialidades.get(linha["especialidade"], linha["especialidade"]),
            "perfil_url": "Não encontrado",
            "fonte": "Base própria",
        })
    return resultado


def buscar_base_propria_por_nome(nome: str, cidade: str, uf: str, nomes_especialidades: dict, limite: int = 60):
    """Busca na base própria pelo nome do médico (cidade/UF opcionais).
    O banco filtra pela palavra mais longa digitada — com '_' no lugar das
    vogais e do 'c', pra 'joao' achar 'João' — e aqui confere se TODAS as
    palavras batem (sem acento, em qualquer ordem)."""
    from scraper import nome_bate, normalizar
    palavras = [p for p in normalizar(nome or "").split() if len(p) >= 2]
    if not palavras:
        return []
    maior = max(palavras, key=len)
    padrao = "%" + re.sub(r"[aeiouc]", "_", maior) + "%"
    filtros, valores = ["LOWER(nome) LIKE ?"], [padrao]
    if (cidade or "").strip():
        filtros.append("cidade_norm = ?")
        valores.append(_normalizar_cidade(cidade))
    if (uf or "").strip():
        filtros.append("UPPER(uf) = ?")
        valores.append(uf.strip().upper())
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q(f"SELECT * FROM medicos_base_propria WHERE {' AND '.join(filtros)} LIMIT 500"), tuple(valores))
        linhas = [l for l in cursor.fetchall() if nome_bate(l["nome"], nome)]
    return [{
        "nome": l["nome"],
        "crm": l["crm"] or "Não encontrado",
        "cidade": l["cidade"] or "Não encontrado",
        "uf": l["uf"] or "Não encontrado",
        "endereco": l["endereco"] or "Não encontrado",
        "telefone": l["telefone"] or "Não disponível",
        "especialidade": nomes_especialidades.get(l["especialidade"], l["especialidade"]),
        "perfil_url": "Não encontrado",
        "fonte": "Base própria",
    } for l in linhas[:limite]]


def contar_base_propria():
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) AS total FROM medicos_base_propria")
        return cursor.fetchone()["total"]


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
                      (usuario_id, chave_medico, nome, crm, especialidade, cidade, uf, endereco, telefone,
                       telefone_pessoal, status, adicionado_em)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'prospectado', ?)"""),
                (
                    usuario_id, chave,
                    medico.get("nome", ""), medico.get("crm", ""),
                    medico.get("especialidade", ""), medico.get("cidade", ""),
                    medico.get("uf", ""), medico.get("endereco", ""),
                    medico.get("telefone", ""), (medico.get("telefone_pessoal") or "").strip()[:40], agora,
                ),
            )
        except ErroIntegridade:
            return False, "Esse médico já está no seu painel."
    return True, "Adicionado ao painel médico."


LIMITE_IMPORTACAO_PAINEL_ADMIN = 5000


def admin_importar_painel_usuario(usuario_alvo_id: int, linhas: list):
    """
    O admin insere vários médicos de uma vez direto no painel de um usuário
    específico (planilha de leads comprados, por exemplo). Usa a mesma regra
    de sempre: nome é obrigatório, e um médico que já está em outro painel
    (do mesmo usuário ou de outro) não entra duas vezes. Não interrompe no
    meio — cada linha com problema é reportada, e o resto continua.
    """
    if len(linhas) > LIMITE_IMPORTACAO_PAINEL_ADMIN:
        return {
            "adicionados": 0, "total": len(linhas),
            "erros": [{"linha": 0, "motivo": f"Máximo de {LIMITE_IMPORTACAO_PAINEL_ADMIN} médicos por arquivo."}],
        }

    adicionados = 0
    erros = []
    for i, linha in enumerate(linhas, start=2):  # linha 1 do arquivo é o cabeçalho
        medico = {
            campo: (linha.get(campo) or "").strip()
            for campo in ("nome", "crm", "especialidade", "cidade", "uf", "endereco", "telefone", "telefone_pessoal")
        }
        if not medico["nome"]:
            erros.append({"linha": i, "motivo": "Sem nome — linha ignorada."})
            continue
        ok, mensagem = adicionar_ao_painel(usuario_alvo_id, medico)
        if ok:
            adicionados += 1
        else:
            erros.append({"linha": i, "motivo": f"{medico['nome']}: {mensagem}"})

    return {"adicionados": adicionados, "total": len(linhas), "erros": erros}


RESULTADOS_VISITA = {
    "interessado": "Interessado",
    "sem_interesse": "Sem interesse",
    "pedir_retorno": "Pedir retorno",
    "amostra_entregue": "Amostra entregue",
}


def _alertar_se_cliente(cursor, usuario_id: int, entrada_id: int, tipo: str, detalhe: str = "", visita_log_id=None):
    """Se o médico é "cliente" (marcado pelo admin), registra um alerta pro
    admin: o promotor agendou ou visitou esse médico."""
    cursor.execute(
        _q("SELECT nome, crm, ja_comprou FROM painel_medicos WHERE id = ? AND usuario_id = ?"),
        (entrada_id, usuario_id),
    )
    medico = cursor.fetchone()
    if not medico or not medico["ja_comprou"]:
        return
    cursor.execute(
        _q("""INSERT INTO alertas_clientes (usuario_id, painel_medico_id, visita_log_id, tipo, detalhe,
                  medico_nome, medico_crm, criado_em, lida)
              VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)"""),
        (usuario_id, entrada_id, visita_log_id, tipo, detalhe or "", medico["nome"], medico["crm"] or "",
         datetime.utcnow().isoformat()),
    )


def marcar_medico_cliente(usuario_id: int, entrada_id: int, cliente: bool) -> bool:
    """Admin marca/desmarca um médico do painel do promotor como "cliente"
    (já comprou dele)."""
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(
            _q("UPDATE painel_medicos SET ja_comprou = ?, marcado_cliente_em = ? WHERE id = ? AND usuario_id = ?"),
            (1 if cliente else 0, datetime.utcnow().isoformat() if cliente else None, entrada_id, usuario_id),
        )
        return cursor.rowcount > 0


def listar_painel_todos_resumo():
    """Todos os médicos de todos os painéis (só o necessário pra casar com a
    planilha de pedidos)."""
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, usuario_id, nome, crm, ja_comprou FROM painel_medicos")
        return [dict(l) for l in cursor.fetchall()]


def listar_painel_todos_completo():
    """Todos os médicos de todos os painéis, com o nome do dono (pra revisão
    de duplicados do admin)."""
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT p.id, p.usuario_id, u.nome AS usuario_nome, p.nome, p.crm, p.especialidade, p.cidade, p.uf,
                   p.endereco, p.telefone, p.telefone_pessoal, p.status, p.ja_comprou, p.adicionado_em, p.visitado_em
            FROM painel_medicos p LEFT JOIN usuarios u ON u.id = p.usuario_id
        """)
        return [dict(l) for l in cursor.fetchall()]


def listar_todos_medicos_relatorio():
    """Relatório geral do admin: todos os médicos de todos os painéis, com o
    promotor, a situação, a última visita e o próximo agendamento."""
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT p.id, p.usuario_id, u.nome AS usuario_nome, p.nome, p.crm, p.especialidade, p.cidade, p.uf,
                   p.endereco, p.telefone, p.telefone_pessoal, p.status, p.ja_comprou, p.ultimo_pedido_em,
                   p.adicionado_em, p.visitado_em, p.resultado_visita, p.observacoes,
                   (SELECT MIN(a.data || ' ' || a.horario) FROM agenda_compromissos a
                     WHERE a.painel_medico_id = p.id) AS agendado_para,
                   (SELECT COUNT(*) FROM historico_visitas h WHERE h.painel_medico_id = p.id) AS total_visitas
            FROM painel_medicos p LEFT JOIN usuarios u ON u.id = p.usuario_id
            ORDER BY u.nome, p.nome
        """)
        return [dict(l) for l in cursor.fetchall()]


def apagar_medicos_paineis_admin(ids: list) -> int:
    """Admin apaga médicos de qualquer painel (revisão de duplicados). As
    visitas já feitas continuam no histórico. Devolve quantos apagou."""
    apagados = 0
    with _conexao() as conn:
        cursor = conn.cursor()
        for entrada_id in ids:
            cursor.execute(_q("SELECT id FROM painel_medicos WHERE id = ?"), (entrada_id,))
            if cursor.fetchone():
                _excluir_medico_do_painel(cursor, entrada_id)
                apagados += 1
    return apagados


def aplicar_importacao_clientes(acoes: list, medico_para_painel):
    """Grava o plano da importação de clientes numa transação só:
    - 'marcar' / 'ja_cliente': marca como cliente e atualiza o último pedido;
    - 'adicionar': coloca o médico no painel do promotor, já como cliente.
    Devolve {"marcados", "atualizados", "adicionados", "pulados"}."""
    agora = datetime.utcnow().isoformat()
    resultado = {"marcados": 0, "atualizados": 0, "adicionados": 0, "pulados": 0}
    with _conexao() as conn:
        cursor = conn.cursor()
        for acao in acoes:
            if acao["tipo"] in ("marcar", "ja_cliente"):
                cursor.execute(
                    _q("""UPDATE painel_medicos
                          SET ja_comprou = 1,
                              marcado_cliente_em = COALESCE(marcado_cliente_em, ?),
                              ultimo_pedido_em = COALESCE(?, ultimo_pedido_em)
                          WHERE id = ? AND usuario_id = ?"""),
                    (agora, acao["ultimo_pedido"], acao["entrada"]["id"], acao["usuario_id"]),
                )
                resultado["marcados" if acao["tipo"] == "marcar" else "atualizados"] += 1
            elif acao["tipo"] == "adicionar":
                medico = medico_para_painel(acao["linha"], acao["registro"])
                chave = computar_chave_medico(medico)
                cursor.execute(_q("SELECT 1 FROM painel_medicos WHERE chave_medico = ? LIMIT 1"), (chave,))
                if cursor.fetchone():
                    resultado["pulados"] += 1
                    continue
                cursor.execute(
                    _q("""INSERT INTO painel_medicos
                          (usuario_id, chave_medico, nome, crm, especialidade, cidade, uf, endereco, telefone,
                           telefone_pessoal, status, adicionado_em, ja_comprou, marcado_cliente_em, ultimo_pedido_em)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', 'prospectado', ?, 1, ?, ?)"""),
                    (acao["usuario_id"], chave, medico["nome"], medico["crm"], medico["especialidade"],
                     medico["cidade"], medico["uf"], medico["endereco"], medico["telefone"],
                     agora, agora, acao["ultimo_pedido"]),
                )
                resultado["adicionados"] += 1
    return resultado


def listar_alertas_clientes(limite: int = 30):
    """Alertas pro sino do admin (mais recentes primeiro), com o nome do promotor."""
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("""
            SELECT a.*, u.nome AS usuario_nome
            FROM alertas_clientes a
            LEFT JOIN usuarios u ON u.id = a.usuario_id
            ORDER BY a.criado_em DESC, a.id DESC
            LIMIT ?
        """), (limite,))
        return [dict(l) for l in cursor.fetchall()]


def contar_alertas_clientes_nao_lidos() -> int:
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) AS total FROM alertas_clientes WHERE lida = 0")
        return cursor.fetchone()["total"]


def marcar_alertas_clientes_lidos():
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE alertas_clientes SET lida = 1 WHERE lida = 0")


def mover_no_painel(usuario_id: int, entrada_id: int, novo_status: str, resultado: str = None) -> bool:
    """Move o médico entre prospectado/visitado (o 'agendado' só nasce pelo
    botão Agendar). Cada visita fica registrada no histórico (visitas_log),
    com o resultado daquela visita, então revisitar o mesmo médico não apaga
    a visita anterior nem o resultado dela das estatísticas."""
    if novo_status not in ("prospectado", "agendado", "visitado"):
        return False
    if resultado is not None and resultado not in RESULTADOS_VISITA:
        return False
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(
            _q("SELECT status, visitado_em FROM painel_medicos WHERE id = ? AND usuario_id = ?"),
            (entrada_id, usuario_id),
        )
        linha = cursor.fetchone()
        if not linha:
            return False
        status_atual = linha["status"]

        if novo_status == "visitado":
            if status_atual == "visitado":
                # Já estava visitado: não conta duas vezes — a não ser que já
                # esteja na coluna "Revisitar" (visita antiga), quando marcar
                # de novo é uma visita nova de verdade.
                limite = (datetime.utcnow() - timedelta(days=DIAS_PARA_REVISITAR)).isoformat()
                if not (linha["visitado_em"] and linha["visitado_em"] < limite):
                    return True
            agora = datetime.utcnow().isoformat()
            cursor.execute(
                _q("UPDATE painel_medicos SET status = ?, visitado_em = ?, resultado_visita = ? WHERE id = ? AND usuario_id = ?"),
                (novo_status, agora, resultado, entrada_id, usuario_id),
            )
            cursor.execute(
                _q("INSERT INTO visitas_log (usuario_id, painel_medico_id, visitado_em, resultado) VALUES (?, ?, ?, ?)"),
                (usuario_id, entrada_id, agora, resultado),
            )
            cursor.execute(
                _q("""SELECT id FROM visitas_log WHERE usuario_id = ? AND painel_medico_id = ? AND visitado_em = ?
                      ORDER BY id DESC LIMIT 1"""),
                (usuario_id, entrada_id, agora),
            )
            log_id = cursor.fetchone()["id"]
            cursor.execute(
                _q("""INSERT INTO historico_visitas (visita_log_id, usuario_id, painel_medico_id, visitado_em, resultado,
                          medico_nome, medico_crm, medico_especialidade, medico_cidade, medico_uf)
                      SELECT ?, ?, id, ?, ?, nome, crm, especialidade, cidade, uf FROM painel_medicos WHERE id = ?"""),
                (log_id, usuario_id, agora, resultado, entrada_id),
            )
            _alertar_se_cliente(cursor, usuario_id, entrada_id, "visitado",
                                RESULTADOS_VISITA.get(resultado or "", ""), visita_log_id=log_id)
            return True

        # Desfazer uma visita (voltar pra Prospectados): tira a visita mais
        # recente do histórico e volta a "última visita" pra anterior (se houver).
        if status_atual == "visitado":
            cursor.execute(
                _q("SELECT id, resultado FROM visitas_log WHERE usuario_id = ? AND painel_medico_id = ? ORDER BY visitado_em DESC, id DESC LIMIT 1"),
                (usuario_id, entrada_id),
            )
            ultima = cursor.fetchone()
            if ultima:
                cursor.execute(_q("DELETE FROM historico_visitas WHERE visita_log_id = ?"), (ultima["id"],))
                cursor.execute(_q("DELETE FROM alertas_clientes WHERE visita_log_id = ?"), (ultima["id"],))
                cursor.execute(_q("DELETE FROM visitas_log WHERE id = ?"), (ultima["id"],))
            cursor.execute(
                _q("""SELECT visitado_em, resultado FROM visitas_log
                      WHERE usuario_id = ? AND painel_medico_id = ?
                      ORDER BY visitado_em DESC LIMIT 1"""),
                (usuario_id, entrada_id),
            )
            anterior_linha = cursor.fetchone()
            anterior = anterior_linha["visitado_em"] if anterior_linha else None
            resultado_anterior = anterior_linha["resultado"] if anterior_linha else None
        else:
            anterior = None
            resultado_anterior = None

        cursor.execute(
            _q("UPDATE painel_medicos SET status = ?, visitado_em = ?, resultado_visita = ? WHERE id = ? AND usuario_id = ?"),
            (novo_status, anterior, resultado_anterior, entrada_id, usuario_id),
        )
        return True


DIAS_PARA_REVISITAR = 60  # dias sem visita pra um médico visitado cair em "Revisitar"


def bucket_painel(painel_rows):
    """Agrupa os registros do painel em 4 colunas: prospectados, agendados,
    visitados e revisitar (visitados há 60 dias ou mais)."""
    agora = datetime.utcnow()
    limite_revisitar = agora - timedelta(days=DIAS_PARA_REVISITAR)
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
# Remover médico do painel (botão "x" ou admin)
# ---------------------------------------------------------------------------


def _excluir_medico_do_painel(cursor, entrada_id: int):
    """Apaga um médico do painel por completo: o próprio registro e tudo que
    aponta pra ele (agenda e histórico de visitas). Só o SQL — quem chama
    decide se guarda algum registro antes (pro caso da exclusão automática)."""
    cursor.execute(_q("DELETE FROM agenda_compromissos WHERE painel_medico_id = ?"), (entrada_id,))
    cursor.execute(_q("UPDATE historico_visitas SET painel_medico_id = NULL, visita_log_id = NULL WHERE painel_medico_id = ?"), (entrada_id,))
    cursor.execute(_q("UPDATE alertas_clientes SET painel_medico_id = NULL, visita_log_id = NULL WHERE painel_medico_id = ?"), (entrada_id,))
    cursor.execute(_q("DELETE FROM visitas_log WHERE painel_medico_id = ?"), (entrada_id,))
    cursor.execute(_q("DELETE FROM painel_medicos WHERE id = ?"), (entrada_id,))


def remover_medico_painel(usuario_id: int, entrada_id: int) -> bool:
    """Remoção manual (o botão "x") — o usuário (ou o admin, em nome de um
    usuário) tira um médico do painel de propósito. Não gera notificação:
    isso é só pra remoção automática por inatividade."""
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(
            _q("SELECT id FROM painel_medicos WHERE id = ? AND usuario_id = ?"),
            (entrada_id, usuario_id),
        )
        if not cursor.fetchone():
            return False
        _excluir_medico_do_painel(cursor, entrada_id)
        return True


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
                SELECT a.horario, a.painel_medico_id, p.nome, p.especialidade, p.cidade, p.uf,
                       p.status, p.resultado_visita, p.ja_comprou, p.visitado_em
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
        ano, mes, dia = data.split("-")
        _alertar_se_cliente(cursor, usuario_id, entrada_id, "reagendado" if antigo_id is not None else "agendado",
                            f"{dia}/{mes}/{ano} às {horario}")

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


def acrescentar_observacao(usuario_id: int, entrada_id: int, texto: str):
    """Junta uma nota nova no FIM das observações, com a data (Brasília) na
    frente — usado pela nota rápida depois da visita. Se passar do limite,
    as notas mais antigas (do começo) é que saem."""
    texto = " ".join((texto or "").split()) if "\n" not in (texto or "") else (texto or "").strip()
    if not texto:
        return False, None
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("SELECT observacoes FROM painel_medicos WHERE id = ? AND usuario_id = ?"),
                       (entrada_id, usuario_id))
        linha = cursor.fetchone()
        if not linha:
            return False, None
        atual = (linha["observacoes"] or "").rstrip()
        nota = f"{agora_brasil().strftime('%d/%m/%Y')} — {texto}"
        novo = (atual + "\n\n" + nota) if atual else nota
        if len(novo) > LIMITE_OBSERVACOES:
            novo = novo[-LIMITE_OBSERVACOES:]
        cursor.execute(_q("UPDATE painel_medicos SET observacoes = ? WHERE id = ? AND usuario_id = ?"),
                       (novo, entrada_id, usuario_id))
        return True, novo


UFS_VALIDAS = {"AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA",
               "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"}


def editar_dados_medico_painel(usuario_id: int, entrada_id: int, dados: dict):
    """O usuário corrige/atualiza os 2 telefones (clínica e pessoal), endereço, cidade e UF de um médico
    do PRÓPRIO painel. Nome, CRM e especialidade não mudam (é por eles que o
    sistema reconhece o médico). Devolve (ok, mensagem_de_erro)."""
    def _limpo(campo, limite):
        return re.sub(r"\s+", " ", str(dados.get(campo) or "")).strip()[:limite]

    telefone = _limpo("telefone", 40)
    telefone_pessoal = _limpo("telefone_pessoal", 40)
    endereco = _limpo("endereco", 200)
    cidade = _limpo("cidade", 80)
    uf = _limpo("uf", 2).upper()

    if telefone and len(re.sub(r"\D", "", telefone)) < 8:
        return False, "Telefone da clínica incompleto: confira o DDD e o número."
    if telefone_pessoal and len(re.sub(r"\D", "", telefone_pessoal)) < 8:
        return False, "Telefone pessoal incompleto: confira o DDD e o número."
    if uf and uf not in UFS_VALIDAS:
        return False, "UF inválida."
    if not cidade:
        return False, "Informe a cidade."

    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(
            _q("""UPDATE painel_medicos SET telefone = ?, telefone_pessoal = ?, endereco = ?, cidade = ?, uf = ?
                  WHERE id = ? AND usuario_id = ?"""),
            (telefone or "Não disponível", telefone_pessoal, endereco or "Não encontrado", cidade, uf, entrada_id, usuario_id),
        )
        if cursor.rowcount == 0:
            return False, "Médico não encontrado no seu painel."
    return True, ""


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
            _q("SELECT (SELECT MAX(visitado_em) FROM visitas_log WHERE usuario_id = ?) AS ultima, "
               "(SELECT MIN(adicionado_em) FROM painel_medicos WHERE usuario_id = ?) AS primeira"),
            (usuario_id, usuario_id),
        )
        linha = cursor.fetchone()
        ultima, primeira = linha["ultima"], linha["primeira"]

    referencia = ultima or primeira
    if not referencia:
        return None

    try:
        dt = datetime.fromisoformat(referencia)
    except ValueError:
        return None

    return ((agora_utc or datetime.utcnow()) - dt).days


def listar_historico_visitas(usuario_id=None, de_utc=None, ate_utc=None, resultado=None, limite=1000):
    """Visitas registradas (mais recentes primeiro), com o nome de quem
    visitou. Filtros opcionais: usuário, período (ISO em UTC, [de, até)) e
    resultado ('sem_resultado' = visitas sem resultado informado)."""
    filtros, valores = [], []
    if usuario_id:
        filtros.append("h.usuario_id = ?"); valores.append(int(usuario_id))
    if de_utc:
        filtros.append("h.visitado_em >= ?"); valores.append(de_utc)
    if ate_utc:
        filtros.append("h.visitado_em < ?"); valores.append(ate_utc)
    if resultado == "sem_resultado":
        filtros.append("(h.resultado IS NULL OR h.resultado = '')")
    elif resultado:
        filtros.append("h.resultado = ?"); valores.append(resultado)
    onde = ("WHERE " + " AND ".join(filtros)) if filtros else ""
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q(f"""
            SELECT h.id, h.usuario_id, u.nome AS usuario_nome, h.visitado_em, h.resultado,
                   h.medico_nome, h.medico_crm, h.medico_especialidade, h.medico_cidade, h.medico_uf,
                   h.painel_medico_id
            FROM historico_visitas h
            LEFT JOIN usuarios u ON u.id = h.usuario_id
            {onde}
            ORDER BY h.visitado_em DESC, h.id DESC
            LIMIT {int(limite) + 1}
        """), tuple(valores))
        linhas = [dict(l) for l in cursor.fetchall()]
    return linhas[:limite], len(linhas) > limite


# ---------------------------------------------------------------------------
# Roteiro do dia (visitas de hoje, em ordem, prontas pro Google Maps)
# ---------------------------------------------------------------------------

def roteiro_do_dia(usuario_id: int, data: str):
    """As visitas agendadas de um dia, em ordem de horário, já com o endereço
    pronto pra virar um roteiro no Google Maps."""
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(
            _q("""
                SELECT a.horario, p.id AS painel_medico_id, p.nome, p.especialidade,
                       p.endereco, p.cidade, p.uf, p.telefone
                FROM agenda_compromissos a
                JOIN painel_medicos p ON p.id = a.painel_medico_id
                WHERE a.usuario_id = ? AND a.data = ?
                ORDER BY a.horario
            """),
            (usuario_id, data),
        )
        return [dict(r) for r in cursor.fetchall()]


def contar_visitas_amanha(usuario_id: int, agora_utc=None) -> int:
    """Quantas visitas o usuário tem agendadas pra amanhã (horário do Brasil).
    Usado pro lembrete do dia anterior."""
    amanha = (agora_brasil(agora_utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(
            _q("SELECT COUNT(*) AS total FROM agenda_compromissos WHERE usuario_id = ? AND data = ?"),
            (usuario_id, amanha),
        )
        return cursor.fetchone()["total"]


# ---------------------------------------------------------------------------
# Ranking entre promotores
# ---------------------------------------------------------------------------

def ranking_visitas_mes(agora_utc=None, limite: int = 10):
    """Quem mais visitou médicos este mês — pro ranking do dashboard geral."""
    agora = agora_utc or datetime.utcnow()
    inicio_mes = _inicio_do_mes(agora).isoformat()
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("""
            SELECT u.id, u.nome, COUNT(v.id) AS total_visitas
            FROM usuarios u
            LEFT JOIN visitas_log v ON v.usuario_id = u.id AND v.visitado_em >= ?
            GROUP BY u.id, u.nome
            ORDER BY total_visitas DESC, u.nome ASC
            LIMIT ?
        """), (inicio_mes, limite))
        return [dict(r) for r in cursor.fetchall()]


def chaves_no_painel_usuario(usuario_id: int) -> set:
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(
            _q("SELECT chave_medico FROM painel_medicos WHERE usuario_id = ?"),
            (usuario_id,),
        )
        return {row["chave_medico"] for row in cursor.fetchall()}


def chaves_no_painel_todos() -> set:
    """Todas as chaves de médico que estão em QUALQUER painel, de QUALQUER
    usuário — usada na busca pra esconder médico que já é de outra pessoa."""
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT chave_medico FROM painel_medicos")
        return {row["chave_medico"] for row in cursor.fetchall()}


def _tabela_existe(cursor, tabela: str) -> bool:
    if USANDO_POSTGRES:
        cursor.execute("SELECT to_regclass(%s) AS t", (f"public.{tabela}",))
        return cursor.fetchone()["t"] is not None
    cursor.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (tabela,))
    return cursor.fetchone() is not None


def excluir_usuario(usuario_id: int) -> bool:
    """Exclui a conta de um usuário e tudo que é dele: painel médico
    (os médicos voltam a aparecer nas buscas pra equipe), agenda, histórico
    de visitas e de buscas, notificações e pedidos de senha. Registros que
    ele criou e que são da empresa (médicos da base própria) continuam, só
    deixam de apontar pra ele. Tudo numa transação só: ou apaga tudo, ou nada."""
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("SELECT id, cpf FROM usuarios WHERE id = ?"), (usuario_id,))
        usuario = cursor.fetchone()
        if not usuario:
            return False

        cursor.execute(_q("DELETE FROM agenda_compromissos WHERE usuario_id = ?"), (usuario_id,))
        cursor.execute(_q("DELETE FROM visitas_log WHERE usuario_id = ?"), (usuario_id,))
        cursor.execute(_q("DELETE FROM historico_visitas WHERE usuario_id = ?"), (usuario_id,))
        cursor.execute(_q("DELETE FROM alertas_clientes WHERE usuario_id = ?"), (usuario_id,))
        # agenda/visitas ligadas aos médicos do painel dele (garantia extra)
        cursor.execute(_q("DELETE FROM agenda_compromissos WHERE painel_medico_id IN (SELECT id FROM painel_medicos WHERE usuario_id = ?)"), (usuario_id,))
        cursor.execute(_q("DELETE FROM visitas_log WHERE painel_medico_id IN (SELECT id FROM painel_medicos WHERE usuario_id = ?)"), (usuario_id,))
        cursor.execute(_q("DELETE FROM painel_medicos WHERE usuario_id = ?"), (usuario_id,))
        cursor.execute(_q("DELETE FROM buscas_log WHERE usuario_id = ?"), (usuario_id,))
        cursor.execute(_q("DELETE FROM medicos_removidos_inatividade WHERE usuario_id = ?"), (usuario_id,))
        cursor.execute(_q("DELETE FROM solicitacoes_senha WHERE cpf = ?"), (usuario["cpf"],))
        cursor.execute(_q("UPDATE medicos_base_propria SET importado_por = NULL WHERE importado_por = ?"), (usuario_id,))
        # tabela da antiga aba Portfólio: pode existir em bancos mais velhos
        if _tabela_existe(cursor, "produtos_portfolio"):
            cursor.execute(_q("UPDATE produtos_portfolio SET criado_por = NULL WHERE criado_por = ?"), (usuario_id,))
        cursor.execute(_q("DELETE FROM usuarios WHERE id = ?"), (usuario_id,))
        return True


def listar_usuarios_com_contagem_painel():
    """Pra tela de admin: cada usuário com os números do painel dele (total,
    por coluna e quantos são clientes) — base dos filtros e da ordenação."""
    limite = (datetime.utcnow() - timedelta(days=DIAS_PARA_REVISITAR)).isoformat()
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("""
            SELECT u.id, u.nome, u.email, u.cpf, u.criado_em, u.ultimo_login, u.is_admin, u.aprovado,
                   u.desativado, u.desativado_em,
                   COUNT(p.id) AS total_painel,
                   COALESCE(SUM(CASE WHEN p.status = 'prospectado' THEN 1 ELSE 0 END), 0) AS prospectados,
                   COALESCE(SUM(CASE WHEN p.status = 'agendado' THEN 1 ELSE 0 END), 0) AS agendados,
                   COALESCE(SUM(CASE WHEN p.status = 'visitado'
                                      AND (p.visitado_em IS NULL OR p.visitado_em = '' OR p.visitado_em >= ?)
                                     THEN 1 ELSE 0 END), 0) AS visitados,
                   COALESCE(SUM(CASE WHEN p.status = 'visitado' AND p.visitado_em <> '' AND p.visitado_em < ?
                                     THEN 1 ELSE 0 END), 0) AS revisitar,
                   COALESCE(SUM(CASE WHEN p.ja_comprou = 1 THEN 1 ELSE 0 END), 0) AS clientes
            FROM usuarios u
            LEFT JOIN painel_medicos p ON p.usuario_id = u.id
            GROUP BY u.id, u.nome, u.email, u.cpf, u.criado_em, u.ultimo_login, u.is_admin, u.aprovado,
                     u.desativado, u.desativado_em
            ORDER BY u.criado_em DESC
        """), (limite, limite))
        return cursor.fetchall()


def resumo_painel_por_usuario():
    """Pra exportação do admin: cada usuário com os NÚMEROS do painel dele
    (prospectados, agendados, visitados e a revisitar) — sem dados de médico.
    Uma consulta só, mesma regra das colunas do painel (bucket_painel)."""
    limite = (datetime.utcnow() - timedelta(days=DIAS_PARA_REVISITAR)).isoformat()
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("""
            SELECT u.id, u.nome, u.email, u.criado_em, u.ultimo_login, u.is_admin, u.aprovado,
                   COALESCE(SUM(CASE WHEN p.status = 'prospectado' THEN 1 ELSE 0 END), 0) AS prospectados,
                   COALESCE(SUM(CASE WHEN p.status = 'agendado' THEN 1 ELSE 0 END), 0) AS agendados,
                   COALESCE(SUM(CASE WHEN p.status = 'visitado'
                                      AND (p.visitado_em IS NULL OR p.visitado_em = '' OR p.visitado_em >= ?)
                                     THEN 1 ELSE 0 END), 0) AS visitados,
                   COALESCE(SUM(CASE WHEN p.status = 'visitado' AND p.visitado_em <> '' AND p.visitado_em < ?
                                     THEN 1 ELSE 0 END), 0) AS revisitar
            FROM usuarios u
            LEFT JOIN painel_medicos p ON p.usuario_id = u.id
            GROUP BY u.id, u.nome, u.email, u.criado_em, u.ultimo_login, u.is_admin, u.aprovado
            ORDER BY u.nome
        """), (limite, limite))
        return cursor.fetchall()


# ---------------------------------------------------------------------------
# "Esqueci minha senha" — vira notificação pro admin, sem envio automático
# ---------------------------------------------------------------------------

_ATENDIDO = True if USANDO_POSTGRES else 1
_NAO_ATENDIDO = False if USANDO_POSTGRES else 0


def registrar_solicitacao_senha(cpf: str):
    """Registra o pedido de redefinição de senha pra aparecer como
    notificação no painel admin. Tenta casar com um usuário existente
    pelo CPF pra já trazer nome/e-mail junto. Devolve os dados registrados
    (nome pode ser None, se o CPF não bater com ninguém), pra quem chamou
    poder avisar o admin por fora (WhatsApp, por exemplo)."""
    cpf_limpo = limpar_cpf(cpf)
    usuario = buscar_usuario_por_cpf(cpf_limpo) if cpf_limpo else None
    agora = datetime.utcnow().isoformat()
    nome = usuario["nome"] if usuario else None
    email = usuario["email"] if usuario else None

    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(
            _q("""INSERT INTO solicitacoes_senha (cpf, nome, email, atendido, criado_em)
                  VALUES (?, ?, ?, ?, ?)"""),
            (cpf_limpo, nome, email, _NAO_ATENDIDO, agora),
        )

    return {"cpf": cpf_limpo, "nome": nome, "email": email}


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

        # Todos os números soltos numa ida só ao banco (subconsultas escalares):
        # no Postgres cada consulta é uma viagem de rede, então 30 COUNTs
        # separados deixavam o Dashboard lento.
        limite_revisitar_str = (agora - timedelta(days=60)).isoformat()
        contagens = [
            ("prospectados", "SELECT COUNT(*) FROM painel_medicos WHERE status = 'prospectado'", ()),
            ("agendados", "SELECT COUNT(*) FROM painel_medicos WHERE status = 'agendado'", ()),
            ("visitados", "SELECT COUNT(*) FROM painel_medicos WHERE status = 'visitado'", ()),
            ("usuarios_total", "SELECT COUNT(*) FROM usuarios", ()),
            ("revisitar_total", "SELECT COUNT(*) FROM painel_medicos WHERE status = 'visitado' AND visitado_em < ?", (limite_revisitar_str,)),
            ("medicos_base", "SELECT COUNT(DISTINCT chave_medico) FROM painel_medicos", ()),
            ("medicos_semana", "SELECT COUNT(DISTINCT chave_medico) FROM painel_medicos WHERE adicionado_em >= ?", (d7_str,)),
            ("medicos_mes", "SELECT COUNT(DISTINCT chave_medico) FROM painel_medicos WHERE adicionado_em >= ?", (d30_str,)),
            ("visitados_mes_atual", "SELECT COUNT(*) FROM visitas_log WHERE visitado_em >= ?", (inicio_mes_atual_str,)),
            ("visitados_mes_anterior", "SELECT COUNT(*) FROM visitas_log WHERE visitado_em >= ? AND visitado_em < ?", (inicio_mes_anterior_str, inicio_mes_atual_str)),
            ("buscas_hoje", "SELECT COUNT(*) FROM buscas_log WHERE criado_em >= ?", (hoje_ts,)),
            ("buscas_7d", "SELECT COUNT(*) FROM buscas_log WHERE criado_em >= ?", (d7_str,)),
            ("buscas_30d", "SELECT COUNT(*) FROM buscas_log WHERE criado_em >= ?", (d30_str,)),
            ("ativos_hoje", "SELECT COUNT(*) FROM usuarios WHERE ultimo_login >= ?", (hoje_ts,)),
            ("ativos_7d", "SELECT COUNT(*) FROM usuarios WHERE ultimo_login >= ?", (d7_str,)),
            ("ativos_30d", "SELECT COUNT(*) FROM usuarios WHERE ultimo_login >= ?", (d30_str,)),
        ]
        for i, (inicio, fim) in enumerate(zip(inicios_meses, fins_meses)):
            faixa = (inicio.isoformat(), fim.isoformat())
            contagens.append((f"novos_{i}", "SELECT COUNT(DISTINCT chave_medico) FROM painel_medicos WHERE adicionado_em >= ? AND adicionado_em < ?", faixa))
            contagens.append((f"visit_{i}", "SELECT COUNT(*) FROM visitas_log WHERE visitado_em >= ? AND visitado_em < ?", faixa))
        cursor.execute(
            _q("SELECT " + ", ".join(f"({sql}) AS {nome}" for nome, sql, _ in contagens)),
            tuple(p for _, _, params in contagens for p in params),
        )
        linha = dict(cursor.fetchone())
        n = {nome: int(linha[nome] or 0) for nome, _, _ in contagens}

        prospectados, agendados, visitados = n["prospectados"], n["agendados"], n["visitados"]
        usuarios_total, revisitar_total = n["usuarios_total"], n["revisitar_total"]
        medicos_base, medicos_semana, medicos_mes = n["medicos_base"], n["medicos_semana"], n["medicos_mes"]
        visitados_mes_atual, visitados_mes_anterior = n["visitados_mes_atual"], n["visitados_mes_anterior"]
        if visitados_mes_anterior > 0:
            variacao_visitados_pct = round((visitados_mes_atual - visitados_mes_anterior) / visitados_mes_anterior * 100)
        else:
            variacao_visitados_pct = 100 if visitados_mes_atual > 0 else 0
        buscas_hoje, buscas_7d, buscas_30d = n["buscas_hoje"], n["buscas_7d"], n["buscas_30d"]
        ativos_hoje, ativos_7d, ativos_30d = n["ativos_hoje"], n["ativos_7d"], n["ativos_30d"]
        serie_meses = [
            {"label": f"{_MESES_ABREV[inicio.month - 1]}/{str(inicio.year)[2:]}",
             "novos": n[f"novos_{i}"], "visitados": n[f"visit_{i}"]}
            for i, inicio in enumerate(inicios_meses)
        ]

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
        "ranking": ranking_visitas_mes(agora),
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

        cursor.execute(_q("""
            SELECT resultado, COUNT(*) AS total FROM visitas_log
            WHERE usuario_id = ? AND visitado_em >= ? AND resultado IS NOT NULL
            GROUP BY resultado
        """), (usuario_id, inicio_mes_atual_str))
        resultados_mes = {r["resultado"]: r["total"] for r in cursor.fetchall()}

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
        "resultados_mes": resultados_mes,
    }


# ---------------------------------------------------------------------------
# Traduções guardadas (artigos científicos)
# ---------------------------------------------------------------------------

def buscar_traducoes(chaves: list) -> dict:
    if not chaves:
        return {}
    with _conexao() as conn:
        cursor = conn.cursor()
        marcadores = ",".join(["?"] * len(chaves))
        cursor.execute(_q(f"SELECT chave, traducao FROM traducoes WHERE chave IN ({marcadores})"), tuple(chaves))
        return {linha["chave"]: linha["traducao"] for linha in cursor.fetchall()}


def guardar_traducoes(novas: dict):
    if not novas:
        return
    agora = datetime.utcnow().isoformat()
    sql = ("INSERT INTO traducoes (chave, traducao, criado_em) VALUES (?, ?, ?) ON CONFLICT (chave) DO NOTHING"
           if USANDO_POSTGRES else
           "INSERT OR IGNORE INTO traducoes (chave, traducao, criado_em) VALUES (?, ?, ?)")
    with _conexao() as conn:
        cursor = conn.cursor()
        for chave, traducao in novas.items():
            cursor.execute(_q(sql), (chave, traducao, agora))


# ---------------------------------------------------------------------------
# Registro do que os admins fizeram
# ---------------------------------------------------------------------------
ACOES_ADMIN = {
    "aprovar_cadastro": "Aprovou cadastro",
    "tornar_admin": "Deu acesso de admin",
    "remover_admin": "Tirou acesso de admin",
    "excluir_usuario": "Excluiu conta",
    "redefinir_senha": "Redefiniu senha",
    "atender_senha": "Marcou pedido de senha como atendido",
    "marcar_cliente": "Marcou médico como cliente",
    "desmarcar_cliente": "Desmarcou médico cliente",
    "adicionar_medico": "Adicionou médico ao painel",
    "remover_medico": "Tirou médico do painel",
    "importar_painel": "Importou médicos para um painel",
    "importar_clientes": "Importou planilha de clientes",
    "apagar_revisao": "Apagou médicos na Revisão",
    "importar_base": "Importou base de médicos",
    "exportar": "Exportou dados",
    "relatorio_config": "Mudou o relatório diário",
    "relatorio_promotor": "Mudou o WhatsApp do relatório de um promotor",
    "relatorio_enviar": "Enviou relatório pelo WhatsApp",
    "whatsapp_conectar": "Conectou o WhatsApp do relatório",
    "whatsapp_desconectar": "Desconectou o WhatsApp do relatório",
    "desativar_usuario": "Desativou conta",
    "reativar_usuario": "Reativou conta",
    "transferir_painel": "Transferiu painel médico",
    "criar_chave_api": "Criou chave de API",
    "revogar_chave_api": "Revogou chave de API",
}


def buscar_entrada_painel(usuario_id, entrada_id):
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("SELECT id, nome, crm, ja_comprou FROM painel_medicos WHERE id = ? AND usuario_id = ?"),
                       (entrada_id, usuario_id))
        linha = cursor.fetchone()
    return dict(linha) if linha else None


def registrar_acao_admin(admin, acao, alvo=None, detalhe=""):
    """`admin` e `alvo` são linhas de usuário (ou None). Nunca derruba a ação
    que está sendo registrada: se der erro aqui, só não fica registrado."""
    try:
        with _conexao() as conn:
            cursor = conn.cursor()
            cursor.execute(_q("""
                INSERT INTO acoes_admin (admin_id, admin_nome, acao, alvo_usuario_id, alvo_nome, detalhe, criado_em)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """), (
                admin["id"] if admin else None, (admin["nome"] if admin else "Sistema"), acao,
                alvo["id"] if alvo else None, alvo["nome"] if alvo else None,
                (detalhe or "")[:500], datetime.utcnow().isoformat(),
            ))
        return True
    except Exception:
        return False


def listar_acoes_admin(admin_id=None, acao=None, de_utc=None, ate_utc=None, limite=1000):
    filtros, params = [], []
    if admin_id:
        filtros.append("admin_id = ?"); params.append(admin_id)
    if acao:
        filtros.append("acao = ?"); params.append(acao)
    if de_utc:
        filtros.append("criado_em >= ?"); params.append(de_utc)
    if ate_utc:
        filtros.append("criado_em < ?"); params.append(ate_utc)
    where = ("WHERE " + " AND ".join(filtros)) if filtros else ""
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q(f"""
            SELECT id, admin_id, admin_nome, acao, alvo_usuario_id, alvo_nome, detalhe, criado_em
            FROM acoes_admin {where}
            ORDER BY criado_em DESC, id DESC
            LIMIT {int(limite) + 1}
        """), tuple(params))
        return [dict(r) for r in cursor.fetchall()]


def listar_admins_do_registro():
    """Quem aparece no registro (inclusive quem já não é admin ou foi excluído)."""
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT admin_id, MAX(admin_nome) AS admin_nome, COUNT(*) AS total
            FROM acoes_admin WHERE admin_id IS NOT NULL
            GROUP BY admin_id ORDER BY MAX(admin_nome)
        """)
        return [dict(r) for r in cursor.fetchall()]


# ---------------------------------------------------------------------------
# Resumo semanal (segunda a domingo, horário de Brasília)
# ---------------------------------------------------------------------------
DIAS_CLIENTE_SEM_VISITA = 60


def inicio_da_semana_br(dia):
    """Segunda-feira da semana de `dia` (date)."""
    return dia - timedelta(days=dia.weekday())


def _br_para_utc_iso(dia):
    """Meia-noite (Brasília) desse dia → ISO em UTC, igual ao que o banco guarda."""
    return (datetime(dia.year, dia.month, dia.day) + FUSO_BRASIL).isoformat()


def calcular_resumo_semana(segunda, agora_utc=None):
    """Números da semana que começa em `segunda` (date, horário de Brasília).
    Tudo em poucas consultas (cada uma é uma viagem ao banco no Postgres)."""
    agora_utc = agora_utc or datetime.utcnow()
    ini = _br_para_utc_iso(segunda)
    fim = _br_para_utc_iso(segunda + timedelta(days=7))
    ini_ant = _br_para_utc_iso(segunda - timedelta(days=7))
    prox_ini = (segunda + timedelta(days=7)).isoformat()
    prox_fim = (segunda + timedelta(days=13)).isoformat()
    limite_cliente = (agora_utc - timedelta(days=DIAS_CLIENTE_SEM_VISITA)).isoformat()

    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("""
            SELECT
              (SELECT COUNT(*) FROM historico_visitas WHERE visitado_em >= ? AND visitado_em < ?) AS visitas,
              (SELECT COUNT(*) FROM historico_visitas WHERE visitado_em >= ? AND visitado_em < ?) AS visitas_anterior,
              (SELECT COUNT(*) FROM painel_medicos WHERE adicionado_em >= ? AND adicionado_em < ?) AS novos_medicos,
              (SELECT COUNT(*) FROM agenda_compromissos WHERE criado_em >= ? AND criado_em < ?) AS agendamentos,
              (SELECT COUNT(*) FROM alertas_clientes WHERE tipo = 'visitado' AND criado_em >= ? AND criado_em < ?) AS clientes_visitados,
              (SELECT COUNT(*) FROM usuarios WHERE criado_em >= ? AND criado_em < ?) AS novos_usuarios,
              (SELECT COUNT(*) FROM buscas_log WHERE criado_em >= ? AND criado_em < ?) AS buscas,
              (SELECT COUNT(*) FROM agenda_compromissos WHERE data >= ? AND data <= ?) AS agenda_proxima
        """), (ini, fim, ini_ant, ini, ini, fim, ini, fim, ini, fim, ini, fim, ini, fim, prox_ini, prox_fim))
        totais = {k: int(v or 0) for k, v in dict(cursor.fetchone()).items()}

        # Por promotor (todo usuário aprovado que não é admin, mesmo sem nada na semana)
        cursor.execute(_q("""
            SELECT u.id, u.nome,
              (SELECT COUNT(*) FROM historico_visitas h WHERE h.usuario_id = u.id AND h.visitado_em >= ? AND h.visitado_em < ?) AS visitas,
              (SELECT COUNT(*) FROM painel_medicos p WHERE p.usuario_id = u.id AND p.adicionado_em >= ? AND p.adicionado_em < ?) AS novos_medicos,
              (SELECT COUNT(*) FROM agenda_compromissos a WHERE a.usuario_id = u.id AND a.criado_em >= ? AND a.criado_em < ?) AS agendamentos,
              (SELECT COUNT(*) FROM alertas_clientes c WHERE c.usuario_id = u.id AND c.tipo = 'visitado' AND c.criado_em >= ? AND c.criado_em < ?) AS clientes_visitados,
              (SELECT COUNT(*) FROM agenda_compromissos a WHERE a.usuario_id = u.id AND a.data >= ? AND a.data <= ?) AS agenda_proxima,
              (SELECT COUNT(*) FROM painel_medicos p WHERE p.usuario_id = u.id AND p.ja_comprou = 1
                  AND (p.visitado_em IS NULL OR p.visitado_em = '' OR p.visitado_em < ?)) AS clientes_esquecidos
            FROM usuarios u
            WHERE u.aprovado = ? AND u.is_admin = ? AND COALESCE(u.desativado, 0) = 0
        """), (ini, fim, ini, fim, ini, fim, ini, fim, prox_ini, prox_fim, limite_cliente,
               True if USANDO_POSTGRES else 1, False if USANDO_POSTGRES else 0))
        promotores = [{k: (int(v or 0) if k not in ("nome",) else v) for k, v in dict(r).items()}
                      for r in cursor.fetchall()]

        cursor.execute(_q("""
            SELECT c.medico_nome, c.medico_crm, c.criado_em, u.nome AS usuario_nome
            FROM alertas_clientes c LEFT JOIN usuarios u ON u.id = c.usuario_id
            WHERE c.tipo = 'visitado' AND c.criado_em >= ? AND c.criado_em < ?
            ORDER BY c.criado_em DESC LIMIT 50
        """), (ini, fim))
        lista_clientes = [dict(r) for r in cursor.fetchall()]

    promotores.sort(key=lambda p: (-p["visitas"], -p["novos_medicos"], (p["nome"] or "").lower()))
    ant = totais["visitas_anterior"]
    variacao = None if ant == 0 else round((totais["visitas"] - ant) / ant * 100)
    return {
        "semana_inicio": segunda.isoformat(),
        "semana_fim": (segunda + timedelta(days=6)).isoformat(),
        "gerado_em": agora_utc.isoformat(),
        "totais": totais,
        "variacao_visitas_pct": variacao,
        "promotores": promotores,
        "sem_visitas": [p["nome"] for p in promotores if p["visitas"] == 0],
        "clientes_visitados": [
            {"medico_nome": c["medico_nome"], "medico_crm": c["medico_crm"] or "",
             "usuario_nome": c["usuario_nome"] or "(usuário excluído)", "criado_em": c["criado_em"]}
            for c in lista_clientes
        ],
        "dias_cliente_sem_visita": DIAS_CLIENTE_SEM_VISITA,
    }


def ultima_semana_fechada(agora_utc=None):
    """Segunda-feira da última semana completa (que já terminou no domingo)."""
    hoje_br = agora_brasil(agora_utc).date()
    return inicio_da_semana_br(hoje_br) - timedelta(days=7)


def guardar_resumo_semana(segunda, dados):
    """Grava o resumo da semana se ainda não existir. Devolve True só para
    quem gravou primeiro (com 2 processos no servidor, só um avisa)."""
    import json
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("""
            INSERT INTO resumos_semanais (semana_inicio, criado_em, dados) VALUES (?, ?, ?)
            ON CONFLICT (semana_inicio) DO NOTHING
        """), (segunda.isoformat(), datetime.utcnow().isoformat(), json.dumps(dados, ensure_ascii=False)))
        return cursor.rowcount == 1


def buscar_resumo_semana(segunda):
    import json
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("SELECT dados FROM resumos_semanais WHERE semana_inicio = ?"), (segunda.isoformat(),))
        linha = cursor.fetchone()
    return json.loads(linha["dados"]) if linha else None


def listar_semanas_com_resumo(limite=52):
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(f"SELECT semana_inicio FROM resumos_semanais ORDER BY semana_inicio DESC LIMIT {int(limite)}")
        return [r["semana_inicio"] for r in cursor.fetchall()]


# ---------------------------------------------------------------------------
# Ajustes gerais (config_sistema)
# ---------------------------------------------------------------------------
def ler_config(chaves_padrao: dict) -> dict:
    """Devolve {chave: valor} pras chaves pedidas, com o padrão quando não há."""
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT chave, valor FROM config_sistema")
        salvos = {l["chave"]: l["valor"] for l in cursor.fetchall()}
    return {k: salvos.get(k, v) for k, v in chaves_padrao.items()}


def salvar_config(valores: dict):
    with _conexao() as conn:
        cursor = conn.cursor()
        for chave, valor in valores.items():
            cursor.execute(_q("""
                INSERT INTO config_sistema (chave, valor) VALUES (?, ?)
                ON CONFLICT (chave) DO UPDATE SET valor = excluded.valor
            """), (chave, None if valor is None else str(valor)))


# ---------------------------------------------------------------------------
# Relatório diário pelo WhatsApp
# ---------------------------------------------------------------------------
def listar_promotores_relatorio():
    """Usuários aprovados, com o WhatsApp do relatório (ou o telefone do
    cadastro, se ainda não foi definido) e o último envio."""
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("""
            SELECT u.id, u.nome, u.cpf, u.telefone, u.relatorio_whatsapp, u.relatorio_ativo, u.is_admin,
                   (SELECT e.status FROM relatorio_envios e WHERE e.usuario_id = u.id
                     ORDER BY e.criado_em DESC, e.id DESC LIMIT 1) AS ultimo_status,
                   (SELECT e.criado_em FROM relatorio_envios e WHERE e.usuario_id = u.id
                     ORDER BY e.criado_em DESC, e.id DESC LIMIT 1) AS ultimo_em,
                   (SELECT e.detalhe FROM relatorio_envios e WHERE e.usuario_id = u.id
                     ORDER BY e.criado_em DESC, e.id DESC LIMIT 1) AS ultimo_detalhe
            FROM usuarios u
            WHERE u.aprovado = ? AND COALESCE(u.desativado, 0) = 0
            ORDER BY u.is_admin, u.nome
        """), (True if USANDO_POSTGRES else 1,))
        linhas = [dict(l) for l in cursor.fetchall()]
    for l in linhas:
        l["whatsapp"] = l["relatorio_whatsapp"] if l["relatorio_whatsapp"] is not None else (l["telefone"] or "")
        l["ativo"] = bool(l["relatorio_ativo"])
    return linhas


def salvar_promotor_relatorio(usuario_id: int, whatsapp: str, ativo: bool) -> bool:
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("UPDATE usuarios SET relatorio_whatsapp = ?, relatorio_ativo = ? WHERE id = ?"),
                       ((whatsapp or "").strip()[:30], 1 if ativo else 0, usuario_id))
        return cursor.rowcount > 0


def reservar_envio(chave_envio: str, usuario: dict, telefone: str, dia: str, tipo: str):
    """Marca o envio como 'enviando' antes de mandar. Devolve o id, ou None
    se esse envio já existe (outro processo já pegou)."""
    agora = datetime.utcnow().isoformat()
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("""
            INSERT INTO relatorio_envios (chave_envio, usuario_id, usuario_nome, telefone, dia, tipo, status, criado_em, atualizado_em)
            VALUES (?, ?, ?, ?, ?, ?, 'enviando', ?, ?)
            ON CONFLICT (chave_envio) DO NOTHING
        """), (chave_envio, usuario["id"], usuario["nome"], telefone, dia, tipo, agora, agora))
        if cursor.rowcount != 1:
            return None
        cursor.execute(_q("SELECT id FROM relatorio_envios WHERE chave_envio = ?"), (chave_envio,))
        return cursor.fetchone()["id"]


def finalizar_envio(envio_id: int, status: str, detalhe: str = "", mensagem: str = None):
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("""
            UPDATE relatorio_envios SET status = ?, detalhe = ?, mensagem = COALESCE(?, mensagem), atualizado_em = ?
            WHERE id = ?
        """), (status, (detalhe or "")[:500], mensagem, datetime.utcnow().isoformat(), envio_id))


def resumo_painel_para_relatorio(usuario_id: int, hoje_br: str) -> dict:
    """O que vai no relatório diário: totais do painel e as próximas visitas
    agendadas (de hoje em diante, horário de Brasília)."""
    limite = (datetime.utcnow() - timedelta(days=DIAS_PARA_REVISITAR)).isoformat()
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("""
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(CASE WHEN status = 'visitado' THEN 1 ELSE 0 END), 0) AS visitados,
                   COALESCE(SUM(CASE WHEN status = 'visitado' AND visitado_em <> '' AND visitado_em < ?
                                     THEN 1 ELSE 0 END), 0) AS revisitar
            FROM painel_medicos WHERE usuario_id = ?
        """), (limite, usuario_id))
        t = dict(cursor.fetchone())
        cursor.execute(_q("""
            SELECT a.data, a.horario, p.nome, p.especialidade, p.cidade
            FROM agenda_compromissos a JOIN painel_medicos p ON p.id = a.painel_medico_id
            WHERE a.usuario_id = ? AND a.data >= ?
            ORDER BY a.data, a.horario
            LIMIT 50
        """), (usuario_id, hoje_br))
        proximas = [dict(l) for l in cursor.fetchall()]
    total, visitados = int(t["total"] or 0), int(t["visitados"] or 0)
    return {"total": total, "visitados": visitados, "revisitar": int(t["revisitar"] or 0),
            "nao_visitados": total - visitados, "proximas": proximas}


def apagar_envio(envio_id: int):
    with _conexao() as conn:
        conn.cursor().execute(_q("DELETE FROM relatorio_envios WHERE id = ?"), (envio_id,))


def destravar_envios_presos(minutos: int = 30) -> int:
    """Envio que ficou 'enviando' (servidor reiniciou no meio) vira erro."""
    limite = (datetime.utcnow() - timedelta(minutes=minutos)).isoformat()
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("""
            UPDATE relatorio_envios SET status = 'erro', detalhe = 'Interrompido (o servidor reiniciou no meio do envio).',
                   atualizado_em = ?
            WHERE status = 'enviando' AND atualizado_em < ?
        """), (datetime.utcnow().isoformat(), limite))
        return cursor.rowcount


def listar_envios_relatorio(limite: int = 150, dia: str = None):
    with _conexao() as conn:
        cursor = conn.cursor()
        if dia:
            cursor.execute(_q(f"""SELECT * FROM relatorio_envios WHERE dia = ?
                                  ORDER BY criado_em DESC, id DESC LIMIT {int(limite)}"""), (dia,))
        else:
            cursor.execute(f"SELECT * FROM relatorio_envios ORDER BY criado_em DESC, id DESC LIMIT {int(limite)}")
        return [dict(l) for l in cursor.fetchall()]


def contar_envios_do_dia(dia: str) -> dict:
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("SELECT status, COUNT(*) AS total FROM relatorio_envios WHERE dia = ? AND tipo = 'automatico' GROUP BY status"), (dia,))
        return {l["status"]: int(l["total"]) for l in cursor.fetchall()}


# ---------------------------------------------------------------------------
# Desativar usuário e transferir o painel médico
# ---------------------------------------------------------------------------
def desativar_usuario(usuario_id: int, desativar: bool = True) -> bool:
    """Desativada, a conta não entra mais (e quem estava logado sai), mas
    nada é apagado: painel, visitas e histórico ficam. Dá pra reativar."""
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("UPDATE usuarios SET desativado = ?, desativado_em = ?, relatorio_ativo = 0 WHERE id = ?")
                       if desativar else
                       _q("UPDATE usuarios SET desativado = ?, desativado_em = ? WHERE id = ?"),
                       (1 if desativar else 0, datetime.utcnow().isoformat() if desativar else None, usuario_id))
        return cursor.rowcount > 0


def previa_transferencia(origem_id: int, destino_id: int) -> dict:
    """Quantos médicos vão e quantos o destino já tem (esses são juntados)."""
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("""
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(CASE WHEN EXISTS (SELECT 1 FROM painel_medicos d
                                                  WHERE d.usuario_id = ? AND d.chave_medico = o.chave_medico)
                                     THEN 1 ELSE 0 END), 0) AS repetidos,
                   COALESCE(SUM(CASE WHEN o.ja_comprou = 1 THEN 1 ELSE 0 END), 0) AS clientes
            FROM painel_medicos o WHERE o.usuario_id = ?
        """), (destino_id, origem_id))
        l = dict(cursor.fetchone())
        cursor.execute(_q("SELECT COUNT(*) AS n FROM agenda_compromissos WHERE usuario_id = ? AND data >= ?"),
                       (origem_id, agora_brasil().date().isoformat()))
        agenda = cursor.fetchone()["n"]
    return {"total": int(l["total"] or 0), "repetidos": int(l["repetidos"] or 0),
            "clientes": int(l["clientes"] or 0), "agendamentos": int(agenda or 0)}


def transferir_painel(origem_id: int, destino_id: int, nome_origem: str = "") -> dict:
    """Passa o painel médico inteiro de um usuário para outro, numa transação só.

    - Médico que o destino ainda não tem: muda de dono, com status, observações,
      marcação de cliente e agendamentos (se o destino já tiver outro
      compromisso no mesmo dia e horário, esse agendamento é cancelado e o
      médico volta para Prospectados).
    - Médico que o destino já tem: os dois são juntados no do destino —
      a marcação de cliente e as observações de quem saiu vão junto, e o
      histórico de visitas passa a apontar para ele.
    - O histórico de visitas continua no nome de quem visitou."""
    if origem_id == destino_id:
        raise ValueError("Escolha outro usuário para receber o painel.")
    r = {"movidos": 0, "juntados": 0, "agendas_movidas": 0, "agendas_canceladas": 0}
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("""SELECT chave_medico, id, ja_comprou, marcado_cliente_em, ultimo_pedido_em, observacoes
                             FROM painel_medicos WHERE usuario_id = ?"""), (destino_id,))
        do_destino = {l["chave_medico"]: dict(l) for l in cursor.fetchall()}
        cursor.execute(_q("""SELECT id, chave_medico, status, ja_comprou, marcado_cliente_em, ultimo_pedido_em, observacoes
                             FROM painel_medicos WHERE usuario_id = ? ORDER BY id"""), (origem_id,))
        da_origem = [dict(l) for l in cursor.fetchall()]
        rotulo = f"Notas de {nome_origem}" if nome_origem else "Notas do promotor anterior"

        for m in da_origem:
            alvo = do_destino.get(m["chave_medico"])
            if alvo:
                # junta no médico que o destino já tem
                obs = (alvo["observacoes"] or "").rstrip()
                if (m["observacoes"] or "").strip():
                    obs = (obs + "\n\n" if obs else "") + f"{rotulo}:\n{m['observacoes'].strip()}"
                    obs = obs[-LIMITE_OBSERVACOES:]
                pedido = max([x for x in (alvo["ultimo_pedido_em"], m["ultimo_pedido_em"]) if x] or [None])
                cursor.execute(_q("""
                    UPDATE painel_medicos SET
                        ja_comprou = CASE WHEN ja_comprou = 1 OR ? = 1 THEN 1 ELSE 0 END,
                        marcado_cliente_em = COALESCE(marcado_cliente_em, ?),
                        ultimo_pedido_em = ?, observacoes = ?
                    WHERE id = ?
                """), (1 if m["ja_comprou"] else 0, m["marcado_cliente_em"], pedido, obs or None, alvo["id"]))
                for tabela in ("visitas_log", "historico_visitas", "alertas_clientes"):
                    cursor.execute(_q(f"UPDATE {tabela} SET painel_medico_id = ? WHERE painel_medico_id = ?"),
                                   (alvo["id"], m["id"]))
                cursor.execute(_q("DELETE FROM agenda_compromissos WHERE painel_medico_id = ?"), (m["id"],))
                r["agendas_canceladas"] += cursor.rowcount
                cursor.execute(_q("DELETE FROM painel_medicos WHERE id = ?"), (m["id"],))
                r["juntados"] += 1
                continue

            # muda de dono, com os agendamentos (se o horário estiver livre no destino)
            cursor.execute(_q("SELECT id, data, horario FROM agenda_compromissos WHERE painel_medico_id = ?"), (m["id"],))
            for ag in [dict(l) for l in cursor.fetchall()]:
                cursor.execute(_q("SELECT 1 FROM agenda_compromissos WHERE usuario_id = ? AND data = ? AND horario = ?"),
                               (destino_id, ag["data"], ag["horario"]))
                if cursor.fetchone():
                    cursor.execute(_q("DELETE FROM agenda_compromissos WHERE id = ?"), (ag["id"],))
                    r["agendas_canceladas"] += 1
                else:
                    cursor.execute(_q("UPDATE agenda_compromissos SET usuario_id = ? WHERE id = ?"), (destino_id, ag["id"]))
                    r["agendas_movidas"] += 1
            novo_status = m["status"]
            if m["status"] == "agendado":
                cursor.execute(_q("SELECT 1 FROM agenda_compromissos WHERE painel_medico_id = ?"), (m["id"],))
                if not cursor.fetchone():
                    novo_status = "prospectado"
            cursor.execute(_q("UPDATE painel_medicos SET usuario_id = ?, status = ? WHERE id = ?"),
                           (destino_id, novo_status, m["id"]))
            do_destino[m["chave_medico"]] = {**m, "id": m["id"]}
            r["movidos"] += 1
        # agendamento solto (sem médico no painel) não deve sobrar na agenda de quem saiu
        cursor.execute(_q("""DELETE FROM agenda_compromissos WHERE usuario_id = ?
                             AND painel_medico_id NOT IN (SELECT id FROM painel_medicos WHERE usuario_id = ?)"""),
                       (origem_id, origem_id))
    return r


# ---------------------------------------------------------------------------
# Chaves de API (outros sistemas lendo médicos, promotores e visitas)
# ---------------------------------------------------------------------------
PREFIXO_CHAVE_API = "icn_"


def _hash_chave_api(chave: str) -> str:
    return hashlib.sha256((chave or "").strip().encode("utf-8")).hexdigest()


def criar_chave_api(nome: str, admin) -> str:
    """Cria a chave e devolve o texto dela — a ÚNICA vez que ele existe:
    no banco fica só o hash."""
    chave = PREFIXO_CHAVE_API + secrets.token_urlsafe(32)
    with _conexao() as conn:
        conn.cursor().execute(_q("""
            INSERT INTO api_chaves (nome, prefixo, hash, criado_por, criado_por_nome, criado_em)
            VALUES (?, ?, ?, ?, ?, ?)
        """), ((nome or "").strip()[:80] or "Sem nome", chave[:12], _hash_chave_api(chave),
               admin["id"] if admin else None, admin["nome"] if admin else None, datetime.utcnow().isoformat()))
    return chave


def validar_chave_api(chave: str):
    """Devolve a chave (dict) se existe e não foi revogada; senão None."""
    if not chave or not chave.startswith(PREFIXO_CHAVE_API) or len(chave) > 200:
        return None
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("SELECT id, nome, revogada_em, ultimo_uso_em FROM api_chaves WHERE hash = ?"),
                       (_hash_chave_api(chave),))
        linha = cursor.fetchone()
        if not linha or linha["revogada_em"]:
            return None
        agora = datetime.utcnow().isoformat()
        cursor.execute(_q("UPDATE api_chaves SET ultimo_uso_em = ?, usos = usos + 1 WHERE id = ?"), (agora, linha["id"]))
        return dict(linha)


def listar_chaves_api():
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute("""SELECT id, nome, prefixo, criado_por_nome, criado_em, ultimo_uso_em, usos, revogada_em
                          FROM api_chaves ORDER BY revogada_em IS NOT NULL, criado_em DESC""")
        return [dict(l) for l in cursor.fetchall()]


def revogar_chave_api(chave_id: int):
    """Devolve o nome da chave revogada (ou None se não existia/já revogada)."""
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q("SELECT nome FROM api_chaves WHERE id = ? AND revogada_em IS NULL"), (chave_id,))
        linha = cursor.fetchone()
        if not linha:
            return None
        cursor.execute(_q("UPDATE api_chaves SET revogada_em = ? WHERE id = ?"), (datetime.utcnow().isoformat(), chave_id))
        return linha["nome"]


def _situacao_medico(status, visitado_em, limite_revisitar):
    if status == "visitado" and visitado_em and visitado_em < limite_revisitar:
        return "revisitar"
    return status


def api_listar_medicos(promotor_id=None, situacao=None, alterado_desde=None, limite=500, offset=0):
    """Médicos de todos os painéis (ou de um promotor), paginados por id."""
    limite_revisitar = (datetime.utcnow() - timedelta(days=DIAS_PARA_REVISITAR)).isoformat()
    filtros, params = [], []
    if promotor_id:
        filtros.append("p.usuario_id = ?"); params.append(promotor_id)
    if situacao == "revisitar":
        filtros.append("p.status = 'visitado' AND p.visitado_em <> '' AND p.visitado_em < ?"); params.append(limite_revisitar)
    elif situacao == "visitado":
        filtros.append("p.status = 'visitado' AND (p.visitado_em IS NULL OR p.visitado_em = '' OR p.visitado_em >= ?)")
        params.append(limite_revisitar)
    elif situacao in ("prospectado", "agendado"):
        filtros.append("p.status = ?"); params.append(situacao)
    elif situacao == "cliente":
        filtros.append("p.ja_comprou = 1")
    if alterado_desde:
        filtros.append("(p.adicionado_em >= ? OR p.visitado_em >= ? OR p.marcado_cliente_em >= ?)")
        params += [alterado_desde] * 3
    where = ("WHERE " + " AND ".join(filtros)) if filtros else ""
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q(f"SELECT COUNT(*) AS total FROM painel_medicos p {where}"), tuple(params))
        total = int(cursor.fetchone()["total"] or 0)
        cursor.execute(_q(f"""
            SELECT p.id, p.usuario_id, u.nome AS promotor_nome, u.cpf AS promotor_cpf,
                   p.nome, p.crm, p.especialidade, p.cidade, p.uf, p.endereco, p.telefone, p.telefone_pessoal,
                   p.status, p.ja_comprou, p.marcado_cliente_em, p.ultimo_pedido_em, p.adicionado_em,
                   p.visitado_em, p.resultado_visita, p.observacoes,
                   (SELECT MIN(a.data || ' ' || a.horario) FROM agenda_compromissos a
                     WHERE a.painel_medico_id = p.id) AS agendado_para,
                   (SELECT COUNT(*) FROM historico_visitas h WHERE h.painel_medico_id = p.id) AS total_visitas
            FROM painel_medicos p LEFT JOIN usuarios u ON u.id = p.usuario_id
            {where}
            ORDER BY p.id
            LIMIT {int(limite)} OFFSET {int(offset)}
        """), tuple(params))
        linhas = [dict(l) for l in cursor.fetchall()]
    for l in linhas:
        l["situacao"] = _situacao_medico(l["status"], l["visitado_em"], limite_revisitar)
    return total, linhas


def api_listar_visitas(de_utc=None, ate_utc=None, promotor_id=None, limite=500, offset=0):
    """Histórico permanente de visitas (fica mesmo se o médico sair do painel)."""
    filtros, params = [], []
    if de_utc:
        filtros.append("h.visitado_em >= ?"); params.append(de_utc)
    if ate_utc:
        filtros.append("h.visitado_em < ?"); params.append(ate_utc)
    if promotor_id:
        filtros.append("h.usuario_id = ?"); params.append(promotor_id)
    where = ("WHERE " + " AND ".join(filtros)) if filtros else ""
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q(f"SELECT COUNT(*) AS total FROM historico_visitas h {where}"), tuple(params))
        total = int(cursor.fetchone()["total"] or 0)
        cursor.execute(_q(f"""
            SELECT h.id, h.usuario_id, u.nome AS promotor_nome, u.cpf AS promotor_cpf,
                   h.painel_medico_id, h.visitado_em, h.resultado,
                   h.medico_nome, h.medico_crm, h.medico_especialidade, h.medico_cidade, h.medico_uf
            FROM historico_visitas h LEFT JOIN usuarios u ON u.id = h.usuario_id
            {where}
            ORDER BY h.visitado_em DESC, h.id DESC
            LIMIT {int(limite)} OFFSET {int(offset)}
        """), tuple(params))
        return total, [dict(l) for l in cursor.fetchall()]


def _api_paginado(colunas: str, origem: str, filtros: list, params: list, ordem: str, limite: int, offset: int):
    """Conta e busca uma página (total, linhas) — base das listas da API."""
    where = ("WHERE " + " AND ".join(filtros)) if filtros else ""
    with _conexao() as conn:
        cursor = conn.cursor()
        cursor.execute(_q(f"SELECT COUNT(*) AS total FROM {origem} {where}"), tuple(params))
        total = int(cursor.fetchone()["total"] or 0)
        cursor.execute(_q(f"SELECT {colunas} FROM {origem} {where} ORDER BY {ordem} "
                          f"LIMIT {int(limite)} OFFSET {int(offset)}"), tuple(params))
        return total, [dict(l) for l in cursor.fetchall()]


def api_listar_agenda(de_dia=None, ate_dia=None, promotor_id=None, limite=500, offset=0):
    filtros, params = [], []
    if de_dia:
        filtros.append("a.data >= ?"); params.append(de_dia)
    if ate_dia:
        filtros.append("a.data <= ?"); params.append(ate_dia)
    if promotor_id:
        filtros.append("a.usuario_id = ?"); params.append(promotor_id)
    return _api_paginado(
        "a.id, a.usuario_id, u.nome AS promotor_nome, u.cpf AS promotor_cpf, a.painel_medico_id, a.data, a.horario, "
        "a.criado_em, p.nome AS medico_nome, p.crm AS medico_crm, p.especialidade AS medico_especialidade, "
        "p.cidade AS medico_cidade, p.uf AS medico_uf, p.endereco AS medico_endereco, p.status AS medico_status, "
        "p.ja_comprou AS medico_cliente",
        "agenda_compromissos a LEFT JOIN usuarios u ON u.id = a.usuario_id "
        "LEFT JOIN painel_medicos p ON p.id = a.painel_medico_id",
        filtros, params, "a.data, a.horario, a.id", limite, offset)


def api_listar_alertas_clientes(de_utc=None, ate_utc=None, limite=500, offset=0):
    filtros, params = [], []
    if de_utc:
        filtros.append("a.criado_em >= ?"); params.append(de_utc)
    if ate_utc:
        filtros.append("a.criado_em < ?"); params.append(ate_utc)
    return _api_paginado(
        "a.id, a.usuario_id, u.nome AS promotor_nome, a.painel_medico_id, a.tipo, a.detalhe, "
        "a.medico_nome, a.medico_crm, a.criado_em",
        "alertas_clientes a LEFT JOIN usuarios u ON u.id = a.usuario_id",
        filtros, params, "a.criado_em DESC, a.id DESC", limite, offset)


def api_listar_envios_relatorio(de_dia=None, ate_dia=None, limite=500, offset=0):
    filtros, params = [], []
    if de_dia:
        filtros.append("dia >= ?"); params.append(de_dia)
    if ate_dia:
        filtros.append("dia <= ?"); params.append(ate_dia)
    return _api_paginado(
        "id, usuario_id, usuario_nome, telefone, dia, tipo, status, detalhe, mensagem, criado_em",
        "relatorio_envios", filtros, params, "criado_em DESC, id DESC", limite, offset)


def api_listar_registro(de_utc=None, ate_utc=None, limite=500, offset=0):
    filtros, params = [], []
    if de_utc:
        filtros.append("criado_em >= ?"); params.append(de_utc)
    if ate_utc:
        filtros.append("criado_em < ?"); params.append(ate_utc)
    return _api_paginado(
        "id, admin_id, admin_nome, acao, alvo_usuario_id, alvo_nome, detalhe, criado_em",
        "acoes_admin", filtros, params, "criado_em DESC, id DESC", limite, offset)


def api_listar_base_medicos(especialidade=None, uf=None, limite=500, offset=0):
    filtros, params = [], []
    if especialidade:
        filtros.append("especialidade = ?"); params.append(especialidade)
    if uf:
        filtros.append("uf = ?"); params.append(uf.upper())
    return _api_paginado(
        "id, nome, crm, especialidade, cidade, uf, endereco, telefone, importado_em",
        "medicos_base_propria", filtros, params, "id", limite, offset)

