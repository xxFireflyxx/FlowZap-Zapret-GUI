// Компилируется вместе с исходником службы, с отдельной точкой входа.
// Не вызывает Host/Server, установщик, сеть, DNS, драйвер или запуск winws.
using System;
using System.IO;

namespace FlowZap.Service
{
    static class RecoveryTests
    {
        static string root;
        static int passed;

        static void Setup(string name)
        {
            Paths.Root = Path.Combine(root, name);
            Directory.CreateDirectory(Paths.Root);
        }

        static void Payload(string path, string version)
        {
            Directory.CreateDirectory(path);
            foreach (string file in Const.EngineFiles)
                File.WriteAllText(Path.Combine(path, file), "test-file-" + version);
            File.WriteAllText(Path.Combine(path, "version.txt"), version);
        }

        static void Check(bool condition, string message)
        {
            if (!condition) throw new Exception(message);
            passed++;
        }

        static string Version(Engine engine)
        {
            return (string)engine.Status(1, -1)["engine_version"];
        }

        public static int Main()
        {
            root = Path.Combine(AppDomain.CurrentDomain.BaseDirectory,
                                "recovery-cases-" + Guid.NewGuid().ToString("N"));
            Setup("missing-current");
            Payload(Paths.Engine + ".old", "old");
            Check(Version(new Engine()) == "old", "Не восстановлена прежняя версия");
            Check(!Directory.Exists(Paths.Engine + ".old"), "Резервная папка не перенесена");

            Setup("incomplete-current");
            Payload(Paths.Engine + ".old", "old");
            Payload(Paths.Engine, "broken");
            File.Delete(Path.Combine(Paths.Engine, "WinDivert.dll"));
            Check(Version(new Engine()) == "old", "Неполная версия не заменена прежней");
            Check(Directory.GetDirectories(Paths.Root, "engine.incomplete-*").Length == 1,
                  "Неполная папка не сохранена");

            Setup("completed-replacement");
            Payload(Paths.Engine + ".old", "old");
            Payload(Paths.Engine, "new");
            Check(Version(new Engine()) == "new", "Успешная замена ошибочно отменена");

            Setup("partial-backup-cleanup");
            Payload(Paths.Engine + ".old", "old");
            File.Delete(Path.Combine(Paths.Engine + ".old", "winws.exe"));
            Payload(Paths.Engine, "new");
            Check(Version(new Engine()) == "new", "Сбой уборки испортил новую версию");

            Setup("incomplete-backup");
            Payload(Paths.Engine + ".old", "old");
            File.Delete(Path.Combine(Paths.Engine + ".old", "winws.exe"));
            Check(Version(new Engine()) == "", "Неполная копия объявлена рабочей");
            Check(Directory.Exists(Paths.Engine + ".old"), "Неполная копия уничтожена");

            Setup("locked-backup");
            Payload(Paths.Engine + ".old", "old");
            using (FileStream held = File.Open(Path.Combine(Paths.Engine + ".old", "winws.exe"),
                                               FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                Engine engine = new Engine();
                Check(Version(engine) == "", "Занятая копия объявлена установленной");
                Check(Directory.Exists(Paths.Engine + ".old"), "Занятая копия уничтожена");
            }
            Check(Version(new Engine()) == "old", "Восстановление не повторилось после снятия блокировки");

            Setup("first-start");
            Check(Version(new Engine()) == "", "Первый запуск без движка не работает");

            Setup("invalid-new-payload");
            Payload(Paths.Engine, "old");
            string candidate = Paths.Engine + ".new";
            Directory.CreateDirectory(candidate);
            bool rejected = false;
            try { new Engine().ReplaceEngine(candidate); }
            catch (InvalidOperationException) { rejected = true; }
            Check(rejected, "Неполная новая версия принята");
            Check(Engine.ReadEngineVersion() == "old", "Рабочая версия затронута");

            Console.WriteLine("Проверки восстановления службы: " + passed + " OK");
            return 0;
        }
    }
}
