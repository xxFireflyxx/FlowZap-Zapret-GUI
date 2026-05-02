# FlowZap

**RU** | [EN](#english)

Графический интерфейс для управления [Flowseal/zapret-discord-youtube](https://github.com/Flowseal/zapret-discord-youtube) на Windows.  
Позволяет легко переключать пресеты обхода блокировок, управлять DNS — без командной строки.

---

## Возможности

- 🔓 Обход блокировок Discord, YouTube и других сайтов через zapret
- 🌐 Управление DNS-серверами (основной + запасные) — по умолчанию используется [xbox-dns.ru](https://xbox-dns.ru), можно заменить на свои
- 📋 Выбор пресета из списка с цветным индикатором пинга (🟢 🟡 🔴)
- 💾 Запоминает выбранный пресет между запусками
- 🎨 Несколько тем оформления интерфейса
- 🔄 Обновление zapret (Core) прямо из приложения — во вкладке Настройки
- 🔔 Обновление FlowZap — во вкладке Обновления
- 🖥️ Не требует навыков работы с консолью

---

## Установка

### Вариант 1 — EXE (рекомендуется)

1. Скачай последний релиз со страницы [Releases](../../releases)
2. Распакуй архив
3. Убедись что папка `zapret/` находится рядом с `FlowZap.exe`  
   *(папку zapret нужно скачать отдельно с [Flowseal/zapret-discord-youtube](https://github.com/Flowseal/zapret-discord-youtube), либо обновить через вкладку Настройки)*
4. Запусти `FlowZap.exe`

### Вариант 2 — из исходников

**Требования:** Python 3.11+

1. Скачай репозиторий: кнопка **Code → Download ZIP** на GitHub
2. Распакуй архив
3. Запусти `run_admin.bat` — он установит зависимости и запустит FlowZap

---

## Использование

1. Открой приложение
2. В главном окне выбери нужный пресет из списка
3. Нажми **Запуск**
4. При необходимости включи **DNS** кнопкой на главном экране
5. Для смены пресета — просто выбери другой, zapret перезапустится автоматически

### Обновление zapret
Вкладка **Настройки** → раздел «Версии и обновления» → «Проверить обновления» → «Обновить»

### Обновление FlowZap
Вкладка **Обновления** → «Проверить» → «Обновить»

---

## Скриншоты

> *(скоро будут добавлены)*

---

## Благодарности

- [Flowseal](https://github.com/Flowseal) — за пресеты и скрипты zapret-discord-youtube

---

## Автор

Разработано [xxFireflyxx](https://github.com/xxFireflyxx)

---

## Лицензия

MIT License — делай что хочешь, упоминание автора приветствуется.

---

<a name="english"></a>

# FlowZap — English

A graphical interface for managing [Flowseal/zapret-discord-youtube](https://github.com/Flowseal/zapret-discord-youtube) on Windows.  
Bypass site blocks (Discord, YouTube, etc.) without touching the command line.

---

## Features

- 🔓 Bypass blocks for Discord, YouTube and other sites via zapret
- 🌐 DNS server management (primary + fallback) — [xbox-dns.ru](https://xbox-dns.ru) by default, customizable
- 📋 Preset selector with color-coded ping indicator (🟢 🟡 🔴)
- 💾 Remembers selected preset between sessions
- 🎨 Multiple UI themes
- 🔄 Update zapret (Core) directly from the app — in the Settings tab
- 🔔 Update FlowZap — in the Updates tab
- 🖥️ No command line skills needed

---

## Installation

### Option 1 — EXE (recommended)

1. Download the latest release from [Releases](../../releases)
2. Extract the archive
3. Make sure the `zapret/` folder is placed next to `FlowZap.exe`  
   *(download zapret separately from [Flowseal/zapret-discord-youtube](https://github.com/Flowseal/zapret-discord-youtube), or update it via the Settings tab)*
4. Run `FlowZap.exe`

### Option 2 — from source

**Requirements:** Python 3.11+

1. Download the repository: click **Code → Download ZIP** on GitHub
2. Extract the archive
3. Run `run_admin.bat` — it will install dependencies and launch FlowZap

---

## Usage

1. Open the app
2. Select a preset from the dropdown list
3. Click **Start**
4. Enable **DNS** using the button on the main screen if needed
5. To switch presets — just select another one, zapret will restart automatically

### Update zapret
**Settings** tab → "Versions & Updates" section → "Check for updates" → "Update"

### Update FlowZap
**Updates** tab → "Check" → "Update"

---

## Credits

- [Flowseal](https://github.com/Flowseal) — for zapret-discord-youtube presets and scripts

---

## Author

Developed by [xxFireflyxx](https://github.com/xxFireflyxx)

---

## License

MIT License — free to use, attribution appreciated.
