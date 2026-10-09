// FlowZapBridge.cs — «мост» для обновления со старых версий FlowZap (0.5.x).
//
// Зачем: самообновление 0.5.x меняет FlowZap.exe и папку _internal скриптом,
// который считает приложение закрытым, как только смог переименовать
// FlowZap.exe. Но Windows даёт переименовать и работающий exe, а 0.5.x
// закрывается только через 2 с — замена _internal (её файлы ещё заняты)
// не удаётся, скрипт откатывает всё назад, и остаётся 0.5.x. Один exe без
// _internal тот же скрипт меняет без проблем.
//
// Поэтому в релизе есть архив flowzap-vX.Y.Z.zip только с этим exe (0.5.x
// выбирает его по своему правилу), а полная сборка вшита в него ресурсом
// payload.zip. Мост:
//   1. ждёт, пока все другие FlowZap.exe из этой папки действительно
//      завершатся (по процессам, а не по файлу);
//   2. распаковывает полную сборку во временную папку рядом;
//   3. ставит её: _internal → _internal.old, новый _internal, сам мост
//      (FlowZap.exe) → FlowZap.exe.bridge, новый FlowZap.exe; при любой
//      ошибке — откат;
//   4. запускает новый FlowZap.exe; хвосты удаляет он сам или следующий старт.
// config.toml, zapret/, tgproxy/, logs/ пользователя не трогаются; шаблон
// config.toml кладётся, только если файла нет (мост запущен в пустой папке —
// тогда он работает как установщик).
//
// Собирается встроенным компилятором .NET Framework 4 (C# 5): bridge/build.py.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

[assembly: AssemblyTitle("FlowZap")]
[assembly: AssemblyDescription("FlowZap — установка обновления")]
[assembly: AssemblyProduct("FlowZap")]
[assembly: AssemblyCompany("xxFireflyxx")]
[assembly: AssemblyCopyright("© 2026 xxFireflyxx. Все права защищены")]
[assembly: AssemblyVersion(FlowZap.Bridge.Info.AssemblyVersion)]
[assembly: AssemblyFileVersion(FlowZap.Bridge.Info.AssemblyVersion)]

namespace FlowZap.Bridge
{
    static partial class Info
    {
        // AssemblyVersion и Version — в BridgeVersion.cs (пишет bridge/build.py)
    }

    static class Program
    {
        const string ExeName = "FlowZap.exe";
        const string BridgeLeftover = "FlowZap.exe.bridge";
        const int WaitSeconds = 90;

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        static extern int MessageBoxW(IntPtr hwnd, string text, string caption, uint type);

        const uint MB_ICONERROR = 0x10, MB_ICONWARNING = 0x30, MB_RETRYCANCEL = 0x5;
        const int IDRETRY = 4;

        static string dir;
        static string logFile;

        [STAThread]
        static int Main()
        {
            dir = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            logFile = Path.Combine(dir, "logs", "update.log");
            Log("Мост: установка FlowZap " + Info.Version);

            if (!WaitForOthers())
            {
                Log("Мост: FlowZap не закрылся — установка отменена");
                return 1;
            }

            string tmp = Path.Combine(dir, "_flowzap_bridge_tmp");
            try
            {
                Extract(tmp);
            }
            catch (Exception e)
            {
                Log("Мост: не удалось распаковать — " + e.Message);
                TryDelete(tmp);
                Fail("Не удалось распаковать обновление FlowZap:\n" + e.Message +
                     "\n\nСкачайте FlowZap заново со страницы релиза.");
                return 1;
            }

            string error = Swap(tmp);
            TryDelete(tmp);
            if (error != null)
            {
                Log("Мост: откат — " + error);
                Fail("Не удалось установить обновление FlowZap — оставлена прежняя версия.\n\n" + error +
                     "\n\nСкачайте архив со страницы релиза и распакуйте поверх папки FlowZap.");
                StartIfExists(Path.Combine(dir, ExeName));
                return 1;
            }

            Log("Update applied: мост установил FlowZap " + Info.Version);
            StartIfExists(Path.Combine(dir, ExeName));
            // Сам мост — FlowZap.exe.bridge, пока он работает, его не удалить:
            // через пару секунд это сделает cmd (и на всякий случай — новый FlowZap).
            DeleteLater(Path.Combine(dir, BridgeLeftover));
            return 0;
        }

        // ── Ожидание закрытия старой версии ──────────────────────────────

        static bool WaitForOthers()
        {
            while (true)
            {
                DateTime until = DateTime.Now.AddSeconds(WaitSeconds);
                while (DateTime.Now < until)
                {
                    if (!OthersRunning()) return true;
                    Thread.Sleep(500);
                }
                int answer = MessageBoxW(IntPtr.Zero,
                    "FlowZap всё ещё работает, а обновлению нужно, чтобы он закрылся.\n\n" +
                    "Закройте FlowZap (значок в трее → «Выход») и нажмите «Повторить».",
                    "FlowZap — обновление", MB_RETRYCANCEL | MB_ICONWARNING);
                if (answer != IDRETRY) return false;
            }
        }

        static bool OthersRunning()
        {
            int self = Process.GetCurrentProcess().Id;
            string selfPath = Assembly.GetExecutingAssembly().Location;
            foreach (Process p in Process.GetProcessesByName("FlowZap"))
            {
                using (p)
                {
                    if (p.Id == self) continue;
                    string path = null;
                    try { path = p.MainModule.FileName; }
                    catch (Exception) { }
                    // Путь не прочитать (другой пользователь, процесс уже уходит) —
                    // считаем, что это наш: лучше подождать лишнее.
                    if (path == null) return true;
                    if (string.Equals(Path.GetDirectoryName(path), dir, StringComparison.OrdinalIgnoreCase))
                        return true;
                    if (string.Equals(path, selfPath, StringComparison.OrdinalIgnoreCase))
                        return true;
                }
            }
            return false;
        }

        // ── Распаковка полной сборки ─────────────────────────────────────

        static void Extract(string tmp)
        {
            TryDelete(tmp);
            Directory.CreateDirectory(tmp);
            string root = Path.GetFullPath(tmp) + Path.DirectorySeparatorChar;
            using (Stream res = Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip"))
            {
                if (res == null) throw new InvalidOperationException("в мосте нет полной сборки");
                using (ZipArchive zip = new ZipArchive(res, ZipArchiveMode.Read))
                {
                    foreach (ZipArchiveEntry entry in zip.Entries)
                    {
                        string name = entry.FullName.Replace('/', '\\');
                        if (name.StartsWith("FlowZap\\", StringComparison.OrdinalIgnoreCase))
                            name = name.Substring("FlowZap\\".Length);
                        if (name.Length == 0 || name.EndsWith("\\")) continue;
                        string dest = Path.GetFullPath(Path.Combine(tmp, name));
                        if (!dest.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                            throw new InvalidOperationException("подозрительный путь в архиве: " + entry.FullName);
                        Directory.CreateDirectory(Path.GetDirectoryName(dest));
                        using (Stream src = entry.Open())
                        using (FileStream dst = File.Create(dest))
                            src.CopyTo(dst);
                    }
                }
            }
            if (!File.Exists(Path.Combine(tmp, ExeName)) || !Directory.Exists(Path.Combine(tmp, "_internal")))
                throw new InvalidOperationException("в полной сборке нет FlowZap.exe или _internal");
        }

        // ── Замена с откатом ─────────────────────────────────────────────

        static string Swap(string tmp)
        {
            string exe = Path.Combine(dir, ExeName);
            string bridge = Path.Combine(dir, BridgeLeftover);
            string internalDir = Path.Combine(dir, "_internal");
            string internalOld = internalDir + ".old";
            bool internalParked = false, exeParked = false, newInternal = false;
            try
            {
                // Хвосты прошлых попыток и самообновления 0.5.x (оно оставляет FlowZap.exe.old)
                TryDelete(Path.Combine(dir, "FlowZap.exe.old"));
                TryDelete(internalOld);
                TryDelete(bridge);

                if (Directory.Exists(internalDir))
                {
                    Directory.Move(internalDir, internalOld);
                    internalParked = true;
                }
                Directory.Move(Path.Combine(tmp, "_internal"), internalDir);
                newInternal = true;

                if (File.Exists(exe))
                {
                    File.Move(exe, bridge);         // работающий exe переименовать можно
                    exeParked = true;
                }
                File.Move(Path.Combine(tmp, ExeName), exe);

                // Рядом с exe: лицензия — всегда свежая; шаблон настроек — только если своих нет
                CopyIfPresent(Path.Combine(tmp, "LICENSE.txt"), Path.Combine(dir, "LICENSE.txt"), true);
                CopyIfPresent(Path.Combine(tmp, "config.toml"), Path.Combine(dir, "config.toml"), false);

                TryDelete(internalOld);
                return null;
            }
            catch (Exception e)
            {
                try
                {
                    if (exeParked)
                    {
                        if (File.Exists(exe)) File.Delete(exe);
                        File.Move(bridge, exe);
                    }
                    if (newInternal) TryDelete(internalDir);
                    if (internalParked && !Directory.Exists(internalDir)) Directory.Move(internalOld, internalDir);
                }
                catch (Exception rollback)
                {
                    return e.Message + "\nОткат тоже не удался: " + rollback.Message;
                }
                return e.Message;
            }
        }

        static void CopyIfPresent(string src, string dst, bool overwrite)
        {
            if (!File.Exists(src)) return;
            if (!overwrite && File.Exists(dst)) return;
            File.Copy(src, dst, overwrite);
        }

        // ── Мелочи ───────────────────────────────────────────────────────

        static void TryDelete(string path)
        {
            try
            {
                if (Directory.Exists(path)) Directory.Delete(path, true);
                else if (File.Exists(path)) File.Delete(path);
            }
            catch (Exception) { }
        }

        static void StartIfExists(string exe)
        {
            if (!File.Exists(exe)) return;
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo(exe);
                psi.WorkingDirectory = dir;
                psi.UseShellExecute = true;
                Process.Start(psi);
            }
            catch (Exception e) { Log("Мост: не удалось запустить FlowZap — " + e.Message); }
        }

        static void DeleteLater(string path)
        {
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo("cmd.exe",
                    "/c ping -n 4 127.0.0.1 >nul & del /f /q \"" + path + "\"");
                psi.CreateNoWindow = true;
                psi.UseShellExecute = false;
                psi.WindowStyle = ProcessWindowStyle.Hidden;
                Process.Start(psi);
            }
            catch (Exception) { }
        }

        static void Fail(string text)
        {
            MessageBoxW(IntPtr.Zero, text, "FlowZap — обновление", MB_ICONERROR);
        }

        static void Log(string message)
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(logFile));
                File.AppendAllText(logFile, DateTime.Now.ToString("dd.MM.yyyy HH:mm:ss") + " " + message + Environment.NewLine,
                                   new UTF8Encoding(false));
            }
            catch (Exception) { }
        }
    }
}
