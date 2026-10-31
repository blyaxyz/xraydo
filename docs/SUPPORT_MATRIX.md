# Матрица поддержки Linux

Источником истины для CI служит
[`src/xraydo/data/support_matrix.json`](../src/xraydo/data/support_matrix.json).
Workflow строит матрицу непосредственно из этого файла, поэтому документация и
CI не могут незаметно разойтись.

## Tier 1

- Ubuntu Server 22.04, 24.04 и 26.04;
- Debian 12 и 13;
- AlmaLinux 8, 9 и 10;
- Rocky Linux 8, 9 и 10.

## Tier 2

- Alpine 3.20 и 3.21 с musl/OpenRC;
- openSUSE Leap 15.6.

Каждый вариант проверяется на `linux/amd64` и `linux/arm64`. Compatibility job
устанавливает зависимости штатным package manager, запускает unit-тесты,
installer-тесты, dry-run и CLI smoke-check. ARM64 выполняется через QEMU.

Контейнерная проверка не подтверждает взаимодействие с systemd/OpenRC, reboot,
SELinux, AppArmor или реальным firewall. Такие проверки требуют отдельной VM и
остаются release-gate до объявления production-ready.

Ubuntu 20.04 не заявлен поддерживаемым: его штатный Python 3.8 ниже минимального
Python 3.9, требуемого xraydo. Производные дистрибутивы могут определяться через
`ID_LIKE`, но не наследуют Tier базового дистрибутива без собственного CI.
