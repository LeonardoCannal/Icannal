from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for
from scraper import (
    buscar_medicos_doctoralia,
    buscar_medicos_sechat,
    buscar_medicos_fonte_generica,
    FONTES_GENERICAS,
)
from auth import (
    inicializar_auth,
    criar_usuario,
    buscar_usuario_por_email,
    buscar_usuario_por_cpf,
    buscar_usuario_por_id,
    cpf_valido,
    formatar_cpf,
    checar_senha,
    atualizar_ultimo_login,
    listar_usuarios_com_contagem_painel,
    usuario_logado,
    fazer_login,
    fazer_logout,
    login_required,
    admin_required,
    ErroIntegridade,
    computar_chave_medico,
    adicionar_ao_painel,
    mover_no_painel,
    listar_painel_usuario,
    chaves_no_painel_usuario,
    registrar_solicitacao_senha,
    listar_solicitacoes_senha_pendentes,
    contar_solicitacoes_senha_pendentes,
    marcar_solicitacao_atendida,
    buscar_solicitacao_senha_por_id,
    definir_senha,
    registrar_busca,
    estatisticas_dashboard,
    estatisticas_usuario,
    bucket_painel,
    listar_horarios_dia,
    agendar_medico,
    cancelar_agendamento,
    listar_compromissos_mes,
    salvar_observacoes,
    dias_sem_visitar,
    roteiro_do_dia,
    contar_visitas_amanha,
    definir_meta_usuario,
    RESULTADOS_VISITA,
    buscar_compromisso_do_medico,
    data_valida,
)
import webbrowser
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
import os
import io
import secrets
import time
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill


def resource_path(relative_path):
    """Acha o caminho certo dos arquivos SÓ DE LEITURA (templates, static),
    tanto rodando normal quanto dentro do .exe empacotado."""
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def caminho_dados_persistentes(nome_arquivo):
    """
    Acha o caminho certo pra arquivos que precisam ser GRAVADOS e
    PERSISTIR entre execuções (como o banco de dados de usuários).
    Dentro do .exe, a pasta que o resource_path() usa é temporária e é
    apagada quando o programa fecha — então o banco de dados precisa
    ficar do lado do .exe, não dentro dela.
    """
    if getattr(sys, "frozen", False):
        base_path = os.path.dirname(sys.executable)
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, nome_arquivo)


app = Flask(
    __name__,
    template_folder=resource_path("templates"),
    static_folder=resource_path("static"),
)

# A SECRET_KEY protege as sessões de login. Em produção (site no ar),
# defina a variável de ambiente SECRET_KEY com um valor fixo e secreto —
# senão, toda vez que o servidor reiniciar, todo mundo é deslogado.
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", secrets.token_hex(32))

# Cookie de login: não acessível por JavaScript e não enviado em requisições
# vindas de outros sites. Com o site em HTTPS (VPS/Render), defina a variável
# COOKIE_SEGURO=1 pra ele também só trafegar criptografado.
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SEGURO") == "1"


def _corpo_json():
    """Corpo JSON da requisição como dict (ou {} se vier vazio/inválido)."""
    dados = request.get_json(force=True, silent=True)
    return dados if isinstance(dados, dict) else {}


def _inteiro(valor):
    """Converte pra int ou devolve None (evita erro 500 com id inválido)."""
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


# Proteção simples contra tentativas de adivinhar senha: depois de várias
# falhas seguidas no mesmo CPF, o login espera alguns minutos.
_TENTATIVAS_LOGIN = {}
_TRAVA_LOGIN = threading.Lock()
MAX_TENTATIVAS_LOGIN = 8
JANELA_LOGIN_SEG = 600


def _chave_login(cpf):
    return "".join(ch for ch in (cpf or "") if ch.isdigit())


def _login_bloqueado(chave):
    agora = time.time()
    with _TRAVA_LOGIN:
        recentes = [t for t in _TENTATIVAS_LOGIN.get(chave, []) if agora - t < JANELA_LOGIN_SEG]
        if recentes:
            _TENTATIVAS_LOGIN[chave] = recentes
        else:
            _TENTATIVAS_LOGIN.pop(chave, None)
        return len(recentes) >= MAX_TENTATIVAS_LOGIN


def _registrar_falha_login(chave):
    agora = time.time()
    with _TRAVA_LOGIN:
        if len(_TENTATIVAS_LOGIN) > 5000:
            for k in [k for k, v in _TENTATIVAS_LOGIN.items() if not v or agora - v[-1] >= JANELA_LOGIN_SEG]:
                _TENTATIVAS_LOGIN.pop(k, None)
        _TENTATIVAS_LOGIN.setdefault(chave, []).append(agora)


def _limpar_falhas_login(chave):
    with _TRAVA_LOGIN:
        _TENTATIVAS_LOGIN.pop(chave, None)

inicializar_auth(app, caminho_dados_persistentes("cannal.db"))

MAX_ESPECIALIDADES_POR_BUSCA = 3

ESPECIALIDADES = {
    "neurologista": {"nome": "Neurologista", "doctoralia": "neurologista", "sechat": "n6k3wbvn8k5ajvtb75ikhaaw"},
    "psiquiatra": {"nome": "Psiquiatra", "doctoralia": "psiquiatra", "sechat": "jiagob2yvu3v78j041jb70oz"},
    "neurologista-pediatrico": {"nome": "Neuropediatra", "doctoralia": "neurologista-pediatrico", "sechat": "k9b3og0pgrbrizm138hkamr8"},
    "medico-clinico-geral": {"nome": "Clínico / Clínica Médica", "doctoralia": "medico-clinico-geral", "sechat": "eol7qqtdgtvf8buc2px9hbrq"},
    "pediatra": {"nome": "Pediatra", "doctoralia": "pediatra", "sechat": "dfwzf1l7z9efzucsufeqqeu9"},
    "medico-de-familia": {"nome": "Medicina de Família", "doctoralia": "medico-de-familia", "sechat": "yycwljzp344y327g6rkuyvp1"},
    "especialista-em-dor": {"nome": "Médico da Dor / Anestesiologia", "doctoralia": "especialista-em-dor", "sechat": "zyqcuzojsnjny7wl1lk1g7ah"},
    "ortopedista-traumatologista": {"nome": "Ortopedista", "doctoralia": "ortopedista-traumatologista", "sechat": "wc5z54hdmebdmv8n783876nk"},
    "oncologista": {"nome": "Oncologista", "doctoralia": "oncologista", "sechat": "r742v7goz2k4q6467wsk99di"},
    "geriatra": {"nome": "Geriatra", "doctoralia": "geriatra", "sechat": "ly7dotxntqq446jbqlg6b3l5"},
    "alergista": {"nome": "Alergologista", "doctoralia": "alergista", "sechat": "homqkrplnu5aqeu2uota9wu9"},
    "cardiologista": {"nome": "Cardiologista", "doctoralia": "cardiologista", "sechat": "x14ir5c9kvx9s11bwbvi1mys"},
    "dermatologista": {"nome": "Dermatologista", "doctoralia": "dermatologista", "sechat": "pylctsugyjl97vtfjjs8da1k"},
    "endocrinologista": {"nome": "Endocrinologista", "doctoralia": "endocrinologista", "sechat": "u28uuuaeimpxf3k25cnxru8o"},
    "endocrinologista-pediatrico": {"nome": "Endocrinologista Pediátrico", "doctoralia": "endocrinologista-pediatrico", "sechat": None},
    "especialista-em-medicina-fisica-e-reabilitacao": {"nome": "Medicina Física e Reabilitação", "doctoralia": "especialista-em-medicina-fisica-e-reabilitacao", "sechat": "alfpewwlicu8tux4xcta4yh5"},
    "especialista-em-medicina-preventiva": {"nome": "Medicina Preventiva", "doctoralia": "especialista-em-medicina-preventiva", "sechat": "wutli6r3320ylrt2uclsfp57"},
    "gastroenterologista": {"nome": "Gastroenterologista", "doctoralia": "gastroenterologista", "sechat": "f6w5gv0vbyw6tpambkg1mxag"},
    "generalista": {"nome": "Generalista", "doctoralia": "generalista", "sechat": "c6vsx97abcofv176xjliox2w"},
    "ginecologista": {"nome": "Ginecologista", "doctoralia": "ginecologista", "sechat": "mlhojg8vxi8iftl7jbgudfsw"},
    "medico-acupunturista": {"nome": "Médico Acupunturista", "doctoralia": "medico-acupunturista", "sechat": "djkdf9qhvdw4jzfbhgp4o1pk"},
    "medico-do-esporte": {"nome": "Médico do Esporte", "doctoralia": "medico-do-esporte", "sechat": "kqs3bhoscy5nnf3vdb3vxn1w"},
    "medico-do-sono": {"nome": "Médico do Sono", "doctoralia": "medico-do-sono", "sechat": None},
    "medico-do-trabalho": {"nome": "Médico do Trabalho", "doctoralia": "medico-do-trabalho", "sechat": "evc6v5o06an5xtefc9339ay9"},
    "traumatologista": {"nome": "Traumatologista", "doctoralia": "ortopedista-traumatologista", "sechat": "wc5z54hdmebdmv8n783876nk"},
    "reumatologista": {"nome": "Reumatologista", "doctoralia": "reumatologista", "sechat": "qeiljtcgscrqrwp7wy8jr3cp"},
}

NOMES_ESPECIALIDADES = {slug: info["nome"] for slug, info in ESPECIALIDADES.items()}


@app.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    if usuario_logado():
        return redirect(url_for("index"))

    erro = None
    nome_form = ""
    email_form = ""
    cpf_form = ""

    if request.method == "POST":
        nome_form = request.form.get("nome", "").strip()
        email_form = request.form.get("email", "").strip().lower()
        cpf_form = request.form.get("cpf", "").strip()
        senha = request.form.get("senha", "")
        confirmar = request.form.get("confirmar", "")

        if not nome_form or not email_form or not cpf_form or not senha:
            erro = "Preencha todos os campos."
        elif not cpf_valido(cpf_form):
            erro = "CPF inválido. Confira os números digitados."
        elif senha != confirmar:
            erro = "As senhas não coincidem."
        elif len(senha) < 6:
            erro = "A senha precisa ter pelo menos 6 caracteres."
        elif buscar_usuario_por_email(email_form):
            erro = "Já existe uma conta cadastrada com esse e-mail."
        elif buscar_usuario_por_cpf(cpf_form):
            erro = "Já existe uma conta cadastrada com esse CPF."

        if not erro:
            try:
                user_id = criar_usuario(nome_form, email_form, cpf_form, senha)
            except ErroIntegridade:
                erro = "Já existe uma conta cadastrada com esse e-mail ou CPF."
            else:
                atualizar_ultimo_login(user_id)
                fazer_login(user_id)
                return redirect(url_for("index"))

    return render_template("cadastro.html", erro=erro, nome=nome_form, email=email_form, cpf=cpf_form)


@app.route("/login", methods=["GET", "POST"])
def login():
    if usuario_logado():
        return redirect(url_for("index"))

    erro = None
    if request.method == "POST":
        cpf = request.form.get("cpf", "").strip()
        senha = request.form.get("senha", "")
        chave = _chave_login(cpf)

        if _login_bloqueado(chave):
            erro = "Muitas tentativas de login. Aguarde alguns minutos e tente de novo."
            return render_template("login.html", erro=erro), 429

        usuario = buscar_usuario_por_cpf(cpf)

        if usuario and checar_senha(usuario, senha):
            _limpar_falhas_login(chave)
            atualizar_ultimo_login(usuario["id"])
            fazer_login(usuario["id"])
            return redirect(url_for("index"))

        _registrar_falha_login(chave)
        erro = "CPF ou senha incorretos."

    return render_template("login.html", erro=erro)


def formatar_data_br(valor_iso):
    if not valor_iso:
        return "Nunca"
    try:
        return datetime.fromisoformat(valor_iso).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return valor_iso


@app.route("/perfil", methods=["GET", "POST"])
@login_required
def perfil():
    usuario = usuario_logado()
    erro = None
    sucesso = None

    if request.method == "POST":
        senha_atual = request.form.get("senha_atual", "")
        nova_senha = request.form.get("nova_senha", "")
        confirmar_senha = request.form.get("confirmar_senha", "")

        if not checar_senha(usuario, senha_atual):
            erro = "Senha atual incorreta."
        elif len(nova_senha) < 6:
            erro = "A nova senha precisa ter pelo menos 6 caracteres."
        elif nova_senha != confirmar_senha:
            erro = "As senhas novas não coincidem."
        else:
            definir_senha(usuario["id"], nova_senha)
            usuario = buscar_usuario_por_id(usuario["id"])
            sucesso = "Senha atualizada com sucesso."

    return render_template(
        "perfil.html",
        usuario=usuario,
        cpf_formatado=formatar_cpf(usuario["cpf"]),
        criado_em=formatar_data_br(usuario["criado_em"]),
        ultimo_login=formatar_data_br(usuario["ultimo_login"]),
        erro=erro,
        sucesso=sucesso,
    )


@app.route("/logout")
@login_required
def logout():
    fazer_logout()
    return redirect(url_for("login"))


@app.route("/api/esqueci-senha", methods=["POST"])
def api_esqueci_senha():
    dados = _corpo_json()
    cpf = dados.get("cpf", "")
    registrar_solicitacao_senha(cpf)
    return jsonify({"ok": True})


@app.route("/admin")
@admin_required
def admin():
    usuarios = [
        {
            "id": u["id"],
            "nome": u["nome"],
            "email": u["email"],
            "cpf": formatar_cpf(u["cpf"]),
            "criado_em": formatar_data_br(u["criado_em"]),
            "ultimo_login": formatar_data_br(u["ultimo_login"]),
            "is_admin": bool(u["is_admin"]),
            "total_painel": u["total_painel"],
        }
        for u in listar_usuarios_com_contagem_painel()
    ]
    return render_template("admin.html", usuarios=usuarios)


@app.route("/admin/exportar/<int:user_id>")
@admin_required
def admin_exportar_painel(user_id):
    usuario = buscar_usuario_por_id(user_id)
    if not usuario:
        return "Usuário não encontrado.", 404

    painel = listar_painel_usuario(user_id)

    wb = Workbook()
    ws = wb.active
    ws.title = "Painel Médico"

    colunas = ["Nome", "CRM", "Especialidade", "Cidade", "UF", "Endereço", "Telefone", "Status", "Adicionado em"]
    ws.append(colunas)

    cabecalho_fonte = Font(bold=True, color="FFFFFF")
    cabecalho_fundo = PatternFill(start_color="343C4C", end_color="343C4C", fill_type="solid")
    for celula in ws[1]:
        celula.font = cabecalho_fonte
        celula.fill = cabecalho_fundo

    for m in painel:
        try:
            data_fmt = datetime.fromisoformat(m["adicionado_em"]).strftime("%d/%m/%Y %H:%M")
        except (ValueError, TypeError):
            data_fmt = m["adicionado_em"] or ""

        ws.append([
            m["nome"], m["crm"], m["especialidade"], m["cidade"], m["uf"],
            m["endereco"], m["telefone"],
            "Visitado" if m["status"] == "visitado" else "Prospectado",
            data_fmt,
        ])

    larguras = [28, 16, 24, 18, 6, 38, 18, 14, 18]
    for i, largura in enumerate(larguras, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = largura

    arquivo = io.BytesIO()
    wb.save(arquivo)
    arquivo.seek(0)

    nome_usuario_arquivo = usuario["nome"].strip().replace(" ", "_")
    nome_arquivo = f"painel_{nome_usuario_arquivo}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"

    return send_file(
        arquivo,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=nome_arquivo,
    )


@app.route("/admin/painel/<int:user_id>")
@admin_required
def admin_painel_usuario(user_id):
    usuario = buscar_usuario_por_id(user_id)
    if not usuario:
        return jsonify({"erro": "Usuário não encontrado."}), 404

    painel = listar_painel_usuario(user_id)
    prospectados, agendados, visitados, revisitar = bucket_painel(painel)

    agendados_dict = [dict(m) for m in agendados]
    for m in agendados_dict:
        m["compromisso"] = buscar_compromisso_do_medico(user_id, m["id"])

    return jsonify({
        "usuario": {"nome": usuario["nome"], "email": usuario["email"]},
        "prospectados": [dict(m) for m in prospectados],
        "agendados": agendados_dict,
        "visitados": [dict(m) for m in visitados],
        "revisitar": [dict(m) for m in revisitar],
    })


@app.route("/admin/estatisticas/<int:user_id>")
@admin_required
def admin_estatisticas_usuario(user_id):
    usuario = buscar_usuario_por_id(user_id)
    if not usuario:
        return jsonify({"erro": "Usuário não encontrado."}), 404

    stats = estatisticas_usuario(user_id)
    stats["usuario"] = {"nome": usuario["nome"], "email": usuario["email"]}
    return jsonify(stats)


@app.route("/admin/notificacoes")
@admin_required
def admin_notificacoes():
    pendentes = listar_solicitacoes_senha_pendentes()
    return jsonify({
        "total": len(pendentes),
        "solicitacoes": [
            {
                "id": s["id"],
                "cpf": formatar_cpf(s["cpf"]),
                "nome": s["nome"],
                "email": s["email"],
                "criado_em": formatar_data_br(s["criado_em"]),
            }
            for s in pendentes
        ],
    })


@app.route("/admin/notificacoes/<int:solicitacao_id>/atender", methods=["POST"])
@admin_required
def admin_notificacao_atender(solicitacao_id):
    ok = marcar_solicitacao_atendida(solicitacao_id)
    return jsonify({"ok": ok})


@app.route("/admin/notificacoes/<int:solicitacao_id>/redefinir", methods=["POST"])
@admin_required
def admin_notificacao_redefinir(solicitacao_id):
    dados = _corpo_json()
    nova_senha = dados.get("senha", "")

    if len(nova_senha) < 6:
        return jsonify({"ok": False, "mensagem": "A senha precisa ter pelo menos 6 caracteres."}), 400

    solicitacao = buscar_solicitacao_senha_por_id(solicitacao_id)
    if not solicitacao:
        return jsonify({"ok": False, "mensagem": "Solicitação não encontrada."}), 404

    usuario = buscar_usuario_por_cpf(solicitacao["cpf"])
    if not usuario:
        return jsonify({"ok": False, "mensagem": "Não existe conta cadastrada com esse CPF."}), 404

    definir_senha(usuario["id"], nova_senha)
    marcar_solicitacao_atendida(solicitacao_id)
    return jsonify({"ok": True})


@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    return render_template("dashboard.html", stats=estatisticas_dashboard())


@app.route("/")
@login_required
def index():
    usuario = usuario_logado()
    dias_inativo = dias_sem_visitar(usuario["id"])
    visitas_amanha = contar_visitas_amanha(usuario["id"])
    return render_template(
        "index.html",
        especialidades=NOMES_ESPECIALIDADES,
        max_especialidades=MAX_ESPECIALIDADES_POR_BUSCA,
        aviso_inatividade=(dias_inativo is not None and dias_inativo >= 10),
        dias_inativo=dias_inativo,
        visitas_amanha=visitas_amanha,
        resultados_visita=RESULTADOS_VISITA,
    )


@app.route("/api/buscar")
@login_required
def api_buscar():
    especialidades_selecionadas = request.args.getlist("especialidade")
    cidade = request.args.get("cidade", "")
    uf = request.args.get("uf", "")

    if not especialidades_selecionadas:
        return jsonify({"erro": "Selecione ao menos uma especialidade."}), 400
    if len(especialidades_selecionadas) > MAX_ESPECIALIDADES_POR_BUSCA:
        return jsonify({"erro": f"Selecione no máximo {MAX_ESPECIALIDADES_POR_BUSCA} especialidades por busca."}), 400
    for slug in especialidades_selecionadas:
        if slug not in ESPECIALIDADES:
            return jsonify({"erro": f"Especialidade inválida: {slug}"}), 400
    if not cidade.strip():
        return jsonify({"erro": "Informe a cidade."}), 400

    usuario = usuario_logado()
    registrar_busca(usuario["id"])

    nomes_especialidades = [ESPECIALIDADES[slug]["nome"] for slug in especialidades_selecionadas]
    rotulo_especialidades = ", ".join(nomes_especialidades)

    # Monta a lista de tarefas a rodar em paralelo:
    # - Doctoralia e Sechat entram uma vez POR especialidade selecionada
    #   (cada um tem uma URL diferente por especialidade).
    # - As fontes genéricas (Ama-me, Kaya Doc, Cannaceia) não filtram por
    #   especialidade — a página é a mesma não importa o que você busca —
    #   então elas entram só UMA VEZ na lista toda.
    tarefas = []  # cada item: (rótulo_pra_erro, função, args)

    for slug in especialidades_selecionadas:
        info = ESPECIALIDADES[slug]
        nome_especialidade = info["nome"]

        tarefas.append((
            f"Doctoralia ({nome_especialidade})",
            buscar_medicos_doctoralia,
            (info["doctoralia"], nome_especialidade, cidade, uf),
        ))

        if info["sechat"]:
            tarefas.append((
                f"Sechat ({nome_especialidade})",
                buscar_medicos_sechat,
                (info["sechat"], nome_especialidade, cidade, uf),
            ))

    for chave_fonte, dados_fonte in FONTES_GENERICAS.items():
        tarefas.append((
            dados_fonte["nome"],
            buscar_medicos_fonte_generica,
            (chave_fonte, rotulo_especialidades, cidade, uf),
        ))

    todos_medicos = []
    fontes_com_erro = []

    # Roda tudo em paralelo (até 12 buscas ao mesmo tempo) em vez de uma
    # atrás da outra — é isso que evita o erro de timeout quando várias
    # especialidades são selecionadas juntas.
    with ThreadPoolExecutor(max_workers=12) as executor:
        futuros = {
            executor.submit(funcao, *args): rotulo
            for rotulo, funcao, args in tarefas
        }
        for futuro in as_completed(futuros):
            rotulo = futuros[futuro]
            try:
                todos_medicos.extend(futuro.result())
            except Exception as e:
                fontes_com_erro.append(f"{rotulo}: {e}")

    # Marca quais médicos já estão no painel do usuário logado, pra
    # mostrar "Presente no painel médico" em vez do botão de adicionar.
    chaves_do_painel = chaves_no_painel_usuario(usuario["id"])
    for medico in todos_medicos:
        medico["no_painel"] = computar_chave_medico(medico) in chaves_do_painel

    return jsonify({
        "especialidades": nomes_especialidades,
        "cidade": cidade,
        "uf": uf,
        "total": len(todos_medicos),
        "medicos": todos_medicos,
        "avisos": fontes_com_erro,
    })


@app.route("/api/painel", methods=["GET"])
@login_required
def api_painel_listar():
    usuario = usuario_logado()
    painel = listar_painel_usuario(usuario["id"])
    prospectados, agendados, visitados, revisitar = bucket_painel(painel)

    agendados_dict = [dict(m) for m in agendados]
    for m in agendados_dict:
        m["compromisso"] = buscar_compromisso_do_medico(usuario["id"], m["id"])

    return jsonify({
        "prospectados": [dict(m) for m in prospectados],
        "agendados": agendados_dict,
        "visitados": [dict(m) for m in visitados],
        "revisitar": [dict(m) for m in revisitar],
    })


@app.route("/api/painel/adicionar", methods=["POST"])
@login_required
def api_painel_adicionar():
    usuario = usuario_logado()
    medico = _corpo_json()

    if not medico.get("nome"):
        return jsonify({"ok": False, "mensagem": "Dados do médico incompletos."}), 400

    ok, mensagem = adicionar_ao_painel(usuario["id"], medico)
    return jsonify({"ok": ok, "mensagem": mensagem})


@app.route("/api/painel/mover", methods=["POST"])
@login_required
def api_painel_mover():
    usuario = usuario_logado()
    dados = _corpo_json()
    entrada_id = _inteiro(dados.get("id"))
    novo_status = dados.get("status")
    resultado = dados.get("resultado")

    # "agendado" só existe através do botão Agendar (que exige data e horário).
    if not entrada_id or novo_status not in ("prospectado", "visitado"):
        return jsonify({"ok": False, "mensagem": "Requisição inválida."}), 400
    if resultado is not None:
        if novo_status != "visitado":
            return jsonify({"ok": False, "mensagem": "Resultado só se aplica ao marcar como visitado."}), 400
        if resultado not in RESULTADOS_VISITA:
            return jsonify({"ok": False, "mensagem": "Resultado da visita inválido."}), 400

    ok = mover_no_painel(usuario["id"], entrada_id, novo_status, resultado=resultado)
    return jsonify({"ok": ok})


@app.route("/api/painel/<int:entrada_id>/observacoes", methods=["POST"])
@login_required
def api_painel_observacoes(entrada_id):
    usuario = usuario_logado()
    dados = _corpo_json()
    texto = (dados.get("texto") or "").strip()

    ok = salvar_observacoes(usuario["id"], entrada_id, texto)
    return jsonify({"ok": ok})


@app.route("/api/painel/agendar", methods=["POST"])
@login_required
def api_painel_agendar():
    usuario = usuario_logado()
    dados = _corpo_json()
    entrada_id = _inteiro(dados.get("id"))
    data = str(dados.get("data") or "").strip()
    horario = str(dados.get("horario") or "").strip()

    if not entrada_id or not data or not horario:
        return jsonify({"ok": False, "mensagem": "Escolha uma data e um horário."}), 400

    ok, mensagem = agendar_medico(usuario["id"], entrada_id, data, horario)
    return jsonify({"ok": ok, "mensagem": mensagem})


@app.route("/api/painel/<int:entrada_id>/cancelar-agendamento", methods=["POST"])
@login_required
def api_painel_cancelar_agendamento(entrada_id):
    usuario = usuario_logado()
    ok = cancelar_agendamento(usuario["id"], entrada_id)
    return jsonify({"ok": ok})


@app.route("/agenda")
@login_required
def agenda():
    return render_template("agenda.html")


@app.route("/api/agenda/mes")
@login_required
def api_agenda_mes():
    usuario = usuario_logado()
    ano = _inteiro(request.args.get("ano"))
    mes = _inteiro(request.args.get("mes"))
    if ano is None or mes is None or not (2000 <= ano <= 2100) or not (1 <= mes <= 12):
        return jsonify({"erro": "Parâmetros inválidos."}), 400

    return jsonify({"dias": listar_compromissos_mes(usuario["id"], ano, mes)})


@app.route("/api/agenda/dia")
@login_required
def api_agenda_dia():
    usuario = usuario_logado()
    data = (request.args.get("data") or "").strip()
    if not data_valida(data):
        return jsonify({"erro": "Informe uma data válida (AAAA-MM-DD)."}), 400

    return jsonify({"horarios": listar_horarios_dia(usuario["id"], data)})


@app.route("/api/roteiro")
@login_required
def api_roteiro():
    usuario = usuario_logado()
    data = (request.args.get("data") or "").strip()
    if not data_valida(data):
        return jsonify({"erro": "Informe uma data válida (AAAA-MM-DD)."}), 400

    return jsonify({"visitas": roteiro_do_dia(usuario["id"], data)})


@app.route("/admin/usuarios/<int:user_id>/meta", methods=["POST"])
@admin_required
def admin_definir_meta(user_id):
    dados = _corpo_json()
    meta = dados.get("meta")
    if meta == "" or meta is None:
        meta = None
    ok = definir_meta_usuario(user_id, meta)
    return jsonify({"ok": ok})


def abrir_navegador():
    webbrowser.open("http://127.0.0.1:5000")


if __name__ == "__main__":
    threading.Timer(1.2, abrir_navegador).start()
    app.run(debug=False, use_reloader=False, port=5000)
