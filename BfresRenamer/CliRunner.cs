using System;
using System.Linq;
using System.Text.Json;

namespace BfresRenamer
{
    internal static class CliRunner
    {
        // Aufruf: BfresRenamer.exe <pfad.bfres.zs> <alteNamen (kommasepariert)> <neuerName>
        // Diagnose:  BfresRenamer.exe --list <pfad.bfres.zs>
        public static int Run(string[] args)
        {
            if (args.Length >= 2 && args[0] == "--list")
            {
                return RunList(args[1]);
            }

            if (args.Length < 3)
            {
                Console.WriteLine(JsonSerializer.Serialize(new { status = "error", message = "Usage: <path> <oldName[,oldName2,...]> <newName>  |  --list <path>" }));
                return 1;
            }

            string path = args[0];
            string oldNamesArg = args[1];
            string newName = args[2];

            var oldNames = oldNamesArg
                .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);

            try
            {
                var core = new BfresCore();
                core.Load(path);
                var renamed = core.RenameExact(oldNames, newName);
                core.Save(path);

                Console.WriteLine(JsonSerializer.Serialize(new
                {
                    status = "ok",
                    renamed_count = renamed.Count,
                    renamed = renamed.Select(r => new { dict = r.DictName, old = r.OldKey, @new = r.NewKey })
                }));
                return 0;
            }
            catch (Exception ex)
            {
                Console.WriteLine(JsonSerializer.Serialize(new { status = "error", message = ex.Message }));
                return 1;
            }
        }

        private static int RunList(string path)
        {
            try
            {
                var core = new BfresCore();
                core.Load(path);
                var entries = core.CollectEntries();
                Console.WriteLine(JsonSerializer.Serialize(new
                {
                    status = "ok",
                    entries = entries.Select(e => new { dict = e.DictName, key = e.Key })
                }));
                return 0;
            }
            catch (Exception ex)
            {
                Console.WriteLine(JsonSerializer.Serialize(new { status = "error", message = ex.Message }));
                return 1;
            }
        }
    }
}