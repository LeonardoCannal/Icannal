// Service worker do i.cannal.
//
// O que ele faz: deixa o site instalável (ícone na tela do celular, abre em
// tela cheia) e guarda em cache só os arquivos que não mudam a cada visita
// (CSS, ícones). Páginas e dados (buscas, painel, agenda, dashboards) SEMPRE
// vêm da internet — nunca do cache — porque essas informações mudam o tempo
// todo e mostrar uma versão velha seria pior do que mostrar "sem conexão".
//
// Se aumentar a versão aqui embaixo (v1 -> v2), o navegador troca o cache
// antigo pelo novo sozinho na próxima visita.
const CACHE_VERSAO = 'icannal-v4';

const ARQUIVOS_ESTATICOS = [
  '/static/auth.css',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/fontes/inter.woff2',
  '/static/fontes/montserrat.woff2',
  '/offline',
];

self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_VERSAO).then((cache) => cache.addAll(ARQUIVOS_ESTATICOS).catch(() => {}))
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((nomes) =>
      Promise.all(nomes.filter((nome) => nome !== CACHE_VERSAO).map((nome) => caches.delete(nome)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const { request } = event;

  // Só GET entra no cache. POST/PUT/DELETE (login, salvar médico, agendar
  // visita etc.) sempre precisam ir direto pra internet.
  if (request.method !== 'GET') return;

  const url = new URL(request.url);

  // CSS e ícones: usa o cache na hora (site abre rápido), mas atualiza o
  // cache em segundo plano pra próxima vez.
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(request).then((doCache) => {
        const daRede = fetch(request)
          .then((resposta) => {
            if (resposta.ok) {
              const copia = resposta.clone();
              caches.open(CACHE_VERSAO).then(async (cache) => {
                // versão nova de um arquivo (?v=...): apaga as antigas dele
                if (url.search) {
                  for (const chave of await cache.keys()) {
                    const u = new URL(chave.url);
                    if (u.pathname === url.pathname && u.search !== url.search) cache.delete(chave);
                  }
                }
                cache.put(request, copia);
              });
            }
            return resposta;
          })
          .catch(() => doCache);
        return doCache || daRede;
      })
    );
    return;
  }

  // Todo o resto (páginas e /api/...) vem sempre da internet. Se não tiver
  // conexão: uma tela de aviso pras páginas, e uma resposta JSON de erro
  // pras chamadas de dados (pra não travar o site esperando algo que nunca
  // vai chegar).
  event.respondWith(
    fetch(request).catch(() => {
      if (request.mode === 'navigate') {
        return caches.match('/offline');
      }
      return new Response(
        JSON.stringify({ ok: false, erro: 'Sem conexão com a internet.' }),
        { status: 503, headers: { 'Content-Type': 'application/json' } }
      );
    })
  );
});
