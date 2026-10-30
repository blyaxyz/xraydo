# own-xray

`own-xray` is an early, terminal-only management panel for Xray services. Its
single entry point is `xraydo`. The current `0.1.0` milestone provides the safe
local foundation: English/Russian output, an interactive menu, user lifecycle
basics, atomic state storage, and a Linux installer bootstrap. It does **not**
install or configure Xray-core, Hysteria 2, or Telemt yet.

## Quick start / Быстрый старт

Development / разработка:

```sh
cd own-xray
PYTHONPATH=src XRAYDO_DATA_DIR=/tmp/xraydo-dev python3 -m xraydo.cli --help
python3 -m unittest discover -s tests -v
```

Linux installation / установка на Linux:

```sh
./install.sh                 # interactive / интерактивно
./install.sh --dry-run --non-interactive
sudo ./install.sh --non-interactive --yes --lang en
```

Main commands / основные команды:

```sh
xraydo                       # interactive menu; requires a TTY
xraydo status
xraydo user create alice
xraydo newUser alice         # compatibility alias
xraydo user list
xraydo user show alice
xraydo user set-enabled alice off
xraydo settings language ru
xraydo --lang en status      # one-command override
xraydo --json user list
```

The subscription token returned during user creation is shown once. Only its
SHA-256 digest is stored. State is written atomically under `/var/lib/xraydo`
for root or `$XDG_STATE_HOME/xraydo` for an unprivileged administrator.

## Architecture / Архитектура

The intended architecture has three roles: `standalone`, `master`, and `node`.
The master owns global user identities and desired state; node agents will apply
versioned changes and report health over a mutually authenticated channel. The
present milestone implements only the local application/service layer shared by
direct commands and menus. Remote node control is not implemented and must not
be assumed secure or available.

Планируемые роли: `standalone`, `master` и `node`. Master хранит глобальные
идентификаторы пользователей и желаемое состояние; агенты нод будут применять
версионированные изменения и возвращать health-данные по взаимно
аутентифицированному каналу. Сейчас реализован только локальный слой приложения.

## Migration / Миграция

The state file contains `schema_version`. Back up `state.json` before upgrading.
Version `0.1.0` supports schema `1`; no migration from 3x-ui is available yet.
Never copy production secrets into the repository.

Файл состояния содержит `schema_version`. Перед обновлением сохраните резервную
копию `state.json`. Версия `0.1.0` поддерживает схему `1`; импорт из 3x-ui пока
не реализован. Не помещайте production-секреты в репозиторий.

See [TODO.md](TODO.md) for the full product requirements.
