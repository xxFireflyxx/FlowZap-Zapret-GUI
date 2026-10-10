// FlowZapService.cs — фоновая служба FlowZap.
//
// Зачем: winws.exe (WinDivert) и DNS требуют прав администратора. Служба
// ставится один раз (одно окно UAC), работает от LocalSystem и выполняет
// только узкий набор команд от FlowZap, который сам запускается без прав
// администратора: запустить/остановить winws, включить/сбросить DNS,
// обновить свой winws до нужной версии zapret (сама, с GitHub — без UAC).
//
// Связь — именованный канал \\.\pipe\flowzap-service: сообщения
// «4 байта длины (LE) + JSON в UTF-8». Доступ к каналу — только у
// пользователей, вошедших в Windows на этом компьютере (не по сети).
//
// Безопасность:
//   * служба запускает только свой winws.exe из своей папки в Program Files
//     (пользователи её только читают); произвольные программы — нет;
//   * файлы (списки, .bin) клиент присылает содержимым — служба не открывает
//     пути, указанные клиентом, и пишет их только в свою папку run\;
//   * аргументы winws проверяются по устройству, а не по списку имён (новые
//     параметры из новых версий zapret проходят без обновления службы):
//     «--имя[=значение]», в значении нет путей (\, X:, /…, ..), кавычек и
//     управляющих символов;
//   * свой winws служба обновляет только сама: скачивает релиз Flowseal с
//     GitHub (или зеркала SourceForge) и сверяет sha256 из GitHub API; от
//     клиента — лишь номер версии, файлов от него служба не берёт;
//   * DNS — только IP-адреса (проверяются как IP); ставятся через Windows
//     API, запасной путь — свой скрипт PowerShell, от клиента в него
//     попадают лишь адреса;
//   * обновление самого FlowZap (замена FlowZap.exe и _internal после его
//     закрытия, перезапуск) служба делает от имени попросившего
//     пользователя — с его правами, не с правами системы (см. AppUpdate);
//   * winws в job-объекте: падение службы гасит и его; отключение FlowZap,
//     запустившего обход, — тоже. DNS, включённый FlowZap, при его
//     отключении (закрыли, упал) сбрасывается на автоматический.
//
// Собирается встроенным в Windows компилятором .NET Framework 4 (C# 5):
// service/build.py. Поэтому без синтаксиса новее C# 5.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.ServiceProcess;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;

[assembly: AssemblyTitle("FlowZap Service")]
[assembly: AssemblyDescription("Фоновая служба FlowZap: запуск обхода и системные настройки")]
[assembly: AssemblyProduct("FlowZap")]
[assembly: AssemblyVersion("1.4.0.0")]
[assembly: AssemblyFileVersion("1.4.0.0")]

namespace FlowZap.Service
{
    static class Const
    {
        public const string ServiceName = "FlowZapService";
        public const string DisplayName = "FlowZap Service";
        public const string Version = "1.4.0";
        public const int Protocol = 1;
        public const int MaxRequestBytes = 64 * 1024 * 1024;
        public const int MaxLines = 300;
        public const string ReadyMark = "capture is started";
        public static readonly string[] EngineFiles = { "winws.exe", "cygwin1.dll", "WinDivert.dll", "WinDivert64.sys" };
        // Для --console (разработка): своя папка и своё имя канала
        public static string PipeName = "flowzap-service";
        public static bool ConsoleMode;
    }

    static class Paths
    {
        public static string Root;

        public static string DefaultRoot()
        {
            return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "FlowZap", "Service");
        }

        public static string Engine { get { return Path.Combine(Root, "engine"); } }
        public static string Run { get { return Path.Combine(Root, "run"); } }
        public static string Logs { get { return Path.Combine(Root, "logs"); } }
        public static string Exe { get { return Path.Combine(Root, "FlowZapService.exe"); } }
    }

    static class Log
    {
        static readonly object Gate = new object();

        public static void Write(string message)
        {
            Append("service.log", message);
        }

        public static void Append(string fileName, string message)
        {
            try
            {
                lock (Gate)
                {
                    Directory.CreateDirectory(Paths.Logs);
                    string file = Path.Combine(Paths.Logs, fileName);
                    FileInfo info = new FileInfo(file);
                    if (info.Exists && info.Length > 512 * 1024)
                    {
                        string old = file + ".1";
                        if (File.Exists(old)) File.Delete(old);
                        File.Move(file, old);
                    }
                    File.AppendAllText(file, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " " + message + Environment.NewLine, Encoding.UTF8);
                }
            }
            catch (Exception)
            {
                // лог не должен ронять службу
            }
        }
    }

    static class Native
    {
        [StructLayout(LayoutKind.Sequential)]
        public struct IO_COUNTERS
        {
            public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount;
            public ulong ReadTransferCount, WriteTransferCount, OtherTransferCount;
        }

        [StructLayout(LayoutKind.Sequential)]
        public struct JOBOBJECT_BASIC_LIMIT_INFORMATION
        {
            public long PerProcessUserTimeLimit;
            public long PerJobUserTimeLimit;
            public uint LimitFlags;
            public UIntPtr MinimumWorkingSetSize;
            public UIntPtr MaximumWorkingSetSize;
            public uint ActiveProcessLimit;
            public UIntPtr Affinity;
            public uint PriorityClass;
            public uint SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        public struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        {
            public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
            public IO_COUNTERS IoInfo;
            public UIntPtr ProcessMemoryLimit;
            public UIntPtr JobMemoryLimit;
            public UIntPtr PeakProcessMemoryUsed;
            public UIntPtr PeakJobMemoryUsed;
        }

        public const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000;
        public const int JobObjectExtendedLimitInformation = 9;
        public const int MOVEFILE_DELAY_UNTIL_REBOOT = 0x4;

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        public static extern IntPtr CreateJobObject(IntPtr attributes, string name);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool SetInformationJobObject(IntPtr job, int infoClass, ref JOBOBJECT_EXTENDED_LIMIT_INFORMATION info, uint size);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool CloseHandle(IntPtr handle);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool GetNamedPipeClientProcessId(IntPtr pipe, out uint processId);

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        public static extern bool MoveFileEx(string existing, string replacement, int flags);

        // ── запуск программы от имени пользователя (обновление FlowZap) ──

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        public struct STARTUPINFO
        {
            public int cb;
            public string lpReserved, lpDesktop, lpTitle;
            public int dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
            public short wShowWindow, cbReserved2;
            public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
        }

        [StructLayout(LayoutKind.Sequential)]
        public struct PROCESS_INFORMATION
        {
            public IntPtr hProcess, hThread;
            public int dwProcessId, dwThreadId;
        }

        public const uint TOKEN_ALL_ACCESS = 0xF01FF;
        public const int SecurityImpersonation = 2, TokenPrimary = 1;
        public const uint CREATE_UNICODE_ENVIRONMENT = 0x400;
        public const int ERROR_PRIVILEGE_NOT_HELD = 1314;

        [DllImport("advapi32.dll", SetLastError = true)]
        public static extern bool DuplicateTokenEx(IntPtr token, uint access, IntPtr attributes, int level, int type, out IntPtr newToken);

        [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        public static extern bool CreateProcessAsUser(IntPtr token, string application, StringBuilder commandLine,
            IntPtr processAttributes, IntPtr threadAttributes, bool inheritHandles, uint flags, IntPtr environment,
            string currentDirectory, ref STARTUPINFO startup, out PROCESS_INFORMATION info);

        [DllImport("userenv.dll", SetLastError = true)]
        public static extern bool CreateEnvironmentBlock(out IntPtr environment, IntPtr token, bool inherit);

        [DllImport("userenv.dll", SetLastError = true)]
        public static extern bool DestroyEnvironmentBlock(IntPtr environment);

        // Аргумент командной строки по правилам Windows (CommandLineToArgvW)
        public static string Quote(string arg)
        {
            if (arg.Length > 0 && arg.IndexOfAny(new[] { ' ', '\t', '"' }) < 0) return arg;
            StringBuilder sb = new StringBuilder("\"");
            int slashes = 0;
            foreach (char c in arg)
            {
                if (c == '\\') { slashes++; continue; }
                if (c == '"') { sb.Append('\\', slashes * 2 + 1); sb.Append('"'); slashes = 0; continue; }
                sb.Append('\\', slashes); slashes = 0; sb.Append(c);
            }
            sb.Append('\\', slashes * 2);
            sb.Append('"');
            return sb.ToString();
        }
    }

    // ── Проверка запроса на запуск winws ─────────────────────────────────

    sealed class StartPlan
    {
        public List<object> Args = new List<object>();          // string (готовый аргумент) или FileArg
        public List<KeyValuePair<string, byte[]>> Files = new List<KeyValuePair<string, byte[]>>();
    }

    sealed class FileArg
    {
        public string Option;
        public string Prefix;
        public int Index;
    }

    static class Validation
    {
        static readonly Regex OptionName = new Regex(@"\A--[a-z0-9][a-z0-9-]{0,63}\z");
        static readonly Regex FilePrefix = new Regex(@"\A(\+[0-9]{1,6})?@?\z");
        // Путь с диском (C:x, @D:\x, ,E:y) — в любом месте значения
        static readonly Regex DrivePath = new Regex(@"(\A|[@,=+:])[A-Za-z]:(?!:)");
        static readonly Regex SafeName = new Regex(@"[^A-Za-z0-9._-]");

        public static StartPlan Parse(Dictionary<string, object> request)
        {
            StartPlan plan = new StartPlan();
            object[] files = request.ContainsKey("files") ? request["files"] as object[] : new object[0];
            object[] args = request.ContainsKey("args") ? request["args"] as object[] : null;
            if (files == null || args == null) throw new ArgumentException("Некорректный запрос на запуск");
            if (args.Length == 0 || args.Length > 2000) throw new ArgumentException("Некорректное число аргументов");

            long total = 0;
            for (int i = 0; i < files.Length; i++)
            {
                Dictionary<string, object> f = files[i] as Dictionary<string, object>;
                if (f == null || !(f.ContainsKey("name") && f["name"] is string) || !(f.ContainsKey("data") && f["data"] is string))
                    throw new ArgumentException("Некорректный файл в запросе");
                byte[] data = Convert.FromBase64String((string)f["data"]);
                total += data.Length;
                if (total > Const.MaxRequestBytes) throw new ArgumentException("Слишком большие файлы пресета");
                string name = SafeName.Replace(Path.GetFileName((string)f["name"]), "_");
                if (name.Length == 0 || name.Length > 100) name = "file";
                plan.Files.Add(new KeyValuePair<string, byte[]>("f" + i + "_" + name, data));
            }

            foreach (object item in args)
            {
                string text = item as string;
                if (text != null)
                {
                    plan.Args.Add(CheckArg(text));
                    continue;
                }
                Dictionary<string, object> fa = item as Dictionary<string, object>;
                if (fa == null || !(fa.ContainsKey("opt") && fa["opt"] is string) || !fa.ContainsKey("file"))
                    throw new ArgumentException("Некорректный аргумент в запросе");
                string option = (string)fa["opt"];
                string prefix = fa.ContainsKey("prefix") && fa["prefix"] is string ? (string)fa["prefix"] : "";
                int index = Convert.ToInt32(fa["file"]);
                if (!OptionName.IsMatch(option)) throw new ArgumentException("Недопустимый параметр: " + Cut(option));
                if (!FilePrefix.IsMatch(prefix)) throw new ArgumentException("Недопустимый параметр: " + Cut(option));
                if (index < 0 || index >= plan.Files.Count) throw new ArgumentException("Нет файла для " + option);
                FileArg arg = new FileArg();
                arg.Option = option; arg.Prefix = prefix; arg.Index = index;
                plan.Args.Add(arg);
            }
            return plan;
        }

        static string CheckArg(string arg)
        {
            if (arg.Length > 8192) throw new ArgumentException("Слишком длинный параметр");
            int eq = arg.IndexOf('=');
            string name = eq < 0 ? arg : arg.Substring(0, eq);
            string value = eq < 0 ? "" : arg.Substring(eq + 1);
            if (!OptionName.IsMatch(name) || !SafeValue(value))
                throw new ArgumentException("Недопустимый параметр: " + Cut(arg));
            return arg;
        }

        // Значение без путей: файлы приходят только содержимым (см. FileArg)
        static bool SafeValue(string value)
        {
            foreach (char c in value)
                if (c < 0x20 || c == 0x7f || c == '"' || c == '\\') return false;
            if (value.Contains("..") || value.Contains("//") || value.Contains("/cygdrive")) return false;
            if (value.StartsWith("/") || value.StartsWith("@/") || value.StartsWith("~")) return false;
            if (DrivePath.IsMatch(value)) return false;
            return true;
        }

        static string Cut(string s)
        {
            return s.Length > 80 ? s.Substring(0, 80) + "…" : s;
        }
    }

    // ── winws ─────────────────────────────────────────────────────────────

    sealed class Engine
    {
        readonly object gate = new object();
        Process process;
        IntPtr job = IntPtr.Zero;
        bool exited;
        bool ready;
        object exitCode;            // int или null (остановлен по запросу / не запускался)
        int ownerClient;            // кто запустил: его отключение гасит обход
        bool timestampsDone;
        long seq;
        readonly LinkedList<KeyValuePair<long, string>> lines = new LinkedList<KeyValuePair<long, string>>();

        public static string EngineFile(string name) { return Path.Combine(Paths.Engine, name); }

        // Поставить новые файлы движка (папка newDir уже проверена и заполнена):
        // остановить winws и драйвер WinDivert, поменять папки местами, при
        // сбое — вернуть старую.
        public void ReplaceEngine(string newDir)
        {
            lock (gate)
            {
                StopLocked();
                KillStrays();
                Installer.TrySc("stop", "WinDivert");
                Installer.TrySc("stop", "WinDivert14");
                string old = Paths.Engine + ".old";
                Installer.DeleteTree(old);
                bool hadOld = Directory.Exists(Paths.Engine);
                if (hadOld) MoveDirWithRetry(Paths.Engine, old);
                try { MoveDirWithRetry(newDir, Paths.Engine); }
                catch (Exception)
                {
                    if (hadOld) MoveDirWithRetry(old, Paths.Engine);
                    throw;
                }
                Installer.DeleteTree(old);
                AddLine("[служба] winws обновлён до zapret " + ReadEngineVersion());
            }
        }

        static void MoveDirWithRetry(string from, string to)
        {
            for (int i = 0; ; i++)
            {
                try { Directory.Move(from, to); return; }
                catch (IOException)
                {
                    if (i >= 20) throw;
                    Thread.Sleep(300);
                }
            }
        }

        public Dictionary<string, object> Start(Dictionary<string, object> request, int client)
        {
            StartPlan plan = Validation.Parse(request);    // до остановки текущего: плохой запрос ничего не ломает
            lock (gate)
            {
                foreach (string f in Const.EngineFiles)
                    if (!File.Exists(EngineFile(f)))
                        throw new InvalidOperationException("В службе нет " + f + " — переустановите службу в настройках FlowZap");

                StopLocked();
                KillStrays();
                EnableTimestamps();
                PrepareRunDir();

                List<string> argv = new List<string>();
                List<string> paths = new List<string>();
                foreach (KeyValuePair<string, byte[]> f in plan.Files)
                {
                    string path = Path.Combine(Paths.Run, f.Key);
                    File.WriteAllBytes(path, f.Value);
                    paths.Add(path);
                }
                foreach (object a in plan.Args)
                {
                    FileArg fa = a as FileArg;
                    argv.Add(fa == null ? (string)a : fa.Option + "=" + fa.Prefix + paths[fa.Index]);
                }

                StringBuilder cmd = new StringBuilder();
                foreach (string a in argv) { if (cmd.Length > 0) cmd.Append(' '); cmd.Append(Native.Quote(a)); }

                ProcessStartInfo psi = new ProcessStartInfo(EngineFile("winws.exe"), cmd.ToString());
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardOutput = true;
                psi.RedirectStandardError = true;
                psi.StandardOutputEncoding = Encoding.UTF8;
                psi.StandardErrorEncoding = Encoding.UTF8;
                psi.WorkingDirectory = Paths.Run;

                Process p = new Process();
                p.StartInfo = psi;
                p.EnableRaisingEvents = true;
                p.OutputDataReceived += delegate(object s, DataReceivedEventArgs e) { OnLine(p, e.Data); };
                p.ErrorDataReceived += delegate(object s, DataReceivedEventArgs e) { OnLine(p, e.Data); };
                // Exited .NET вызывает под внутренней блокировкой процесса, а
                // WaitForExit в StopLocked (под нашей gate) берёт ту же блокировку:
                // ждать gate прямо в обработчике = взаимная блокировка. Поэтому —
                // через очередь пула потоков.
                p.Exited += delegate(object s, EventArgs e) { ThreadPool.QueueUserWorkItem(delegate { OnExit(p); }); };

                process = p;
                exited = false;
                ready = false;
                exitCode = null;
                ownerClient = client;
                p.Start();
                job = CreateKillOnCloseJob(p);
                p.BeginOutputReadLine();
                p.BeginErrorReadLine();
                AddLine("[служба] winws запущен (PID " + p.Id + ")");
                Log.Write("winws запущен, PID " + p.Id + ", клиент " + client);
                return StatusLocked(client, -1);
            }
        }

        public Dictionary<string, object> Stop(int client)
        {
            lock (gate)
            {
                StopLocked();
                return StatusLocked(client, -1);
            }
        }

        public Dictionary<string, object> Status(int client, long since)
        {
            lock (gate) { return StatusLocked(client, since); }
        }

        public void ClientGone(int client)
        {
            lock (gate)
            {
                if (client == ownerClient && process != null && !exited)
                {
                    Log.Write("FlowZap отключился — останавливаю winws");
                    StopLocked();
                }
            }
        }

        public void Shutdown()
        {
            lock (gate) { StopLocked(); }
        }

        void StopLocked()
        {
            if (process == null) return;
            bool wasRunning = !exited;
            // До Kill: WaitForExit может вызвать Exited прямо здесь (под этой же
            // блокировкой) — остановка по запросу не должна выглядеть падением
            exited = true;
            try
            {
                if (wasRunning && !process.HasExited)
                {
                    process.Kill();
                    process.WaitForExit(5000);
                    AddLine("[служба] winws остановлен");
                    Log.Write("winws остановлен");
                }
            }
            catch (Exception e) { Log.Write("Остановка winws: " + e.Message); }
            if (job != IntPtr.Zero) { Native.CloseHandle(job); job = IntPtr.Zero; }
            exited = true;
            exitCode = null;
            ready = false;
            process = null;
        }

        // Только один WinDivert-перехват на компьютер — другие winws мешали бы
        static void KillStrays()
        {
            foreach (Process p in Process.GetProcessesByName("winws"))
            {
                try { p.Kill(); p.WaitForExit(3000); Log.Write("Завершён посторонний winws, PID " + p.Id); }
                catch (Exception) { }
                finally { p.Dispose(); }
            }
        }

        void EnableTimestamps()
        {
            if (timestampsDone) return;
            timestampsDone = true;
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo(
                    Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "netsh.exe"),
                    "interface tcp set global timestamps=enabled");
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                using (Process p = Process.Start(psi)) { p.WaitForExit(10000); }
            }
            catch (Exception e) { Log.Write("TCP timestamps: " + e.Message); }
        }

        static void PrepareRunDir()
        {
            Installer.CheckNoReparse(Paths.Run);
            Directory.CreateDirectory(Paths.Run);
            foreach (string f in Directory.GetFiles(Paths.Run))
            {
                try { File.Delete(f); } catch (Exception) { }
            }
        }

        static IntPtr CreateKillOnCloseJob(Process p)
        {
            IntPtr handle = Native.CreateJobObject(IntPtr.Zero, null);
            if (handle == IntPtr.Zero) return IntPtr.Zero;
            Native.JOBOBJECT_EXTENDED_LIMIT_INFORMATION info = new Native.JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
            info.BasicLimitInformation.LimitFlags = Native.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            bool ok = Native.SetInformationJobObject(handle, Native.JobObjectExtendedLimitInformation, ref info,
                (uint)Marshal.SizeOf(typeof(Native.JOBOBJECT_EXTENDED_LIMIT_INFORMATION)));
            if (ok) ok = Native.AssignProcessToJobObject(handle, p.Handle);
            if (!ok)
            {
                Log.Write("Job-объект не создан: " + Marshal.GetLastWin32Error());
                Native.CloseHandle(handle);
                return IntPtr.Zero;
            }
            return handle;
        }

        void OnLine(Process p, string line)
        {
            if (line == null) return;
            lock (gate)
            {
                if (p != process) return;
                AddLine(line);
                if (line.IndexOf(Const.ReadyMark, StringComparison.OrdinalIgnoreCase) >= 0) ready = true;
            }
        }

        void OnExit(Process p)
        {
            lock (gate)
            {
                if (p != process || exited) return;
                exited = true;
                ready = false;
                try { exitCode = p.ExitCode; } catch (Exception) { exitCode = -1; }
                AddLine("[служба] winws завершился с кодом " + exitCode);
                Log.Write("winws завершился сам, код " + exitCode);
            }
        }

        void AddLine(string line)
        {
            seq++;
            lines.AddLast(new KeyValuePair<long, string>(seq, line));
            while (lines.Count > Const.MaxLines) lines.RemoveFirst();
        }

        Dictionary<string, object> StatusLocked(int client, long since)
        {
            bool running = process != null && !exited;
            Dictionary<string, object> s = new Dictionary<string, object>();
            s["ok"] = true;
            s["protocol"] = Const.Protocol;
            s["version"] = Const.Version;
            s["running"] = running;
            s["ready"] = running && ready;
            s["pid"] = running ? (object)process.Id : null;
            s["exit_code"] = exitCode;
            s["owner"] = running && client == ownerClient;
            s["engine_version"] = ReadEngineVersion();
            s["features"] = new[] { "app-update" };
            s["seq"] = seq;
            if (since >= 0)
            {
                List<string> fresh = new List<string>();
                foreach (KeyValuePair<long, string> l in lines)
                    if (l.Key > since) fresh.Add(l.Value);
                s["lines"] = fresh;
            }
            return s;
        }

        public static string ReadEngineVersion()
        {
            try
            {
                string f = EngineFile("version.txt");
                return File.Exists(f) ? File.ReadAllText(f).Trim() : "";
            }
            catch (Exception) { return ""; }
        }
    }

    // ── Обновление winws (zapret Flowseal) ────────────────────────────────

    static class EngineUpdater
    {
        const string Repo = "Flowseal/zapret-discord-youtube";
        const string Mirror = "https://sourceforge.net/projects/flowseal.mirror/files/";
        const int MaxZip = 64 * 1024 * 1024;
        static readonly Regex Tag = new Regex(@"\A[A-Za-z0-9._-]{1,40}\z");
        static readonly object Gate = new object();

        public static Dictionary<string, object> Update(string tag, Engine engine)
        {
            if (!string.IsNullOrEmpty(tag) && !Tag.IsMatch(tag)) throw new ArgumentException("Некорректная версия zapret");
            lock (Gate)     // два обновления сразу не нужны
            {
                ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12 | (SecurityProtocolType)12288; // + TLS 1.3
                JavaScriptSerializer json = new JavaScriptSerializer();
                string api = "https://api.github.com/repos/" + Repo + "/releases/" +
                             (string.IsNullOrEmpty(tag) ? "latest" : "tags/" + Uri.EscapeDataString(tag));
                Dictionary<string, object> release;
                try { release = json.DeserializeObject(Encoding.UTF8.GetString(Fetch(api, 1024 * 1024))) as Dictionary<string, object>; }
                catch (WebException e)
                {
                    HttpWebResponse r = e.Response as HttpWebResponse;
                    if (r != null && r.StatusCode == HttpStatusCode.NotFound)
                        throw new InvalidOperationException("На GitHub нет релиза zapret " + tag);
                    throw new InvalidOperationException("GitHub недоступен — не удалось узнать о релизе zapret (" + e.Message + ")");
                }
                if (release == null || !(release.ContainsKey("tag_name") && release["tag_name"] is string))
                    throw new InvalidOperationException("GitHub вернул непонятный ответ о релизе zapret");
                string realTag = (string)release["tag_name"];
                if (!Tag.IsMatch(realTag)) throw new InvalidOperationException("Некорректная версия релиза zapret");

                Dictionary<string, object> asset = null;
                object[] assets = release.ContainsKey("assets") ? release["assets"] as object[] : null;
                if (assets != null)
                    foreach (object o in assets)
                    {
                        Dictionary<string, object> a = o as Dictionary<string, object>;
                        string name = a != null && a.ContainsKey("name") ? (a["name"] as string ?? "").ToLowerInvariant() : "";
                        if (name.StartsWith("zapret") && name.EndsWith(".zip")) { asset = a; break; }
                    }
                if (asset == null) throw new InvalidOperationException("В релизе zapret " + realTag + " нет архива");
                string fileName = (string)asset["name"];
                long size = asset.ContainsKey("size") ? Convert.ToInt64(asset["size"]) : 0;
                string digest = asset.ContainsKey("digest") ? (asset["digest"] as string ?? "") : "";
                if (!digest.StartsWith("sha256:"))
                    throw new InvalidOperationException("В релизе zapret нет контрольной суммы — обновление отменено");
                string sha = digest.Substring(7).ToLowerInvariant();

                byte[] zip = null;
                string githubError = null;
                try { zip = Verified(Fetch((string)asset["browser_download_url"], MaxZip), size, sha); }
                catch (Exception e) { githubError = e.Message; }
                if (zip == null)
                {
                    Log.Write("zapret с GitHub: " + githubError + " — пробую зеркало SourceForge");
                    try { zip = Verified(Fetch(Mirror + Uri.EscapeDataString(realTag) + "/" + Uri.EscapeDataString(fileName) + "/download", MaxZip), size, sha); }
                    catch (Exception e)
                    {
                        Log.Write("zapret с SourceForge: " + e.Message);
                        throw new InvalidOperationException("Не удалось скачать zapret " + realTag + " ни с GitHub, ни с зеркала");
                    }
                }

                string staging = Paths.Engine + ".new";
                DeleteDir(staging);
                Directory.CreateDirectory(staging);
                using (ZipArchive archive = new ZipArchive(new MemoryStream(zip), ZipArchiveMode.Read))
                {
                    foreach (string f in Const.EngineFiles)
                    {
                        ZipArchiveEntry found = null;
                        foreach (ZipArchiveEntry e in archive.Entries)
                        {
                            string full = e.FullName.Replace('\\', '/');
                            if (string.Equals(full, "bin/" + f, StringComparison.OrdinalIgnoreCase) ||
                                full.EndsWith("/bin/" + f, StringComparison.OrdinalIgnoreCase)) { found = e; break; }
                        }
                        if (found == null) throw new InvalidOperationException("В архиве zapret нет bin/" + f);
                        using (Stream src = found.Open())
                        using (FileStream dst = File.Create(Path.Combine(staging, f)))
                            src.CopyTo(dst);
                    }
                }
                File.WriteAllText(Path.Combine(staging, "version.txt"), realTag);
                engine.ReplaceEngine(staging);
                Log.Write("winws обновлён до zapret " + realTag + " (sha256 совпал)");
                Dictionary<string, object> result = new Dictionary<string, object>();
                result["ok"] = true;
                result["engine_version"] = realTag;
                return result;
            }
        }

        static byte[] Verified(byte[] data, long size, string sha)
        {
            if (size > 0 && data.Length != size) throw new IOException("размер " + data.Length + " вместо " + size);
            using (SHA256 h = SHA256.Create())
            {
                StringBuilder hex = new StringBuilder();
                foreach (byte b in h.ComputeHash(data)) hex.Append(b.ToString("x2"));
                if (hex.ToString() != sha) throw new IOException("sha256 не совпал");
            }
            return data;
        }

        static byte[] Fetch(string url, int limit)
        {
            HttpWebRequest req = (HttpWebRequest)WebRequest.Create(url);
            req.UserAgent = "FlowZap-Service/" + Const.Version;
            req.Accept = url.Contains("api.github.com") ? "application/vnd.github+json" : "*/*";
            req.Timeout = 20000;
            req.ReadWriteTimeout = 60000;
            req.AllowAutoRedirect = true;
            req.MaximumAutomaticRedirections = 10;
            using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
            using (Stream s = resp.GetResponseStream())
            using (MemoryStream ms = new MemoryStream())
            {
                byte[] buffer = new byte[65536];
                int n;
                while ((n = s.Read(buffer, 0, buffer.Length)) > 0)
                {
                    ms.Write(buffer, 0, n);
                    if (ms.Length > limit) throw new IOException("слишком большой файл");
                }
                return ms.ToArray();
            }
        }

        static void DeleteDir(string path)
        {
            if (!Directory.Exists(path)) return;
            Installer.CheckNoReparse(path);
            Directory.Delete(path, true);
        }
    }

    // ── Обновление самого FlowZap ─────────────────────────────────────────
    //
    // Замена FlowZap.exe и _internal\ после закрытия FlowZap — то же, что
    // скрипт PowerShell в core/updates/app.py, но делает это процесс, который
    // не заменяется, не умирает вместе с приложением и не зависит от
    // PowerShell (политики, антивирус, кодировки путей). Подпись архива уже
    // проверил FlowZap и распаковал его рядом с собой (_flowzap_update_*).
    //
    // Безопасность: со всеми файлами служба работает от имени того, кто
    // попросил (олицетворение клиента канала), — с его правами, а не с
    // правами системы. Через неё нельзя прочитать, заменить или удалить то,
    // что пользователь не может сам; ссылки и junction в папке FlowZap ничего
    // не дают. Новый FlowZap запускается тоже от его имени и в его сеансе.
    sealed class AppUpdate
    {
        const int MinWait = 5, MaxWait = 300, DefaultWait = 90;
        static int busy;    // одно обновление за раз

        WindowsIdentity user;
        Process app;
        int wait;
        string exe, dir, payload, newExe, newInt, curInt, exeOld, intOld, userLog;

        public static Dictionary<string, object> Begin(Dictionary<string, object> request, NamedPipeServerStream pipe, uint pid)
        {
            string exeArg = request.ContainsKey("exe") ? request["exe"] as string : null;
            string payloadArg = request.ContainsKey("payload") ? request["payload"] as string : null;
            if (string.IsNullOrEmpty(exeArg) || string.IsNullOrEmpty(payloadArg) || pid == 0)
                throw new ArgumentException("Некорректный запрос на обновление FlowZap");
            int wait = request.ContainsKey("wait") ? Convert.ToInt32(request["wait"]) : DefaultWait;
            if (Interlocked.CompareExchange(ref busy, 1, 0) != 0)
                throw new InvalidOperationException("Обновление FlowZap уже идёт");

            AppUpdate u = new AppUpdate();
            try
            {
                u.user = ClientIdentity(pipe);
                // Процесс FlowZap открываем сейчас, пока он на связи: ждать будем
                // именно его, а не программу, которой Windows потом отдаст тот же PID
                u.app = Process.GetProcessById((int)pid);
                IntPtr handle = u.app.Handle;
                u.wait = Math.Max(MinWait, Math.Min(MaxWait, wait));
                u.exe = Path.GetFullPath(exeArg);
                u.dir = Path.GetDirectoryName(u.exe);
                u.payload = Path.GetFullPath(payloadArg).TrimEnd('\\');
                u.newExe = Path.Combine(u.payload, "FlowZap.exe");
                u.newInt = Path.Combine(u.payload, "_internal");
                u.curInt = Path.Combine(u.dir, "_internal");
                u.exeOld = u.exe + ".old";
                u.intOld = u.curInt + ".old";
                u.userLog = Path.Combine(u.dir, "logs", "update.log");

                string error = null;
                u.AsUser(delegate { error = u.Check(); });
                if (error != null) throw new ArgumentException(error);

                // До запуска потока: после Start() объект принадлежит ему (Release)
                Log.Write("Обновление FlowZap принято: " + u.exe + " (жду закрытия PID " + pid + ", пользователь " + u.user.Name + ")");
                Thread t = new Thread(u.Run);
                t.IsBackground = true;
                t.Name = "app-update";
                t.Start();
            }
            catch (Exception)
            {
                u.Release();
                throw;
            }
            Dictionary<string, object> result = new Dictionary<string, object>();
            result["ok"] = true;
            result["accepted"] = true;
            return result;
        }

        // Кто на том конце канала. FlowZap подключается для этой команды с
        // уровнем «олицетворение» (обычное соединение — только «опознание»)
        static WindowsIdentity ClientIdentity(NamedPipeServerStream pipe)
        {
            WindowsIdentity result = null;
            pipe.RunAsClient(delegate
            {
                using (WindowsIdentity current = WindowsIdentity.GetCurrent(true))
                    if (current != null) result = new WindowsIdentity(current.Token);
            });
            if (result == null) throw new InvalidOperationException("Не удалось определить пользователя FlowZap");
            if (result.ImpersonationLevel != TokenImpersonationLevel.Impersonation &&
                result.ImpersonationLevel != TokenImpersonationLevel.Delegation)
            {
                result.Dispose();
                throw new InvalidOperationException("FlowZap подключился без права действовать от имени пользователя");
            }
            return result;
        }

        void Release()
        {
            if (app != null) { app.Dispose(); app = null; }
            if (user != null) { user.Dispose(); user = null; }
            Interlocked.Exchange(ref busy, 0);
        }

        void AsUser(Action action)
        {
            using (WindowsImpersonationContext context = user.Impersonate())
                action();
        }

        string Check()
        {
            if (!string.Equals(Path.GetFileName(exe), "FlowZap.exe", StringComparison.OrdinalIgnoreCase))
                return "Обновлять можно только FlowZap.exe";
            if (!File.Exists(exe)) return "Не найден " + exe;
            if (!string.Equals(Path.GetDirectoryName(payload), dir, StringComparison.OrdinalIgnoreCase) ||
                !Path.GetFileName(payload).StartsWith("_flowzap_update_", StringComparison.OrdinalIgnoreCase))
                return "Папка обновления должна лежать рядом с FlowZap.exe";
            if (!File.Exists(newExe)) return "В папке обновления нет FlowZap.exe";
            return null;
        }

        void Run()
        {
            try
            {
                bool closed;
                try { closed = app.WaitForExit(wait * 1000); }
                catch (Exception) { closed = true; }
                if (!closed)
                {
                    AsUser(delegate
                    {
                        UserLog("Обновление отменено: FlowZap не закрылся за " + wait + " с");
                        DeleteDir(payload);
                    });
                    Log.Write("Обновление FlowZap отменено: приложение не закрылось за " + wait + " с");
                    return;
                }
                bool ok = false, haveExe = false;
                AsUser(delegate
                {
                    ok = Swap();
                    DeleteDir(payload);
                    haveExe = File.Exists(exe);
                });
                Log.Write(ok ? "Обновление FlowZap установлено"
                             : "Обновление FlowZap не встало, вернул прежнюю версию (подробности в logs\\update.log FlowZap)");
                if (haveExe) Launch();
            }
            catch (Exception e)
            {
                Log.Write("Обновление FlowZap: " + e);
            }
            finally
            {
                Release();
            }
        }

        // Старое в сторону, новое на место; при любой ошибке — всё как было
        bool Swap()
        {
            TryDeleteFile(exeOld);
            if (!TryMove(exe, exeOld, false)) { UserLog("Обновление отменено: FlowZap.exe занят"); return false; }
            bool intMoved = false;
            if (Directory.Exists(newInt))
            {
                DeleteDir(intOld);
                if (Directory.Exists(curInt))
                {
                    if (!TryMove(curInt, intOld, true))
                    {
                        RestoreExe();
                        UserLog("Обновление отменено: папка _internal занята");
                        return false;
                    }
                    intMoved = true;
                }
                if (!TryMove(newInt, curInt, true))
                {
                    if (intMoved) RestoreInt();
                    RestoreExe();
                    UserLog("Откат: новая папка _internal не встала на место");
                    return false;
                }
            }
            if (!TryMove(newExe, exe, false))
            {
                if (intMoved) RestoreInt();
                RestoreExe();
                UserLog("Откат: новый FlowZap.exe не встал на место");
                return false;
            }
            TryDeleteFile(exeOld);
            DeleteDir(intOld);
            UserLog("Обновление установлено службой: " + exe);
            return true;
        }

        void RestoreExe()
        {
            if (File.Exists(exeOld)) TryMove(exeOld, exe, false);
        }

        void RestoreInt()
        {
            if (!Directory.Exists(intOld)) return;
            DeleteDir(curInt);
            TryMove(intOld, curInt, true);
        }

        // Несколько попыток: антивирус или индексатор могут ненадолго держать файл
        bool TryMove(string from, string to, bool isDir)
        {
            string last = null;
            for (int i = 0; i < 20; i++)
            {
                try
                {
                    if (isDir) Directory.Move(from, to);
                    else File.Move(from, to);
                    return true;
                }
                catch (Exception e)
                {
                    last = e.Message;
                    Thread.Sleep(500);
                }
            }
            UserLog("Не удалось перенести " + from + " → " + to + ": " + last);
            return false;
        }

        static void TryDeleteFile(string path)
        {
            try { if (File.Exists(path)) File.Delete(path); } catch (Exception) { }
        }

        static void DeleteDir(string path)
        {
            for (int i = 0; i < 5 && Directory.Exists(path); i++)
            {
                try { Directory.Delete(path, true); }
                catch (Exception) { Thread.Sleep(300); }
            }
        }

        void UserLog(string message)
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(userLog));
                File.AppendAllText(userLog, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " " + message + Environment.NewLine, Encoding.UTF8);
            }
            catch (Exception) { }
        }

        // Запустить новый FlowZap от имени пользователя в его сеансе, с его
        // переменными окружения (не системными: иначе APPDATA, TEMP — чужие)
        void Launch()
        {
            IntPtr primary = IntPtr.Zero, env = IntPtr.Zero;
            try
            {
                if (!Native.DuplicateTokenEx(user.Token, Native.TOKEN_ALL_ACCESS, IntPtr.Zero,
                        Native.SecurityImpersonation, Native.TokenPrimary, out primary))
                    throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
                if (!Native.CreateEnvironmentBlock(out env, primary, false)) env = IntPtr.Zero;
                Native.STARTUPINFO si = new Native.STARTUPINFO();
                si.cb = Marshal.SizeOf(typeof(Native.STARTUPINFO));
                si.lpDesktop = @"winsta0\default";
                Native.PROCESS_INFORMATION pi;
                if (!Native.CreateProcessAsUser(primary, exe, new StringBuilder(Native.Quote(exe)), IntPtr.Zero, IntPtr.Zero,
                        false, env != IntPtr.Zero ? Native.CREATE_UNICODE_ENVIRONMENT : 0, env, dir, ref si, out pi))
                {
                    int error = Marshal.GetLastWin32Error();
                    // --console (разработка): у обычного процесса нет права
                    // запускать от чужого маркера, а пользователь и так тот же
                    if (error == Native.ERROR_PRIVILEGE_NOT_HELD && Const.ConsoleMode)
                    {
                        ProcessStartInfo psi = new ProcessStartInfo(exe);
                        psi.UseShellExecute = false;
                        psi.WorkingDirectory = dir;
                        using (Process p = Process.Start(psi))
                            Log.Write("FlowZap запущен заново (консольный режим), PID " + p.Id);
                        return;
                    }
                    throw new System.ComponentModel.Win32Exception(error);
                }
                Native.CloseHandle(pi.hThread);
                Native.CloseHandle(pi.hProcess);
                Log.Write("FlowZap запущен заново, PID " + pi.dwProcessId);
            }
            catch (Exception e)
            {
                Log.Write("Не удалось запустить FlowZap после обновления: " + e.Message);
            }
            finally
            {
                if (env != IntPtr.Zero) Native.DestroyEnvironmentBlock(env);
                if (primary != IntPtr.Zero) Native.CloseHandle(primary);
            }
        }
    }

    // ── DNS ───────────────────────────────────────────────────────────────

    // Сначала напрямую через Windows API (DnsApi — доли секунды). Не вышло
    // (старая Windows без нужной функции, ошибка, Windows не приняла адреса) —
    // то же действие через PowerShell: медленно (первый запуск после загрузки
    // Windows — до 15 с), зато работает везде.
    sealed class DnsControl
    {
        // Адаптеры: при включении — через которые идёт интернет (есть маршрут по
        // умолчанию); нет таких — все включённые. Сброс — на всех включённых.
        // Status и маршруты не переводятся — работает на любом языке Windows.
        const string Script = @"
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
try {
    $up = @(Get-NetAdapter | Where-Object { $_.Status -eq 'Up' })
    if ($up.Count -eq 0) { Write-Output 'NOADAPTER'; exit 0 }
    $targets = $up
    if (__ENABLE__) {
        $gw = @(Get-NetRoute -DestinationPrefix '0.0.0.0/0', '::/0' -ErrorAction SilentlyContinue |
                Select-Object -ExpandProperty ifIndex -Unique)
        $withGw = @($up | Where-Object { $gw -contains $_.ifIndex })
        if ($withGw.Count -gt 0) { $targets = $withGw }
    }
    foreach ($a in $targets) {
        try {
            if (__ENABLE__) {
                Set-DnsClientServerAddress -InterfaceIndex $a.ifIndex -ServerAddresses @(__ADDRESSES__)
            } else {
                Set-DnsClientServerAddress -InterfaceIndex $a.ifIndex -ResetServerAddresses
            }
            Write-Output ('OK|' + $a.Name)
        } catch {
            Write-Output ('ERR|' + $a.Name + '|' + $_.Exception.Message)
        }
    }
    Clear-DnsClientCache -ErrorAction SilentlyContinue
} catch {
    Write-Output ('FATAL|' + $_.Exception.Message)
    exit 1
}
";

        readonly object gate = new object();
        int owner;          // кто включил DNS: его отключение сбрасывает DNS

        public Dictionary<string, object> Set(object[] servers, int client)
        {
            List<IPAddress> addresses = new List<IPAddress>();
            if (servers != null)
                foreach (object o in servers)
                {
                    IPAddress ip;
                    string text = o as string;
                    if (text == null || text.IndexOf('%') >= 0 || !IPAddress.TryParse(text, out ip) ||
                        (ip.AddressFamily != AddressFamily.InterNetwork && ip.AddressFamily != AddressFamily.InterNetworkV6))
                        throw new ArgumentException("Не IP-адрес: " + text);
                    addresses.Add(ip);
                }
            if (addresses.Count == 0 || addresses.Count > 8) throw new ArgumentException("Нужны IP-адреса DNS");
            lock (gate)
            {
                Dictionary<string, object> result = Run(true, addresses);
                owner = client;
                Log.Write("DNS установлен: " + string.Join(", ", addresses) + " (клиент " + client + ", " + result["method"] + ")");
                return result;
            }
        }

        public Dictionary<string, object> Reset(string reason)
        {
            lock (gate)
            {
                Dictionary<string, object> result = Run(false, new List<IPAddress>());
                owner = 0;
                Log.Write("DNS сброшен на автоматический (" + result["method"] + ")" + (reason == null ? "" : " — " + reason));
                return result;
            }
        }

        public void ClientGone(int client)
        {
            bool mine;
            lock (gate) { mine = owner != 0 && owner == client; }
            if (!mine) return;
            try { Reset("FlowZap отключился"); }
            catch (Exception e) { Log.Write("Сброс DNS: " + e.Message); }
        }

        public void Shutdown()
        {
            bool set;
            lock (gate) { set = owner != 0; }
            if (!set) return;
            try { Reset("служба останавливается"); }
            catch (Exception e) { Log.Write("Сброс DNS: " + e.Message); }
        }

        static Dictionary<string, object> Run(bool enable, List<IPAddress> addresses)
        {
            try
            {
                Dictionary<string, object> result = DnsApi.Apply(enable, addresses);
                int failed = ((List<object>)result["failed"]).Count;
                if (!(bool)result["no_adapter"] && failed == 0)
                {
                    result["method"] = "api";
                    return result;
                }
                Log.Write("DNS через API: " + (failed > 0 ? "не применился на адаптерах: " + failed : "не найдено включённых адаптеров") +
                          " — пробую через PowerShell");
            }
            catch (Exception e)
            {
                Log.Write("DNS через API: " + e.Message + " — пробую через PowerShell");
            }
            Dictionary<string, object> fallback = RunPowerShell(enable, addresses);
            fallback["method"] = "powershell";
            return fallback;
        }

        static Dictionary<string, object> RunPowerShell(bool enable, List<IPAddress> addresses)
        {
            List<string> quoted = addresses.ConvertAll(ip => "'" + ip.ToString() + "'");
            string script = Script.Replace("__ENABLE__", enable ? "$true" : "$false").Replace("__ADDRESSES__", string.Join(", ", quoted));
            ProcessStartInfo psi = new ProcessStartInfo(
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), @"WindowsPowerShell\v1.0\powershell.exe"),
                "-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand " +
                Convert.ToBase64String(Encoding.Unicode.GetBytes(script)));
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.StandardOutputEncoding = Encoding.UTF8;

            List<string> done = new List<string>();
            List<object> failed = new List<object>();
            bool noAdapter = false;
            string fatal = null;
            using (Process p = Process.Start(psi))
            {
                string stdout = p.StandardOutput.ReadToEnd();
                p.StandardError.ReadToEnd();
                if (!p.WaitForExit(30000)) { try { p.Kill(); } catch (Exception) { } throw new InvalidOperationException("Windows не ответила вовремя — попробуйте ещё раз"); }
                foreach (string raw in stdout.Split('\n'))
                {
                    string line = raw.Trim();
                    if (line == "NOADAPTER") noAdapter = true;
                    else if (line.StartsWith("OK|")) done.Add(line.Substring(3));
                    else if (line.StartsWith("ERR|"))
                    {
                        string[] parts = line.Substring(4).Split(new[] { '|' }, 2);
                        Dictionary<string, object> f = new Dictionary<string, object>();
                        f["name"] = parts[0];
                        f["error"] = parts.Length > 1 ? parts[1] : "";
                        failed.Add(f);
                        Log.Write("DNS: " + parts[0] + ": " + f["error"]);
                    }
                    else if (line.StartsWith("FATAL|")) fatal = line.Substring(6);
                }
            }
            if (fatal != null)
            {
                Log.Write("DNS: " + fatal);
                throw new InvalidOperationException("Не удалось изменить DNS — подробности в журнале службы");
            }
            Dictionary<string, object> result = new Dictionary<string, object>();
            result["ok"] = true;
            result["no_adapter"] = noAdapter;
            result["adapters"] = done;
            result["failed"] = failed;
            return result;
        }
    }

    // DNS напрямую через Windows API: SetInterfaceDnsSettings (iphlpapi,
    // Windows 10 2004+; на более старых — EntryPointNotFoundException, и
    // DnsControl повторит через PowerShell). Адаптеры — те же, что в скрипте
    // DnsControl. После записи адреса читаются обратно: если Windows сказала
    // «готово», а стоит другое, — это тоже ошибка (и тоже повтор через
    // PowerShell). IPv4 и IPv6 ставятся раздельно; семейство, для которого у
    // пары нет адресов, сбрасывается на автоматический — иначе при смене пары
    // остались бы IPv6-адреса прошлой.
    static class DnsApi
    {
        [StructLayout(LayoutKind.Sequential)]
        struct DNS_INTERFACE_SETTINGS
        {
            public uint Version;
            public ulong Flags;
            public IntPtr Domain;
            public IntPtr NameServer;
            public IntPtr SearchList;
            public uint RegistrationEnabled;
            public uint RegisterAdapterName;
            public uint EnableLLMNR;
            public uint QueryAdapterName;
            public IntPtr ProfileNameServer;
        }

        const uint DNS_INTERFACE_SETTINGS_VERSION1 = 1;
        const ulong DNS_SETTING_IPV6 = 0x1;
        const ulong DNS_SETTING_NAMESERVER = 0x2;
        const ushort AF_UNSPEC = 0;

        // MIB_IPFORWARD_TABLE2: NumEntries, выравнивание до 8, затем строки
        // MIB_IPFORWARD_ROW2 по 104 байта; в строке InterfaceIndex — смещение 8,
        // DestinationPrefix.PrefixLength — 40 (0 = маршрут по умолчанию).
        const int RouteRowsOffset = 8, RouteRowSize = 104, RouteIfIndex = 8, RoutePrefixLength = 40;

        [DllImport("iphlpapi.dll")]
        static extern int SetInterfaceDnsSettings(Guid iface, ref DNS_INTERFACE_SETTINGS settings);

        [DllImport("iphlpapi.dll")]
        static extern int GetInterfaceDnsSettings(Guid iface, ref DNS_INTERFACE_SETTINGS settings);

        [DllImport("iphlpapi.dll")]
        static extern void FreeInterfaceDnsSettings(ref DNS_INTERFACE_SETTINGS settings);

        [DllImport("iphlpapi.dll")]
        static extern int GetIpForwardTable2(ushort family, out IntPtr table);

        [DllImport("iphlpapi.dll")]
        static extern void FreeMibTable(IntPtr memory);

        [DllImport("dnsapi.dll")]
        static extern bool DnsFlushResolverCache();

        sealed class Adapter
        {
            public string Name;
            public Guid Id;
            public int Index;
        }

        // Ответ в том же виде, что у DnsControl.RunPowerShell
        public static Dictionary<string, object> Apply(bool enable, List<IPAddress> addresses)
        {
            List<Adapter> targets = UpAdapters();
            if (enable && targets.Count > 0)
            {
                HashSet<int> gateways = DefaultRouteInterfaces();
                List<Adapter> withGateway = targets.FindAll(a => gateways.Contains(a.Index));
                if (withGateway.Count > 0) targets = withGateway;
            }
            string v4 = Servers(addresses, AddressFamily.InterNetwork);
            string v6 = Servers(addresses, AddressFamily.InterNetworkV6);

            List<string> done = new List<string>();
            List<object> failed = new List<object>();
            foreach (Adapter a in targets)
            {
                string error = ApplyFamily(a.Id, false, v4) ?? ApplyFamily(a.Id, true, v6);
                if (error == null) { done.Add(a.Name); continue; }
                Dictionary<string, object> f = new Dictionary<string, object>();
                f["name"] = a.Name;
                f["error"] = error;
                failed.Add(f);
                Log.Write("DNS через API: " + a.Name + ": " + error);
            }
            if (done.Count > 0)
                try { DnsFlushResolverCache(); } catch (Exception) { }

            Dictionary<string, object> result = new Dictionary<string, object>();
            result["ok"] = true;
            result["no_adapter"] = targets.Count == 0;
            result["adapters"] = done;
            result["failed"] = failed;
            return result;
        }

        // null — на адаптере теперь ровно эти адреса этого семейства
        // (servers = "" — автоматический DNS), иначе текст ошибки.
        static string ApplyFamily(Guid id, bool ipv6, string servers)
        {
            int setError = Call(id, ipv6, servers);
            string now;
            int readError = Read(id, ipv6, out now);
            if (readError == 0 && Normalize(now) == Normalize(servers)) return null;
            // Семейство не настроено на адаптере (например, IPv6 выключен) —
            // сбрасывать нечего
            if (readError != 0 && servers.Length == 0) return null;
            if (setError != 0) return new System.ComponentModel.Win32Exception(setError).Message;
            if (readError != 0) return "не удалось проверить: " + new System.ComponentModel.Win32Exception(readError).Message;
            return "Windows оставила " + (now.Length > 0 ? now : "автоматический");
        }

        static int Call(Guid id, bool ipv6, string servers)
        {
            DNS_INTERFACE_SETTINGS s = new DNS_INTERFACE_SETTINGS();
            s.Version = DNS_INTERFACE_SETTINGS_VERSION1;
            s.Flags = DNS_SETTING_NAMESERVER | (ipv6 ? DNS_SETTING_IPV6 : 0);
            s.NameServer = Marshal.StringToHGlobalUni(servers);
            try { return SetInterfaceDnsSettings(id, ref s); }
            finally { Marshal.FreeHGlobal(s.NameServer); }
        }

        static int Read(Guid id, bool ipv6, out string servers)
        {
            servers = "";
            DNS_INTERFACE_SETTINGS s = new DNS_INTERFACE_SETTINGS();
            s.Version = DNS_INTERFACE_SETTINGS_VERSION1;
            s.Flags = DNS_SETTING_NAMESERVER | (ipv6 ? DNS_SETTING_IPV6 : 0);
            int error = GetInterfaceDnsSettings(id, ref s);
            if (error != 0) return error;
            if (s.NameServer != IntPtr.Zero) servers = Marshal.PtrToStringUni(s.NameServer) ?? "";
            FreeInterfaceDnsSettings(ref s);
            return 0;
        }

        static string Servers(List<IPAddress> addresses, AddressFamily family)
        {
            return string.Join(",", addresses.FindAll(ip => ip.AddressFamily == family).ConvertAll(ip => ip.ToString()));
        }

        // Windows хранит список через запятую, но может и через пробел
        static string Normalize(string servers)
        {
            List<string> list = new List<string>();
            foreach (string part in servers.Split(new[] { ',', ' ', ';' }, StringSplitOptions.RemoveEmptyEntries))
            {
                IPAddress ip;
                list.Add(IPAddress.TryParse(part, out ip) ? ip.ToString() : part);
            }
            return string.Join(",", list);
        }

        static List<Adapter> UpAdapters()
        {
            List<Adapter> result = new List<Adapter>();
            foreach (NetworkInterface ni in NetworkInterface.GetAllNetworkInterfaces())
            {
                if (ni.OperationalStatus != OperationalStatus.Up ||
                    ni.NetworkInterfaceType == NetworkInterfaceType.Loopback ||
                    ni.NetworkInterfaceType == NetworkInterfaceType.Tunnel) continue;
                Guid id;
                if (!Guid.TryParse(ni.Id, out id)) continue;
                Adapter a = new Adapter();
                a.Name = ni.Name;
                a.Id = id;
                a.Index = InterfaceIndex(ni);
                result.Add(a);
            }
            return result;
        }

        static int InterfaceIndex(NetworkInterface ni)
        {
            IPInterfaceProperties props = ni.GetIPProperties();
            try { if (ni.Supports(NetworkInterfaceComponent.IPv4)) return props.GetIPv4Properties().Index; }
            catch (NetworkInformationException) { }
            try { if (ni.Supports(NetworkInterfaceComponent.IPv6)) return props.GetIPv6Properties().Index; }
            catch (NetworkInformationException) { }
            return -1;
        }

        static HashSet<int> DefaultRouteInterfaces()
        {
            IntPtr table;
            int error = GetIpForwardTable2(AF_UNSPEC, out table);
            if (error != 0) throw new System.ComponentModel.Win32Exception(error);
            HashSet<int> result = new HashSet<int>();
            try
            {
                int count = Marshal.ReadInt32(table);
                for (int i = 0; i < count; i++)
                {
                    IntPtr row = new IntPtr(table.ToInt64() + RouteRowsOffset + (long)i * RouteRowSize);
                    if (Marshal.ReadByte(row, RoutePrefixLength) == 0)
                        result.Add(Marshal.ReadInt32(row, RouteIfIndex));
                }
            }
            finally { FreeMibTable(table); }
            return result;
        }
    }

    // ── Канал связи ───────────────────────────────────────────────────────

    sealed class Server
    {
        readonly Engine engine;
        readonly DnsControl dns = new DnsControl();
        readonly ManualResetEvent stopping = new ManualResetEvent(false);
        Thread acceptThread;
        int nextClient;

        public Server(Engine engine) { this.engine = engine; }

        public void Start()
        {
            acceptThread = new Thread(AcceptLoop);
            acceptThread.IsBackground = true;
            acceptThread.Name = "pipe-accept";
            acceptThread.Start();
        }

        public void Stop()
        {
            stopping.Set();
            engine.Shutdown();
            dns.Shutdown();
        }

        static PipeSecurity Security()
        {
            PipeSecurity ps = new PipeSecurity();
            ps.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.NetworkSid, null),
                PipeAccessRights.FullControl, AccessControlType.Deny));
            ps.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.InteractiveSid, null),
                PipeAccessRights.ReadWrite | PipeAccessRights.Synchronize, AccessControlType.Allow));
            ps.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null),
                PipeAccessRights.FullControl, AccessControlType.Allow));
            ps.AddAccessRule(new PipeAccessRule(new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null),
                PipeAccessRights.FullControl, AccessControlType.Allow));
            // Владелец процесса службы (LocalSystem; в --console — разработчик)
            // создаёт следующие экземпляры канала
            ps.AddAccessRule(new PipeAccessRule(WindowsIdentity.GetCurrent().User,
                PipeAccessRights.FullControl, AccessControlType.Allow));
            return ps;
        }

        void AcceptLoop()
        {
            while (!stopping.WaitOne(0))
            {
                NamedPipeServerStream pipe = null;
                try
                {
                    pipe = new NamedPipeServerStream(Const.PipeName, PipeDirection.InOut,
                        NamedPipeServerStream.MaxAllowedServerInstances, PipeTransmissionMode.Byte,
                        PipeOptions.Asynchronous, 65536, 65536, Security());
                    IAsyncResult ar = pipe.BeginWaitForConnection(null, null);
                    if (WaitHandle.WaitAny(new WaitHandle[] { ar.AsyncWaitHandle, stopping }) == 1)
                    {
                        pipe.Dispose();
                        return;
                    }
                    pipe.EndWaitForConnection(ar);
                    int id = Interlocked.Increment(ref nextClient);
                    NamedPipeServerStream connected = pipe;
                    pipe = null;
                    Thread t = new Thread(delegate() { Serve(connected, id); });
                    t.IsBackground = true;
                    t.Name = "client-" + id;
                    t.Start();
                }
                catch (Exception e)
                {
                    if (pipe != null) pipe.Dispose();
                    Log.Write("Канал: " + e.Message);
                    stopping.WaitOne(1000);
                }
            }
        }

        void Serve(NamedPipeServerStream pipe, int client)
        {
            uint pid = 0;
            Native.GetNamedPipeClientProcessId(pipe.SafePipeHandle.DangerousGetHandle(), out pid);
            Log.Write("Подключился FlowZap, PID " + pid + " (клиент " + client + ")");
            JavaScriptSerializer json = new JavaScriptSerializer();
            json.MaxJsonLength = int.MaxValue;
            try
            {
                while (true)
                {
                    byte[] head = ReadExact(pipe, 4);
                    if (head == null) break;
                    int size = BitConverter.ToInt32(head, 0);
                    if (size <= 0 || size > Const.MaxRequestBytes * 2) break;
                    byte[] body = ReadExact(pipe, size);
                    if (body == null) break;

                    Dictionary<string, object> reply;
                    try
                    {
                        Dictionary<string, object> request = json.DeserializeObject(Encoding.UTF8.GetString(body)) as Dictionary<string, object>;
                        if (request == null) throw new ArgumentException("Некорректный запрос");
                        reply = Handle(request, client, pipe, pid);
                    }
                    catch (Exception e)
                    {
                        reply = new Dictionary<string, object>();
                        reply["ok"] = false;
                        reply["error"] = e.Message;
                        if (!(e is ArgumentException || e is InvalidOperationException))
                            Log.Write("Ошибка запроса: " + e);
                    }
                    byte[] data = Encoding.UTF8.GetBytes(json.Serialize(reply));
                    pipe.Write(BitConverter.GetBytes(data.Length), 0, 4);
                    pipe.Write(data, 0, data.Length);
                    pipe.Flush();
                }
            }
            catch (IOException) { }
            catch (Exception e) { Log.Write("Клиент " + client + ": " + e.Message); }
            finally
            {
                try { pipe.Dispose(); } catch (Exception) { }
                engine.ClientGone(client);
                dns.ClientGone(client);
                Log.Write("FlowZap отключился (клиент " + client + ")");
            }
        }

        Dictionary<string, object> Handle(Dictionary<string, object> request, int client, NamedPipeServerStream pipe, uint pid)
        {
            string op = request.ContainsKey("op") ? request["op"] as string : null;
            long since = request.ContainsKey("since") ? Convert.ToInt64(request["since"]) : -1;
            switch (op)
            {
                case "hello":
                case "status":
                    return engine.Status(client, since);
                case "start":
                    return engine.Start(request, client);
                case "stop":
                    return engine.Stop(client);
                case "dns-set":
                    return dns.Set(request.ContainsKey("servers") ? request["servers"] as object[] : null, client);
                case "dns-reset":
                    return dns.Reset(null);
                case "engine-update":
                    return EngineUpdater.Update(request.ContainsKey("tag") ? request["tag"] as string : null, engine);
                case "app-update":
                    return AppUpdate.Begin(request, pipe, pid);
                default:
                    throw new ArgumentException("Неизвестная команда: " + op);
            }
        }

        static byte[] ReadExact(Stream s, int size)
        {
            byte[] buffer = new byte[size];
            int got = 0;
            while (got < size)
            {
                int n = s.Read(buffer, got, size - got);
                if (n <= 0) return null;
                got += n;
            }
            return buffer;
        }
    }

    sealed class Host : ServiceBase
    {
        Server server;

        public Host()
        {
            ServiceName = Const.ServiceName;
            CanStop = true;
            CanShutdown = true;
            AutoLog = false;
        }

        protected override void OnStart(string[] args)
        {
            server = new Server(new Engine());
            server.Start();
            Log.Write("Служба запущена, версия " + Const.Version);
        }

        protected override void OnStop()
        {
            if (server != null) server.Stop();
            Log.Write("Служба остановлена");
        }

        protected override void OnShutdown()
        {
            OnStop();
        }
    }

    // ── Установка / удаление (запускается с правами администратора) ──────

    static class Installer
    {
        // Службой можно управлять: система и администраторы — полностью,
        // вошедшие пользователи — только смотреть состояние и запускать её
        // (если остановлена), но не менять и не удалять.
        const string ServiceSddl =
            "D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)(A;;CCLCSWLOCRRCRP;;;IU)(A;;CCLCSWLOCRRC;;;SU)";

        public const int Ok = 0, NotAdmin = 2, Failed = 4;

        static bool IsAdmin()
        {
            return new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator);
        }

        public static int Install(string source, string coreVersion)
        {
            Paths.Root = Paths.DefaultRoot();
            if (!IsAdmin()) return NotAdmin;
            try
            {
                // winws берём из zapret FlowZap, если он уже скачан. Нет — служба
                // всё равно ставится (DNS работает), winws появится с zapret.
                source = string.IsNullOrEmpty(source) ? "" : Path.GetFullPath(source);
                bool haveEngine = source.Length > 0;
                foreach (string f in Const.EngineFiles)
                    if (haveEngine && !File.Exists(Path.Combine(source, f))) haveEngine = false;

                StopService();
                SecureTree();

                string self = Assembly.GetExecutingAssembly().Location;
                if (!string.Equals(Path.GetFullPath(self), Paths.Exe, StringComparison.OrdinalIgnoreCase))
                    CopyWithRetry(self, Paths.Exe);
                // winws у уже установленной службы не трогаем: им управляет сама
                // служба (engine-update), а WinDivert64.sys может быть занят
                // загруженным драйвером — перезапись обрывала обновление службы.
                bool engineInstalled = File.Exists(Path.Combine(Paths.Engine, "winws.exe"));
                if (haveEngine && !engineInstalled)
                {
                    try
                    {
                        foreach (string f in Const.EngineFiles)
                            CopyWithRetry(Path.Combine(source, f), Path.Combine(Paths.Engine, f));
                        File.WriteAllText(Path.Combine(Paths.Engine, "version.txt"), coreVersion ?? "");
                    }
                    catch (Exception e)
                    {
                        // Не беда: служба сама докачает winws перед первым запуском
                        Log.Append("install.log", "winws не скопирован (" + e.Message + ") — служба скачает его сама");
                    }
                }
                else if (!haveEngine && !engineInstalled)
                    Log.Append("install.log", "zapret ещё не скачан — служба ставится без winws");

                string bin = Native.Quote(Paths.Exe) + " --service";
                if (ServiceExists())
                    Sc("config", Const.ServiceName, "binPath=", bin, "start=", "auto", "obj=", "LocalSystem", "DisplayName=", Const.DisplayName);
                else
                    Sc("create", Const.ServiceName, "binPath=", bin, "start=", "auto", "obj=", "LocalSystem", "DisplayName=", Const.DisplayName);
                Sc("description", Const.ServiceName,
                    "Фоновая служба FlowZap: запускает обход блокировок (winws) по командам FlowZap. Сама ничего не делает.");
                Sc("failure", Const.ServiceName, "reset=", "86400", "actions=", "restart/5000/restart/10000/none/0");
                Sc("sdset", Const.ServiceName, ServiceSddl);
                Sc("start", Const.ServiceName);
                WaitStatus(ServiceControllerStatus.Running, 20);
                RemoveLegacyTask();
                Log.Append("install.log", "Установлена служба " + Const.Version + ", zapret " + coreVersion);
                return Ok;
            }
            catch (Exception e)
            {
                Log.Append("install.log", "Ошибка установки: " + e.Message);
                return Failed;
            }
        }

        public static int Uninstall()
        {
            Paths.Root = Paths.DefaultRoot();
            if (!IsAdmin()) return NotAdmin;
            try
            {
                StopService();
                if (ServiceExists()) Sc("delete", Const.ServiceName);
                // Драйвер WinDivert держит WinDivert64.sys из папки службы
                TrySc("stop", "WinDivert");
                TrySc("stop", "WinDivert14");
                DeleteTree(Paths.Root);
                string parent = Path.GetDirectoryName(Paths.Root);
                if (Directory.Exists(parent) && Directory.GetFileSystemEntries(parent).Length == 0)
                    Directory.Delete(parent);
                return Ok;
            }
            catch (Exception e)
            {
                Log.Append("install.log", "Ошибка удаления: " + e.Message);
                return Failed;
            }
        }

        // Задача Планировщика «FlowZap» от версий до 1.0 запускала FlowZap от
        // администратора. Без прав её не удалить — поэтому здесь; автозапуск
        // FlowZap после установки переносит в реестр пользователя.
        static void RemoveLegacyTask()
        {
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo(
                    Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "schtasks.exe"),
                    "/Delete /TN FlowZap /F");
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                using (Process p = Process.Start(psi))
                {
                    p.WaitForExit(15000);
                    if (p.ExitCode == 0) Log.Append("install.log", "Удалена старая задача автозапуска FlowZap");
                }
            }
            catch (Exception) { }
        }

        static bool ServiceExists()
        {
            foreach (ServiceController s in ServiceController.GetServices())
            {
                using (s)
                {
                    if (string.Equals(s.ServiceName, Const.ServiceName, StringComparison.OrdinalIgnoreCase)) return true;
                }
            }
            return false;
        }

        static void StopService()
        {
            if (!ServiceExists()) return;
            using (ServiceController s = new ServiceController(Const.ServiceName))
            {
                if (s.Status != ServiceControllerStatus.Stopped && s.Status != ServiceControllerStatus.StopPending)
                    s.Stop();
                s.WaitForStatus(ServiceControllerStatus.Stopped, TimeSpan.FromSeconds(30));
            }
            // Процесс службы мог ещё не выйти — ждём, иначе exe не перезаписать
            foreach (Process p in Process.GetProcessesByName("FlowZapService"))
            {
                try
                {
                    if (p.Id != Process.GetCurrentProcess().Id) p.WaitForExit(10000);
                }
                catch (Exception) { }
                finally { p.Dispose(); }
            }
        }

        static void WaitStatus(ServiceControllerStatus status, int seconds)
        {
            using (ServiceController s = new ServiceController(Const.ServiceName))
                s.WaitForStatus(status, TimeSpan.FromSeconds(seconds));
        }

        // Program Files\FlowZap и вложенные: система и администраторы — всё,
        // пользователи — только чтение. Без ссылок/junction на пути.
        static void SecureTree()
        {
            string parent = Path.GetDirectoryName(Paths.Root);
            foreach (string dir in new[] { parent, Paths.Root, Paths.Engine, Paths.Run, Paths.Logs })
                SecureDirectory(dir);
        }

        static void SecureDirectory(string path)
        {
            CheckNoReparse(path);
            DirectorySecurity acl = new DirectorySecurity();
            acl.SetAccessRuleProtection(true, false);
            acl.SetOwner(new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null));
            InheritanceFlags inherit = InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit;
            foreach (WellKnownSidType sid in new[] { WellKnownSidType.LocalSystemSid, WellKnownSidType.BuiltinAdministratorsSid })
                acl.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(sid, null), FileSystemRights.FullControl,
                    inherit, PropagationFlags.None, AccessControlType.Allow));
            acl.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(WellKnownSidType.BuiltinUsersSid, null),
                FileSystemRights.ReadAndExecute, inherit, PropagationFlags.None, AccessControlType.Allow));
            if (Directory.Exists(path)) Directory.SetAccessControl(path, acl);
            else Directory.CreateDirectory(path, acl);
        }

        public static void CheckNoReparse(string path)
        {
            for (DirectoryInfo dir = new DirectoryInfo(path); dir != null; dir = dir.Parent)
                if (dir.Exists && (dir.Attributes & FileAttributes.ReparsePoint) != 0)
                    throw new IOException("Папка службы не должна быть ссылкой: " + dir.FullName);
        }

        static void CopyWithRetry(string from, string to)
        {
            for (int i = 0; ; i++)
            {
                try { File.Copy(from, to, true); return; }
                catch (IOException)
                {
                    if (i >= 20) throw;
                    Thread.Sleep(300);
                }
            }
        }

        public static void DeleteTree(string path)
        {
            if (!Directory.Exists(path)) return;
            CheckNoReparse(path);
            foreach (string f in Directory.GetFiles(path, "*", SearchOption.AllDirectories))
            {
                bool deleted = false;
                for (int i = 0; i < 10 && !deleted; i++)
                {
                    try { File.SetAttributes(f, FileAttributes.Normal); File.Delete(f); deleted = true; }
                    catch (Exception) { Thread.Sleep(300); }
                }
                if (!deleted) Native.MoveFileEx(f, null, Native.MOVEFILE_DELAY_UNTIL_REBOOT);
            }
            try { Directory.Delete(path, true); }
            catch (Exception) { Native.MoveFileEx(path, null, Native.MOVEFILE_DELAY_UNTIL_REBOOT); }
        }

        static void Sc(params string[] args)
        {
            int code = RunSc(args);
            if (code != 0) throw new IOException("sc " + args[0] + " — код " + code);
        }

        public static void TrySc(params string[] args)
        {
            try { RunSc(args); } catch (Exception) { }
        }

        static int RunSc(string[] args)
        {
            StringBuilder line = new StringBuilder();
            foreach (string a in args) { if (line.Length > 0) line.Append(' '); line.Append(Native.Quote(a)); }
            ProcessStartInfo psi = new ProcessStartInfo(
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "sc.exe"), line.ToString());
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            using (Process p = Process.Start(psi))
            {
                if (!p.WaitForExit(30000)) throw new IOException("sc " + args[0] + " не ответил");
                return p.ExitCode;
            }
        }
    }

    static class Program
    {
        static int Main(string[] args)
        {
            string mode = args.Length > 0 ? args[0] : "";
            if (mode == "--service")
            {
                Paths.Root = Paths.DefaultRoot();
                ServiceBase.Run(new Host());
                return 0;
            }
            if (mode == "--install")
                return Installer.Install(args.Length >= 2 ? args[1] : "", args.Length >= 3 ? args[2] : "");
            if (mode == "--uninstall")
                return Installer.Uninstall();
            if (mode == "--version")
            {
                Console.WriteLine(Const.Version);
                return 0;
            }
            if (mode == "--console" && args.Length >= 3)
            {
                // Разработка: служба в обычном процессе, со своей папкой и каналом
                Paths.Root = Path.GetFullPath(args[1]);
                Const.PipeName = args[2];
                Const.ConsoleMode = true;
                Server server = new Server(new Engine());
                server.Start();
                Console.WriteLine("FlowZap Service " + Const.Version + " (console): \\\\.\\pipe\\" + Const.PipeName);
                new ManualResetEvent(false).WaitOne();
                return 0;
            }
            Console.WriteLine("FlowZap Service " + Const.Version + ". Устанавливается из FlowZap (Настройки → Фоновая служба).");
            return 1;
        }
    }
}
