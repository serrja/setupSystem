# Neovim и терминальная работа

Это руководство для установленного профиля `terminalDev` в `setupSystem`. Редактор открывает файлы и выполняет только явные действия; команды проекта и Git остаются за проектом и вами. Конфигурация хранится в `setupSystem/assets/nvim`, рабочая копия — в `${XDG_CONFIG_HOME:-$HOME/.config}/nvim`, плагины и parsers — в XDG data. На поддерживаемом Debian обновляйте рабочую копию через `setup-system apply --profile terminalDev --yes`, предварительно проверив `plan` и backup. Общий installer остаётся Debian-only; на Ubuntu эта команда не становится допустимой от наличия готовых пользовательских файлов. Адресное применение пользовательской конфигурации требует отдельного допуска и backup.

## 🚀 Быстрый старт

```bash
setup-system doctor --profile terminalDev
setup-system projects list
setup-system projects open --name serrja/jobdesk
```

Для другого проекта подставьте его точное имя из списка. Без `--name` команда предложит выбор; пустой ответ отменяет запуск. `--tmux` создаёт или подключает отдельную сессию, если `tmux` установлен; при его отсутствии команда сообщает об ошибке. Проекты открываются из конкретного корня; `.env` и `.envrc` не исполняются.

При первом запуске вы в **normal mode**. Нажмите `i`, чтобы вводить текст; `Esc` возвращает в normal mode. `:w` сохраняет, `:q` закрывает окно, `:wq` сохраняет и закрывает, `:q!` закрывает **с потерей несохранённых изменений**. Встроенная справка: `:help` и `:checkhealth`.

## ⌨️ Клавиши

`Space` — leader. `Space ?` открывает встроенную which-key шпаргалку; `Space gD` показывает Git diff текущего файла. Последовательность нажимается по порядку, без одновременного удержания клавиш.

| Действие | Клавиши | Контекст | Примечание |
| --- | --- | --- | --- |
| Сохранить / закрыть окно | `Space w` / `Space q`  normal mode | LSP-команды требуют доступного сервера |
| Найти файл / текст / открытый буфер | `Space ff` / `Space fg` / `Space fb`  normal mode | LSP-команды требуют доступного сервера |
| Символы файла / workspace / файловый обзор | `Space fs` / `Space fS` / `Space e`  normal mode | LSP-команды требуют доступного сервера |
| Outline файла | `Space o`  normal mode | LSP-команды требуют доступного сервера |
| Входящие / исходящие вызовы LSP | `Space ci` / `Space co`  normal mode | LSP-команды требуют доступного сервера |
| Реализации / предыдущий / следующий буфер | `gI` / `[b` / `]b`  normal mode | LSP-команды требуют доступного сервера |
| Переключить мягкий перенос | `Space uw`  normal mode | LSP-команды требуют доступного сервера |
| Вертикальное / горизонтальное разделение | `Space sv` / `Space sh`  normal mode | LSP-команды требуют доступного сервера |
| Терминал в разделении | `Space tt`; два `Esc` возвращают normal mode  normal mode | LSP-команды требуют доступного сервера |
| Определение / ссылки | `gd` / `gr`  normal mode | LSP-команды требуют доступного сервера |
| Переименовать символ / действие LSP | `Space rn` / `Space ca`  normal mode | LSP-команды требуют доступного сервера |
| Диагностика / предыдущая / следующая | `Space dd` / `[d` / `]d`  normal mode | LSP-команды требуют доступного сервера |
| Форматировать явно | `Space cf`  normal mode | LSP-команды требуют доступного сервера |
| Git status / изменение / blame | `Space gs` / `Space gh` / `Space gb`  normal mode | LSP-команды требуют доступного сервера |

`Space ff` и `Space fg` используют `ripgrep` в ближайшем проекте внутри корня открытой сессии. Запуск из общей группы без project markers отклоняется; пустой групповой `.git` не считается репозиторием. Поиск исключает `.env*`, каталоги ключей и credentials, private state, caches и backup; обычные `authService` и исходники авторизации видны. Поиск не следует по symlink. Эти фильтры не являются контролем доступа ОС. Уже открытый секретный буфер может появиться в списке буферов. Не открывайте приватные данные в недоверенном checkout. `Space cf` отключён для vault и не запускается при сохранении. Для Python используется Ruff, для Lua StyLua, для C/C++ — `clang-format`, для Dart — `dart format`, если соответствующий executable доступен. Vue/TS/JS используют Prettier только с явным конфигом в корне проекта. Нативный EditorConfig имеет приоритет над значениями отступов по умолчанию. Проектные CI-команды и настройки остаются источником истины.

## 🔎 Обычная работа

1. Выполните `setup-system projects list`, затем `setup-system projects open --name serrja/jobdesk`. В Neovim нажмите `Space ff`, наберите часть имени файла и Enter.
2. Откройте Python-файл. `gd` перейдёт к определению, когда запущен basedpyright. `gr` покажет ссылки; `Space rn` переименует символ через LSP. Перед сохранением просмотрите изменения. LSP выбирает `.venv/bin/python` из открытого корня, если он существует; окружение не создаётся автоматически.
3. Нажмите `Space sv` для второго окна, `Ctrl-w w` для перехода между окнами. `:ls` перечислит буферы, `:bnext` перейдёт к следующему. `Space tt` откроет терминал, где можно выполнить штатную команду из README проекта. Выход из terminal mode — два `Esc`.
4. Посмотрите изменение `Space gh` или `Space gs`, затем в терминале выполните `git diff --check`, `git diff` и адресный `git add <path>`. Commit и push выполняйте вручную после проверки staged diff и outgoing range; редактор их не делает автоматически.

Для копирования внутри Neovim: `v` выделяет, `y` копирует, `p` вставляет. Системный clipboard не включён автоматически: на Debian, в SSH и tmux он зависит от отдельного provider. Проверяйте `:checkhealth provider` и используйте штатный регистр только после проверки доступа. Для согласованных PUA-иконок oil/Aerial/devicons нужен шрифт терминала с этими glyphs; `guifont` не меняет TUI. Закреплённый JetBrainsMono Nerd Font Mono и лицензия описаны в [font manifest](../manifests/fonts.toml); установка выполняется явно через `setup-system workstation apply --profile fonts --yes`, см. [шрифты](fonts.md). Emoji обслуживает отдельный fallback, например уже установленный Noto Color Emoji. Не меняйте глобально TERM, LC_ALL или ambiwidth ради символов.

## 🧪 Проверки и восстановление

```bash
setup-system plan --profile terminalDev
setup-system doctor --profile terminalDev
nvim --headless '+checkhealth' '+qa'
nvim --clean
setup-system backup --profile terminalDev
setup-system restore --backup BACKUP_ID --dry-run
```

`nvim --clean` полезен для диагностики без этой конфигурации, но не является песочницей ОС. В недоверенном checkout запускайте его и не включайте project-local hooks, `.nvim.lua`, `.exrc`, `.envrc` или автоматические задачи. Обычная конфигурация отключает project-local plugin specs, modeline, session history и установку/обновление плагинов при старте. Плагины исполняют код с правами текущего пользователя.

Если LSP не появился, проверьте `:LspInfo`, `:checkhealth vim.lsp`, `:echo getcwd()` и `setup-system doctor --profile terminalDev`; корень должен быть ближайшим проектом внутри выбранного репозитория, `.venv` — проектной, а `basedpyright-langserver` — в `PATH`. Для C/C++ нужен `clangd` и реальный `compile_commands.json` проекта; открытие файла не запускает CMake. Отсутствующий parser/formatter проверяйте через `:checkhealth nvim-treesitter` и `:ConformInfo`, затем явный `setup-system apply --profile terminalDev --yes`. При несовместимом plugin или ошибке обновления сверьте `manifests/terminalDev.toml` и `assets/nvim/lazy-lock.json`; installer откажется перезаписать неизвестный checkout. Для PATH проверьте `command -v nvim` и `nvim --version`. Для clipboard — `:checkhealth provider`; для отсутствующих значков проверьте фактический font family терминала и установленную версию Nerd Font. Outline через Aerial может использовать parser без LSP; call hierarchy требует поддержки сервера и не показывает межсервисную runtime-архитектуру.

Headless-запуск и проверка синтаксиса не подтверждают интерактивные окна, clipboard и работу на другой ОС. Проверяйте эти функции в соответствующей графической или терминальной сессии.

## Языковые инструменты

Конфигурация включает сервер только при наличии его команды в PATH: basedpyright для Python, clangd для C++, lua-language-server, typescript-language-server, dart, vscode-json-language-server, yaml-language-server и marksman. Наличие Lua-конфига не доказывает установку сервера. Для Vue дополнительно нужны vue-language-server и совместимый `@vue/typescript-plugin` в `stdpath("data")/tools/node_modules`; без полного набора Vue LSP не включается частично. Пакеты и версии устанавливаются явно, без загрузки при старте редактора.

Pinned plugins и parsers определяет [terminalDev manifest](../manifests/terminalDev.toml), commits плагинов дублируются в стандартном lazy lock. Открытие shell/editor не выполняет установку, обновление, project-local Lua, тесты или сборку. Lint запускайте явно в терминале проекта после проверки зависимостей Make; full CI остаётся ручной операцией.

Для проверки отображения сравните чистый и настроенный Neovim, затем терминал вне tmux и внутри него: кириллица, рамки, PUA-значки, emoji, составной emoji, длинная строка и таб. Headless-проверка ширины не подтверждает фактические ячейки терминала. Снимок native GUI, состояние файлов и загрузка parsers — разные результаты.
