// Tela "Relatório diário" do admin: conexão do WhatsApp (QR code), ajustes
// do envio automático, quem recebe, prévia e envio manual.
(function () {
  const $ = (s, r) => (r || document).querySelector(s);
  const aviso = $('#rd-aviso');
  let avisoTempo;

  function avisar(msg, erro) {
    aviso.textContent = msg;
    aviso.classList.toggle('erro', !!erro);
    aviso.hidden = false;
    clearTimeout(avisoTempo);
    avisoTempo = setTimeout(() => { aviso.hidden = true; }, 4500);
  }

  async function chamar(url, metodo, corpo) {
    const r = await fetch(url, {
      method: metodo || 'GET',
      headers: corpo ? { 'Content-Type': 'application/json' } : {},
      body: corpo ? JSON.stringify(corpo) : undefined,
    });
    let dados = {};
    try { dados = await r.json(); } catch (_) { dados = { ok: false, erro: 'Resposta inesperada do servidor.' }; }
    if (!r.ok && dados.ok === undefined) dados.ok = false;
    return dados;
  }

  function ocupado(botao, sim, texto) {
    if (!botao) return;
    if (sim) { botao.dataset.textoOriginal = botao.textContent; botao.textContent = texto || 'Aguarde…'; }
    else if (botao.dataset.textoOriginal) botao.textContent = botao.dataset.textoOriginal;
    botao.disabled = sim;
  }

  // ---------------- WhatsApp ----------------
  const pill = $('#rd-wa-pill');
  const corpoWa = $('#rd-wa-corpo');
  const blocoQr = $('#rd-qr');
  const imgQr = $('#rd-qr-img');
  const btnCon = $('#rd-wa-conectar');
  const btnDes = $('#rd-wa-desconectar');
  const formTeste = $('#rd-wa-teste');
  const instalado = !corpoWa.querySelector('.rd-cmd');
  let espera = null;

  const ROTULOS = {
    conectado: ['Conectado', 'ok'],
    qr: ['Esperando o QR code', 'aguardando'],
    iniciando: ['Abrindo o WhatsApp…', 'aguardando'],
    autenticando: ['Entrando…', 'aguardando'],
    desligado: ['Desconectado', ''],
    erro: ['Com problema', 'erro'],
    indisponivel: ['Robô fora do ar', 'erro'],
  };

  function desenharWa(st) {
    const [rot, cls] = ROTULOS[st.estado] || [st.estado, ''];
    pill.textContent = rot;
    pill.className = 'rd-pill ' + cls;
    blocoQr.hidden = !(st.estado === 'qr' && st.qr);
    if (st.estado === 'qr' && st.qr && imgQr.src !== st.qr) imgQr.src = st.qr;
    btnCon.hidden = !instalado || !['desligado', 'erro'].includes(st.estado);
    btnDes.hidden = !instalado || !['conectado', 'qr', 'iniciando', 'autenticando'].includes(st.estado);
    btnDes.textContent = st.estado === 'conectado' ? 'Desconectar' : 'Cancelar';
    formTeste.hidden = st.estado !== 'conectado';

    if (!instalado) return;
    let html = '';
    if (st.estado === 'conectado') {
      html = `<p class="rd-texto">Enviando pelo número <strong>${formatarNumero(st.numero)}</strong>${st.nome ? ' (' + esc(st.nome) + ')' : ''}.</p>`;
    } else if (st.estado === 'desligado') {
      html = '<p class="rd-texto">Nenhum WhatsApp conectado. Clique em <strong>Conectar WhatsApp</strong> e leia o QR code com o celular.</p>';
    } else if (st.estado === 'iniciando' || st.estado === 'autenticando') {
      html = '<p class="rd-texto">Abrindo o WhatsApp no servidor… isso leva alguns segundos.</p>';
    } else if (st.estado === 'qr') {
      html = '<p class="rd-texto">Leia o código abaixo. Ele muda sozinho a cada poucos segundos.</p>';
    }
    if (st.erro) html += `<p class="rd-texto rd-erro-txt">${esc(st.erro)}</p>`;
    corpoWa.innerHTML = html;
  }

  function esc(v) {
    return String(v == null ? '' : v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function formatarNumero(n) {
    const d = String(n || '').replace(/\D/g, '');
    const m = d.match(/^55(\d{2})(\d{4,5})(\d{4})$/);
    return m ? `(${m[1]}) ${m[2]}-${m[3]}` : esc(n || '—');
  }

  async function atualizarWa() {
    clearTimeout(espera);
    const st = await chamar('/admin/relatorio-diario/whatsapp').catch(() => ({ estado: 'indisponivel', erro: 'Sem conexão com o i.cannal.' }));
    desenharWa(st);
    // enquanto está conectando, olha de 2 em 2 s; parado, de 20 em 20 s
    const rapido = ['qr', 'iniciando', 'autenticando'].includes(st.estado);
    if (!document.hidden) espera = setTimeout(atualizarWa, rapido ? 2000 : 20000);
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) atualizarWa(); });

  btnCon.addEventListener('click', async () => {
    ocupado(btnCon, true, 'Abrindo…');
    const r = await chamar('/admin/relatorio-diario/whatsapp/conectar', 'POST', {});
    ocupado(btnCon, false);
    if (!r.ok) avisar(r.erro || 'Não foi possível ligar o robô.', true);
    atualizarWa();
  });
  btnDes.addEventListener('click', async () => {
    if (btnDes.textContent === 'Desconectar' &&
        !confirm('Desconectar este WhatsApp? O relatório diário para de sair até alguém ler o QR code de novo.')) return;
    ocupado(btnDes, true, 'Desconectando…');
    const r = await chamar('/admin/relatorio-diario/whatsapp/desconectar', 'POST', {});
    ocupado(btnDes, false);
    if (!r.ok) avisar(r.erro || 'Não foi possível desconectar.', true);
    atualizarWa();
  });
  formTeste.addEventListener('submit', async (e) => {
    e.preventDefault();
    const botao = formTeste.querySelector('button');
    ocupado(botao, true, 'Enviando…');
    const r = await chamar('/admin/relatorio-diario/whatsapp/teste', 'POST', { numero: $('#rd-teste-num').value });
    ocupado(botao, false);
    avisar(r.ok ? 'Mensagem de teste enviada. Confira o WhatsApp.' : (r.erro || 'Não foi possível enviar.'), !r.ok);
  });

  // ---------------- Envio automático ----------------
  const formCfg = $('#rd-cfg');
  formCfg.addEventListener('submit', async (e) => {
    e.preventDefault();
    const botao = formCfg.querySelector('button[type=submit]');
    const dias = Array.from(formCfg.querySelectorAll('input[name=dias]:checked')).map((i) => i.value);
    ocupado(botao, true, 'Salvando…');
    const r = await chamar('/admin/relatorio-diario/config', 'POST', {
      ligado: $('#rd-ligado').checked, hora: $('#rd-hora').value, dias,
    });
    ocupado(botao, false);
    if (r.ok) { avisar('Ajustes salvos.'); setTimeout(() => location.reload(), 700); }
    else avisar(r.erro || 'Não foi possível salvar.', true);
  });

  // ---------------- Promotores ----------------
  document.querySelectorAll('.rd-tabela tr[data-id]').forEach((tr) => {
    const id = tr.dataset.id;
    const num = tr.querySelector('.rd-num');
    const ativo = tr.querySelector('.rd-ativo');
    let salvo = { whatsapp: num.value, ativo: ativo.checked };

    async function salvar() {
      const novo = { whatsapp: num.value.trim(), ativo: ativo.checked };
      if (novo.whatsapp === salvo.whatsapp && novo.ativo === salvo.ativo) return;
      const r = await chamar(`/admin/relatorio-diario/promotor/${id}`, 'POST', novo);
      if (r.ok) {
        salvo = novo;
        tr.classList.add('rd-salvo');
        setTimeout(() => tr.classList.remove('rd-salvo'), 1200);
      } else {
        avisar(r.erro || 'Não foi possível salvar.', true);
        ativo.checked = salvo.ativo;
        if (novo.ativo && !salvo.ativo) num.focus();
      }
    }
    ativo.addEventListener('change', salvar);
    num.addEventListener('change', salvar);
    num.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); num.blur(); } });

    tr.querySelector('.rd-previa').addEventListener('click', () => abrirPrevia(id, tr.dataset.nome));
    tr.querySelector('.rd-enviar').addEventListener('click', async (e) => {
      const botao = e.currentTarget;
      if (!confirm(`Enviar agora o relatório de ${tr.dataset.nome} para ${num.value || '(sem número)'}?`)) return;
      await salvar();
      ocupado(botao, true, 'Enviando…');
      const r = await chamar(`/admin/relatorio-diario/enviar/${id}`, 'POST', {});
      ocupado(botao, false);
      avisar(r.ok ? `Relatório enviado para ${tr.dataset.nome}.` : (r.erro || 'Não foi possível enviar.'), !r.ok);
      if (r.ok) setTimeout(() => location.reload(), 1200);
    });
  });

  // ---------------- Prévia ----------------
  const dialogo = $('#rd-dialogo');
  const textoPrevia = $('#rd-dialogo-texto');
  function formatarWhats(t) {
    // *negrito* e _itálico_ como no WhatsApp
    return esc(t).replace(/\*([^*\n]+)\*/g, '<strong>$1</strong>').replace(/(^|\s)_([^_\n]+)_/g, '$1<em>$2</em>').replace(/\n/g, '<br>');
  }
  async function abrirPrevia(id, nome) {
    $('#rd-dialogo-titulo').textContent = 'Prévia — ' + nome;
    textoPrevia.classList.remove('erro');
    textoPrevia.textContent = 'Montando a prévia…';
    if (dialogo.showModal) dialogo.showModal(); else dialogo.setAttribute('open', '');
    const r = await chamar(`/admin/relatorio-diario/previa/${id}`);
    if (r.ok) textoPrevia.innerHTML = formatarWhats(r.texto);
    else { textoPrevia.classList.add('erro'); textoPrevia.textContent = r.erro || 'Não foi possível montar a prévia.'; }
  }
  $('#rd-dialogo-fechar').addEventListener('click', () => dialogo.close());
  dialogo.addEventListener('click', (e) => { if (e.target === dialogo) dialogo.close(); });

  atualizarWa();
})();
