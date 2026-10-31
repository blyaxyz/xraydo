# TODO — xraydo

Актуальный backlog проекта. Пересобран после аудита кода, тестов, документации и
GitHub workflows 31 октября 2026 года.

Проверенная база на момент аудита:

- `187` Python-тестов проходят на Debian 13/systemd test server; локально
  проходят `183`, а `4` socket-теста (включая mTLS) пропущены только из-за
  запрета socket в sandbox;
- `tests/test_install.sh` и синтаксическая проверка установщика проходят;
- локально есть незакоммиченные изменения в `install.sh`, `i18n.py` и их тестах;
- проект уже имеет terminal-only CLI, standalone-установщик, локальные роли,
  пользователей и AccessKey, подписки, private CA, REALITY, Hysteria 2, Telemt,
  inbound revisions и persistent sync queue;
- GitHub Actions запускает unit/smoke-тесты и публикует versioned installer,
  source archive и SHA-256 при создании тега.

Этот файл содержит только незавершённую работу. Подробности уже реализованных
возможностей находятся в `README.md` и `CHANGELOG.md`.

## P0 — до production-эксплуатации

### Защищённый control plane master → node

- [x] Реализовать отдельный node-agent, который получает и применяет операции
  очереди; команды `sync claim/ack/fail` не должны быть удалённым API сами по
  себе.
- [x] Добавить взаимную аутентификацию master и node: bootstrap с коротким сроком
  жизни, mTLS, rotation/revocation сертификатов и явное удаление доверия к ноде.
- [x] Защитить протокол от replay, подмены `node_id`, повторного ACK и выдачи
  операции не той ноде; ограничить размер, частоту и время жизни сообщений.
- [x] Версионировать wire protocol и capability negotiation, чтобы master не
  отправлял неподдерживаемую операцию старому агенту.
- [x] Реализовать идемпотентное применение, retry/backoff, lease/timeout для
  зависших `delivering` операций и reconciliation desired/actual state.
- [x] Добавить интеграционные тесты регистрации, потери связи, повторной доставки,
  rotation/revocation и несовместимых версий агентов.

### Целостность state и полный lifecycle пользователя

- [x] Добавить межпроцессную блокировку и транзакционную модель
  read-modify-write для `state.json`; сейчас параллельные CLI/agent процессы
  могут потерять изменения, несмотря на атомарный `os.replace`.
- [x] Отключение пользователя должно атомарно отключать все его AccessKey,
  создавать операции для всех активных нод и исключать endpoints из подписки.
- [x] Добавить удаление пользователя с plan/confirmation, grace period и
  подтверждением результата по каждой ноде; необратимое удаление секретов
  выполнять только после успешной блокировки доступа.
- [x] Унифицировать audit для create/rename/enable/disable/delete, изменений
  alias, лимитов и административных операций. Не писать значения секретов,
  токены и клиентские IP в обычный audit payload.
- [x] Добавить retention/compaction для audit, usage events, IP observations,
  inbound history и завершённых sync operations.
- [x] Добавить тесты конкурентной записи, аварии между шагами транзакции и
  восстановления после повреждённого/частично записанного state.

### Backup, restore и хранение секретов

- [ ] Реализовать версионированный backup/restore всего состояния, конфигураций,
  managed certificates и метаданных ядер; существующий TLS backup и отдельные
  core-config backup не заменяют disaster recovery.
- [ ] Шифровать backup, проверять manifest/MAC до восстановления и поддержать
  dry-run restore с проверкой версии схемы, владельцев и прав файлов.
- [ ] Определить модель защиты protocol credentials и node credentials at rest:
  root-only `0600` недостаточно для сценария утечки диска/backup.
- [ ] Добавить проверяемую процедуру восстановления master и node на чистой
  системе без повторного выпуска пользовательских credentials.

### Транзакционный install/update/uninstall

- [ ] Сделать установку и обновление транзакционными: журнал созданных объектов,
  rollback файлов, units, пользователей и бинарников при ошибке; ранее рабочая
  версия должна оставаться доступной.
- [ ] Перед обновлением сохранять проверяемый полный backup и реализовать
  автоматический rollback при провале миграции, запуска или health-check.
- [ ] Добавить `--channel` либо удалить его из продуктовых требований; production
  update в любом случае должен использовать только tag/version или явно
  подтверждённый commit, никогда плавающий `main`.
- [ ] Исключить secrets из аргументов процессов и shell history во всех путях;
  принимать чувствительные значения через `0600` файлы/stdin и редактировать их
  в plan/log.
- [ ] Добавить тесты реальной установки, повторного запуска, update, rollback,
  uninstall и purge в systemd VM и OpenRC VM.

### Health и применение политик

- [ ] Расширить `xraydo status`: отличать наличие процесса от operational state,
  проверять config, ожидаемые TCP/UDP listeners, локальные API и безопасные
  protocol-aware probes.
- [ ] Проверять HTTPS подписки, SAN/chain/expiry, REALITY target отдельно, DNS,
  disk/inodes, RAM/swap, file descriptors и права рабочих каталогов.
- [ ] Добавить историю health-состояний с retention, signed node health payload,
  maintenance mode на уровне сервисов и защиту от restart loop.
- [ ] Запускать сбор traffic/IP и применение expiry/quota/IP policies по
  расписанию, а не только при обращении к AccessKey через CLI.
- [ ] После изменения конфигурации публиковать endpoint только после ACK и
  успешного health-check конкретной ноды; при ошибке сохранять предыдущую
  рабочую конфигурацию и подписку.

## P1 — завершение пользовательского продукта

### Интерактивное меню

- [x] Перестроить `xraydo` без аргументов в полное вложенное меню: nodes, users,
  keys, subscriptions, inbounds, cores, TLS, health, audit и settings.
- [x] Добавить breadcrumbs, `назад`/`главное меню`/`помощь`/`выход`, обработку
  EOF и Ctrl+C, сохранение текущего контекста и понятный результат операции.
- [ ] Для списков добавить пагинацию, поиск и выбор по номеру; для массовых и
  опасных действий — plan/dry-run, точный объект, последствия и подтверждение.
- [x] Поддержать узкий терминал, SSH, `NO_COLOR`, Unicode и ASCII fallback; не
  показывать секреты без отдельного действия.
- [ ] Добавить snapshot/navigation-тесты EN/RU для корректного, ошибочного и
  прерванного ввода.

### Локализация

- [ ] Вынести оставшиеся строки CLI и `install.sh` в версионированные EN/RU
  каталоги; сейчас часть prompts, plan, validation и ошибок захардкожена.
- [ ] Локализовать весь wizard, update/uninstall/purge, health, core managers и
  inbound/REALITY interactive flow, сохранив стабильные английские JSON keys и
  error codes.
- [ ] Добавить pluralization/format snapshot-тесты и безопасное поведение для
  не-UTF-8 терминала.
- [ ] Дополнить русскую документацию первого запуска и основные сценарии; сейчас
  значительная часть подробного README доступна только на английском.

### Управление Xray и inbound

- [ ] Добавить Xray install/update/check-update/rollback/backup и историю
  проверенных артефактов на уровне Hysteria 2 и Telemt.
- [ ] Реализовать guided wizard для WSS/TLS inbound: отдельные SNI, HTTP Host,
  public address, path, ALPN и certificate; проверять SAN и безопасно генерировать
  path.
- [ ] Добавить управляемую rotation уже генерируемых `shortId`/`SpiderX`: plan,
  подтверждение и обновление затронутых подписок только после успешного apply.
- [ ] Добавить remote port probe через аутентифицированный agent и различать
  managed conflict, сторонний процесс, TCP и UDP до применения конфигурации.
- [ ] Завершить массовое применение/rolling restart по нодам с max-unavailable,
  canary, per-node result и автоматической остановкой при ошибках.

### TLS и подписки

- [x] Добавить ACME для DNS-домена и поддерживаемого public-IP профиля, challenge
  preflight, renewal, atomic switch и reload зависимых сервисов.
- [ ] Реализовать безопасное управление сертификатами по нодам: list, renew,
  replace, revoke, delete и назначение конкретному TLS inbound без передачи
  private key лишним нодам.
- [ ] Добавить subscription expiry/traffic policy и автоматическое обновление
  состава при изменении node/user/key desired state.
- [ ] Проверить cache headers, rate limits и abuse protection subscription
  server в сетевых интеграционных тестах; сетевые тесты не должны постоянно
  оставаться skipped в CI.

### Миграция и эксплуатация

- [ ] Реализовать импорт из 3x-ui через отдельный offline-конвертер с dry-run,
  конфликтами и отчётом; не включать 3x-ui в runtime проекта.
- [ ] Добавить экспорт конфигураций для поддерживаемых клиентов и migration
  guide с сохранением credentials там, где это безопасно и возможно.
- [ ] Определить RBAC для локальных администраторов и node agents; root не должен
  быть единственной моделью авторизации будущего control plane.

## P2 — платформы, CI и релизы

### Матрица Linux

- [ ] Хранить support matrix в data-файле: Tier 1, Tier 2, best effort и EOL;
  `--allow-unsupported` должен требовать явного подтверждения.
- [ ] Tier 1: Ubuntu Server 22.04/24.04/26.04, Debian 12/13,
  AlmaLinux/Rocky Linux 8/9/10 на amd64 и arm64, где доступны все выбранные ядра.
- [ ] Tier 2: поддерживаемые Alpine/musl с OpenRC, openSUSE Leap и Ubuntu 20.04
  ESM, пока зависимости остаются безопасными.
- [ ] Производные определять через `ID_LIKE`, но не обещать уровень поддержки
  базового дистрибутива без соответствующего CI/VM результата.
- [ ] Проверять в VM service start/restart, permissions, TLS, firewall/SELinux/
  AppArmor interaction, reboot persistence и removal; контейнеры использовать
  только для быстрых package-manager тестов.

### Quality gates и supply chain

- [ ] Добавить lint/format, статический анализ, type checks, dependency/license
  audit и secret scanning; зафиксировать dev dependencies и версии инструментов.
- [ ] Расширить CI с одной Ubuntu runner до заявленной OS/init/architecture
  матрицы и публиковать результаты совместимости для каждого релиза.
- [ ] Публиковать versioned артефакты для поддерживаемых платформ, SHA-256,
  подписи/attestations, SBOM и provenance; документировать проверку пользователем.
- [ ] Защитить release workflow через минимальные permissions, pinned actions,
  protected tags/environments и воспроизводимую сборку.
- [ ] Выполнить внешний security review node transport, installer, TLS manager,
  subscription server и обработки секретов до объявления production-ready.

## Условия закрытия milestone

- [ ] Все P0 задачи закрыты и подтверждены интеграционными тестами на реальных
  systemd и OpenRC окружениях.
- [ ] Нет известных путей, где отключённый/удалённый пользователь продолжает
  получать доступ на активной ноде.
- [ ] Потеря master или обрыв update не уничтожает state, credentials и последнюю
  рабочую конфигурацию; восстановление регулярно проверяется автоматически.
- [ ] Документированная support matrix совпадает с CI, release artifacts и
  фактическими ограничениями installer/core binaries.
- [ ] README, CHANGELOG, SECURITY и этот backlog соответствуют одному релизу и
  не объявляют незавершённые функции готовыми.
