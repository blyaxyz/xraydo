<p align="center">
  <a href="./README.md"><img alt="English" src="https://img.shields.io/badge/Language-English-64748b?style=for-the-badge"></a>
  <a href="./README.ru.md"><img alt="Русский" src="https://img.shields.io/badge/Язык-Русский-2563eb?style=for-the-badge"></a>
</p>

# xraydo

`xraydo` — интерактивная панель и командная строка для установки и управления
Xray, Hysteria 2 и Telemt на поддерживаемых версиях Debian и Ubuntu Server.
Проект работает как на одном сервере, так и в защищённой схеме master → node.

## Основные возможности

- быстрая установка одной командой и настраиваемый продвинутый режим;
- единое меню для пользователей, ключей, подписок, TLS, обновления и удаления;
- Xray REALITY, Hysteria 2 и Telegram MTProxy на базе Telemt;
- сертификаты Let's Encrypt для домена или поддерживаемого публичного IP;
- короткие семисимвольные ID пользователей и удобные цифровые алиасы;
- ссылки подписки, QR-коды, журнал аудита и резервные копии;
- аутентифицированный control plane master → node с защитой от replay-атак;
- русский и английский интерфейсы установщика и CLI.

## Быстрая установка

Скачайте актуальный установщик напрямую из ветки `master` и запустите его:

```bash
curl -fsSL https://raw.githubusercontent.com/blyaxyz/xraydo/refs/heads/master/install.sh -o install.sh
sudo bash ./install.sh
```

Или используйте `wget`:

```bash
wget -O install.sh https://raw.githubusercontent.com/blyaxyz/xraydo/refs/heads/master/install.sh
sudo bash ./install.sh
```

Установщик проверит операционную систему, установит зависимости и выбранные
ядра, создаст службы systemd и откроет меню `xraydo`.

## Режимы установки

| Режим | Выбор или команда | Для чего подходит |
|---|---|---|
| Быстрая установка | `quick` | Один сервер с полностью автоматической настройкой |
| Продвинутая установка | `advanced` | Свои роли, ядра, TLS, порты и лимиты |
| Только Telegram Proxy | `--telemt-only` | Серверу нужен только Telemt/MTProxy |

Для большинства пользователей подходит быстрая установка. После её выбора
xraydo не задаёт дополнительных вопросов: устанавливает все три ядра,
настраивает REALITY, запускает Telemt с Fake TLS на порту `8443`, получает
сертификат Let's Encrypt для определённого публичного адреса, если он
поддерживается, и создаёт пользователя `default` без лимитов. На итоговом
экране показываются ссылка подписки, её QR-код и все клиентские ссылки.

Выберите продвинутую установку, если нужен домен, роль
standalone/master/node, другой набор ядер, свои порты, параметры TLS или
пользовательские лимиты.

## Только Telegram Proxy

```bash
sudo bash ./install.sh --telemt-only
```

В этом режиме мастер спрашивает только язык интерфейса, порт и Fake TLS-домен.
Публичный адрес, пользователь `hello`, служба и автозапуск настраиваются
автоматически. В результате выводятся ссылки обоих форматов:

```text
tg://proxy?server=...&port=...&secret=...
https://t.me/proxy?server=...&port=...&secret=...
```

Копия сохраняется с правами `0600` в `/var/lib/xraydo`. Если выбранный порт уже
занят, установщик сообщит о конфликте, а не подставит другой порт без ведома
пользователя.

## Первые шаги

```bash
sudo xraydo                 # открыть интерактивное меню
sudo xraydo status          # проверить компоненты и протоколы
sudo xraydo system status   # проверить установку
xraydo --help               # показать все команды CLI
```

Интерактивное меню покрывает те же повседневные задачи, что и CLI:
пользователей, ключи, подписки, TLS, службы, обновление, деинсталляцию и полное
удаление. Раздел пользователей сразу показывает их список. После выбора
открывается полная карточка с комментарием, подпиской, клиентскими ссылками,
credentials и доступными действиями.

## Пользователи и ключи доступа

```bash
sudo xraydo user create alice
sudo xraydo user list
sudo xraydo user show alice
```

`user show` — административная команда. Она выводит комментарий, состояние,
содержимое подписки, credentials и клиентские ссылки пользователя. Результат
содержит секреты — не публикуйте его в открытых журналах и чатах.

Ключи доступа также можно посмотреть напрямую:

```bash
sudo xraydo key list --user alice --full
sudo xraydo key show ACCESS_KEY_ID
sudo xraydo key show ACCESS_KEY_ID --redact  # скрыть credentials
```

При создании ключа xraydo определяет публичный IP сервера через HTTPS,
перепроверяет страну сети через RDAP, сохраняет результат локально и добавляет
локацию в имя, например `🇵🇱 Польша · phone`. Это же название используется как
remark в клиентской ссылке. Временная недоступность GeoIP не блокирует создание
ключа, а автоматическое добавление локации можно отключить в настройках.

Проверить или принудительно обновить локацию можно командой:

```bash
sudo xraydo country location --refresh
```

Каждому пользователю назначаются семисимвольный ID и цифровой алиас. В командах
можно указывать имя, короткий ID или цифровой алиас.

## Xray REALITY

Проверить цель REALITY и настроить службу можно командами:

```bash
sudo xraydo reality scan --target ya.ru --sni ya.ru
xraydo reality configure --help
```

## Подписки и QR-коды

```bash
sudo xraydo subscription create alice
sudo xraydo subscription show alice
sudo xraydo subscription qr alice
sudo xraydo subscription rotate alice
sudo xraydo subscription revoke alice
```

Секреты и bootstrap-данные не попадают в общий журнал. В неинтерактивном
режиме одноразовые credentials записываются в доступный только root файл, а
установщик показывает путь к нему. Для первого пользователя файл содержит
HTTPS-подписку, полную карточку и ключи доступа.

## TLS и Let's Encrypt

В продвинутом режиме установочный мастер может запросить сертификат для домена
или поддерживаемого публичного IP и попросит указать email. Для IP-сертификатов
нужен Certbot 5.4 или новее; если пакет дистрибутива устарел, установщик
использует актуальную snap-версию.

```bash
sudo xraydo tls status
sudo xraydo tls acme-issue --identifier example.com --email admin@example.com
sudo xraydo tls acme-renew --identifier example.com
```

Установщик включает таймер `xraydo-certbot-renew.timer` и deploy-hook. После
успешного продления xraydo импортирует сертификат и перезапускает зависимые
службы. Для ACME-проверки публичный TCP-порт 80 должен быть доступен извне.

## Службы и компоненты

```bash
sudo xraydo status
sudo xraydo telemt status
sudo xraydo hysteria status
sudo xraydo inbound list
sudo xraydo key list --user alice
```

Полный список действий каждой группы доступен через `--help`, например
`xraydo telemt --help`.

## Обновление и удаление

После установки исходный `install.sh` не нужен. Установленная команда `xraydo`
поддерживает полный lifecycle:

```bash
sudo xraydo system update --dry-run
sudo xraydo system update --yes
sudo xraydo system uninstall --yes  # сохранить конфигурацию и state
sudo xraydo system purge --dry-run
sudo xraydo system purge --yes      # удалить все данные xraydo
```

> [!CAUTION]
> `purge` необратимо удаляет пользователей, ключи, TLS-данные, журналы и state.
> Сначала проверьте dry run и сделайте резервную копию.

## Master → node

Установите профиль `master` на управляющем сервере, а профиль `node` — на
управляемых. Control plane использует взаимную аутентификацию, ограниченные по
времени токены, защиту от replay-атак и журнал аудита.

```bash
xraydo control --help
xraydo agent --help
xraydo node --help
xraydo sync --help
```

Не открывайте control endpoint всему интернету. Ограничьте firewall доступом
только с доверенных адресов master и node.

## Диагностика

```bash
sudo xraydo status
sudo xraydo audit list
systemctl status xraydo-xray xraydo-hysteria xraydo-telemt
journalctl -u xraydo-xray -n 100 --no-pager
systemctl status xraydo-certbot-renew.timer
```

Рабочие данные находятся в `/var/lib/xraydo`, конфигурация — в `/etc/xraydo`,
журналы установки — в `/var/log/xraydo`. Приватные ключи, state и одноразовые
credentials должны оставаться доступными только root.

## Безопасность

- запускайте административные команды через `sudo`;
- не публикуйте пароли, ключи доступа и ссылки подписок;
- ограничьте управляющие порты с помощью firewall;
- регулярно проверяйте `xraydo audit list` и резервные копии;
- удаляйте bootstrap-файлы после импорта их содержимого;
- просматривайте изменения установщика перед обновлением.

Порядок сообщения об уязвимостях описан в [SECURITY.md](SECURITY.md), а проверка
подписей, SBOM и provenance релиза — в
[docs/SUPPLY_CHAIN.md](docs/SUPPLY_CHAIN.md).

## Разработка и тесты

```bash
git clone https://github.com/blyaxyz/xraydo.git
cd xraydo
PYTHONPATH=src python3 -m unittest discover -s tests -v
bash tests/test_install.sh
```

- [Поддерживаемые версии Debian и Ubuntu](docs/SUPPORT_MATRIX.md)
- [План работ и оставшиеся задачи](TODO.md)
- [История изменений](CHANGELOG.md)
