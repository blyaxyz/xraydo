# xraydo

`xraydo` — интерактивная панель и CLI для установки и управления Xray,
Hysteria 2 и Telemt на Linux-сервере. Проект работает как на одном сервере,
так и в схеме master → node.

- мастер установки на русском и английском;
- единое меню для пользователей, ключей, TLS, обновления и удаления;
- Xray REALITY с целью `ya.ru` по умолчанию;
- Hysteria 2 и Telegram MTProxy на базе Telemt;
- сертификаты Let's Encrypt для домена или публичного IP;
- короткие ID пользователей и числовые алиасы;
- подписки, QR-коды, аудит и резервные копии;
- защищённый control plane между master и node.

> [!IMPORTANT]
> Установщик ниже загружается из текущей ветки `master`. Перед запуском на
> важном сервере рекомендуется просмотреть `install.sh` и сделать backup.

## Быстрый старт

Требуется Linux-сервер с root-доступом. Поддерживаются `apt`, `dnf`, `yum`,
`zypper` и `apk`, архитектуры `amd64` и `arm64`.

### Через curl

```bash
curl -fsSLO https://raw.githubusercontent.com/blyaxyz/xraydo/refs/heads/master/install.sh
bash ./install.sh
```

Одной командой:

```bash
curl -fsSL https://raw.githubusercontent.com/blyaxyz/xraydo/refs/heads/master/install.sh -o install.sh && bash ./install.sh
```

### Через wget

```bash
wget -O install.sh https://raw.githubusercontent.com/blyaxyz/xraydo/refs/heads/master/install.sh
bash ./install.sh
```

Установщик проверит окружение, доставит зависимости, установит выбранные ядра,
создаст systemd/OpenRC-службы и откроет меню `xraydo`.

## Что выбрать в установщике

| Сценарий | Профиль | Когда использовать |
|---|---|---|
| Быстрая установка | `quick` | Один сервер, полностью автоматически |
| Продвинутая установка | `advanced` | Роль standalone/master/node и ручные параметры |
| Только Telegram Proxy | `--telemt-only` | Xray и Hysteria не нужны |

Для большинства пользователей подходит `quick`. После его выбора установщик
больше ничего не спрашивает: автоматически ставит все три ядра, настраивает
REALITY с `ya.ru`, Telemt на `8443` с Fake TLS `vk.com`, выпускает сертификат
Let's Encrypt на определённый публичный IP и создаёт пользователя `default`.
В конце он выводит подписку и все готовые ключи.

Для домена, схемы master → node, другого набора ядер, TLS, портов или лимитов
выберите `advanced`: все ручные настройки собраны только в этом сценарии.

## Только Telemt / Telegram Proxy

```bash
sudo bash ./install.sh --telemt-only
```

Мастер спросит только язык, порт (например, `8443`) и Fake TLS-домен (например,
`vk.com`). Публичный адрес, пользователь `hello`, конфигурация, служба и
автозапуск настраиваются автоматически. В конце появятся готовые ссылки:

```text
tg://proxy?server=...&port=...&secret=...
https://t.me/proxy?server=...&port=...&secret=...
```

Копия ссылок сохраняется с правами `0600` в `/var/lib/xraydo`. Если выбранный
порт занят другой программой, установщик сообщит об этом и не заменит его без
вашего ведома.

## Первые шаги

```bash
sudo xraydo                 # открыть интерактивное меню
sudo xraydo status          # проверить компоненты
sudo xraydo system status   # проверить установку
xraydo --help               # показать весь CLI
```

Меню покрывает повседневный lifecycle: создание и удаление пользователей,
ключи, подписки, TLS, сервисы, обновление, деинсталляцию и полную очистку.
При входе в раздел пользователей список выводится сразу. После выбора
пользователя открывается карточка с комментарием, подпиской, всеми ключами и
действиями над этим пользователем без повторного ввода имени или ID.

## Пользователи

```bash
sudo xraydo user create alice
sudo xraydo user list
sudo xraydo user show alice
```

`user show` — административная команда: она выводит комментарий, состояние и
содержимое подписки, а также все credentials и клиентские ссылки пользователя.
Вывод содержит секреты, поэтому его не следует отправлять в публичные журналы.

Ключи также можно посмотреть напрямую без сокращения вывода:

```bash
sudo xraydo key list --user alice --full
sudo xraydo key show ACCESS_KEY_ID
sudo xraydo key show ACCESS_KEY_ID --redact  # скрыть credentials
```

Пользователю назначаются короткий семисимвольный ID и дополнительный числовой
алиас. В командах можно использовать имя, короткий ID или числовой алиас.

Частые операции:

```bash
sudo xraydo user set-enabled alice false
sudo xraydo user set-enabled alice true
sudo xraydo user rename alice alice-new
sudo xraydo user set-note alice-new "Телефон"
sudo xraydo user search alice
```

Перед удалением можно проверить план:

```bash
sudo xraydo user delete-plan alice-new
sudo xraydo user delete alice-new
sudo xraydo user delete-status alice-new
```

Создание пользователя выполняется локально и не ждёт сетевого REALITY-скана.
Сканирование и смена цели запускаются отдельно.

## Xray REALITY

Цель и SNI по умолчанию — `ya.ru`:

```bash
sudo xraydo reality scan --target ya.ru --sni ya.ru
xraydo reality configure --help
```

Цель можно явно задать при установке:

```bash
sudo bash ./install.sh --reality-target ya.ru
```

## Подписки и QR-коды

```bash
sudo xraydo subscription create alice
sudo xraydo subscription show alice
sudo xraydo subscription qr alice
sudo xraydo subscription rotate alice
sudo xraydo subscription revoke alice
```

Секреты и bootstrap-данные не пишутся в общий журнал. В неинтерактивном режиме
одноразовые данные сохраняются в root-only файл, путь к которому выводится после
установки. При создании базового пользователя этот файл содержит HTTPS-подписку,
полную карточку пользователя и его ключи.

## TLS и Let's Encrypt

Установочный quiz предлагает сертификат для домена или публичного IP и
запрашивает email. Для IP-сертификатов требуется Certbot 5.4 или новее; если
пакет дистрибутива устарел, установщик использует актуальный Certbot через snap.

```bash
sudo xraydo tls status
sudo xraydo tls acme-issue --identifier example.com --email admin@example.com
sudo xraydo tls acme-renew --identifier example.com
```

Установщик добавляет timer `xraydo-certbot-renew.timer` и deploy-hook. После
продления сертификат импортируется, а зависимые сервисы перезапускаются. Для
проверки ACME публичный TCP-порт 80 должен быть доступен.

## Компоненты

```bash
sudo xraydo status
sudo xraydo telemt status
sudo xraydo hysteria status
sudo xraydo inbound list
sudo xraydo key list --user alice
```

Подробные подкоманды доступны через `--help`, например `xraydo telemt --help`.

## Обновление и удаление

Исходный `install.sh` искать не нужно: установленная команда `xraydo` содержит
полный lifecycle.

```bash
sudo xraydo system update --dry-run
sudo xraydo system update --yes
sudo xraydo system uninstall --yes  # сохранить конфигурацию и state
sudo xraydo system purge --dry-run
sudo xraydo system purge --yes      # удалить все данные xraydo
```

> [!CAUTION]
> `purge` необратимо удаляет пользователей, ключи, TLS-данные, логи и state.
> Сначала проверьте `--dry-run` и сделайте резервную копию.

## Неинтерактивная установка

```bash
sudo bash ./install.sh --non-interactive --yes --lang ru
sudo bash ./install.sh --non-interactive --yes --answers answers.example
bash ./install.sh --dry-run --non-interactive
```

Файл ответов имеет строгий формат `KEY=VALUE`: неизвестные параметры
отклоняются, сам файл не исполняется как shell-код.

## Master → node

Установите профиль `master` на управляющем сервере и `node` на остальных.
Control plane использует взаимную аутентификацию, ограниченные по времени
токены, защиту от replay и аудит операций.

```bash
xraydo control --help
xraydo agent --help
xraydo node --help
xraydo sync --help
```

Не публикуйте control endpoint без firewall: разрешайте подключения только с
доверенных адресов master/node.

## Диагностика

```bash
sudo xraydo status
sudo xraydo audit list
systemctl status xraydo-xray xraydo-hysteria xraydo-telemt
journalctl -u xraydo-xray -n 100 --no-pager
systemctl status xraydo-certbot-renew.timer
```

Основные данные находятся в `/var/lib/xraydo`, конфигурация — в `/etc/xraydo`,
журналы установки — в `/var/log/xraydo`. Приватные ключи, state и одноразовые
credentials должны оставаться доступными только root.

## Безопасность

- запускайте административные команды через `sudo`;
- не отправляйте пароли, ключи и subscription URL в публичные чаты;
- ограничьте управляющие порты firewall;
- проверяйте `xraydo audit list` и резервные копии;
- удаляйте ненужные bootstrap-файлы после импорта;
- перед обновлением просматривайте изменения в `install.sh`.

Порядок сообщения об уязвимостях: [SECURITY.md](SECURITY.md).
Проверка подписей, SBOM и provenance релиза: [docs/SUPPLY_CHAIN.md](docs/SUPPLY_CHAIN.md).

## Разработка и тесты

```bash
git clone https://github.com/blyaxyz/xraydo.git
cd xraydo
PYTHONPATH=src python3 -m unittest discover -s tests -v
bash tests/test_install.sh
```

Локальный запуск без установки:

```bash
PYTHONPATH=src XRAYDO_DATA_DIR=/tmp/xraydo-dev python3 -m xraydo.cli --help
```

План работ: [TODO.md](TODO.md). История изменений: [CHANGELOG.md](CHANGELOG.md).
Поддерживаемые Linux-системы: [docs/SUPPORT_MATRIX.md](docs/SUPPORT_MATRIX.md).

## English quick start

Download the installer from the current `master` branch and run it as root:

```bash
curl -fsSLO https://raw.githubusercontent.com/blyaxyz/xraydo/refs/heads/master/install.sh
chmod +x install.sh
sudo bash ./install.sh
```

Or use wget:

```bash
wget -O install.sh https://raw.githubusercontent.com/blyaxyz/xraydo/refs/heads/master/install.sh
chmod +x install.sh
sudo bash ./install.sh
```

Choose `quick` for a fully automatic single-server installation. There are no
more questions after that selection; the installer prints the subscription and
all client keys when it finishes. Choose `advanced` when you need custom roles,
cores, TLS, ports, or user limits. Then run `sudo xraydo` for the interactive
menu, `sudo xraydo status` for health information, or `xraydo --help` for the
complete CLI reference.
