// Lista de usuários do admin: busca, filtros e ordenação na própria tela
// (sem recarregar). Os filtros ficam no endereço da página, então dá pra
// recarregar ou mandar o link e abrir do mesmo jeito.
(function () {
  const tabela = document.getElementById('tabela-usuarios');
  if (!tabela) return;
  const corpo = tabela.tBodies[0];
  const linhas = Array.from(corpo.querySelectorAll('tr.us-linha'));
  const vazio = document.getElementById('us-vazio');
  const contador = document.getElementById('us-contador');
  const btnLimpar = document.getElementById('us-limpar');
  const campos = {
    q: document.getElementById('us-busca'),
    tipo: document.getElementById('us-tipo'),
    acesso: document.getElementById('us-acesso'),
    painel: document.getElementById('us-painel'),
    ordem: document.getElementById('us-ordem'),
  };
  const ORDEM_PADRAO = 'nome';
  const DIA = 86400000;
  const agora = Date.now();

  function semAcento(t) {
    return String(t || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  }
  function data(iso) {
    if (!iso) return null;
    // as datas vêm em UTC sem fuso ("2026-11-09T19:48:00")
    const t = Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z');
    return isNaN(t) ? null : t;
  }

  // dados de cada linha, lidos uma vez só
  const dados = linhas.map((tr, i) => {
    const d = tr.dataset;
    return {
      tr, i,
      nome: d.nome || '',
      texto: semAcento(d.nome + ' ' + d.email) + ' ' + (d.cpf || '').replace(/\D/g, ''),
      admin: d.admin === '1',
      desativado: d.desativado === '1',
      aprovado: d.aprovado === '1',
      criado: data(d.criado),
      login: data(d.login),
      total: +d.total || 0,
      agendados: +d.agendados || 0,
      visitados: +d.visitados || 0,
      revisitar: +d.revisitar || 0,
      clientes: +d.clientes || 0,
    };
  });

  const colador = new Intl.Collator('pt-BR', { sensitivity: 'base', numeric: true });
  const ORDENS = {
    nome: (a, b) => colador.compare(a.nome, b.nome),
    criado: (a, b) => (a.criado || 0) - (b.criado || 0),
    // quem nunca entrou conta como o mais antigo
    login: (a, b) => (a.login || 0) - (b.login || 0),
    total: (a, b) => a.total - b.total,
    visitados: (a, b) => (a.visitados + a.revisitar) - (b.visitados + b.revisitar),
    agendados: (a, b) => a.agendados - b.agendados,
    clientes: (a, b) => a.clientes - b.clientes,
  };

  function passa(u, f) {
    if (f.q) {
      const termos = semAcento(f.q).split(/\s+/).filter(Boolean);
      for (const t of termos) {
        const digitos = t.replace(/[.\-]/g, '');
        if (!u.texto.includes(t) && !(/^\d+$/.test(digitos) && u.texto.includes(digitos))) return false;
      }
    }
    if (f.tipo === 'admin' && !u.admin) return false;
    if (f.tipo === 'promotor' && (u.admin || !u.aprovado)) return false;
    if (f.tipo === 'pendente' && u.aprovado) return false;
    if (f.tipo === 'desativado' && !u.desativado) return false;
    if ((f.tipo === 'promotor' || f.tipo === 'admin') && u.desativado) return false;
    if (f.acesso === '7' && !(u.login && agora - u.login <= 7 * DIA)) return false;
    if (f.acesso === '30' && !(u.login && agora - u.login <= 30 * DIA)) return false;
    if (f.acesso === 'sumido30' && !(u.login && agora - u.login > 30 * DIA)) return false;
    if (f.acesso === 'nunca' && u.login) return false;
    if (f.painel === 'com' && !u.total) return false;
    if (f.painel === 'vazio' && u.total) return false;
    if (f.painel === 'agendados' && !u.agendados) return false;
    if (f.painel === 'revisitar' && !u.revisitar) return false;
    if (f.painel === 'clientes' && !u.clientes) return false;
    return true;
  }

  function lerFiltros() {
    const f = {};
    for (const k in campos) f[k] = campos[k] ? campos[k].value.trim() : '';
    return f;
  }

  function aplicar() {
    const f = lerFiltros();
    const ordem = f.ordem || ORDEM_PADRAO;
    const desc = ordem.startsWith('-');
    const comparar = ORDENS[ordem.replace('-', '')] || ORDENS.nome;
    const lista = dados.slice().sort((a, b) => {
      const r = desc ? comparar(b, a) : comparar(a, b);
      return r || colador.compare(a.nome, b.nome) || a.i - b.i;
    });

    let visiveis = 0;
    const frag = document.createDocumentFragment();
    for (const u of lista) {
      const ok = passa(u, f);
      u.tr.hidden = !ok;
      if (ok) visiveis++;
      frag.appendChild(u.tr);
    }
    frag.appendChild(vazio);
    corpo.appendChild(frag);

    vazio.hidden = visiveis > 0;
    const filtrando = !!(f.q || f.tipo || f.acesso || f.painel);
    contador.textContent = filtrando
      ? `${visiveis} de ${dados.length} usuário(s)`
      : `${dados.length} usuário(s)`;
    btnLimpar.hidden = !filtrando && ordem === ORDEM_PADRAO;

    // cabeçalho clicável mostra a ordem atual
    tabela.querySelectorAll('.us-th').forEach((b) => {
      const chave = b.dataset.ordem.replace('-', '');
      const th = b.closest('th');
      if (ordem.replace('-', '') === chave) th.setAttribute('aria-sort', desc ? 'descending' : 'ascending');
      else th.removeAttribute('aria-sort');
    });

    // guarda no endereço (sem criar histórico a cada tecla)
    const url = new URL(location.href);
    for (const k in f) {
      if (f[k] && !(k === 'ordem' && f[k] === ORDEM_PADRAO)) url.searchParams.set(k, f[k]);
      else url.searchParams.delete(k);
    }
    history.replaceState(null, '', url.pathname + (url.search || '') + url.hash);
  }

  // estado inicial vindo do endereço
  const params = new URLSearchParams(location.search);
  for (const k in campos) {
    const v = params.get(k);
    if (v && campos[k]) {
      if (campos[k].tagName === 'SELECT' && !Array.from(campos[k].options).some((o) => o.value === v)) continue;
      campos[k].value = v;
    }
  }
  if (!campos.ordem.value) campos.ordem.value = ORDEM_PADRAO;

  let espera;
  campos.q.addEventListener('input', () => { clearTimeout(espera); espera = setTimeout(aplicar, 120); });
  ['tipo', 'acesso', 'painel', 'ordem'].forEach((k) => campos[k].addEventListener('change', aplicar));

  tabela.querySelectorAll('.us-th').forEach((b) => {
    b.addEventListener('click', () => {
      const base = b.dataset.ordem;              // direção que faz mais sentido no 1º clique
      const chave = base.replace('-', '');
      const atual = campos.ordem.value;
      let nova = base;
      if (atual.replace('-', '') === chave) nova = atual.startsWith('-') ? chave : '-' + chave;
      if (!Array.from(campos.ordem.options).some((o) => o.value === nova)) {
        // garante a opção no select (ex.: "Mais clientes" invertido)
        const o = document.createElement('option');
        o.value = nova; o.textContent = b.textContent.trim() + (nova.startsWith('-') ? ' (decrescente)' : ' (crescente)');
        campos.ordem.appendChild(o);
      }
      campos.ordem.value = nova;
      aplicar();
    });
  });

  function limpar() {
    campos.q.value = ''; campos.tipo.value = ''; campos.acesso.value = ''; campos.painel.value = '';
    campos.ordem.value = ORDEM_PADRAO;
    aplicar();
    campos.q.focus();
  }
  btnLimpar.addEventListener('click', limpar);
  vazio.querySelector('[data-limpar]').addEventListener('click', limpar);

  // "/" foca a busca (atalho comum), se não estiver digitando em outro campo
  document.addEventListener('keydown', (e) => {
    if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
    const alvo = e.target;
    if (alvo && (alvo.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(alvo.tagName))) return;
    e.preventDefault(); campos.q.focus();
  });

  aplicar();
})();
