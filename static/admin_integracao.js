// Tela "Integração" do admin: testar a conexão, ligar a sincronização
// automática, sincronizar agora e marcar conflitos como resolvidos.
(function () {
  const $ = (s) => document.querySelector(s);
  const aviso = $('#rd-aviso');
  function avisar(msg, erro) {
    aviso.textContent = msg; aviso.classList.toggle('erro', !!erro); aviso.hidden = false;
    clearTimeout(avisar._t); avisar._t = setTimeout(() => { aviso.hidden = true; }, 4500);
  }
  async function chamar(url, corpo) {
    const op = corpo === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(corpo) };
    const r = await fetch(url, op);
    try { return await r.json(); } catch (_) { return { ok: false, mensagem: 'Resposta inesperada do servidor.' }; }
  }

  const testar = $('#in-testar');
  if (testar) testar.addEventListener('click', async () => {
    const res = $('#in-teste-res');
    testar.disabled = true; testar.textContent = 'Testando…'; res.textContent = '';
    const r = await chamar('/admin/integracao/testar', {});
    testar.disabled = false; testar.textContent = 'Testar conexão';
    res.textContent = r.mensagem || (r.ok ? 'Conectado.' : 'Não conectou.');
    res.classList.toggle('rd-erro-txt', !r.ok);
  });

  $('#in-cfg').addEventListener('submit', async (e) => {
    e.preventDefault();
    const botao = e.currentTarget.querySelector('button[type=submit]');
    botao.disabled = true; botao.textContent = 'Salvando…';
    const r = await chamar('/admin/integracao/config', { ligado: $('#in-ligado').checked, intervalo: $('#in-intervalo').value });
    botao.disabled = false; botao.textContent = 'Salvar';
    if (r.ok) { avisar('Ajustes salvos.'); setTimeout(() => location.reload(), 700); }
    else avisar(r.mensagem || 'Não foi possível salvar.', true);
  });

  // Enquanto sincroniza, confere de 3 em 3 s e recarrega no fim.
  let viuRodando = $('#in-script').dataset.sincronizando === '1';
  async function acompanhar() {
    const r = await chamar('/admin/integracao/status').catch(() => null);
    if (r && r.sincronizando) { viuRodando = true; setTimeout(acompanhar, 3000); return; }
    if (viuRodando) location.reload();
  }
  if (viuRodando) setTimeout(acompanhar, 3000);

  const agora = $('#in-agora');
  agora.addEventListener('click', async () => {
    agora.disabled = true; agora.textContent = 'Sincronizando…';
    const r = await chamar('/admin/integracao/sincronizar', {});
    if (!r.ok) { agora.disabled = false; agora.textContent = 'Sincronizar agora'; avisar(r.mensagem || 'Não foi possível sincronizar.', true); return; }
    avisar('Sincronizando. A primeira vez pode levar alguns minutos.');
    viuRodando = true; setTimeout(acompanhar, 2000);
  });

  document.querySelectorAll('form.in-crm').forEach((f) => f.addEventListener('submit', async (e) => {
    e.preventDefault();
    const tr = f.closest('tr');
    const num = f.querySelector('.in-crm-num');
    const uf = f.querySelector('.in-crm-uf');
    if (!num.value.replace(/\D/g, '')) { avisar('Digite o número do CRM.', true); num.focus(); return; }
    if (!uf.value) { avisar('Escolha a UF do CRM.', true); uf.focus(); return; }
    const botao = f.querySelector('button');
    botao.disabled = true; botao.textContent = 'Salvando…';
    const r = await chamar(`/admin/integracao/medicos/${tr.dataset.id}/crm`, { crm: num.value, uf: uf.value });
    if (r.ok) { tr.remove(); avisar('CRM salvo. O médico vai na próxima sincronização.'); return; }
    botao.disabled = false; botao.textContent = 'Salvar';
    avisar(r.mensagem || 'Não foi possível salvar.', true);
  }));

  document.querySelectorAll('.in-transferir').forEach((b) => b.addEventListener('click', async () => {
    if (!confirm(`Passar ${b.dataset.medico} para ${b.dataset.promotor} no MICHAEL?`)) return;
    b.disabled = true; b.textContent = 'Transferindo…';
    const r = await chamar(`/admin/integracao/conflitos/${b.dataset.id}/transferir`, {});
    if (r.ok) { b.closest('tr').remove(); avisar(r.mensagem || 'Transferido.'); return; }
    b.disabled = false; b.textContent = 'Transferir no MICHAEL';
    avisar(r.mensagem || 'Não foi possível transferir.', true);
  }));

  document.querySelectorAll('.in-resolver').forEach((b) => b.addEventListener('click', async () => {
    b.disabled = true;
    const r = await chamar(`/admin/integracao/conflitos/${b.dataset.id}/resolver`, {});
    if (r.ok) { b.closest('tr').remove(); avisar('Marcado como resolvido.'); }
    else { b.disabled = false; avisar(r.mensagem || 'Não foi possível marcar.', true); }
  }));
})();
