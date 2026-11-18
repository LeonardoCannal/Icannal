// Página "Início": os números de vendas do promotor, buscados do MICHAEL
// pelo CPF dele. Nada fica guardado no navegador: é buscado a cada visita.
(function () {
  const raiz = document.getElementById('ini');
  const estado = document.getElementById('ini-estado');
  const conteudo = document.getElementById('ini-conteudo');
  const selMes = document.getElementById('ini-mes');
  const selPromotor = document.getElementById('ini-promotor');
  const sub = document.getElementById('ini-sub');
  const promotor = raiz.dataset.promotor || '';
  const MESES = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];
  const MESES_LONGOS = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro'];

  const esc = (v) => String(v == null ? '' : v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const inteiro = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 0 });
  const umaCasa = new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  function moeda(v) {
    const n = Number(v) || 0;
    const casas = Number.isInteger(n) ? 0 : 2;
    return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', minimumFractionDigits: casas, maximumFractionDigits: casas }).format(n);
  }
  function mesCurto(c) { const [a, m] = String(c).split('-'); return m ? `${MESES[+m - 1]}/${a.slice(2)}` : ''; }
  function mesLongo(c) { const [a, m] = String(c).split('-'); return m ? `${MESES_LONGOS[+m - 1]} de ${a}` : ''; }
  function mesAnterior(c) { let [a, m] = String(c).split('-').map(Number); m -= 1; if (m < 1) { m = 12; a -= 1; } return `${a}-${String(m).padStart(2, '0')}`; }
  function dataBr(iso) {
    const m = String(iso || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
    return m ? `${m[3]}/${m[2]}/${m[1]}` : '';
  }
  function dataHoraBr(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d)) return dataBr(iso);
    return new Intl.DateTimeFormat('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }).format(d);
  }
  const plural = (n, um, varios) => `${inteiro.format(n)} ${n === 1 ? um : varios}`;

  function variacao(pct, d) {
    if (pct == null) return '<p class="ini-var neutra">sem comparação com o mês anterior</p>';
    const base = `vs ${mesCurto(mesAnterior(d.competencia))}${d.em_andamento && d.ate_dia ? ' até o dia ' + d.ate_dia : ''}`;
    if (pct === 0) return `<p class="ini-var neutra"><strong>igual</strong> ${base}</p>`;
    const sobe = pct > 0;
    return `<p class="ini-var ${sobe ? 'sobe' : 'desce'}"><strong><span aria-hidden="true">${sobe ? '↑' : '↓'}</span> ` +
      `<span class="ini-so-leitor">${sobe ? 'subiu' : 'caiu'}</span> ${umaCasa.format(Math.abs(pct))}%</strong> ${base}</p>`;
  }

  function kpi(rotulo, valor, extra, destaque) {
    return `<article class="ini-kpi${destaque ? ' destaque' : ''}"><h3>${rotulo}</h3><p class="ini-valor">${valor}</p>${extra || ''}</article>`;
  }

  function desenharKpis(d) {
    const i = d.indicadores;
    const f = i.faturamento;
    let extraFat = variacao(f.variacao_pct, d);
    if (f.anterior_mes_inteiro != null) {
      const ant = mesCurto(mesAnterior(d.competencia));
      extraFat += `<p class="ini-nota">${d.em_andamento && d.ate_dia && f.anterior_ate_dia != null ? `Até o dia ${d.ate_dia} de ${ant}: ${moeda(f.anterior_ate_dia)} · mês inteiro: ` : `${ant}: `}${moeda(f.anterior_mes_inteiro)}</p>`;
    }
    if (d.em_andamento && d.ate_dia) {
      const [a, m] = d.competencia.split('-').map(Number);
      const diasNoMes = new Date(a, m, 0).getDate();
      const projecao = Math.round((f.valor / d.ate_dia) * diasNoMes);
      extraFat = `<p class="ini-projecao">Projeção até o fim do mês: <strong>${moeda(projecao)}</strong></p>` + extraFat;
    }
    const c = i.carteira;
    document.getElementById('ini-kpis').innerHTML = [
      kpi('Faturamento', moeda(f.valor), extraFat, true),
      kpi('Pedidos pagos', inteiro.format(i.pedidos_pagos.valor), variacao(i.pedidos_pagos.variacao_pct, d)),
      kpi('Ticket médio', moeda(i.ticket_medio.valor), variacao(i.ticket_medio.variacao_pct, d)),
      kpi('Frascos vendidos', inteiro.format(i.frascos_vendidos.valor), variacao(i.frascos_vendidos.variacao_pct, d)),
      kpi('Comissão', moeda(i.comissao.valor), variacao(i.comissao.variacao_pct, d)),
      kpi('Clientes novos', inteiro.format(i.clientes_novos.valor), variacao(i.clientes_novos.variacao_pct, d) +
        `<p class="ini-nota">${plural(i.clientes_novos.pacientes_compraram, 'paciente comprou', 'pacientes compraram')} no mês</p>`),
      kpi('Carteira', `${inteiro.format(c.clientes)} <small>${c.clientes === 1 ? 'cliente' : 'clientes'}</small>`,
        `<p class="ini-nota">${plural(c.medicos, 'médico', 'médicos')}${c.medicos_novos ? ` (${plural(c.medicos_novos, 'novo', 'novos')} no mês)` : ''}</p>`),
    ].join('');
  }

  function desenharPedidos(d) {
    const lista = d.pedidos_pagos;
    document.getElementById('ini-pedidos-titulo').textContent = 'Pedidos pagos em ' + mesCurto(d.competencia);
    document.getElementById('ini-pedidos-qtd').textContent = plural(lista.length, 'pedido', 'pedidos');
    const alvo = document.getElementById('ini-pedidos');
    if (!lista.length) { alvo.innerHTML = `<p class="ini-vazio">Nenhum pedido pago em ${mesCurto(d.competencia)}${d.em_andamento ? ' até agora' : ''}.</p>`; return; }
    alvo.innerHTML = '<div class="ini-rolagem"><table class="ini-tabela"><thead><tr>' +
      '<th>Pedido</th><th>Cliente</th><th>Pago em</th><th>Pagamento</th><th class="num">Frascos</th><th class="num">Valor base</th><th class="num">Comissão</th><th>Etapa</th>' +
      '</tr></thead><tbody>' + lista.map((p) =>
        `<tr><td data-rotulo="Pedido" translate="no">${esc(p.pedido) || '—'}</td><td data-rotulo="Cliente" translate="no">${esc(p.cliente) || '—'}</td>` +
        `<td data-rotulo="Pago em">${dataHoraBr(p.pago_em) || '—'}</td><td data-rotulo="Pagamento">${esc(p.pagamento) || '—'}</td>` +
        `<td data-rotulo="Frascos" class="num">${inteiro.format(p.frascos)}</td><td data-rotulo="Valor base" class="num">${moeda(p.valor_base)}</td>` +
        `<td data-rotulo="Comissão" class="num">${moeda(p.comissao)}</td><td data-rotulo="Etapa">${p.etapa ? `<span class="ini-etapa">${esc(p.etapa)}</span>` : '—'}</td></tr>`
      ).join('') + '</tbody></table></div>';
  }

  function numeros(itens) {
    return '<div class="ini-numeros">' + itens.map(([n, rotulo, cor]) =>
      `<div class="ini-numero ${cor || ''}"><strong>${inteiro.format(n)}</strong><span>${rotulo}</span></div>`).join('') + '</div>';
  }
  function listaNomes(titulo, itens, vazio) {
    if (!itens.length) return `<h3 class="ini-lista-titulo">${titulo}</h3><p class="ini-vazio">${vazio}</p>`;
    return `<h3 class="ini-lista-titulo">${titulo}</h3><ul class="ini-lista">` +
      itens.map((x) => `<li><span translate="no">${esc(x.nome)}</span><time>${dataBr(x.data)}</time></li>`).join('') + '</ul>';
  }

  function diaMes(iso) { const m = String(iso || '').match(/^\d{4}-(\d{2})-(\d{2})/); return m ? `${m[2]}/${m[1]}` : ''; }
  function seloVisita(quando) {
    return quando ? `<span class="ini-selo ok">Visitado ${diaMes(quando)}</span>` : '<span class="ini-selo falta">Não visitado</span>';
  }
  const SITUACAO = { vai_recomprar: 'vai recomprar', acabou_de_vencer: 'acabou de vencer', venceu: 'venceu' };

  function desenharRecompra(d) {
    const r = d.pacientes_recompra;
    const alvo = document.getElementById('ini-rec');
    let html = numeros([[r.vao_recomprar, 'vão recomprar', 'bom'], [r.acabaram_de_vencer, 'acabaram de vencer', 'atencao'], [r.venceram_ha_mais_tempo, 'venceram há mais tempo', 'ruim']]);
    if (!r.lista.length) { alvo.innerHTML = html + listaNomes('Próximas recompras', r.proximas, 'Nenhuma recompra prevista.'); return; }
    const faltam = r.lista.filter((p) => !p.medico_visitado_em).length;
    html += `<div class="ini-lista-topo"><h3 class="ini-lista-titulo">Pacientes de recompra</h3>` +
      `<label class="ini-filtro-check"><input type="checkbox" id="ini-rec-so-falta"${faltam ? ' checked' : ''}> Só com médico ainda não visitado em ${mesCurto(d.competencia)} (${faltam})</label></div>` +
      '<ul class="ini-lista ini-lista-rica" id="ini-rec-lista"></ul>';
    alvo.innerHTML = html;
    const ul = document.getElementById('ini-rec-lista');
    const check = document.getElementById('ini-rec-so-falta');
    function listar() {
      const itens = r.lista.filter((p) => !check.checked || !p.medico_visitado_em);
      ul.innerHTML = itens.length ? itens.map((p) =>
        `<li><div><span translate="no">${esc(p.nome)}</span>` +
        `<small>${SITUACAO[p.situacao] || ''}${p.data ? ' em ' + dataBr(p.data) : ''}${p.medico_nome ? ` · <span translate="no">${esc(p.medico_nome)}</span>` : ''}</small></div>` +
        `${p.medico_nome ? seloVisita(p.medico_visitado_em) : ''}</li>`).join('')
        : '<li class="ini-vazio">Todos os médicos desses pacientes já foram visitados neste mês.</li>';
    }
    check.addEventListener('change', listar);
    listar();
  }

  function desenharTres(d) {
    desenharRecompra(d);

    const m = d.medicos;
    const total = m.ja_prescreveram + m.nunca_prescreveram;
    const pct = total ? Math.round((m.ja_prescreveram / total) * 100) : 0;
    let listaMed;
    if (m.prescritores.length) {
      const visitados = m.prescritores.filter((x) => x.visitado_em).length;
      const ordenados = m.prescritores.slice().sort((a, b) => (a.visitado_em ? 1 : 0) - (b.visitado_em ? 1 : 0));
      listaMed = `<div class="ini-lista-topo"><h3 class="ini-lista-titulo">Já prescreveram para ${promotor ? 'este promotor' : 'você'}</h3>` +
        `<span class="ini-sub">${visitados} de ${m.prescritores.length} visitados em ${mesCurto(d.competencia)}</span></div>` +
        '<ul class="ini-lista ini-lista-rica">' + ordenados.map((x) =>
          `<li><div><span translate="no">${esc(x.nome)}</span><small>${[x.crm ? 'CRM ' + esc(x.crm) + (x.uf ? ' ' + esc(x.uf) : '') : '', esc(x.cidade),
            x.ultima_prescricao ? 'última prescrição em ' + dataBr(x.ultima_prescricao) : '',
            x.pedidos_pagos_no_mes == null ? '' : (x.pedidos_pagos_no_mes ? plural(x.pedidos_pagos_no_mes, 'pedido pago', 'pedidos pagos') + ' em ' + mesCurto(d.competencia) : 'sem venda em ' + mesCurto(d.competencia))].filter(Boolean).join(' · ')}</small></div>${seloVisita(x.visitado_em)}</li>`).join('') + '</ul>';
    } else {
      listaMed = listaNomes('Últimas prescrições', m.ultimas_prescricoes, 'Nenhuma prescrição ainda.');
    }
    document.getElementById('ini-med').innerHTML =
      numeros([[m.ja_prescreveram, 'já prescreveram', 'bom'], [m.nunca_prescreveram, 'nunca prescreveram', 'ruim']]) +
      `<div class="ini-barra" role="img" aria-label="${pct}% dos médicos já prescreveram"><span style="width:${pct}%"></span></div>` + listaMed;

    const p = d.prescricoes_por_mes;
    const max = Math.max(1, ...p.serie.map((x) => x.total));
    const resumo = p.serie.map((x) => `${mesCurto(x.mes)}: ${x.total}`).join(', ');
    document.getElementById('ini-pre').innerHTML =
      numeros([[p.no_mes, 'em ' + mesCurto(d.competencia), 'bom'], [p.ultimos_12_meses, 'nos últimos 12 meses', '']]) +
      (p.serie.length
        ? `<div class="ini-grafico" role="img" aria-label="Prescrições por mês. ${esc(resumo)}">` + p.serie.map((x) =>
            `<div class="ini-coluna${x.mes === d.competencia ? ' atual' : ''}" title="${mesCurto(x.mes)}: ${x.total}">` +
            `<span class="ini-coluna-valor">${x.total || ''}</span>` +
            `<span class="ini-coluna-barra" style="height:${x.total ? Math.max(4, Math.round((x.total / max) * 100)) : 0}%"></span>` +
            `<span class="ini-coluna-mes">${MESES[+x.mes.slice(5) - 1]}</span></div>`).join('') + '</div>'
        : '<p class="ini-vazio">Sem prescrições nos últimos 12 meses.</p>');
  }

  function mostrarEstado(html) { conteudo.hidden = true; estado.hidden = false; estado.innerHTML = html; }

  function preencherMeses(d) {
    const lista = d.competencias && d.competencias.length ? d.competencias : [d.competencia];
    if (!lista.includes(d.competencia)) lista.unshift(d.competencia);
    selMes.innerHTML = lista.map((c) => `<option value="${c}"${c === d.competencia ? ' selected' : ''}>${mesCurto(c)}</option>`).join('');
    selMes.disabled = lista.length < 2;
  }

  async function carregar(competencia) {
    const params = new URLSearchParams();
    if (competencia) params.set('competencia', competencia);
    if (promotor) params.set('promotor', promotor);
    let r;
    try {
      const resp = await fetch('/api/inicio?' + params.toString(), { cache: 'no-store' });
      r = await resp.json();
    } catch (_) { r = { estado: 'erro', mensagem: 'Sem conexão. Confira a internet e tente de novo.' }; }

    if (r.estado === 'ok') {
      const d = r.dados;
      sub.textContent = `${promotor ? 'Números' : 'Seus números'} de ${mesLongo(d.competencia)}` +
        (d.em_andamento ? ` (mês em andamento${d.ate_dia ? ', até o dia ' + d.ate_dia : ''}).` : '.');
      preencherMeses(d);
      desenharKpis(d); desenharPedidos(d); desenharTres(d);
      estado.hidden = true; conteudo.hidden = false;
      return;
    }
    if (r.estado === 'sem_dados') {
      sub.textContent = 'Seus números de vendas aparecem aqui.';
      mostrarEstado('<div class="ini-card ini-aviso"><h2>Ainda não há números de vendas ' + (promotor ? 'para este promotor' : 'para você') + '</h2>' +
        '<p>Assim que houver pedidos no seu nome, eles aparecem nesta página. Enquanto isso, siga com a prospecção.</p></div>');
      return;
    }
    mostrarEstado('<div class="ini-card ini-aviso"><h2>Não foi possível mostrar os números agora</h2>' +
      `<p>${esc(r.mensagem || r.erro || 'Tente de novo em alguns minutos.')}</p>` +
      '<button type="button" class="ini-atalho principal" id="ini-de-novo">Tentar de novo</button></div>');
    document.getElementById('ini-de-novo').addEventListener('click', () => {
      mostrarEstado('<p class="ini-carregando">Buscando seus números…</p>'); carregar(competencia);
    });
  }

  selMes.addEventListener('change', () => {
    conteudo.classList.add('trocando');
    carregar(selMes.value).finally(() => conteudo.classList.remove('trocando'));
  });
  if (selPromotor) selPromotor.addEventListener('change', () => {
    location.href = '/inicio' + (selPromotor.value ? '?promotor=' + encodeURIComponent(selPromotor.value) : '');
  });

  carregar('');
})();
