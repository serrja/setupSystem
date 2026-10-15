# Neovim и терминальная работа

Это руководство для установленного профиля `terminalDev` в `setupSystem`. Редактор открывает файлы и выполняет только явные действия; команды проекта и Git остаются за проектом и вами. Конфигурация хранится в `setupSystem/assets/nvim`, рабочая копия — в `${XDG_CONFIG_HOME:-$HOME/.config}/nvim`, плагины и parsers — в XDG data. Обновляйте рабочую копию через `setup-system apply --profile terminalDev --yes`, предварительно проверив `plan` и backup.

## 🚀 Быстрый старт

```bash
setup-system doctor --profile terminalDev
setup-system projects list
setup-system projects open --name Projects/jobdesk
```

Для другого проекта подставьте его точное имя из списка. Без `--name` команда предложит выбор; пустой ответ отменяет запуск. `--tmux` создаёт или подключает отдельную сессию, если `tmux` установлен. На этой Ubuntu машине он пока недоступен из-за запрета `sudo`, поэтому обычный запуск Neovim работает, а `--tmux` честно сообщает об ошибке. Проекты открываются из конкретного корня; `.env` и `.envrc` не исполняются.

При первом запуске вы в **normal mode**. Нажмите `i`, чтобы вводить текст; `Esc` возвращает в normal mode. `:w` сохраняет, `:q` закрывает окно, `:wq` сохраняет и закрывает, `:q!` закрывает **с потерей несохранённых изменений**. Встроенная справка: `:help` и `:checkhealth`.

## ⌨️ Клавиши

`Space` — leader. Последовательность нажимается по порядку, без одновременного удержания клавиш.

| Действие | Клавиши в normal mode |
| --- | --- |
| Сохранить / закрыть окно | `Space w` / `Space q` |
| Найти файл / текст / открытый буфер | `Space ff` / `Space fg` / `Space fb` |
| Символы файла / файловый обзор | `Space fs` / `Space e` |
| Вертикальное / горизонтальное разделение | `Space sv` / `Space sh` |
| Терминал в разделении | `Space tt`; два `Esc` возвращают normal mode |
| Определение / ссылки | `gd` / `gr` |
| Переименовать символ / действие LSP | `Space rn` / `Space ca` |
| Диагностика / предыдущая / следующая | `Space dd` / `[d` / `]d` |
| Форматировать явно | `Space cf` |
| Git status / изменение / blame | `Space gs` / `Space gh` / `Space gb` |

`Space ff` и `Space fg` используют `ripgrep` в текущем проекте. По умолчанию исключены `.env*`, файлы с `auth` в имени, backup и private state; поиск не следует по symlink. Уже открытый секретный буфер может появиться в списке буферов. Не открывайте приватные данные в недоверенном checkout. `Space cf` отключён для vault и не запускается при сохранении. Для Python используется Ruff, для Lua StyLua, для C/C++ — `clang-format`, если он доступен. Проектные CI-команды и настройки остаются источником истины.

## 🔎 Обычная работа

1. Выполните `setup-system projects list`, затем `setup-system projects open --name Projects/jobdesk`. В Neovim нажмите `Space ff`, наберите часть имени файла и Enter.
2. Откройте Python-файл. `gd` перейдёт к определению, когда запущен basedpyright. `gr` покажет ссылки; `Space rn` переименует символ через LSP. Перед сохранением просмотрите изменения. LSP выбирает `.venv/bin/python` из открытого корня, если он существует; окружение не создаётся автоматически.
3. Нажмите `Space sv` для второго окна, `Ctrl-w w` для перехода между окнами. `:ls` перечислит буферы, `:bnext` перейдёт к следующему. `Space tt` откроет терминал, где можно выполнить штатную команду из README проекта. Выход из terminal mode — два `Esc`.
4. Посмотрите изменение `Space gh` или `Space gs`, затем в терминале выполните `git diff --check`, `git diff` и адресный `git add <path>`. Commit и push выполняйте вручную после проверки staged diff и outgoing range; редактор их не делает автоматически.

Для копирования внутри Neovim: `v` выделяет, `y` копирует, `p` вставляет. Системный clipboard не включён автоматически: в локальной Ubuntu, macOS, SSH и tmux он зависит от отдельного provider. Проверяйте `:checkhealth provider` и используйте штатный регистр только после проверки доступа. Nerd Font не обязателен.

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

Если LSP не появился, проверьте `:LspInfo`, `:checkhealth vim.lsp`, `:echo getcwd()` и `setup-system doctor --profile terminalDev`; корень должен быть нужным репозиторием, `.venv` — проектной, а `basedpyright-langserver` — в `PATH`. Для C/C++ нужен `clangd` и реальный `compile_commands.json` проекта; открытие файла не запускает CMake. Отсутствующий parser/formatter проверяйте через `:checkhealth nvim-treesitter` и `:ConformInfo`, затем явный `setup-system apply --profile terminalDev --yes`. При несовместимом plugin или ошибке обновления сверьте `manifests/terminalDev.toml` и `assets/nvim/lazy-lock.json`; installer откажется перезаписать неизвестный checkout. Для PATH проверьте `command -v nvim` и `nvim --version`. Для clipboard — `:checkhealth provider`; для отсутствующих значков используйте обычный шрифт.

Список проверок на текущей машине ограничен headless-запуском и синтаксисом. Интерактивные окна, clipboard и macOS пока требуют отдельной проверки в соответствующей среде.
