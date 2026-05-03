> [!WARNING]
> ## ⚠️ Проект находится в активной разработке
> 
> Приложение может содержать баги и нестабильные функции. Если вы столкнулись с проблемой или что-то не работает — пожалуйста, [создайте Issue](../../issues/new) и опишите проблему. Ваши отчёты помогают улучшить проект.



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

1. Скачай последний релиз со страницы **[Releases](https://github.com/xxFireflyxx/Flowzap-zapret-dns/releases)**
2. Распакуй архив
3. Запусти `FlowZap.exe` от имени администратора
4. Если у вас уже есть папка `zapret/` — просто положите её рядом с `FlowZap.exe`
5. Если папки нет — перейди во вкладку **Настройки** → раздел **Версии и обновления** → нажми **Проверить обновления** → нажми **Обновить Core** — zapret скачается автоматически
6. Готово — можно запускать обход блокировок

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
4. При необходимости включи DNS кнопкой на главном экране
5. Для смены пресета — просто выбери другой, zapret перезапустится автоматически

### Управление DNS

1. Перейди во вкладку **Параметры** → раздел **DNS серверы**
2. Нажми **+** чтобы добавить новую пару DNS (основной + запасной адрес)
3. Чтобы выбрать активный DNS — нажми на нужную запись в списке
4. Чтобы удалить — нажми **✕** рядом с записью
5. Включи/выключи DNS кнопкой на главном экране

### Списки запрета

1. Перейди во вкладку **Параметры** → раздел **Списки zapret**
2. Введи домен в поле ввода и нажми **Добавить**
3. Чтобы удалить домен — выбери его в списке и нажми **Удалить**

### Обновление zapret
Вкладка **Настройки** → раздел **Версии и обновления** → **Проверить обновления** → **Обновить Core**

### Обновление FlowZap
Вкладка **Обновления** → **Проверить** → **Обновить**
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


> [!WARNING]
> ## ⚠️ This project is under active development
> 
> The application may contain bugs and unstable features. If you encounter a problem or something doesn't work — please [open an Issue](../../issues/new) and describe the problem. Your reports help improve the project.



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

1. Download the latest release from the **[Releases](https://github.com/xxFireflyxx/Flowzap-zapret-dns/releases)** page
2. Extract the archive
3. Run `FlowZap.exe` as administrator
4. If you already have a `zapret/` folder — place it next to `FlowZap.exe`
5. If you don't have it — go to the **Settings** tab → **Versions and Updates** section → click **Check for updates** → click **Update Core** — zapret will be downloaded automatically
6. Done — you can now start bypassing blocks

### Option 2 — from source

**Requirements:** Python 3.11+

1. Download the repository: click **Code → Download ZIP** on GitHub
2. Extract the archive
3. Run `run_admin.bat` — it will install dependencies and launch FlowZap

---

markdown## Usage

1. Open the application
2. In the main window, select the desired preset from the list
3. Click **Start**
4. If needed, enable DNS using the button on the main screen
5. To switch presets — just select another one, zapret will restart automatically

### DNS Management

1. Go to the **Parameters** tab → **DNS Servers** section
2. Click **+** to add a new DNS pair (primary + backup address)
3. To select the active DNS — click on the desired entry in the list
4. To delete — click **✕** next to the entry
5. Enable/disable DNS using the button on the main screen

### Block Lists

1. Go to the **Parameters** tab → **zapret Lists** section
2. Enter a domain in the input field and click **Add**
3. To remove a domain — select it in the list and click **Delete**

### Updating zapret
**Settings** tab → **Versions and Updates** section → **Check for updates** → **Update Core**

### Updating FlowZap
**Updates** tab → **Check** → **Update**

---

## Credits

- [Flowseal](https://github.com/Flowseal) — for zapret-discord-youtube presets and scripts

---

## Author

Developed by [xxFireflyxx](https://github.com/xxFireflyxx)

---

## License

MIT License — free to use, attribution appreciated.
