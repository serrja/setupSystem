# Рабочий стол Hyprland

Профиль подготовлен для Debian 13 и официального пакета
[Hyprland 0.55.2 из trixie-backports](https://packages.debian.org/trixie-backports/hyprland).
Конфигурация Lua сверена с официальным тегом v0.55.2, commit
`39d7e209c79d451efab1b21151d5938289da838d`, и
[документацией серии 0.55](https://wiki.hypr.land/0.55.0/Configuring/Start/).
Применение требует установленную 0.55.2; другой номер требует пересмотра профиля.
Наличие файлов в Git не подтверждает вход, блокировку или совместимость оборудования.

## Подготовка и установка

Сначала [установите Debian](debian.md), сохраните рабочую DE, вход через TTY и
backup пользовательских конфигураций. Проверьте ОС, GPU/драйвер, мониторы и пакеты:

```bash
cat /etc/os-release
uname -m
lspci -nnk
apt-cache policy hyprland hyprlock hypridle xdg-desktop-portal-hyprland
setup-system workstation plan --profile hyprland
setup-system workstation backup --profile hyprland
```

Подключение официального backports — отдельное действие администратора по
[инструкции Debian](https://backports.debian.org/Instructions/), с сохранением текущих
APT sources. Профиль не добавляет sources, sid/testing или случайные Git-сборки.
После обновления списка пакетов просмотрите план установки. На Ubuntu эту команду
не запускайте: backend её отклонит.

```bash
setup-system workstation apply --profile hyprland --install-debian-packages --yes
setup-system workstation doctor --profile hyprland
```

APT сначала выполняет симуляцию и запрещает удаление пакетов через `--no-remove`.
Установка требует штатного sudo и подтверждения APT. После неё проверяется реальная
версия Hyprland, затем записывается профиль с backup. Без пакетного флага apply
настраивает уже установленную подходящую версию. Драйверы, display manager, системные
библиотеки, автологин и текущий сеанс команда не заменяет.

Пользовательский вход — `hypr/hyprland.lua`, управляемые файлы —
`hypr/setup-system/`. Существующий непустой входной файл требует ручного согласования
`require("setup-system.profile")`: сохраните личные правила и устраните конфликты
старых binds/autostart. Не включайте два профиля одновременно. Мониторы по умолчанию
используют preferred mode и автоматический масштаб; уточните конкретные outputs
после `hyprctl monitors`, сохраняя пользовательские настройки после include.

## Компоненты и сеанс

| Функция | Компонент | Поведение |
| --- | --- | --- |
| Панель | Waybar | Workspaces, часы, RU/EN, звук, сеть, яркость, батарея, tray |
| Launcher и меню | Wofi | Приложения, clipboard, подтверждение питания |
| Уведомления | Mako | Три сообщения, без тяжёлых эффектов |
| Терминал | foot | Только в будущем Hyprland-профиле, текущий терминал не заменяется |
| Блокировка / idle | hyprlock / hypridle | Блокировка через 5 минут, дисплеи через 10; lock до suspend |
| Clipboard | wl-clipboard + cliphist | История копирования, выбор через Wofi |
| Скриншот | grim + slurp | Выделенная область в clipboard, временный файл удаляется |
| Звук | PipeWire + WirePlumber + pavucontrol | Один audio stack, без второго daemon |
| Сеть | NetworkManager + nm-applet | Настройка через nm-connection-editor |
| Яркость / питание | brightnessctl / logind | Аппаратные клавиши, подтверждение logout/reboot/poweroff |
| Polkit | hyprpolkitagent | Один агент в Hyprland-сеансе |
| Portals | Hyprland + GTK | ScreenCast/ScreenShot через Hyprland, FileChooser через GTK |

Session helper запускается только событием старта Hyprland, экспортирует Wayland
окружение в D-Bus/systemd и запускает нужные пользовательские процессы один раз.
При завершении сеанса он останавливает только свои дочерние процессы. PipeWire и
WirePlumber используют штатные user units; их не отключают при выходе. XDG portal
службы активируются через D-Bus. Не запускайте параллельно старый bar/notification/
Polkit autostart. XWayland и GTK/Qt Wayland-пакеты сохранены; приложения не принуждаются
к глобальному backend. Keyring остаётся существующим системным компонентом.

Единые шрифты задаются [отдельным профилем](fonts.md). Палитра тёмная, отступы 4/8 px,
без blur и анимаций. Это ограничение эффектов для предсказуемого профиля, не заявление
о замеренном ускорении текущего компьютера. Sysctl, security updates, firewall,
журналирование, CPU-защита и энергосбережение не отключаются.

## Клавиши

Клавиши запуска используют Return/F-клавиши, поэтому работают одинаково при RU/EN.
Обычные комбинации приложений и Vim остаются доступными.

| Действие | Клавиши | Контекст | Примечание |
| --- | --- | --- | --- |
| Терминал / приложения | Super+Return / Super+F1 | Hyprland | foot / Wofi |
| Фокус / перемещение | Super+стрелки / Super+Shift+стрелки | Окна | Направление |
| Изменить размер | Super+Ctrl+стрелки | Окно | Шаг 20 px |
| Переместить / размер мышью | Super+ЛКМ / Super+ПКМ | Окно | Drag |
| Workspace / перенести окно | Super+1…0 / Super+Shift+1…0 | Hyprland | 0 означает 10 |
| Закрыть / floating / fullscreen | Super+F4 / Super+F5 / Super+F11 | Окно | Закрытие обрабатывает приложение |
| RU/EN | Super+Space | Клавиатура | XKB group toggle |
| Блокировка | Super+Escape | Сеанс | Через logind и hypridle |
| Скриншот области | Super+Print | Сеанс | В clipboard |
| История clipboard | Super+F8 | Сеанс | Wofi |
| Звук / сеть | Super+F2 / Super+F3 | Сеанс | Штатные диалоги |
| Питание | Super+F9 | Сеанс | Разрушительные действия требуют выбора Confirm |
| Справка | Super+F12 | Сеанс | Локальная шпаргалка, q закрывает |
| Громкость / яркость | Аппаратные клавиши | Сеанс | Без перехвата клавиш редактора |

## Реальный переход и восстановление

После backup и установки самостоятельно завершите текущий сеанс и выберите Hyprland
в существующем display manager. Агент не должен завершать свой GUI или перезагружать
компьютер без подтверждения. Выполните реальную проверку: все мониторы/масштаб и RU/EN,
звук/микрофон, сеть, clipboard, screenshot, GTK/Qt file chooser и screen sharing,
XWayland-приложение, Polkit, lock/unlock, lid/suspend/resume и доступ через TTY.
Сохраните версии и результаты; затем назначьте сессию основной штатным выбором входа.
Автологин не включается.

Только после успешной проверки составьте точный список пакетов старой DE и выполните
`apt-get --simulate remove PACKAGE1 PACKAGE2`. Просмотрите каждое удаление, сохраните
список установленных пакетов и backup. Не используйте wildcard purge или слепой
autoremove. Общие GTK/Qt-библиотеки, драйверы, сеть/звук, Polkit, keyring, portals,
XWayland и пользовательские приложения остаются. При недоступной GUI-проверке
удаление DE ожидает; в текущей задаче смена оболочки вообще исключена владельцем.

Из TTY (Ctrl+Alt+F3) войдите своим пользователем и выполните:

```bash
setup-system workstation restore BACKUP_ID
setup-system workstation restore BACKUP_ID --apply
systemctl status display-manager
journalctl --user -b --no-pager -n 100
```

Если применялся только новый профиль, rollback удалит его неизменённые новые файлы
и восстановит прежние. Выберите сохранённую DE на экране входа. Если display manager
не работает, выясните причину через `journalctl -b -u display-manager`; его restart
выполняется вручную после сохранения работы и завершит активные GUI-сеансы. Не
удаляйте fallback до проверенного восстановления. Общий [recovery guide](recovery.md)
содержит остальные backup-команды.
