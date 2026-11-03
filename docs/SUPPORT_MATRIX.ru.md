[English](./SUPPORT_MATRIX.md) | [Русский](./SUPPORT_MATRIX.ru.md)

# Матрица поддержки

Источником истины для CI служит
[`src/xraydo/data/support_matrix.json`](../src/xraydo/data/support_matrix.json).
Workflow строит матрицу непосредственно из этого файла, поэтому документация и
CI не могут незаметно разойтись.

Поддерживаются только официальные серверные сборки Debian и Ubuntu с systemd:

- Ubuntu Server 26.10 — текущий interim-релиз, поддержка до июля 2027 года;
- Ubuntu Server 26.04 LTS — стандартная поддержка до мая 2031 года;
- Ubuntu Server 24.04 LTS — стандартная поддержка до мая 2029 года;
- Ubuntu Server 22.04 LTS — стандартная поддержка до мая 2027 года;
- Debian 13 — текущий stable, LTS до 30 июня 2030 года;
- Debian 12 — текущий Debian LTS, поддержка до 30 июня 2028 года.

AlmaLinux, Rocky Linux, Alpine, openSUSE, производные Debian/Ubuntu и системы без
systemd больше не входят в поддерживаемую матрицу. Установщик завершает работу до
изменения системы, если `ID` или `VERSION_ID` не совпадает с матрицей.

Каждый вариант проверяется на `linux/amd64` и `linux/arm64`. Compatibility job
проверяет идентичность системы, доступность штатного Python, синтаксис
установщика, dry-run и запуск CLI. Полный набор unit-тестов выполняется отдельно
на минимальном и актуальном Python, а installer-тесты — один раз. ARM64
выполняется через QEMU; для AMD64 QEMU не запускается.

Контейнерная проверка не подтверждает взаимодействие с systemd, reboot,
SELinux, AppArmor или реальным firewall. Такие проверки требуют отдельной VM и
остаются release-gate до объявления production-ready.

Матрица пересматривается после каждого выпуска Debian stable и Ubuntu release.
Версия удаляется после окончания обычной поддержки/LTS; платные Ubuntu Pro/ESM
и Debian ELTS не расширяют матрицу проекта.

Сроки сверяются с официальными страницами
[Ubuntu release cycle](https://ubuntu.com/about/release-cycle) и
[Debian Releases](https://www.debian.org/releases/).
