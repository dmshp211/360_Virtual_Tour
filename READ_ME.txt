# Как запустить проект на своём компьютере

Откройте терминал (cmd) в папке проекта и выполните:

python server.py

Откройте в браузере:
- Просмотр туров:   http://localhost:3000/index.html
- Редактор:   http://localhost:3000/editor.html

==============================================
==============================================
==============================================

## На сервере в локальной сети

### Шаг 1. Перенесите проект на сервер

Скопируйте папку проекта на сервер любым способом (флешка, `scp`, `git`).

### Шаг 2. Запустите

```bash
cd /путь/к/проекту
python3 server.py --host 0.0.0.0 --port 3000
```

Сайт будет доступен по адресу `http://<IP-сервера>:3000/`

### Шаг 3. Настройте автозапуск (systemd)

Создайте файл `/etc/systemd/system/virtual-tours.service`:

```ini
[Unit]
Description=360 Virtual Tours Server
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/virtual-tours
ExecStart=/usr/bin/python3 server.py --host 0.0.0.0 --port 3000
Restart=always

[Install]
WantedBy=multi-user.target
```

Выполните:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now virtual-tours
```

---

## Безопасность в локальной сети

### Если стоит Nginx — ограничьте доступ по IP

```nginx
server {
    listen 80;
    server_name tours.loc;

    client_max_body_size 100M;

    # Разрешить только локальную сеть
    allow 192.168.1.0/24;
    allow 127.0.0.1;
    deny all;  # всем остальным — отказ

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

Если Nginx нет — просто запускайте сервер на `127.0.0.1` и пользуйтесь только на том же компьютере.

> В самом проекте уже встроена базовая защита: проверка ID туров, блокировка `../` в путях, только картинки (jpg/png/gif), лимит 100 MB на файл. Для локальной сети этого достаточно.

---

## Бэкап туров

```bash
# Скопировать папку tours в надёжное место
cp -r ./tours ./backup-tours-2026-07-16
```
