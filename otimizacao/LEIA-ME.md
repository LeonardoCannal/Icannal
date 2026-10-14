# Arquivos fixos direto pelo nginx

Faz o nginx entregar CSS, fontes e ícones (`/static/`) sem passar pelo Python.
O Python fica livre só para páginas e buscas, e esses arquivos ficam guardados
no navegador por até 1 ano (eles trocam de endereço quando são atualizados).

## Instalar (uma vez só, depois do `git pull`)

    sudo bash /home/cannal/cannal/otimizacao/instalar_nginx.sh

O script guarda uma cópia da configuração atual do nginx, faz a mudança e
testa. Se o teste falhar, ele volta a configuração antiga sozinho.

## Conferir

    curl -sI "https://icannal.com.br/static/fontes/inter.woff2" | grep -i -E "server|cache-control"

Deve aparecer `cache-control: public, max-age=31536000, immutable`.

## Desfazer

A cópia antiga fica ao lado do arquivo original, com o nome terminando em
`.antes-estaticos-DATA`. Para voltar:

    sudo cp /etc/nginx/sites-enabled/ARQUIVO.antes-estaticos-DATA /etc/nginx/sites-enabled/ARQUIVO
    sudo rm /etc/nginx/conf.d/icannal-cache.conf
    sudo nginx -t && sudo systemctl reload nginx
