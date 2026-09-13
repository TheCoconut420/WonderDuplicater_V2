using System;
using System.Text;
using System.Text.Json;

namespace BfresRenamer
{
    internal static class Program
    {
        static int Main(string[] args)
        {
            // Verhindert, dass .NET ein BOM vor die JSON-Ausgabe schreibt,
            // wenn stdout umgeleitet wird (z.B. durch subprocess.run in Python).
            Console.OutputEncoding = new UTF8Encoding(encoderShouldEmitUTF8Identifier: false);

            if (args.Length == 0)
            {
                Console.WriteLine(JsonSerializer.Serialize(new
                {
                    status = "error",
                    message = "Usage: BfresRenamer.exe <path> <oldName[,oldName2,...]> <newName>  |  --list <path>"
                }));
                return 1;
            }

            try
            {
                return CliRunner.Run(args);
            }
            catch (Exception ex)
            {
                Console.WriteLine(JsonSerializer.Serialize(new { status = "error", message = "Fatal: " + ex.Message }));
                return 1;
            }
            finally
            {
                Console.Out.Flush();
            }
        }
    }
}